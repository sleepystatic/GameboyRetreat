# The Game — Design Notes

A small original game that runs inside the on-screen Game Boy. Working title TBD.
Built by hand, not generated — this doc is the spec, not the implementation.

---

## 1. Concept

A side-scrolling grapple-traversal game. You play the Piston Alley character. You
swing between anchor points using a grappling hand, and the red antenna on his head
is the entire HUD.

The target feeling is **comfortingly monotonous** — rhythm and momentum, not
challenge. Closer to skipping stones than to a platformer. There is no death, no
timer, and no score to protect.

### Why grapple and not running

A runner needs obstacles to be interesting, and obstacles need failure. A grapple
is satisfying on its own: the arc, the release, the moment you get the timing right.
That is a loop that can be gentle and still hold attention.

---

## 2. Resolution — 160 x 144

Native render target is **160 x 144**: the original Game Boy's resolution. Scaled by
whole numbers only, never fractionally.

| Console view | screen element | scale | rendered |
|---|---|---|---|
| GBA (desktop) | 546 x 327 | **2x** | 320 x 288, letterboxed |
| GBASP (mobile portrait) | 218 x 160 | **1x** | 160 x 144 |

Integer scaling with `image-rendering: pixelated` keeps every pixel square and sharp.
A fractional scale would produce the same mush that made the menu text look blurry
before the `zoom` fix.

160 x 144 also happens to be the right creative constraint: it forces big readable
sprites and stops the scope from sprawling.

### Palette

Four shades, Game Boy style, sampled from the site's existing green so the game
belongs to the shop:

```
lightest  #b2f2bb   (already the screen colour)
light     #7bc98a
dark      #3d7a52
darkest   #14291c
```

The antenna is the one thing allowed to break the palette — a warm red that only it
uses, so the eye always finds it.

---

## 3. Input

### Mapping

| Action | Button | Keyboard |
|---|---|---|
| Aim | D-pad (8 directions) | Arrows |
| Fire / attach grapple | A | Enter |
| Detach | B | Escape |
| Reel in / out | Up / Down while attached | Arrows |
| Pause | Start | Space |
| Exit to menu | Select (hold) | Shift |

### The problem that has to be solved first

The existing button zones dispatch a **single synthetic `keydown` on click/touchend**.
That is right for a menu and wrong for a game — there is no `keyup`, so nothing can
know a button is being *held*, and aiming needs held state.

The game needs a small input layer that:

- listens to `pointerdown` / `pointerup` / `pointercancel` on `.button-zones .btn`
- tracks a live set of pressed buttons rather than firing events
- mirrors physical `keydown` / `keyup` into the same set
- handles `pointercancel` and window blur by clearing everything, or a button sticks
  down when a finger slides off it

The menu keeps its current behaviour; the game reads the pressed set each frame.

---

## 4. Mechanics

### Grapple

- **Aim** with the D-pad in 8 directions. No free aim: there is no pointer inside the
  console, and free aim would work on desktop and be unusable on a phone.
- **A** fires the hand toward the nearest valid anchor within a cone around the aim
  direction. Snapping to an anchor rather than requiring pixel precision is what
  makes it feel good on a D-pad.
- The hand extends over a few frames, attaches, and the player swings on a rope.
- **Up / Down** reel in and out while attached — reeling in tightens the arc and
  speeds you up, the main skill expression in the game.
- **B**, or releasing **A**, detaches and you keep your momentum.
- Cooldown of roughly 0.4s after detaching before another grapple can fire.

### Physics feel

Numbers are starting points, tune by feel:

```
gravity              0.35 px/frame^2
air drag             0.99 multiplier per frame
rope stiffness       pendulum constraint, not a spring
reel speed           0.8 px/frame
max rope length      96 px  (~60% of screen height)
min rope length      16 px
grapple travel       6 px/frame
grapple cooldown     24 frames
terminal velocity    8 px/frame
```

Fixed timestep at 60Hz with an accumulator, so physics does not change with frame
rate. Render interpolates between steps.

### The antenna is the HUD

No bars, no numbers, no UI chrome on a 160 x 144 screen.

| Antenna state | Means |
|---|---|
| Dark | On cooldown |
| Dim glow | Ready |
| Steady bright | An anchor is in range of the current aim |
| Rapid pulse | Attached and reeling in |
| Slow fade | Free-falling for a while, grapple ready |

This is the whole interface, rendered on the character. It also gives the sprite a
reason to exist beyond decoration.

---

## 5. No fail state

Falling off the bottom does not kill you. The world has a soft floor: drop below it
and a gentle updraft carries you back into anchor range over a second or two. The
antenna fades slowly during this so it reads as a rest, not a punishment.

Nothing is lost. There is no score to protect, so there is no tension to resolve.

**Progress is distance travelled**, shown only when you pause. Best distance is
stored locally. That is the entire progression system in v1.

---

## 6. World

Procedurally placed anchors, generated a screen ahead and discarded a screen behind.

- Anchor spacing: 40-90 px apart, height varying within a band
- Never generate a gap that cannot be crossed at reasonable speed — check reachability
  against max rope length before placing
- Three parallax layers: far (slow, darkest), mid, near (fast, silhouettes)
- Over distance the background shifts through a slow day/night cycle. That is the only
  "progression" the world needs — it gives a reason to keep going without adding
  difficulty.

---

## 7. Integration with the site

### Entry

A `Play` item in the main menu, and eventually the Konami code as an alternate way in.

### Takeover

The pattern already exists in `enlargeImage()`: remove `mainKeyHandler`, replace the
screen contents, restore both on exit. The game does the same:

1. Stash `screen.innerHTML`
2. Remove `mainKeyHandler` so the menu cannot act on input
3. Mount `<canvas width=160 height=144>` scaled with CSS
4. Attach the game's own input layer
5. On exit: cancel the RAF loop, detach listeners, restore the stashed HTML,
   re-add `mainKeyHandler`, call `updateActivate(currentIndex)`

### Things that will break if forgotten

- **Cancel `requestAnimationFrame` on exit.** A leaked loop keeps running behind the
  menu and eats battery.
- **Pause on `visibilitychange`.** A backgrounded tab throttles RAF and the
  accumulator will try to catch up with a huge delta on return.
- **Re-mount on console switch.** The screen element is the same node but the size
  changes between GBA and GBASP; the canvas scale has to be recomputed. The existing
  `MutationObserver` on `data-console` is the hook.
- **The chat widget still takes focus.** Guard against the chat input swallowing keys,
  the way `mainKeyHandler` already does.

### Save data

`localStorage`, one key, versioned so the shape can change later:

```json
{ "v": 1, "best": 0, "runs": 0, "muted": false }
```

---

## 8. Assets

| Asset | Size | Frames |
|---|---|---|
| Player idle | 16 x 16 | 2 |
| Player swing | 16 x 16 | 4 (arc positions) |
| Player fall | 16 x 16 | 2 |
| Player land | 16 x 16 | 2 |
| Antenna glow | overlay | 4 brightness levels |
| Grapple hand | 8 x 8 | 1 + rope drawn procedurally |
| Anchor point | 8 x 8 | 2 (idle, latched) |
| Parallax layers | 160 x 144 tiles | 3 |

The side-profile sprite that already exists is the base for all player frames.

Rope is drawn as a line, not a sprite — cheaper and it can flex.

---

## 9. Scope

**v1 — ship this and nothing else:**

- One screen of parallax, one anchor type
- Grapple: aim, fire, swing, reel, release
- Antenna HUD
- Soft floor, no death
- Distance tracking, local best
- Pause and exit

Explicitly **not** in v1: enemies, collectibles, levels, sound, multiple characters,
an ending, leaderboards.

**Later, in rough order:**

1. Sound — a boot chime and the grapple thunk would add a lot
2. Day/night over distance
3. Konami-code entry
4. Play on the shell colourway the customer actually bought
5. Second anchor type (moving, or one-use)

---

## 10. Open questions

1. **Horizontal or vertical?** This doc assumes horizontal traversal. A vertical climb
   is a different game with the same mechanic and might suit "comfortingly monotonous"
   better — there is no pressure to move forward, only up.
2. **Does the camera push?** Fixed follow is gentler. A slow rightward push adds mild
   urgency. The design here assumes pure follow.
3. **Is the antenna diegetic-only?** Or does distance appear on screen while playing?
   This doc says pause-only.
4. **How does it read at 1x on a phone?** 160 x 144 on a GBASP screen is small.
   Worth prototyping the sprite size there before committing to 16 x 16.

---

## 11. ROM support (deferred)

The emulator question is separate from this game and carries real legal risk. Summary:

- Your own game as a ROM: fine.
- A browser emulator: legal (Sony v. Connectix, Sony v. Bleem).
- **Hosting commercial ROMs, or accepting uploads: do not.** Nintendo litigates rather
  than sending takedowns — LoveROMs settled at $12M. On a domain selling refurbished
  Nintendo hardware, under a name already carrying trademark exposure, it turns a
  low-visibility risk into an obvious target.
- If it ever happens: **local-file-only**. `FileReader`, in memory, never uploaded,
  never stored, never shared. And homebrew rather than commercial titles.
