#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reveal.py - offline renderer for the "ASCII reveal" sprite sheet.

Pre-renders the gallery's reveal animation for a 200x200 transparent pet PNG
into a grid sprite sheet that a web page can play with CSS `steps()` alone.
No JavaScript is needed at view time.

Ported from the WebGL portfolio (look-test.html):
  - `var REVEAL = {...}`            the clock and every dial
  - `function buildArtCells(cv)`    the baked per-cell glyph + arrival texture
  - `var ART_FRAG = [...]`          the composite (blocks + glyph layer)
  - `function beatFor(sec)`         the pixel-beat ladder
  - `function updateReveal(dt,t)`   p = rv walks 0..1 linearly over REVEAL.run
  - `function hash2(seed,a,b)`      the CPU hash for the arrival jitter
  - `buildAtlas()`                  how a ramp glyph is drawn into its cell

What the reveal is, in one paragraph: the TRUE-COLOUR PICTURE IS UNDER
EVERYTHING THE WHOLE TIME.  Over it sits an ASCII layer in the picture's own
uv - one character per cell, the character chosen by the cell's area-average
luminance after a per-image auto-level, the colour the picture's own colour at
that cell lifted to a floor so it still reads, ADDED (linear dodge) rather
than blended.  The picture itself is additionally seen through square pixel
blocks whose size steps down a ladder over the first 1.6 s.  The reveal
removes the ASCII layer cell by cell behind a very ragged right-to-left front;
each cell churns through random ramp characters for a window before it goes.
Nothing ever fades in or out as a whole; there is no black frame.

Usage
-----
    python3 reveal.py IMAGE [IMAGE...] --out reveal/
    python3 reveal.py --all --out reveal/          # the six pets below

    from reveal import render
    info = render("https://media.rescreatu.com/pets/7/ginger/effect-derp.png",
                  "reveal/")

Needs Pillow and numpy.  Needs fontTools+brotli to read the bundled .woff2
(falls back to any .ttf/.otf beside it, then to a system monospace).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.request

try:
    import numpy as np
except ImportError:  # pragma: no cover
    sys.exit("reveal.py needs numpy:  pip install numpy Pillow")

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    sys.exit("reveal.py needs Pillow:  pip install numpy Pillow")


HERE = os.path.dirname(os.path.abspath(__file__))

PETS = [
    "https://media.rescreatu.com/pets/7/ginger/effect-derp-baby.png",
    "https://media.rescreatu.com/pets/30/black/effect-undead.png",
    "https://media.rescreatu.com/pets/45/albino/effect-stardust.png",
    "https://media.rescreatu.com/pets/71/albino/effect-derp.png",
    "https://media.rescreatu.com/pets/33/rainbow/effect-cosmic.png",
    "https://media.rescreatu.com/pets/37/albino/effect-frostlight.png",
]

# ---------------------------------------------------------------------------
# REVEAL - the portfolio's own dials.  Values copied from look-test.html's
# `var REVEAL = {` unless a comment says they were rescaled for 200 px art.
# ---------------------------------------------------------------------------

ART_RAMP = " .:-=+*#%@"          # look-test.html: `var ART_RAMP`
RAMP_N = len(ART_RAMP)           # 10

REVEAL = dict(
    run=3.77,            # the clip's own clock, 113 frames at 30 fps
    scramble=0.25,       # how long a cell churns before it goes, in p units
    churn=0.10,          # seconds between re-rolls while it churns
    jitter=1.00,         # how ragged the right-to-left front is
    lift=0.55,           # a cell's colour is raised until its max channel is this
    glow=0.60,           # the glow on the ASCII layer only
    glow_t=0.12,         # ...and the ramp density it starts at
    glow_r=0.14,         # NOT the portfolio's: how far that glow blooms off
                         # the ink, as a fraction of the cell's height.  See
                         # render_frame() for why a flat cell is wrong here.
    contrast=1.05,       # after the per-image auto-level
    # the glitch: inside the window, every `every` s, for `hold` s, 4 rows of
    # the remaining characters slide sideways by up to `shift` cells.
    glitch_from=2.60, glitch_to=4.90, glitch_every=0.80, glitch_hold=0.10,
    glitch_rows=4, glitch_shift=5.0,
    # the pixel beats.  The portfolio's ladder is [17, 8, 4, 2] px of a
    # 1080-wide frame, i.e. 1/63.5, 1/135, 1/270, 1/540 of the width.  On
    # 200 px art those are 3.1, 1.5, 0.7, 0.4 px: the last three are at or
    # under one source pixel and read as nothing at all.  Same halving shape,
    # rescaled so the first block is about 1/25 of the width and the last is
    # one source pixel (= full resolution):
    beat=[8.0, 4.0, 2.0, 1.0],
    beat_t=0.40,
    beat_ref=200.0,      # the ladder is in px of a `beat_ref`-wide picture
)

# the glyph cell, in SOURCE pixels (the portfolio's desktop cell is 7 x 13 CSS
# px, its phone cell about 5.5 x 10).  On 200 px art 7 x 13 gives 29 x 15
# cells, which is what reads best - see the module docstring / report.
CELL_W = 7.0
CELL_H = 13.0

COLS = 8                  # sheet columns, fixed by the output contract
FRAMES = 80               # 8 x 10, ~21.2 fps over 3.77 s
SCALE = 2
QUALITY = 90              # 100/95/90/85 = lossless / 6 / 5 / 4-bit near-lossless;
                          # anything else is plain lossy WebP.  See _save().
OVERSAMPLE = 3            # render at OUT*3 and box-reduce; exact for the art

CLOSE_FRAMES = 24         # the optional "losing focus" sheet, 8 x 3
CLOSE_DUR = 0.95


# ---------------------------------------------------------------------------
# hashes, ported verbatim
# ---------------------------------------------------------------------------

def hash2(seed: int, a, b):
    """look-test.html `function hash2(seed, a, b)` - the CPU hash that makes
    the arrival front ragged.  Math.imul is a 32-bit signed multiply."""
    a = np.asarray(a, dtype=np.int64)
    b = np.asarray(b, dtype=np.int64)
    m32 = np.uint32

    def imul(x, y):
        return np.asarray((x.astype(np.int64) * np.int64(y)) & 0xFFFFFFFF,
                          dtype=np.uint32)

    h = m32((int(seed) & 0xFFFFFFFF) ^ 0x9E3779B9)
    h = np.broadcast_to(h, a.shape).astype(np.uint32)
    h = imul(h ^ a.astype(np.uint32), 0x85EBCA6B)
    h = h ^ (h >> np.uint32(13))
    h = imul(h ^ b.astype(np.uint32), 0xC2B2AE35)
    h = h ^ (h >> np.uint32(16))
    h = imul(h ^ (h >> np.uint32(7)), 0x27D4EB2F)
    h = h ^ (h >> np.uint32(15))
    return h.astype(np.float64) / 4294967296.0


def hg(x, y):
    """ART_FRAG `float hg(vec2 p)` - the GLSL hash used for the churn roll and
    for picking the glitch's rows.  fract(sin(dot(p, (127.1, 311.7))) * 43758.5453)."""
    d = np.asarray(x, dtype=np.float64) * 127.1 + np.asarray(y, dtype=np.float64) * 311.7
    v = np.sin(d) * 43758.5453
    return v - np.floor(v)


# ---------------------------------------------------------------------------
# the glyph atlas
# ---------------------------------------------------------------------------

def _font_file() -> str | None:
    """IBM Plex Mono weight 500, as the portfolio letters with.  The bundled
    face is .woff2, which FreeType will not open, so convert it once into the
    temp cache with fontTools."""
    cache = os.path.join(tempfile.gettempdir(), "reveal-font-cache")
    os.makedirs(cache, exist_ok=True)
    woff = os.path.join(HERE, "fonts", "ibm-plex-mono-latin-500-normal.woff2")
    for cand in (woff, os.path.join(HERE, "fonts",
                                    "ibm-plex-mono-latin-400-normal.woff2")):
        if not os.path.exists(cand):
            continue
        out = os.path.join(cache, os.path.basename(cand)[:-6] + ".ttf")
        if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(cand):
            try:
                from fontTools.ttLib import TTFont
                f = TTFont(cand)
                f.flavor = None
                f.save(out)
            except Exception as exc:                       # pragma: no cover
                print("  ! could not convert %s (%s)" % (os.path.basename(cand), exc))
                continue
        return out
    for cand in ("/System/Library/Fonts/Menlo.ttc",
                 "/System/Library/Fonts/Monaco.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"):
        if os.path.exists(cand):
            print("  ! falling back to %s" % cand)
            return cand
    return None


_ATLAS_CACHE: dict = {}


def build_atlas(cw: int, ch: int) -> np.ndarray:
    """One ink-coverage bitmap per ramp character, (RAMP_N, ch, cw) in 0..1.

    look-test.html `buildAtlas()` draws each ramp glyph centred in its atlas
    cell at `ch * 0.92` px, weight 500, shrunk to fit `cw * 0.98`, white on
    black; ART_FRAG then stretches that cell across the reveal cell.  Same
    here, except the cell is already the reveal cell's own pixel size.
    """
    key = (cw, ch)
    if key in _ATLAS_CACHE:
        return _ATLAS_CACHE[key]
    path = _font_file()
    out = np.zeros((RAMP_N, ch, cw), dtype=np.float32)
    if path is None:                                        # pragma: no cover
        print("  ! no font found; the glyph layer will be empty")
        _ATLAS_CACHE[key] = out
        return out
    SS = 3                                                  # extra supersample
    W, H = cw * SS, ch * SS
    for i, chr_ in enumerate(ART_RAMP):
        if chr_ == " ":
            continue
        size = max(4, int(round(H * 0.92)))
        font = ImageFont.truetype(path, size)
        adv = font.getlength(chr_)
        if adv > W * 0.98 and adv > 0:
            size = max(4, int(math.floor(size * (W * 0.98) / adv)))
            font = ImageFont.truetype(path, size)
        img = Image.new("L", (W, H), 0)
        ImageDraw.Draw(img).text((W / 2.0, H / 2.0), chr_, fill=255,
                                 font=font, anchor="mm")
        a = np.asarray(img, dtype=np.float32).reshape(ch, SS, cw, SS)
        out[i] = a.mean(axis=(1, 3)) / 255.0
    _ATLAS_CACHE[key] = out
    return out


# ---------------------------------------------------------------------------
# source image
# ---------------------------------------------------------------------------

def key_for(src: str) -> str:
    """`pets/7/ginger/effect-derp-baby.png` -> `7-ginger-effect-derp-baby`.
    The site sometimes writes a double slash; collapse it."""
    path = re.sub(r"^[a-z]+://[^/]+", "", src)
    path = re.sub(r"/{2,}", "/", path)
    path = re.sub(r"\?.*$", "", path)
    m = re.search(r"pets/(.+)$", path)
    tail = m.group(1) if m else os.path.basename(path)
    tail = re.sub(r"\.(png|jpe?g|webp|gif)$", "", tail, flags=re.I)
    parts = [p for p in re.split(r"[/_\s]+", tail) if p]
    return "-".join(parts)


def fetch(src: str) -> str:
    """Local path through, URL into a temp cache keyed by the URL."""
    if not re.match(r"^[a-z]+://", src):
        return src
    cache = os.path.join(tempfile.gettempdir(), "reveal-src-cache")
    os.makedirs(cache, exist_ok=True)
    name = hashlib.sha1(src.encode("utf-8")).hexdigest()[:16] + ".png"
    dest = os.path.join(cache, name)
    if not os.path.exists(dest) or os.path.getsize(dest) == 0:
        req = urllib.request.Request(src, headers={"User-Agent": "reveal.py/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            if getattr(r, "status", 200) != 200:
                raise RuntimeError("%s returned %s" % (src, r.status))
            data = r.read()
        with open(dest + ".part", "wb") as fh:
            fh.write(data)
        os.replace(dest + ".part", dest)
    return dest


# ---------------------------------------------------------------------------
# the cell bake - look-test.html `function buildArtCells(cv)`
# ---------------------------------------------------------------------------

def bake_cells(rgb: np.ndarray, alpha: np.ndarray, cols: int, rows: int,
               seed: int, opt: dict) -> dict:
    """Per cell: the ramp index its luminance asks for, the moment it leaves,
    its own colour, and how much of it is inside the pet.

    buildArtCells draws the picture down to cols x rows for the area average,
    takes Rec.709 luma, auto-levels over the image's own range, applies
    `contrast`, and stores `idx = round((1 - l2) * (RAMP_N - 1))` - so DARK
    cells get the dense end of the ramp (`@`) and bright cells get the empty
    end.  Arrival is `(1 - u) + jitter * hash2(4900, i, 7)`, renormalised.

    Two things the portfolio never had to think about and this does:
      * ALPHA.  The area average is weighted by alpha, so a cell that is half
        background is the colour of the pet in it and not of the void; the
        auto-level only looks at cells that are really on the pet; and a cell
        carries `ca`, its mean alpha, which gates everything it draws.
      * a per-KEY seed added to hash2's 4900, so six 200x200 pets do not all
        tear along the same front.
    """
    H, W = alpha.shape
    ys = (np.arange(rows + 1) * (H / rows)).round().astype(int)
    xs = (np.arange(cols + 1) * (W / cols)).round().astype(int)

    ca = np.zeros((rows, cols), dtype=np.float64)
    cc = np.zeros((rows, cols, 3), dtype=np.float64)
    for j in range(rows):
        for i in range(cols):
            a = alpha[ys[j]:ys[j + 1], xs[i]:xs[i + 1]]
            c = rgb[ys[j]:ys[j + 1], xs[i]:xs[i + 1]]
            s = a.sum()
            ca[j, i] = a.mean() if a.size else 0.0
            cc[j, i] = (c * a[..., None]).sum(axis=(0, 1)) / s if s > 1e-6 else 0.0

    lum = cc[..., 0] * 0.2126 + cc[..., 1] * 0.7152 + cc[..., 2] * 0.0722
    on = ca > 0.04
    if on.any():
        lo, hi = float(lum[on].min()), float(lum[on].max())
    else:                                                   # pragma: no cover
        lo, hi = 0.0, 1.0
    span = max(0.001, hi - lo)
    l2 = np.clip((lum - lo) / span, 0.0, 1.0)
    l2 = np.clip((l2 - 0.5) * opt["contrast"] + 0.5, 0.0, 1.0)
    idx = np.rint((1.0 - l2) * (RAMP_N - 1)).astype(np.int32)
    idx[~on] = 0

    i_flat = np.arange(rows * cols)
    x_flat = i_flat % cols
    u = x_flat / (cols - 1) if cols > 1 else np.zeros_like(x_flat, dtype=float)
    arr = (1.0 - u) + opt["jitter"] * hash2(4900 + seed, i_flat, 7)
    arr = arr.reshape(rows, cols)
    alo, ahi = float(arr.min()), float(arr.max())
    arr = (arr - alo) / max(0.001, ahi - alo)

    return dict(idx=idx, arr=arr, cc=cc, ca=ca, cols=cols, rows=rows)


# ---------------------------------------------------------------------------
# the beat ladder - look-test.html `function beat_for(sec)`
# ---------------------------------------------------------------------------

def beat_for(sec: float, opt: dict):
    """Blocks across for a moment of the run, or None past the beats.
    Block 0 is also the CLOSED state - what a piece looks like until it opens."""
    beat, bt = opt["beat"], opt["beat_t"]
    k = int(math.floor(sec / bt))
    if k < 0:
        k = 0
    if k > len(beat) - 1 or sec >= len(beat) * bt:
        return None
    across = opt["beat_ref"] / beat[k]
    return None if across >= opt["beat_ref"] - 0.5 else across


# ---------------------------------------------------------------------------
# one frame - look-test.html `ART_FRAG`
# ---------------------------------------------------------------------------

def _block(premult: np.ndarray, alpha: np.ndarray, across: float,
           down: float) -> tuple[np.ndarray, np.ndarray]:
    """The picture through a grid of square blocks.  ART_FRAG takes four taps
    at the block's quarters; a true area average is the same idea without the
    sampling noise, and it is what buildArtCells calls "the area average, for
    free".  Averaged PREMULTIPLIED, so the silhouette blocks too."""
    H, W = alpha.shape
    bx = np.minimum((np.arange(W) * across / W).astype(int), int(across) - 1)
    by = np.minimum((np.arange(H) * down / H).astype(int), int(down) - 1)
    nx, ny = int(across), int(down)
    accP = np.zeros((ny, nx, 3))
    accA = np.zeros((ny, nx))
    cnt = np.zeros((ny, nx))
    np.add.at(accA, (by[:, None], bx[None, :]), alpha)
    np.add.at(cnt, (by[:, None], bx[None, :]), np.ones_like(alpha))
    for c in range(3):
        np.add.at(accP[..., c], (by[:, None], bx[None, :]), premult[..., c])
    cnt = np.maximum(cnt, 1.0)
    accA /= cnt
    accP /= cnt[..., None]
    return accP[by][:, bx], accA[by][:, bx]


def _blur(a: np.ndarray, r: int) -> np.ndarray:
    """Three box passes, separable - a good enough gaussian and cheap."""
    if r < 1:
        return a
    out = a
    for _ in range(3):
        for ax in (0, 1):
            c = np.cumsum(np.moveaxis(out, ax, 0), axis=0)
            pad_lo = np.zeros_like(c[:1])
            c = np.concatenate([pad_lo, c], axis=0)
            n = c.shape[0] - 1
            i = np.arange(n)
            hi = np.minimum(i + r + 1, n)
            lo = np.maximum(i - r, 0)
            out = np.moveaxis((c[hi] - c[lo]) / (hi - lo).reshape(
                (-1,) + (1,) * (c.ndim - 1)), 0, ax)
    return out


def render_frame(p: float, st: dict) -> tuple[np.ndarray, np.ndarray]:
    """ART_FRAG for one value of p (= `p.rv`, 0..1 linearly over REVEAL.run).

    Returns (premultiplied rgb, alpha) at the oversampled working size.
    """
    opt = st["opt"]
    cells = st["cells"]
    cols, rows = cells["cols"], cells["rows"]
    W3 = st["W3"]
    sec = p * opt["run"]

    # ---- the pixel beats: the picture through a grid of square blocks ----
    across = beat_for(sec, opt)
    if across is None:
        art_p, art_a = st["art_p"], st["art_a"]
    else:
        key = round(across, 3)
        if key not in st["beat_cache"]:
            st["beat_cache"][key] = _block(st["art_p"], st["art_a"],
                                           across, across)
        art_p, art_a = st["beat_cache"][key]

    # ---- which cell, and has the glitch slid its row sideways ----
    ci_x, ci_y = st["ci_x"], st["ci_y"]
    shift_row = np.zeros(rows, dtype=np.int64)
    gt = sec
    if opt["glitch_from"] < gt < opt["glitch_to"]:
        burst = math.floor((gt - opt["glitch_from"]) / opt["glitch_every"])
        ph = (gt - opt["glitch_from"]) - burst * opt["glitch_every"]
        if ph < opt["glitch_hold"]:
            for k in range(int(opt["glitch_rows"])):
                row = int(math.floor(float(hg(burst * 3.1 + k, 11.0)) * rows))
                row = min(max(row, 0), rows - 1)
                s = (float(hg(burst, k * 5.7)) * 2.0 - 1.0) * opt["glitch_shift"]
                shift_row[row] = int(math.floor(s))
    col_of = np.clip(np.arange(cols)[None, :] + shift_row[:, None], 0, cols - 1)

    idx = np.take_along_axis(cells["idx"], col_of, axis=1)
    arr = np.take_along_axis(cells["arr"], col_of, axis=1)
    cc = np.take_along_axis(cells["cc"], col_of[..., None].repeat(3, 2), axis=1)
    ca = np.take_along_axis(cells["ca"], col_of, axis=1)

    # ---- the morph: true glyph, then a churn, then gone ----
    live = p < arr
    churning = live & (p > arr - opt["scramble"])
    if churning.any():
        tick = math.floor(sec / max(0.01, st["churn_tick"]))
        gy, gx = np.nonzero(churning)
        r = hg(gx * 1.31 + tick * 7.31, gy * 1.31 + tick * 7.31)
        idx = idx.copy()
        idx[gy, gx] = np.floor(r * (RAMP_N - 1 + 0.999)).astype(np.int32)
    idx = np.clip(idx, 0, RAMP_N - 1)

    # ---- the cell's own colour, lifted so it still reads at this size ----
    mx = cc.max(axis=2, keepdims=True)
    lifted = cc * np.maximum(1.0, opt["lift"] / np.maximum(mx, 0.004))
    dens = idx / float(RAMP_N - 1)
    gw = opt["glow"] * np.maximum(0.0, dens - opt["glow_t"]) * 0.45

    # ---- expand the cell grid to pixels, and tap the glyph atlas ----
    atlas = st["atlas"]
    ax, ay = st["ax"], st["ay"]
    cell_idx = idx[ci_y][:, ci_x]
    cover = atlas[cell_idx, ay[:, None], ax[None, :]]
    cover = cover * live[ci_y][:, ci_x]

    ink = lifted[ci_y][:, ci_x]                 # (H, W, 3)
    ca_px = ca[ci_y][:, ci_x]

    # ---- the glow the characters alone get.
    # ART_FRAG adds `cc * gw` FLAT over the whole cell.  At 60 characters
    # across a wall-sized picture that is a glow field; at 29 across a 200 px
    # pet it is a grid of milky rectangles that eats the glyphs.  Same energy
    # per cell (hence the divide by the glyph's own mean coverage), spread as
    # a soft bloom round the ink instead - "bright cells bloom softly".
    gwn = (gw * live / st["mean_cover"][np.clip(idx, 0, RAMP_N - 1)])
    glow_rgb = _blur(ink * (cover * gwn[ci_y][:, ci_x])[..., None],
                     st["glow_r"]) if opt["glow"] > 0 else 0.0

    # ---- linear dodge, with alpha.  The glyph's own alpha is its INK
    # coverage times how much of the cell is on the pet, so a cell that
    # prints nothing contributes nothing and there is no dark fringe; the
    # per-cell glow rides the PICTURE's alpha, so it never haloes outside
    # the silhouette. ----
    g_a = np.clip(cover, 0.0, 1.0) * ca_px
    out_p = (art_p * art_a[..., None]
             + ink * cover[..., None] * ca_px[..., None]
             + glow_rgb * art_a[..., None])
    out_a = art_a + g_a * (1.0 - art_a)
    return out_p, np.clip(out_a, 0.0, 1.0)


# ---------------------------------------------------------------------------
# frame -> RGBA image
# ---------------------------------------------------------------------------

def _reduce(premult: np.ndarray, alpha: np.ndarray, n: int) -> Image.Image:
    """Box-reduce the oversampled frame by n and un-premultiply."""
    H, W = alpha.shape
    h, w = H // n, W // n
    p = premult[: h * n, : w * n].reshape(h, n, w, n, 3).mean(axis=(1, 3))
    a = alpha[: h * n, : w * n].reshape(h, n, w, n).mean(axis=(1, 3))
    rgb = np.where(a[..., None] > 1e-6, p / np.maximum(a[..., None], 1e-6), 0.0)
    out = np.concatenate([np.clip(rgb, 0, 1), np.clip(a, 0, 1)[..., None]], axis=2)
    return Image.fromarray(np.rint(out * 255.0).astype(np.uint8), "RGBA")


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------

def _state(path: str, key: str, opt: dict) -> dict:
    im = Image.open(path)
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    src_w, src_h = im.size
    out_px = int(round(src_w * opt["scale"]))
    os_ = opt["oversample"]
    W3 = out_px * os_
    up = W3 / src_w
    resample = Image.NEAREST if abs(up - round(up)) < 1e-9 else Image.LANCZOS
    big = im.resize((W3, W3), resample)

    a4 = np.asarray(big, dtype=np.float64) / 255.0
    art_rgb, art_a = a4[..., :3], a4[..., 3]

    small = np.asarray(im, dtype=np.float64) / 255.0
    cols = max(2, int(round(src_w / opt["cell_w"])))
    rows = max(2, int(round(src_h / opt["cell_h"])))
    seed = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16) % 100000
    cells = bake_cells(small[..., :3], small[..., 3], cols, rows, seed, opt)

    # the atlas cell, in working pixels
    cw = max(3, int(round(W3 / cols)))
    ch = max(3, int(round(W3 / rows)))
    atlas = build_atlas(cw, ch)

    xf = (np.arange(W3) + 0.5) * cols / W3
    ci_x = np.clip(xf.astype(int), 0, cols - 1)
    ax = np.clip(((xf - np.floor(xf)) * cw).astype(int), 0, cw - 1)
    yf = (np.arange(W3) + 0.5) * rows / W3
    ci_y = np.clip(yf.astype(int), 0, rows - 1)
    ay = np.clip(((yf - np.floor(yf)) * ch).astype(int), 0, ch - 1)

    # the churn tick, snapped to a whole number of frames so a settled-looking
    # cell never re-rolls on a half frame and nothing strobes at the frame rate
    dt = opt["run"] / max(1, opt["frames"] - 1)
    churn_tick = max(dt, round(opt["churn"] / dt) * dt)

    mean_cover = np.maximum(atlas.reshape(RAMP_N, -1).mean(axis=1), 0.06)
    glow_r = max(1, int(round(opt.get("glow_r", 0.14) * ch)))

    return dict(opt=opt, cells=cells, atlas=atlas, W3=W3, out_px=out_px,
                art_p=art_rgb, art_a=art_a, ci_x=ci_x, ci_y=ci_y,
                ax=ax, ay=ay, beat_cache={}, churn_tick=churn_tick,
                src_size=(src_w, src_h), seed=seed,
                mean_cover=mean_cover, glow_r=glow_r)


def _sheet(st: dict, ps, cols: int) -> Image.Image:
    n = len(ps)
    rows = int(math.ceil(n / cols))
    fp = st["out_px"]
    sheet = Image.new("RGBA", (cols * fp, rows * fp), (0, 0, 0, 0))
    for i, p in enumerate(ps):
        pm, al = render_frame(float(p), st)
        img = _reduce(pm, al, st["opt"]["oversample"])
        sheet.paste(img, ((i % cols) * fp, (i // cols) * fp))
    return sheet


def _save(img: Image.Image, dest: str, quality: int) -> int:
    """WebP with alpha.

    Measured on the busiest of the six pets (33-rainbow-effect-cosmic, 80
    frames, SCALE 2, sheet 3200x4000):

        q100 lossless  5074 kB      q95 6-bit  2892 kB
        q90  5-bit     2326 kB      q85 4-bit  1838 kB
        lossy q70      2973 kB      lossy q80  3498 kB     lossy q96 5878 kB

    Lossy WebP is both LARGER and worse on this content - flat ramp glyphs
    over flat art, half the sheet transparent - so the quality dial is a
    near-lossless one: posterise the colour to N bits a channel and then
    encode lossless.  At 5 bits the largest channel error anywhere is 4/255,
    which is invisible.  Alpha is never touched, so glyph edges stay exactly
    as rendered.

    Two things worth knowing before tuning for size: SCALE must stay a whole
    number (a 1.5x resample makes every pixel unique and TRIPLES the file),
    and dropping frames barely helps, because the encoder already finds the
    repetition between them - 56 frames is only 8% smaller than 80.
    """
    bits = {100: None, 95: 6, 90: 5, 85: 4}.get(int(quality), "lossy")
    if bits == "lossy":
        img.save(dest, "WEBP", quality=int(quality), alpha_quality=100, method=6)
    else:
        if bits is not None:
            a = np.asarray(img).astype(np.uint16)
            q = 1 << (8 - bits)
            a[..., :3] = np.minimum(255, (a[..., :3] + q // 2) // q * q)
            img = Image.fromarray(a.astype(np.uint8), "RGBA")
        img.save(dest, "WEBP", lossless=True, method=6, exact=False)
    return os.path.getsize(dest)


def render(image_url_or_path: str, out_dir: str, **opts) -> dict:
    """Render one pet's reveal sheet.  Returns the manifest entry."""
    opt = dict(REVEAL)
    opt.update(cell_w=CELL_W, cell_h=CELL_H, frames=FRAMES, scale=SCALE,
               quality=QUALITY, oversample=OVERSAMPLE, sheet_cols=COLS,
               close=True, close_frames=CLOSE_FRAMES, close_dur=CLOSE_DUR,
               force=False)
    opt.update({k: v for k, v in opts.items() if v is not None})

    key = key_for(image_url_or_path)
    src = fetch(image_url_or_path)
    os.makedirs(out_dir, exist_ok=True)
    sheet_p = os.path.join(out_dir, key + ".webp")
    closed_p = os.path.join(out_dir, key + "-closed.webp")
    close_p = os.path.join(out_dir, key + "-close.webp")

    frames = int(opt["frames"])
    if frames % opt["sheet_cols"]:
        frames += opt["sheet_cols"] - frames % opt["sheet_cols"]
    opt["frames"] = frames
    rows = frames // opt["sheet_cols"]

    entry = dict(
        key=key, source=image_url_or_path,
        cols=opt["sheet_cols"], rows=rows, frames=frames,
        frame_px=int(round(200 * opt["scale"])), scale=opt["scale"],
        duration_s=round(opt["run"], 3), fps=round(frames / opt["run"], 2),
        cell_cols=None, cell_rows=None,
        cell_px=[opt["cell_w"], opt["cell_h"]], quality=opt["quality"],
        sheet=os.path.basename(sheet_p), closed=os.path.basename(closed_p),
    )

    newest = max(os.path.getmtime(src), os.path.getmtime(os.path.abspath(__file__)))
    fresh = (not opt["force"] and os.path.exists(sheet_p)
             and os.path.exists(closed_p)
             and os.path.getmtime(sheet_p) > newest)

    st = _state(src, key, opt)
    entry["frame_px"] = st["out_px"]
    entry["cell_cols"] = st["cells"]["cols"]
    entry["cell_rows"] = st["cells"]["rows"]
    mp = (opt["sheet_cols"] * st["out_px"]) * (rows * st["out_px"]) / 1e6
    entry["sheet_px"] = [opt["sheet_cols"] * st["out_px"], rows * st["out_px"]]
    entry["megapixels"] = round(mp, 2)
    if mp > 16.0:
        print("  ! %s: sheet is %.1f MP, over the 16 MP iOS ceiling - drop to "
              "%d frames or scale %.1f" % (key, mp, frames // 2, opt["scale"] / 2))

    if fresh:
        entry["sheet_bytes"] = os.path.getsize(sheet_p)
        entry["closed_bytes"] = os.path.getsize(closed_p)
        if os.path.exists(close_p):
            entry["close"] = os.path.basename(close_p)
            entry["close_bytes"] = os.path.getsize(close_p)
            entry["close_frames"] = int(opt["close_frames"])
            entry["close_rows"] = int(math.ceil(opt["close_frames"] / opt["sheet_cols"]))
            entry["close_duration_s"] = opt["close_dur"]
        entry["skipped"] = True
        return entry

    t0 = time.time()
    ps = [i / (frames - 1.0) for i in range(frames)]
    sheet = _sheet(st, ps, opt["sheet_cols"])
    entry["sheet_bytes"] = _save(sheet, sheet_p, opt["quality"])

    pm, al = render_frame(0.0, st)
    entry["closed_bytes"] = _save(_reduce(pm, al, opt["oversample"]),
                                  closed_p, opt["quality"])

    if opt["close"]:
        cf = int(opt["close_frames"])
        if cf % opt["sheet_cols"]:
            cf += opt["sheet_cols"] - cf % opt["sheet_cols"]
        cps = [1.0 - i / (cf - 1.0) for i in range(cf)]
        entry["close"] = os.path.basename(close_p)
        entry["close_bytes"] = _save(_sheet(st, cps, opt["sheet_cols"]),
                                     close_p, opt["quality"])
        entry["close_frames"] = cf
        entry["close_rows"] = cf // opt["sheet_cols"]
        entry["close_duration_s"] = opt["close_dur"]

    entry["render_s"] = round(time.time() - t0, 1)
    return entry


def render_all(sources, out_dir: str, **opts) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    man_p = os.path.join(out_dir, "manifest.json")
    man = {}
    if os.path.exists(man_p):
        try:
            man = json.load(open(man_p))
        except Exception:
            man = {}
    man.setdefault("keys", {})
    for s in sources:
        e = render(s, out_dir, **opts)
        print("  %-34s %5d frames %d x %d  %4dpx  sheet %7.1f kB  closed %5.1f kB%s"
              % (e["key"], e["frames"], e["cols"], e["rows"], e["frame_px"],
                 e["sheet_bytes"] / 1024.0, e["closed_bytes"] / 1024.0,
                 "  (cached)" if e.get("skipped") else ""))
        man["keys"][e["key"]] = e
    man["ramp"] = ART_RAMP
    man["generated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(man_p, "w") as fh:
        json.dump(man, fh, indent=2, sort_keys=True)
    return man


def main(argv=None):
    ap = argparse.ArgumentParser(description="pre-render the ASCII reveal as a sprite sheet")
    ap.add_argument("images", nargs="*")
    ap.add_argument("--all", action="store_true", help="the six pets in PETS")
    ap.add_argument("--out", default="reveal/")
    ap.add_argument("--frames", type=int)
    ap.add_argument("--scale", type=float)
    ap.add_argument("--quality", type=int)
    ap.add_argument("--cell", help="glyph cell in source px, WxH, e.g. 7x13")
    ap.add_argument("--oversample", type=int)
    ap.add_argument("--no-close", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)

    srcs = list(a.images) + (PETS if a.all or not a.images else [])
    kw = dict(frames=a.frames, scale=a.scale, quality=a.quality,
              oversample=a.oversample, force=a.force or None)
    if a.no_close:
        kw["close"] = False
    if a.cell:
        w, h = a.cell.lower().split("x")
        kw["cell_w"], kw["cell_h"] = float(w), float(h)
    render_all(srcs, a.out, **{k: v for k, v in kw.items() if v is not None})
    return 0


if __name__ == "__main__":
    sys.exit(main())
