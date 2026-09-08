"""Shave the dark fringe left behind by GIMP's fuzzy select.

Fuzzy select with antialiasing on leaves a ring of near-black pixels around the
cut-out. Color to Alpha is the usual fix, but it is global - on a shell that is
legitimately black in places it eats the artwork along with the fringe.

This works on the *silhouette boundary* instead. Each pass finds only the pixels
sitting on the outer edge of the opaque region and drops the ones that are dark,
so interior black is never touched no matter how many passes you run.

Usage:
    python tools/trim_shell_edges.py static/gba.png
    python tools/trim_shell_edges.py static/gba.png --passes 2
    python tools/trim_shell_edges.py static/gba.png --threshold 90
    python tools/trim_shell_edges.py static/gba.png --any-color

The original is never modified; output goes to <name>_trimmed.png unless you
pass -o/--output.
"""

import argparse
import os
import sys

try:
    from PIL import Image, ImageChops, ImageFilter
except ImportError:
    sys.exit("Pillow is required:  python -m pip install Pillow")


def trim(image, passes=1, threshold=60, alpha_cutoff=128, any_color=False):
    """Erode `passes` pixels off the outer edge of the opaque region.

    threshold    - a boundary pixel is removed when its luminance is at or below
                   this (0-255). Raise it if a lighter grey halo survives.
    alpha_cutoff - alpha at or above this counts as opaque. Semi-transparent
                   pixels below it are flattened out at the end.
    any_color    - remove every boundary pixel, not just the dark ones.
    """
    image = image.convert('RGBA')

    for _ in range(passes):
        alpha = image.getchannel('A')

        # Binary silhouette, then a 3x3 minimum filter to pull it in by one pixel.
        solid = alpha.point(lambda a: 255 if a >= alpha_cutoff else 0)
        eroded = solid.filter(ImageFilter.MinFilter(3))

        # What the erosion removed - a one-pixel outline of the silhouette.
        boundary = ImageChops.subtract(solid, eroded)

        if any_color:
            doomed = boundary
        else:
            # Keep only the boundary pixels that are actually dark.
            luma = image.convert('L')
            dark = luma.point(lambda v: 255 if v <= threshold else 0)
            doomed = ImageChops.multiply(boundary, dark)

        # Anywhere marked for removal, force alpha to zero.
        keep = doomed.point(lambda v: 0 if v else 255)
        image.putalpha(ImageChops.multiply(alpha, keep))

    # Flatten whatever partial transparency the fuzzy select left behind so the
    # edge reads crisp rather than smudged.
    final_alpha = image.getchannel('A').point(
        lambda a: 255 if a >= alpha_cutoff else 0
    )
    image.putalpha(final_alpha)

    return image


def main():
    parser = argparse.ArgumentParser(
        description="Remove the dark halo around a cut-out PNG."
    )
    parser.add_argument('image', help='PNG to clean up')
    parser.add_argument('-o', '--output', help='output path (default: <name>_trimmed.png)')
    parser.add_argument('-p', '--passes', type=int, default=1,
                        help='pixels to shave off the edge (default: 1)')
    parser.add_argument('-t', '--threshold', type=int, default=60,
                        help='max luminance treated as fringe, 0-255 (default: 60)')
    parser.add_argument('-a', '--alpha-cutoff', type=int, default=128,
                        help='alpha at or above this counts as opaque (default: 128)')
    parser.add_argument('--any-color', action='store_true',
                        help='shave every edge pixel, not just dark ones')
    args = parser.parse_args()

    if not os.path.exists(args.image):
        sys.exit(f"No such file: {args.image}")

    output = args.output
    if not output:
        stem, ext = os.path.splitext(args.image)
        output = f"{stem}_trimmed{ext or '.png'}"

    def opaque_count(img):
        mask = img.getchannel('A').point(lambda a: 255 if a >= args.alpha_cutoff else 0)
        return mask.histogram()[255]

    original = Image.open(args.image)
    opaque_before = opaque_count(original.convert('RGBA'))

    cleaned = trim(
        original,
        passes=args.passes,
        threshold=args.threshold,
        alpha_cutoff=args.alpha_cutoff,
        any_color=args.any_color,
    )

    opaque_after = opaque_count(cleaned)

    cleaned.save(output)

    removed = opaque_before - opaque_after
    pct = (removed / opaque_before * 100) if opaque_before else 0
    print(f"{args.image}  {original.size[0]}x{original.size[1]}")
    print(f"  removed {removed:,} edge pixels ({pct:.2f}% of the silhouette)")
    print(f"  wrote {output}")


if __name__ == '__main__':
    main()
