"""Link-preview images (Open Graph "share cards") and the site icons.

A share card is the picture iMessage, Discord, Facebook, Reddit and X show when
someone pastes a link. They expect 1200x630 (1.91:1); a square product photo
gets its top and bottom cropped off, so every card here is laid out at that
size on purpose.

- Listing cards are drawn on request by app.py (/share/listing/<slug>.jpg):
  the console's own photo, its title, price and availability.
- The site-wide cards and the icons are static files. Regenerate them after
  changing anything below with:

      python share_cards.py

Pillow only - no extra dependencies. Text uses the site's own fonts:
pixel-font.ttf for the wordmark and Px437 (an 8x16 VGA font) for everything
else, at multiples of 16px so it stays pixel-sharp.
"""
import io
import os
import textwrap
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, 'static')
FONT_DIR = os.path.join(STATIC, 'fonts')

CARD_SIZE = (1200, 630)

# The site palette (pages.css) plus the Game Boy colours from the order emails.
INK = (10, 10, 10)
PANEL = (22, 22, 22)
LINE = (51, 51, 51)
GREEN = (178, 242, 187)
GREEN_DIM = (109, 187, 122)
TEXT = (232, 232, 232)
MUTED = (154, 154, 154)
RED = (255, 107, 107)
AMBER = (255, 212, 59)
LCD = (178, 242, 187)
LCD_INK = (34, 34, 34)
LCD_SELECT = (68, 68, 68)

# Bump when the card layout changes, so platforms that cache previews by URL
# fetch the new design (app.py puts it in every card URL).
DESIGN_VERSION = '1'

# The GBA SP artwork: where its body and its transparent screen sit inside
# static/gbasp.png's 800x600 canvas (the same numbers pages.css uses).
SP_FILE = os.path.join(STATIC, 'gbasp.png')
SP_BODY = (252, 28, 549, 581)
SP_SCREEN = (295, 76, 295 + 216, 76 + 143)


@lru_cache(maxsize=None)
def _font(name, size):
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size)


def vga(size):
    """Px437 PhoenixVGA - sharp at 16, 32, 48 and 64px."""
    return _font('Px437_PhoenixVGA_8x16.ttf', size)


def wordmark_font(size):
    return _font('pixel-font.ttf', size)


@lru_cache(maxsize=None)
def _missing_glyph(font_name, size):
    """What the font draws for a character it does not have."""
    font = _font(font_name, size)
    mask = font.getmask('\U0010FFFD')
    return mask.size, bytes(mask)


def card_text(text, font):
    """Text the fonts can draw: dashes and quotes become ASCII, and anything
    else the font lacks (emoji, mostly) is dropped rather than shown as a box."""
    swaps = {'—': '-', '–': '-', '‘': "'", '’': "'",
             '“': '"', '”': '"', '…': '...', '•': '·',
             ' ': ' ', '\t': ' ', '\r': ' ', '\n': ' '}
    text = ''.join(swaps.get(ch, ch) for ch in (text or ''))
    name = os.path.basename(font.path)
    missing = _missing_glyph(name, font.size)
    kept = []
    for ch in text:
        if ch == ' ' or ord(ch) < 0x7F:
            kept.append(ch)
            continue
        mask = font.getmask(ch)
        if (mask.size, bytes(mask)) != missing:
            kept.append(ch)
    return ' '.join(''.join(kept).split())


def wrap(text, font, width, max_lines):
    """Word-wrap to a pixel width (monospace, so by character count), with an
    ellipsis on the last line when the text runs over."""
    char_w = font.getlength('M') or 1
    per_line = max(1, int(width // char_w))
    lines = textwrap.wrap(text, per_line) or ['']
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        lines[-1] = (last[:per_line - 3].rstrip() + '...') if len(last) > per_line - 3 else last + '...'
    return lines


@lru_cache(maxsize=None)
def _texture():
    tile = Image.open(os.path.join(STATIC, 'textures', 'axiom-pattern.png')).convert('RGBA')
    return tile


def background(size, color=INK):
    """Ink with the same faint "axiom" texture as the site's pages."""
    img = Image.new('RGB', size, color)
    tile = _texture()
    layer = Image.new('RGBA', size, (0, 0, 0, 0))
    for y in range(0, size[1], tile.height):
        for x in range(0, size[0], tile.width):
            layer.paste(tile, (x, y))
    img.paste(layer, (0, 0), layer)
    return img


# ---------------------------------------------------------------- the SP --

@lru_cache(maxsize=None)
def _sp_art():
    return Image.open(SP_FILE).convert('RGBA').crop(SP_BODY)


def draw_sp(rows, active=0, height=553):
    """The GBA SP with a menu on its screen, like the landing page's demo.
    rows: list of strings. Returns an RGBA image `height` px tall."""
    art = _sp_art()
    scale = height / art.height
    body = Image.new('RGBA', art.size, (0, 0, 0, 0))

    # Screen first, then the shell on top: the screen hole in the artwork is
    # transparent, exactly as on the website.
    sx0, sy0 = SP_SCREEN[0] - SP_BODY[0], SP_SCREEN[1] - SP_BODY[1]
    sw, sh = SP_SCREEN[2] - SP_SCREEN[0], SP_SCREEN[3] - SP_SCREEN[1]
    screen = Image.new('RGB', (sw, sh), LCD)
    d = ImageDraw.Draw(screen)
    font = vga(16)
    row_h = 24
    top = max(4, (sh - row_h * len(rows)) // 2)
    for i, label in enumerate(rows):
        y = top + i * row_h
        label = card_text(label, font)
        fits = wrap(label, font, sw - 20, 1)[0]
        if i == active:
            d.rectangle((6, y, sw - 7, y + row_h - 3), fill=LCD_SELECT)
            d.text((12, y + 3), fits, font=font, fill=(255, 255, 255))
        else:
            d.text((12, y + 3), fits, font=font, fill=LCD_INK)
    body.paste(screen, (sx0, sy0))
    body.alpha_composite(art)
    if scale != 1:
        body = body.resize((round(art.width * scale), height), Image.LANCZOS)
    return body


# --------------------------------------------------------------- helpers --

def _bullet_line(draw, xy, items, font, fill=MUTED, bullet=GREEN):
    """'■ item   ■ item' - the fonts have no check mark, so the bullet is a
    drawn square."""
    x, y = xy
    size = font.size // 2
    for item in items:
        offset = (font.size - size) // 2
        draw.rectangle((x, y + offset, x + size - 1, y + offset + size - 1), fill=bullet)
        x += size + font.size // 2
        draw.text((x, y), item, font=font, fill=fill)
        x += font.getlength(item) + font.size
    return x


def _chip(draw, xy, label, font, bg, fg):
    x, y = xy
    w = font.getlength(label)
    pad_x, pad_y = 12, 6
    draw.rectangle((x, y, x + w + pad_x * 2, y + font.size + pad_y * 2), fill=bg)
    draw.text((x + pad_x, y + pad_y), label, font=font, fill=fg)
    return x + w + pad_x * 2


def to_jpeg(img, quality=86):
    buf = io.BytesIO()
    img.convert('RGB').save(buf, 'JPEG', quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


# ------------------------------------------------------------ site cards --

MAIN_MENU = ['Shop', 'Cart', 'Sell To Us', 'Notify Me', 'About']

SITE_CARDS = {
    # filename: (headline lines, trust items, screen rows, highlighted row)
    'gameboy-retreat.jpg': (
        ['Refurbished', 'Game Boys,', 'restored by hand.'],
        ['30-day warranty', 'Free shipping'],
        MAIN_MENU, 0),
    'shop-in-a-game-boy.jpg': (
        ['Shop inside', 'a working', 'Game Boy.'],
        ['D-pad to browse', 'A to buy'],
        MAIN_MENU, 0),
}


def draw_site_card(headline, trust, rows, active):
    img = background(CARD_SIZE)
    d = ImageDraw.Draw(img)

    sp = draw_sp(rows, active, height=553)
    img.paste(sp, (CARD_SIZE[0] - sp.width - 96, (CARD_SIZE[1] - sp.height) // 2), sp)

    left = 72
    d.text((left, 70), 'GAMEBOY RETREAT', font=wordmark_font(40), fill=GREEN)
    big = vga(48)
    y = 176
    for line in headline:
        d.text((left, y), line, font=big, fill=TEXT)
        y += 64
    _bullet_line(d, (left, y + 28), trust, vga(32))
    d.text((left, CARD_SIZE[1] - 72 - 32), 'gameboyretreat.com', font=vga(32), fill=GREEN)
    return img


# --------------------------------------------------------- listing cards --

def listing_status(listing):
    """(chip label, chip colour, chip text colour) for a listing dict from
    app.fetch_listings()."""
    if listing.get('is_service'):
        if listing.get('in_stock'):
            return 'BOOKINGS OPEN', GREEN, INK
        return 'OPENING SOON', AMBER, INK
    if listing.get('in_stock'):
        return 'AVAILABLE', GREEN, INK
    if listing.get('status') == 'coming_soon':
        return 'COMING SOON', AMBER, INK
    return 'SOLD OUT', RED, (255, 255, 255)


def listing_trust(listing):
    kind = listing.get('item_type')
    if kind == 'lesson':
        return 'One-to-one over Zoom'
    if kind == 'service':
        return '30-day warranty on all work'
    return '30-day warranty · Free shipping'


def square_photo(photo, size):
    """Centre-crop to a square and resize, cheaply: reduce() first so a 1600px
    photo is not resampled at full size on a small server."""
    photo = photo.convert('RGB')
    w, h = photo.size
    side = min(w, h)
    photo = photo.crop(((w - side) // 2, (h - side) // 2,
                        (w - side) // 2 + side, (h - side) // 2 + side))
    factor = side // (size * 2)
    if factor >= 2:
        photo = photo.reduce(factor)
    return photo.resize((size, size), Image.LANCZOS)


def draw_listing_card(listing, category, photo=None):
    """JPEG bytes. `photo` is a PIL image of the listing's first photo, or None
    to show the SP with the listing on its screen instead."""
    W, H = CARD_SIZE
    img = background(CARD_SIZE)
    d = ImageDraw.Draw(img)

    title = card_text(listing.get('title', ''), vga(32)) or 'Gameboy Retreat'
    price = listing.get('price') or 0
    price_text = '$' + (f'{price:,.0f}' if float(price).is_integer() else f'{price:,.2f}')

    if photo is not None:
        img.paste(square_photo(photo, H), (0, 0))
        d.line((H, 0, H, H), fill=GREEN, width=4)
    else:
        panel = Image.new('RGB', (H, H), PANEL)
        img.paste(panel, (0, 0))
        rows = wrap(title, vga(16), 196, 3) + ['', price_text]
        sp = draw_sp(rows, active=len(rows) - 1, height=560)
        img.paste(sp, ((H - sp.width) // 2, (H - sp.height) // 2), sp)
        d.line((H, 0, H, H), fill=LINE, width=2)

    x = H + 48
    width = W - x - 48
    d.text((x, 44), 'GAMEBOY RETREAT', font=wordmark_font(30), fill=GREEN)

    y = 128
    if category:
        d.text((x, y), card_text(category, vga(16)).upper(), font=vga(16), fill=GREEN_DIM)
        y += 32

    title_font = vga(32)
    lines = wrap(title, title_font, width, 4)
    for line in lines:
        d.text((x, y), line, font=title_font, fill=TEXT)
        y += 40

    y += 18
    d.text((x, y), price_text, font=vga(64), fill=GREEN)
    y += 64 + 22

    label, bg, fg = listing_status(listing)
    _chip(d, (x, y), label, vga(16), bg, fg)

    d.text((x, H - 44 - 16 - 30), listing_trust(listing), font=vga(16), fill=MUTED)
    d.text((x, H - 44 - 16), 'gameboyretreat.com', font=vga(16), fill=GREEN)
    return to_jpeg(img)


# ----------------------------------------------------------------- icons --
# A Game Boy in the email frame's colours, drawn pixel by pixel so it stays
# crisp at 16px instead of being a blurry shrunk photo. Bigger sizes are the
# same pixels scaled up by whole numbers.

ICON_COLORS = {
    '.': INK,                   # the tile
    'G': (200, 200, 200),       # shell
    'g': (142, 142, 150),       # shell shadow / start-select
    'B': (46, 46, 54),          # bezel and D-pad
    'S': LCD,                   # screen
    's': (27, 42, 29),          # screen ink
    'R': (255, 90, 90),         # power LED
    'A': (184, 50, 90),         # A and B buttons
}

ICON_32 = [
    '................................',
    '................................',
    '.........GGGGGGGGGGGGGG.........',
    '........GGGGGGGGGGGGGGGG........',
    '........GGBBBBBBBBBBBBGG........',
    '........GGBBSSSSSSSSBBGG........',
    '........GGBBSsSsssSSBBGG........',
    '........GGRBSSSSSSSSBBGG........',
    '........GGBBSSSsssSSBBGG........',
    '........GGBBSSSSSSSSBBGG........',
    '........GGBBSSSssssSBBGG........',
    '........GGBBSSSSSSSSBBGG........',
    '........GGBBBBBBBBBBBBGG........',
    '........GGBBBBBBBBBBBGGG........',
    '........GGGGGGGGGGGGGGGG........',
    '........GGGGGGGGGGGGGGGG........',
    '........GGGGGGGGGGGGGGGG........',
    '........GGGGGGGGGGGGGGGG........',
    '........GGGGBBGGGGGGGGGG........',
    '........GGGGBBGGGGGGAAGG........',
    '........GGBBBBBBGGGGAAGG........',
    '........GGBBBBBBGAAGGGGG........',
    '........GGGGBBGGGAAGGGGG........',
    '........GGGGBBGGGGGGGGGG........',
    '........GGGGGGGGGGGGGGGG........',
    '........GGGGGggGggGGGGGG........',
    '........GGGGGGGGGGGGGGG.........',
    '........GGGGGGGGGGGGGG..........',
    '........GGGGGGGGGGGGG...........',
    '.........GGGGGGGGGGG............',
    '................................',
    '................................',
]

ICON_16 = [
    '................',
    '.....GGGGGG.....',
    '....GGGGGGGG....',
    '....GBBBBBBG....',
    '....GBSSSSBG....',
    '....GRSSSSBG....',
    '....GBSSSSBG....',
    '....GBBBBBGG....',
    '....GGGGGGGG....',
    '....GGBGGGAG....',
    '....GBBBGAGG....',
    '....GGBGGGGG....',
    '....GGGGGGGG....',
    '....GGGGGGG.....',
    '.....GGGGG......',
    '................',
]


def pixel_icon(grid, scale=1, rounded=True):
    n = len(grid)
    img = Image.new('RGBA', (n, n), (0, 0, 0, 0))
    px = img.load()
    for y, row in enumerate(grid):
        assert len(row) == n, (y, len(row))
        for x, ch in enumerate(row):
            px[x, y] = ICON_COLORS[ch] + (255,)
    if rounded:
        # Round the tile's corners (radius ~ 1/5 of the side) so it sits well
        # in a browser tab; the Game Boy itself never reaches the corners.
        mask = Image.new('L', (n * 8, n * 8), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, n * 8 - 1, n * 8 - 1),
                                               radius=n * 8 // 5, fill=255)
        img.putalpha(mask.resize((n, n), Image.LANCZOS))
    if scale != 1:
        img = img.resize((n * scale, n * scale), Image.NEAREST)
    return img


def build_icons(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    fav16 = pixel_icon(ICON_16)
    fav32 = pixel_icon(ICON_32)
    fav48 = pixel_icon(ICON_16, scale=3)
    fav16.save(os.path.join(out_dir, 'favicon-16.png'))
    fav32.save(os.path.join(out_dir, 'favicon-32.png'))
    fav48.save(os.path.join(out_dir, 'favicon-48.png'))
    fav48.save(os.path.join(out_dir, 'favicon.ico'), format='ICO',
               sizes=[(16, 16), (32, 32), (48, 48)], append_images=[fav16, fav32])

    # iOS draws its own rounded corners and turns transparency black, so the
    # touch icon is a full square: 32px art at 5x, padded out to 180px.
    art = pixel_icon(ICON_32, scale=5, rounded=False)
    touch = Image.new('RGB', (180, 180), INK)
    touch.paste(art, (10, 10), art)
    touch.save(os.path.join(out_dir, 'apple-touch-icon.png'))

    for size in (192, 512):
        art = pixel_icon(ICON_32, scale=size // 32, rounded=False).convert('RGB')
        art.save(os.path.join(out_dir, f'icon-{size}.png'))


def build_site_cards(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for name, spec in SITE_CARDS.items():
        with open(os.path.join(out_dir, name), 'wb') as fh:
            fh.write(to_jpeg(draw_site_card(*spec)))


if __name__ == '__main__':
    build_icons(os.path.join(STATIC, 'icons'))
    build_site_cards(os.path.join(STATIC, 'share'))
    print('Wrote static/icons/* and static/share/*')
