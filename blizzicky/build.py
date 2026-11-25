#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
blizzicky - Rescreatu profile builder.

Reads pets.txt and writes profile.css, profile.html and preview.html.
Re-running this script is the only step needed when the pet list changes.

    python3 build.py

No JavaScript is emitted anywhere. profile.html is the text to paste into the
Rescreatu profile box; profile.css is served from the repo folder (its font and
asset URLs are relative, so the same file works locally and from jsDelivr).
"""

import colorsys
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request

# --------------------------------------------------------------------------- #
# CONFIG                                                                      #
# --------------------------------------------------------------------------- #

HERE = os.path.dirname(os.path.abspath(__file__))

# Rescreatu user id for the gallery link. None -> the GALLERY slot is omitted.
USER_ID = None

# Where the published copy of this folder lives. jsDelivr caches a branch
# name for up to a day, so pass --ref <commit sha> when publishing; every
# absolute URL in profile.html is stamped with it. profile.css never needs
# the ref: all of its own URLs are relative.
CDN_REPO = "https://cdn.jsdelivr.net/gh/BillyTheArtist/rescreatu-images"
CDN_DIR = "/blizzicky/"
REF = "main"              # --ref REF


def cdn_base(ref=None):
    return CDN_REPO + "@" + (ref or REF) + CDN_DIR


def cdn_css(ref=None):
    return cdn_base(ref) + "profile.css"

SEED = 20261125          # fixed: rebuilds are byte-identical
MAXPETS = 40             # generic CSS is pre-written for this many pets
SCENE_W = 396            # scene grid, in character cells (396 * 7px = 2772px)
SCENE_H = 112            # 112 * 13px = 1456px
LIFE_H = 36              # the sparse "life" band is this tall

REVEAL = "1.4s"          # focused pet's stepped reveal sweep
SWEEP = "1.4s"           # environment sweep when the kind changes
SLIDE = ".34s"           # slide between pets
BOOT_HOLD = "2.0s"       # boot screen hold before its .45s fade (measured; see README)
TYPE_CPS = 34.0          # name typing speed, characters a second
MAX_NAME = 20            # name letters that get their own animation delay

PREFIX = "https://www.rescreatu.com"
KINDS = ["sky", "water", "cathedral", "garden", "void", "geode"]
LABEL = {
    "sky": "OPEN SKY", "water": "DEEP WATER", "cathedral": "RELIQUARY",
    "garden": "OVERGROWTH", "void": "THE VOID", "geode": "GEODE",
}
# hue on a 0-1 wheel, four-stop lightness ramp, saturation multiplier
ENV = {
    "sky":       (0.58, [0.46, 0.58, 0.76, 0.98], 0.80),
    "water":     (0.50, [0.30, 0.46, 0.70, 0.96], 1.15),
    "cathedral": (0.09, [0.34, 0.52, 0.74, 0.99], 0.85),
    "garden":    (0.29, [0.30, 0.48, 0.70, 0.95], 1.10),
    "void":      (0.72, [0.26, 0.44, 0.68, 1.00], 0.95),
    "geode":     (0.85, [0.30, 0.50, 0.76, 1.00], 1.20),
}
# where each kind's sparse life band sits
LIFE_TOP = {"sky": "4%", "water": "10%", "cathedral": "12%",
            "garden": "62%", "void": "58%", "geode": "8%"}

SLOTS = [
    ("HATCHERY", PREFIX + "/hatchery/"),
    ("INVENTORY", PREFIX + "/inventory/"),
    ("EXPLORE", PREFIX + "/explore/"),
    ("SHOPS", PREFIX + "/shops/"),
    ("MERCHANT", PREFIX + "/usershops/"),
    ("RANCHER", PREFIX + "/ranchershop/"),
]

# glyph families: (dim, lit), ten steps light to dark
FAM = {
    "air":    ("   ..··**+", "  .·**++**"),
    "racks":  (" .:|!!==##", " !|==#[]HH"),
    "water":  (" .~~(())ss", " ~((ss))SS"),
    "floor":  (" ..,,__--=", " ,__--===="),
    "ceil":   (" ..''\"\"^^+", " '\"\"^^++TT"),
    "walls":  (" .:-=+*#%@", " .-=+*#%@@"),
    "growth": ("  .,,;;oe&", " .,;;oe&%%"),
}

# --------------------------------------------------------------------------- #
# pets.txt                                                                    #
# --------------------------------------------------------------------------- #

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def fetch(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")


def parse_petpage(html):
    """Return (name, image_url) from a Rescreatu pet page."""
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
    if not m:
        raise ValueError("no <h1> on the pet page")
    name = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    tail = html[m.end():]
    im = re.search(r"<img[^>]+src\s*=\s*[\"']?([^\"'\s>]+)", tail, re.I)
    if not im:
        raise ValueError("no pet image after the <h1>")
    src = im.group(1)
    if src.startswith("//"):
        src = "https:" + src
    elif src.startswith("/"):
        src = PREFIX + src
    # the site writes a double slash after the species number: normalise the path
    if "://" in src:
        scheme, rest = src.split("://", 1)
        src = scheme + "://" + re.sub(r"/{2,}", "/", rest)
    return name, src


def measure_kind(url):
    """Pick an environment kind by measuring the pet image (needs Pillow)."""
    try:
        from PIL import Image
        import io
    except Exception:
        return None
    try:
        im = Image.open(io.BytesIO(fetch(url, binary=True))).convert("RGBA")
    except Exception:
        return None
    w, h = im.size
    px = im.load()
    lum, sat, n, dark, edge = [], [], 0, 0, 0
    left = right = 0.0
    prev = None
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            r, g, b, a = px[x, y]
            if a < 40:
                prev = None
                continue
            n += 1
            hh, ll, ss = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
            lum.append(ll)
            sat.append(ss)
            if ll < 0.30:
                dark += 1
            if prev is not None and abs(ll - prev) > 0.22:
                edge += 1
            prev = ll
            if x < w / 2:
                left += ll
            else:
                right += ll
    if not n:
        return None
    mean_l = sum(lum) / n
    mean_s = sum(sat) / n
    spread = max(lum) - min(lum)
    busy = edge / float(n)
    sym = 1.0 - abs(left - right) / max(1e-6, left + right)
    # cool overall?
    cool = 0
    for y in range(0, h, 3):
        for x in range(0, w, 3):
            r, g, b, a = px[x, y]
            if a < 40:
                continue
            if b >= r:
                cool += 1
    cool_f = cool / max(1.0, (h / 3) * (w / 3))
    if sym > 0.93 and spread > 0.72:
        return "cathedral"
    if mean_l < 0.34 and busy > 0.12:
        return "void"
    if cool_f > 0.46:
        return "water"
    if spread > 0.80 or mean_s > 0.55:
        return "geode"
    if mean_l > 0.62 and busy < 0.10:
        return "garden"
    return "sky"


def read_pets(path):
    pets = []
    warn = []
    for raw in open(path, encoding="utf-8"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("placeholder"):
            bits = [b.strip() for b in line.split("|")]
            if len(bits) < 5:
                raise ValueError("bad placeholder line: " + line)
            _, name, img, kind, light = bits[:5]
            pets.append({"name": name, "img": img, "kind": kind,
                         "light": light, "href": None})
            continue
        parts = line.split()
        url = parts[0]
        over = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
        name, img = parse_petpage(fetch(url))
        kind = over.get("kind")
        if not kind:
            kind = measure_kind(img)
            if not kind:
                kind = "sky"
                warn.append("no Pillow / unreadable image for %s - kind=sky" % url)
        pets.append({"name": name, "img": img, "kind": kind,
                     "light": over.get("light", "left"), "href": url})
    for i, p in enumerate(pets):
        if p["kind"] not in KINDS:
            raise ValueError("unknown kind %r" % p["kind"])
        if p["light"] not in ("left", "right"):
            raise ValueError("light must be left or right")
        if not p["href"]:
            p["href"] = "#p%d" % (i + 1)
    return pets, warn

# --------------------------------------------------------------------------- #
# noise                                                                       #
# --------------------------------------------------------------------------- #


def h2(ix, iy, seed):
    """Deterministic 0..1 hash. Mixed in sequence, not added, so the lattice
    has no diagonal correlation (which showed up as streaks in the scenes)."""
    n = ix & 0xFFFFFFFF
    n = (n * 0x9E3779B1 + (iy & 0xFFFFFFFF)) & 0xFFFFFFFF
    n ^= n >> 15
    n = (n * 0x85EBCA6B) & 0xFFFFFFFF
    n = (n + (seed & 0xFFFFFFFF) * 0xC2B2AE35) & 0xFFFFFFFF
    n ^= n >> 13
    n = (n * 0x27D4EB2F) & 0xFFFFFFFF
    n ^= n >> 16
    return n / 4294967295.0


def vnoise(x, y, seed):
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = x - ix, y - iy
    u = fx * fx * (3 - 2 * fx)
    v = fy * fy * (3 - 2 * fy)
    a = h2(ix, iy, seed)
    b = h2(ix + 1, iy, seed)
    c = h2(ix, iy + 1, seed)
    d = h2(ix + 1, iy + 1, seed)
    return (a * (1 - u) + b * u) * (1 - v) + (c * (1 - u) + d * u) * v


def fbm(x, y, seed, octaves=3):
    amp, f, s, norm = 1.0, 1.0, 0.0, 0.0
    for i in range(octaves):
        s += amp * vnoise(x * f, y * f, seed + i * 101)
        norm += amp
        amp *= 0.5
        f *= 2.0
    return s / norm


def clamp01(v):
    return 0.0 if v < 0 else (1.0 if v > 1 else v)


def calm(x, y, w, h):
    """Attenuate density around the centre row, where the pets stand.

    The scene grid is centred on the viewport, so grid row h/2 is the middle of
    the screen and the pets stand a little above it.
    """
    cy = 0.47 * h
    dy = abs(y - cy) / (0.13 * h)
    dx = abs(x - w / 2.0) / (0.26 * w)
    r = math.sqrt(dx * dx * 0.6 + dy * dy)
    return 0.34 + 0.66 * clamp01(r)


def grid(w, h, fill=" "):
    return [[fill] * w for _ in range(h)]


def put(g, x, y, ch):
    if 0 <= y < len(g) and 0 <= x < len(g[0]):
        g[y][x] = ch


def gl(d, fam, lit=False):
    s = FAM[fam][1 if lit else 0]
    i = int(clamp01(d) * 9.999)
    return s[i]

# --------------------------------------------------------------------------- #
# scenes                                                                      #
# --------------------------------------------------------------------------- #


def sparse(w, hh, s, chars, count, key):
    """Two alternative sparse grids; a few characters differ between them."""
    out = []
    for k in range(2):
        g = grid(w, hh)
        for i in range(count):
            x = int(h2(i, 1, s + key) * w)
            y = int(h2(i, 2, s + key + 7) * hh)
            # most characters are the same in both grids; some re-letter
            j = int(h2(i, 3 + (k if h2(i, 9, s + key) > 0.72 else 0), s + key + 13)
                    * len(chars))
            put(g, x, y, chars[j])
        out.append(g)
    return out


def scene_sky(w, h, s):
    """Drifting cloud banks, a sun, quotes and carets thinning to dots."""
    far, near = grid(w, h), grid(w, h)
    for y in range(h):
        for x in range(w):
            n = fbm(x / 40.0, y / 15.0, s)
            band = 0.5 + 0.5 * math.sin(y / 11.0 + n * 3.0 + x / 190.0)
            d = (n * 1.7 - 0.44) + 0.42 * band
            d *= 1.0 - 0.38 * (y / float(h))
            d *= calm(x, y, w, h)
            if d > 0.05:
                far[y][x] = gl(d, "air")
    sx, sy = 0.5 * w + 92, 0.25 * h
    for y in range(h):
        for x in range(w):
            dx, dy = (x - sx) * 0.52, (y - sy)
            r = math.sqrt(dx * dx + dy * dy)
            th = math.atan2(dy, dx)
            ray = max(0.0, math.cos(9.0 * th + 0.6)) ** 3
            d = math.exp(-r / 9.0) + 0.55 * ray * math.exp(-r / 34.0)
            n = fbm(x / 20.0, y / 14.0, s + 7)
            d = d * calm(x, y, w, h) * (0.7 + 0.5 * n)
            # high cloud edges catch the light, in the top third only
            d = max(d, (fbm(x / 34.0, y / 12.0, s + 3) - 0.70) * 2.4
                    * max(0.0, 1.0 - y / (0.52 * h)) * calm(x, y, w, h))
            if d > 0.07:
                near[y][x] = gl(min(1.0, d * 1.3), "ceil", True)
    return far, near, sparse(w, LIFE_H, s, "'·*+.", 540, 31)


def scene_water(w, h, s):
    """Caustic nets, a shaft of light from above, waves and parentheses."""
    far, near = grid(w, h), grid(w, h)
    for y in range(h):
        for x in range(w):
            n1 = fbm(x / 24.0, y / 12.0, s)
            n2 = fbm(x / 15.0, y / 19.0, s + 5)
            c = abs(math.sin(x / 6.8 + n1 * 2.6) * math.sin(y / 5.0 + n2 * 2.2))
            d = (c ** 1.25) * (0.6 + 0.5 * n1)
            d *= calm(x, y, w, h)
            if d > 0.06:
                far[y][x] = gl(d, "water")
    for y in range(h):
        cx = 0.5 * w - 52 + y * 0.52
        half = 9.0 + y * 0.30
        for x in range(w):
            t = abs(x - cx) / half
            d = 0.0
            if t < 1.0:
                d = (1.0 - t) ** 1.3 * (1.0 - 0.45 * (y / float(h)))
                d *= 0.6 + 0.4 * fbm(x / 6.0, y / 4.0, s + 11)
            # surface waves across the top of the grid
            if y < 26:
                d = max(d, 0.5 * abs(math.sin(x / 6.2 + y * 0.42 + fbm(x / 18.0, 0.3, s + 2) * 2))
                        * (1 - y / 26.0))
            d *= calm(x, y, w, h)
            if d > 0.08:
                near[y][x] = gl(min(1.0, d * 1.25), "water", True)
    return far, near, sparse(w, LIFE_H, s, "o.·(0", 400, 61)


def scene_cathedral(w, h, s):
    """Piers, pointed lancet windows, shafts of light, bars and hashes."""
    far, near = grid(w, h), grid(w, h)
    step = 46
    piers = [int(w / 2 + (k - 4) * step + 23) for k in range(9)]
    for px in piers:
        for y in range(h):
            d = 0.85 - 0.22 * (y / float(h)) + 0.16 * fbm(px / 4.0, y / 7.0, s + 2)
            d *= 0.45 + 0.55 * calm(px, y, w, h)
            for o in (-2, -1, 0, 1, 2):
                dd = d * (1.0 - 0.15 * abs(o))
                if dd > 0.08:
                    put(far, px + o, y, gl(min(1.0, dd), "racks"))
    # a lancet window in every bay between two piers
    for k in range(len(piers) - 1):
        cx = (piers[k] + piers[k + 1]) // 2
        top, bot = 22, 68
        for y in range(top, bot):
            t = (y - top) / float(bot - top)
            half = int(1 + 15 * min(1.0, t * 1.7))
            for x in range(cx - half, cx + half + 1):
                edge = 1.0 if (x <= cx - half + 1 or x >= cx + half - 1) else 0.0
                d = (0.34 + 0.40 * t + 0.3 * edge)
                d *= 0.55 + 0.45 * calm(x, y, w, h)
                d *= 0.75 + 0.25 * fbm(x / 5.0, y / 5.0, s + 13)
                if d > 0.07:
                    put(far, x, y, gl(min(1.0, d), "racks"))
    # light falling out of each window, leaning with the hour
    for y in range(h):
        for x in range(w):
            d = 0.0
            for k in range(len(piers) - 1):
                cx = (piers[k] + piers[k + 1]) / 2.0 + (y - 44) * 0.46
                t = abs(x - cx) / (7.0 + y * 0.18)
                if t < 1.0 and y > 20:
                    d = max(d, (1.0 - t) ** 1.25 * max(0.0, 1.0 - (y - 20) / 86.0))
            d *= (0.5 + 0.5 * calm(x, y, w, h)) * (0.72 + 0.28 * fbm(x / 7.0, y / 11.0, s + 17))
            if d > 0.09:
                near[y][x] = gl(min(1.0, d * 1.2), "ceil", True)
    return far, near, sparse(w, LIFE_H, s, ".·'+:", 360, 91)


def scene_garden(w, h, s):
    """Foliage growing off the floor, vines, sparse blooms."""
    far, near = grid(w, h), grid(w, h)

    def floor_top(x):
        clump = fbm(x / 20.0, 0.5, s) * 0.68 + fbm(x / 6.0, 3.5, s + 1) * 0.32
        return int(66 + 20 * (1.0 - clump))

    # the bank of foliage the pets stand in front of
    for x in range(w):
        topy = floor_top(x)
        for y in range(topy, h):
            t = (y - topy) / float(max(1, h - topy))
            d = (0.55 + 0.9 * t) * (0.6 + 0.55 * fbm(x / 8.0, y / 5.0, s + 3))
            d *= 0.55 + 0.45 * calm(x, y, w, h)
            if d > 0.06:
                far[y][x] = gl(min(1.0, d), "growth")
    # canopy across the top of the grid
    for y in range(0, 34):
        for x in range(w):
            n = fbm(x / 13.0, y / 7.0, s + 19)
            d = (n * 1.5 - 0.42) * max(0.25, 1.0 - y / 34.0)
            d *= 0.6 + 0.4 * calm(x, y, w, h)
            if d > 0.06:
                far[y][x] = gl(min(1.0, d), "growth")
    # vines hanging out of the canopy
    for i in range(34):
        x0 = h2(i, 1, s + 29) * w
        ln = int(28 + 52 * h2(i, 2, s + 31))
        for y in range(0, ln):
            x = int(x0 + 5.0 * math.sin(y / 13.0 + i * 1.7))
            d = (0.85 - 0.4 * (y / float(ln))) * (0.5 + 0.5 * calm(x, y, w, h))
            if d > 0.07:
                put(far, x, y, gl(min(1.0, d), "growth"))
                if y > 2:
                    put(far, x + 1, y, gl(min(1.0, d * 0.7), "growth"))
            if y % 5 == 2:
                put(far, x + (2 if i % 2 else -1), y, ";")
    # lit leading edge where the foliage meets the air
    for x in range(w):
        topy = floor_top(x)
        for y in range(topy - 4, topy + 12):
            n = fbm(x / 5.0, y / 4.0, s + 9)
            d = (n - 0.36) * 2.6 * (0.45 + 0.55 * calm(x, y, w, h))
            if d > 0.08:
                put(near, x, y, gl(min(1.0, d), "growth", True))
    for i in range(260):
        x = int(h2(i, 5, s + 37) * w)
        y = int(floor_top(x) - 6 + 30 * h2(i, 6, s + 41))
        if calm(x, y, w, h) > 0.45:
            put(near, x, y, "&%oe"[int(h2(i, 7, s + 43) * 4)])
    return far, near, sparse(w, LIFE_H, s, "o&e,;", 320, 103)


def scene_void(w, h, s):
    """Rose mist, thorn silhouettes, motes, a few steady stars."""
    far, near = grid(w, h), grid(w, h)
    for y in range(h):
        for x in range(w):
            n = fbm(x / 46.0, y / 26.0, s)
            d = (n - 0.36) * 1.9
            d *= calm(x, y, w, h) * 0.95
            if d > 0.06:
                far[y][x] = gl(d, "air")
    for i in range(40):
        x0 = h2(i, 2, s + 19) * w
        bottom = h - 1 if i % 3 else int(h * 0.74)
        lean = (h2(i, 3, s + 23) - 0.5) * 1.6
        ln = int(30 + 60 * h2(i, 4, s + 29))
        for k in range(ln):
            x = int(x0 + lean * k + 3.0 * math.sin(k / 9.0 + i))
            y = bottom - k
            d = 0.98 - 0.45 * (k / float(ln))
            d *= 0.5 + 0.5 * calm(x, y, w, h)
            for o in (0, 1):
                if d > 0.09:
                    put(near, x + o, y, gl(min(1.0, d), "walls"))
            # thorns
            if k % 7 == 3:
                put(near, x + (2 if i % 2 else -2), y, "/" if i % 2 else "\\")
    for i in range(260):
        x = int(h2(i, 8, s + 31) * w)
        y = int(h2(i, 9, s + 37) * h)
        if calm(x, y, w, h) > 0.5:
            put(near, x, y, "*+."[int(h2(i, 10, s + 41) * 3)])
    return far, near, sparse(w, LIFE_H, s, "·.*+'", 300, 113)


def scene_geode(w, h, s):
    """Facets radiating from a light, crystal bands, a hot core."""
    far, near = grid(w, h), grid(w, h)
    cx, cy = 0.5 * w + 74, 0.30 * h
    for y in range(h):
        for x in range(w):
            dx, dy = (x - cx) * 0.5, (y - cy)
            th = math.atan2(dy, dx)
            r = math.sqrt(dx * dx + dy * dy)
            f = (th * 6.0 / math.pi + fbm(x / 50.0, y / 34.0, s) * 0.8) % 1.0
            edge = 1.0 - abs(f - 0.5) * 2.0
            d = (0.3 + 0.75 * edge) * (0.3 + 0.7 * min(1.0, r / 52.0))
            d *= calm(x, y, w, h)
            if d > 0.08:
                far[y][x] = gl(min(1.0, d), "walls")
    for i in range(40):
        a = (i / 40.0) * math.pi * 2 + h2(i, 1, s + 3) * 0.3
        ln = int(44 + 86 * h2(i, 2, s + 7))
        for k in range(10, ln):
            x = int(cx + math.cos(a) * k * 2.0)
            y = int(cy + math.sin(a) * k)
            d = 0.95 - 0.5 * (k / float(ln))
            d *= 0.45 + 0.55 * calm(x, y, w, h)
            if d > 0.1:
                put(near, x, y, gl(min(1.0, d), "walls", True))
                put(near, x + 1, y, gl(min(1.0, d * 0.82), "walls", True))
    for y in range(h):
        for x in range(w):
            dx, dy = (x - cx) * 0.5, (y - cy)
            r = math.sqrt(dx * dx + dy * dy)
            if r < 17:
                d = (1.0 - r / 17.0) ** 1.1
                if d > 0.1:
                    near[y][x] = gl(min(1.0, d), "walls", True)
    return far, near, sparse(w, LIFE_H, s, "*+.'·", 340, 137)


SCENES = {"sky": scene_sky, "water": scene_water, "cathedral": scene_cathedral,
          "garden": scene_garden, "void": scene_void, "geode": scene_geode}


def css_text(g):
    """A character grid as a CSS content string ("\\A " plus one space per line)."""
    out = []
    for row in g:
        line = "".join(row).rstrip()
        out.append(line.replace("\\", "\\\\").replace('"', '\\"'))
    return '"' + "\\A ".join(out) + '"'

# --------------------------------------------------------------------------- #
# colour                                                                      #
# --------------------------------------------------------------------------- #


def ramp(kind):
    hue, ls, sm = ENV[kind]
    out = []
    for l in ls:
        s = min(1.0, max(0.40, 0.58 * sm))
        r, g, b = colorsys.hls_to_rgb(hue, l, s)
        out.append("#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255)))
    return out


def glow_rot(kind):
    """hue-rotate degrees that turn the sepia-saturated silhouette into the kind's hue."""
    return round((ENV[kind][0] * 360.0) - 32.0) % 360

# --------------------------------------------------------------------------- #
# CSS                                                                         #
# --------------------------------------------------------------------------- #

# one 7x13 character cell: a couple of strokes and a dot, so a masked image
# reads as rows of glyphs rather than as a dot screen
MASK_TILE = ("url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
             "width='7' height='13'%3E%3Cg fill='%23fff'%3E"
             "%3Crect x='1' y='3' width='1' height='7'/%3E"
             "%3Crect x='4' y='3' width='1' height='7'/%3E"
             "%3Crect x='1' y='5' width='4' height='1'/%3E"
             "%3Crect x='2' y='8' width='3' height='1'/%3E"
             "%3Crect x='5' y='11' width='1' height='1'/%3E"
             "%3C/g%3E%3C/svg%3E\")")

GRAIN = ("url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
         "width='120' height='120'%3E%3Cfilter id='n'%3E%3CfeTurbulence "
         "type='fractalNoise' baseFrequency='.9' numOctaves='2'/%3E%3C/filter%3E"
         "%3Crect width='120' height='120' filter='url(%23n)' opacity='.5'/%3E"
         "%3C/svg%3E\")")


# --- textures (look 1): a halftone of small dots, and a stain with ring edges
HALFTONE = ("url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
            "width='5' height='5'%3E%3Cg fill='%23ffffff'%3E"
            "%3Ccircle cx='1.4' cy='1.4' r='.95'/%3E"
            "%3Ccircle cx='3.9' cy='3.9' r='.6'/%3E"
            "%3C/g%3E%3C/svg%3E\")")

STAIN = ("url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
         "width='1100' height='760'%3E%3Cfilter id='w'%3E%3CfeTurbulence "
         "type='fractalNoise' baseFrequency='.004' numOctaves='2' seed='9'/%3E"
         "%3CfeDisplacementMap in='SourceGraphic' scale='64' xChannelSelector='R' "
         "yChannelSelector='G'/%3E%3CfeGaussianBlur stdDeviation='2.5'/%3E%3C/filter%3E"
         "%3Cg filter='url(%23w)' fill='none' stroke='%23ffffff'%3E"
         "%3Cellipse cx='215' cy='150' rx='152' ry='112' stroke-width='9' opacity='.48'/%3E"
         "%3Cellipse cx='215' cy='150' rx='120' ry='86' stroke-width='3' opacity='.32'/%3E"
         "%3Cellipse cx='895' cy='635' rx='172' ry='122' stroke-width='10' opacity='.44'/%3E"
         "%3Cellipse cx='895' cy='635' rx='141' ry='97' stroke-width='3' opacity='.28'/%3E"
         "%3C/g%3E%3Cg filter='url(%23w)' fill='%23ffffff' opacity='.1'%3E"
         "%3Cellipse cx='215' cy='150' rx='152' ry='112'/%3E"
         "%3Cellipse cx='895' cy='635' rx='172' ry='122'/%3E"
         "%3C/g%3E%3C/svg%3E\")")

# --------------------------------------------------------------------------- #
# the bar: ink levels, corner pips, and its generated rim and ground text      #
# --------------------------------------------------------------------------- #

TINT_RGB = (0x9f, 0xd8, 0xbd)
INK_RGB = (0x05, 0x07, 0x0a)


def ink(f):
    """A fraction of the tint laid over the near-black ink, as a flat colour.

    Everything on the bar is told apart by ink alone: nothing on it changes
    size, and nothing on it is drawn with a border.
    """
    return "#%02x%02x%02x" % tuple(
        int(round(t * f + i * (1.0 - f))) for t, i in zip(TINT_RGB, INK_RGB))


# four solid corner pips: focus-visible never draws a ring or a fill
PIPS = ('%s{content:"";position:absolute;inset:-6px;'
        "background-image:linear-gradient(var(--tint),var(--tint)),"
        "linear-gradient(var(--tint),var(--tint)),"
        "linear-gradient(var(--tint),var(--tint)),"
        "linear-gradient(var(--tint),var(--tint));"
        "background-size:5px 5px;background-repeat:no-repeat;"
        "background-position:left top,right top,left bottom,right bottom}")


def bar_min(cell=14, cw=7.0):
    """The narrowest the plate may be: the nav row in whole cells, plus the two
    cells of inset on each side (one of them the rim)."""
    labels = [s[0] for s in SLOTS] + (["GALLERY"] if USER_ID else [])
    return int(math.ceil(len(" ".join(labels)) * cell + 4 * cw))


def css_lines(lines):
    """Lines as a CSS content string, with no space after the newline escape.

    css_text() terminates its \\A with a space, which is harmless in a wide
    scene but would push a one-cell-wide column of glyphs out of view.
    """
    out = [l.rstrip().replace("\\", "\\\\").replace('"', '\\"') for l in lines]
    return '"' + "\\00000a".join(out) + '"'


def rim_text(w, h, s):
    """The rim: one cell of dense & @ % glyphs, as a row and as a column."""
    row = "".join("&@%"[int(h2(x, 5, s) * 2.999)] for x in range(w))
    col = ["&@%"[int(h2(7, y, s + 31) * 2.999)] for y in range(h)]
    return row, col


def ground_text(w, h, s):
    """A faint scatter of . : | - over the plate's ground."""
    g = grid(w, h)
    chars = ".:|-"
    for i in range(int(w * h * 0.05)):
        x = int(h2(i, 11, s) * w)
        y = int(h2(i, 12, s + 3) * h)
        put(g, x, y, chars[int(h2(i, 13, s + 5) * 3.999)])
    return g


_RROW, _RCOL = rim_text(200, 16, SEED + 77)
RIMROW = css_lines([_RROW])
RIMCOL = css_lines(_RCOL)
SCATTER = css_lines("".join(r) for r in ground_text(180, 14, SEED + 131))


def build_css(pets):
    n = len(pets)
    L = []
    a = L.append

    a("/* blizzicky - generated by build.py. Do not edit by hand. */")
    for w, f in ((400, "ibm-plex-mono-latin-400-normal.woff2"),
                 (500, "ibm-plex-mono-latin-500-normal.woff2")):
        a('@font-face{font-family:"IBM Plex Mono";font-style:normal;font-weight:%d;'
          'font-display:block;src:url(fonts/%s) format("woff2")}' % (w, f))
    a('@font-face{font-family:"UnifrakturCook";font-style:normal;font-weight:700;'
      'font-display:block;src:url(fonts/unifrakturcook-latin-700-normal.woff2) format("woff2")}')

    a("@property --rev{syntax:'<percentage>';inherits:true;initial-value:100%}")
    a("@property --sw{syntax:'<percentage>';inherits:true;initial-value:130%}")

    # ---- host page ---------------------------------------------------------
    a("html,body{overflow:hidden !important}")

    # ---- overlay + tokens --------------------------------------------------
    a("#my-overlay{position:fixed !important;inset:0 !important;width:100vw;height:100vh;"
      "z-index:999999999 !important;overflow:hidden;background:#05070a !important;"
      "--ink:#05070a;--glass:#0a0f13;--line:#28373c;--text:#c6d6d0;--dim:#677c78;"
      "--tint:#9fd8bd;--accent:#ff4b2b;--slot:#070b0e;--slot2:#0c1215;--slotDown:#0e1519;"
      "--lock:#3c4a48;"
      "--panel:linear-gradient(180deg,rgba(10,15,19,.86),rgba(5,7,10,.94));"
      "--shadow:0 0 22px rgba(0,0,0,.75), inset 0 0 0 1px rgba(255,255,255,.02);"
      "--glowOn:0 0 14px -4px var(--tint);"
      '--mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;'
      '--black:"UnifrakturCook","Pirata One",Georgia,serif;'
      "--cw:7px;--ch:13px;--fs:11.667px;--cw2:14px;--ch2:26px;--fs2:23.334px;"
      "--step:184px;--gap:36px;--nsz:140px;--cy:44%;"
      "--rr:13px;--rt:26px;--rn:26px;--ri:26px;"
      "--barw:min(1120px,max(44vw," + str(bar_min()) + "px));"
      "--barh:calc(2 * var(--rr) + var(--rt) + var(--rn) + var(--ri));--blur:5.5px;"
      "--rev-t:" + REVEAL + ";--sweep:" + SWEEP + ";--slide:" + SLIDE + ";--boot:" + BOOT_HOLD + ";"
      "font-family:var(--mono);font-size:13px;line-height:1.35;color:var(--text);"
      "-webkit-font-smoothing:antialiased;text-align:left}")

    # ---- boot screen (ported, verbatim values) -----------------------------
    # the label keeps the exact position its inline style gave it, so there is no
    # jump between the pre-stylesheet cover and this one
    a("#bzk-boot{position:absolute !important;inset:0;z-index:40;pointer-events:none;"
      "display:block;"
      "background:radial-gradient(125% 92% at 50% 46%, rgba(0,0,0,0) 34%, rgba(0,0,0,.62) 100%), var(--ink) !important;"
      "transition:opacity .45s linear;"
      "animation:bzk-boot .45s linear var(--boot) forwards}")
    a('#bzk-boot::after{content:"";position:absolute;inset:0;z-index:0;pointer-events:none;'
      "background:repeating-linear-gradient(0deg, rgba(0,0,0,.26) 0 1px, rgba(0,0,0,0) 1px 3px),"
      "repeating-linear-gradient(90deg, rgba(255,255,255,.012) 0 1px, rgba(0,0,0,0) 1px 2px)}")
    a("#my-overlay .bzk-bs{z-index:1;"
      "font-family:var(--mono) !important;font-weight:500;font-size:10px !important;"
      "letter-spacing:.28em;text-transform:uppercase;color:var(--dim) !important;opacity:.72;"
      "text-shadow:0 0 10px rgba(0,0,0,.9)}")
    a("@keyframes bzk-boot{from{opacity:1}to{opacity:0;visibility:hidden}}")

    # ---- anchors -----------------------------------------------------------
    a(".bzk-pa{position:fixed;top:0;left:0;width:0;height:0;overflow:hidden}")

    # ---- stage -------------------------------------------------------------
    a(".bzk-stage{position:absolute;inset:0;overflow:hidden;visibility:visible !important;"
      "--lp:18%;--n:" + str(n) + "}")

    # default kind/light come from the stage's own classes; any :target resets them
    a(".bzk-pa:target ~ .bzk-stage{" + "".join(
        "--v-%s:hidden;--cv-%s:hidden;--z-%s:1;--td-%s:1.6s;--an-%s:none;" % (k, k, k, k, k)
        for k in KINDS) + "--lp:18%}")
    for k in KINDS:
        rot = glow_rot(k)
        kc = ramp(k)
        # --envc is the kind's brightest stop (the room's colour, used by the
        # light leak, the edge glow and the pet's glow pool); --envdark is its
        # darkest stop (the scrim that sinks a neighbour into the wall).
        a(".bzk-stage.k-%s,.bzk-pa.k-%s:target ~ .bzk-stage{--v-%s:visible;--cv-%s:visible;"
          "--z-%s:2;--td-%s:0s;--an-%s:bzk-sweep;--grot:%ddeg;--envc:%s;--envdark:%s}"
          % (k, k, k, k, k, k, k, rot, kc[3], kc[0]))
    a(".bzk-stage.l-right,.bzk-pa.l-right:target ~ .bzk-stage{--lp:82%}")

    # ---- scene layers ------------------------------------------------------
    a(".bzk-scenes{position:absolute;inset:0;z-index:1;overflow:hidden;"
      "animation:bzk-settle 3s ease-out calc(var(--boot) + .45s) both}")
    a("@keyframes bzk-settle{0%,17%{opacity:0}100%{opacity:1}}")
    a(".bzk-sc{position:absolute;inset:0;overflow:hidden;"
      "font-family:var(--mono);font-weight:400;font-size:var(--fs);line-height:var(--ch);"
      "letter-spacing:0;white-space:pre;color:transparent;-webkit-text-fill-color:transparent;"
      "--gx:calc(50% - " + str(SCENE_W // 2) + " * var(--cw));"
      "--gy:calc(50% - " + str(SCENE_H // 2) + " * var(--ch));"
      "animation-duration:var(--sweep);animation-timing-function:steps(28,end);"
      "animation-fill-mode:both;transition-behavior:allow-discrete}")
    a('.bzk-sc::before,.bzk-sc::after{position:absolute;left:var(--gx);top:var(--gy);'
      "display:block;white-space:pre;-webkit-background-clip:text;background-clip:text}")
    a(".bzk-sc::before{content:var(--far);left:calc(var(--gx) - 10 * var(--cw));"
      "animation:bzk-drift 36s steps(20,end) infinite alternate;"
      "-webkit-mask-image:linear-gradient(100deg,#000 0 var(--sw),transparent var(--sw));"
      "mask-image:linear-gradient(100deg,#000 0 var(--sw),transparent var(--sw))}")
    a(".bzk-sc::after{content:var(--near);"
      "-webkit-mask-image:linear-gradient(100deg,#000 0 calc(var(--sw) - 6%),transparent calc(var(--sw) - 6%));"
      "mask-image:linear-gradient(100deg,#000 0 calc(var(--sw) - 6%),transparent calc(var(--sw) - 6%))}")
    a(".bzk-sc > i,.bzk-sc > u{position:absolute;left:var(--gx);top:var(--lt,8%);display:block;"
      "white-space:pre;text-decoration:none;font-style:normal;"
      "-webkit-mask-image:linear-gradient(100deg,#000 0 calc(var(--sw) - 3%),transparent calc(var(--sw) - 3%));"
      "mask-image:linear-gradient(100deg,#000 0 calc(var(--sw) - 3%),transparent calc(var(--sw) - 3%))}")
    a(".bzk-sc > i{animation:bzk-lifeA 5.4s steps(1,end) infinite}")
    a(".bzk-sc > u{animation:bzk-lifeB 5.4s steps(1,end) infinite}")
    a("@keyframes bzk-lifeA{0%,49.9%{opacity:1}50%,100%{opacity:0}}")
    a("@keyframes bzk-lifeB{0%,49.9%{opacity:0}50%,100%{opacity:1}}")
    a("@keyframes bzk-drift{from{left:calc(var(--gx) - 10 * var(--cw))}"
      "to{left:calc(var(--gx) - 30 * var(--cw))}}")
    a("@keyframes bzk-sweep{from{--sw:-8%}to{--sw:130%}}")
    a('.bzk-sc > i::before,.bzk-sc > u::before{content:var(--life);display:block;white-space:pre;'
      "-webkit-background-clip:text;background-clip:text}")

    for k in KINDS:
        c = ramp(k)
        # the layer that just lost focus keeps its 1.6s delay, so it stays painted
        # under the incoming sweep; the incoming one has --td:0s and appears at once.
        # transition-behavior comes AFTER the shorthand: the shorthand resets it,
        # and without allow-discrete content-visibility would flip immediately and
        # the bare ground would flash between scenes.
        a(".s-%s{visibility:var(--v-%s,hidden);content-visibility:var(--cv-%s,hidden);"
          "z-index:var(--z-%s,1);--lt:%s;"
          "transition:visibility 0s linear var(--td-%s,1.6s),"
          "content-visibility 0s linear var(--td-%s,1.6s);"
          "transition-behavior:allow-discrete;"
          "animation-name:var(--an-%s,none)}" % (k, k, k, k, LIFE_TOP[k], k, k, k))
        a(".s-" + k + "::before{background-image:radial-gradient(130% 96% at var(--lp) 26%,"
          + c[2] + "," + c[2] + " 16%," + c[1] + " 54%," + c[0] + " 96%)}")
        a(".s-" + k + "::after{background-image:radial-gradient(112% 82% at var(--lp) 22%,"
          + c[3] + "," + c[3] + " 24%," + c[2] + " 56%," + c[1] + " 90%);"
          "text-shadow:0 0 7px " + c[2] + "66}")
        a(".s-" + k + " > i::before,.s-" + k + " > u::before{background-image:"
          "linear-gradient(100deg," + c[3] + "," + c[2] + ");text-shadow:0 0 9px " + c[3] + "99}")

    # ======================================================================= #
    # REPLACEABLE SECTION: SCREEN EFFECTS                                     #
    # Blur field, textures (look 1), side glow, vignette, scanline, grain.    #
    # Markup: fx_html() (under the bar) and fx_top_html() (over it).          #
    # Nothing outside this section depends on it.                             #
    # ======================================================================= #
    # --- blur field: four stacked backdrop-filter bands, each masked so the
    #     blur builds up gradually towards the edges. Band 1 is unmasked, so
    #     there is always at least a slight blur on the room; the clear spot
    #     over the focused pet keeps about a quarter of the full blur. Nothing
    #     here has a hard edge: every mask stop is a wide gradient. The pets
    #     paint above all of this, so pet art is never blurred.
    hd = "min(85vmax,849px)"      # about the half-diagonal of a 1440x900 screen
    a(".bzk-bl{position:absolute;inset:0;z-index:2;pointer-events:none;"
      "-webkit-mask-repeat:no-repeat;mask-repeat:no-repeat;"
      "-webkit-mask-composite:source-in;mask-composite:intersect}")
    for i, (b, s0, s1, c0, c1) in enumerate(
            ((1.4, None, None, None, None),     # the floor: always a little blur
             (2.2, .00, .46, .07, .21),
             (3.0, .22, .74, .07, .24),
             (3.8, .46, 1.0, .07, .28)), 1):
        css = (".bzk-bl.b" + str(i) + "{backdrop-filter:blur(calc(var(--blur) * "
               + ("%.3f" % (b / 5.5)) + "));-webkit-backdrop-filter:blur(calc(var(--blur) * "
               + ("%.3f" % (b / 5.5)) + "))")
        if s0 is not None:
            # the clear spot, in fractions of the half-diagonal, with a
            # smoothstep-ish three-stop falloff
            rad = ("radial-gradient(circle " + hd + " at 50% var(--cy),transparent "
                   + ("%.1f" % (s0 * 100)) + "%,rgba(0,0,0,.25) "
                   + ("%.1f" % ((s0 + (s1 - s0) * .45) * 100)) + "%,#000 "
                   + ("%.1f" % (s1 * 100)) + "%)")
            # the clearer strip down the middle
            lin = ("linear-gradient(90deg,#000 0,#000 " + ("%.1f" % (50 - c1 * 100))
                   + "%,transparent " + ("%.1f" % (50 - c0 * 100)) + "%,transparent "
                   + ("%.1f" % (50 + c0 * 100)) + "%,#000 " + ("%.1f" % (50 + c1 * 100))
                   + "%,#000 100%)")
            css += (";-webkit-mask-image:" + rad + "," + lin
                    + ";mask-image:" + rad + "," + lin)
        a(css + "}")

    # --- textures: look 1, generated, three at once, each kept faint
    a(".bzk-tx{position:absolute;inset:0;z-index:4;pointer-events:none}")
    # 1: light leak, in the room's own colour
    a(".bzk-tx.t1{mix-blend-mode:screen;opacity:.24;"
      "background:linear-gradient(118deg,transparent 0 46%,"
      "color-mix(in srgb,var(--envc,#9fd8bd) 30%,transparent) 62%,"
      "color-mix(in srgb,var(--envc,#9fd8bd) 8%,transparent) 76%,transparent 88%),"
      "radial-gradient(70% 50% at 104% 8%,"
      "color-mix(in srgb,var(--envc,#9fd8bd) 22%,transparent),transparent 72%);"
      "animation:bzk-leak 42s ease-in-out infinite alternate}")
    a("@keyframes bzk-leak{0%{opacity:.17}36%{opacity:.17}100%{opacity:.30}}")
    # 2: halftone wash, small dots, held to the corners
    a(".bzk-tx.t2{opacity:.16;mix-blend-mode:soft-light;background-image:" + HALFTONE + ";"
      "background-size:5px 5px;"
      "-webkit-mask-image:radial-gradient(118% 92% at 50% 50%,transparent 0 44%,#000 100%);"
      "mask-image:radial-gradient(118% 92% at 50% 50%,transparent 0 44%,#000 100%)}")
    # 3: stain with ring edges
    a(".bzk-tx.t3{opacity:.20;mix-blend-mode:overlay;background-image:" + STAIN + ";"
      "background-size:1100px 760px;background-position:center;background-repeat:no-repeat;"
      "animation:bzk-stain 42s ease-in-out infinite alternate}")
    a("@keyframes bzk-stain{0%{opacity:.13}36%{opacity:.13}100%{opacity:.26}}")

    # --- scanline + grain, over the bar as well as the room
    a(".bzk-scan{position:absolute;inset:0;z-index:5;pointer-events:none;"
      "background:repeating-linear-gradient(0deg,rgba(0,0,0,.17) 0 1px,rgba(0,0,0,0) 1px 2px)}")
    a('.bzk-scan::after{content:"";position:absolute;inset:0;opacity:.05;'
      "background-image:" + GRAIN + ";background-size:120px 120px}")
    # bloom is not a layer: only the bright ramp stops carry it, through the
    # text-shadow on the near scene layer and on the life bands, so a dim cell
    # never spills and nothing is hazed.

    # --- side glow: hugs each edge, gaussian-ish over about a tenth of the
    #     width, strongest at mid height, in the room's colour, held off the bar
    a(".bzk-edge{position:absolute;left:0;top:0;bottom:0;width:10.5%;z-index:6;"
      "pointer-events:none;mix-blend-mode:screen;"
      "-webkit-mask-image:linear-gradient(180deg,rgba(0,0,0,.2) 0,#000 46%,rgba(0,0,0,.2) 100%),"
      "linear-gradient(0deg,transparent 0,transparent calc(var(--barh) - 6px),"
      "#000 calc(var(--barh) + 42px));"
      "mask-image:linear-gradient(180deg,rgba(0,0,0,.2) 0,#000 46%,rgba(0,0,0,.2) 100%),"
      "linear-gradient(0deg,transparent 0,transparent calc(var(--barh) - 6px),"
      "#000 calc(var(--barh) + 42px));"
      "-webkit-mask-composite:source-in;mask-composite:intersect;"
      "animation:bzk-breathe 23s ease-in-out infinite}")
    a(".bzk-edge.e-l{background:linear-gradient(90deg,"
      "color-mix(in srgb,var(--envc,#9fd8bd) 30%,transparent) 0,"
      "color-mix(in srgb,var(--envc,#9fd8bd) 8%,transparent) 42%,transparent 100%)}")
    a(".bzk-edge.e-r{left:auto;right:0;background:linear-gradient(270deg,"
      "color-mix(in srgb,var(--envc,#9fd8bd) 33%,transparent) 0,"
      "color-mix(in srgb,var(--envc,#9fd8bd) 9%,transparent) 42%,transparent 100%);"
      "animation-delay:-11.5s}")
    a("@keyframes bzk-breathe{0%,100%{opacity:.40}50%{opacity:.86}}")

    # --- vignette, held off the bar
    a(".bzk-vig{position:absolute;inset:0;z-index:7;pointer-events:none;"
      "background:radial-gradient(125% 92% at 50% 46%,rgba(0,0,0,0) 34%,rgba(0,0,0,.52) 100%);"
      "-webkit-mask-image:linear-gradient(0deg,transparent 0,"
      "transparent calc(var(--barh) - 6px),#000 calc(var(--barh) + 42px));"
      "mask-image:linear-gradient(0deg,transparent 0,"
      "transparent calc(var(--barh) - 6px),#000 calc(var(--barh) + 42px))}")
    # ===================== end REPLACEABLE: SCREEN EFFECTS ================= #

    # ---- pets --------------------------------------------------------------
    a(".bzk-row{position:absolute;left:0;right:0;top:var(--cy);height:0;z-index:8}")
    a(".bzk-pet{position:absolute;top:-100px;width:200px;height:200px;line-height:0;"
      "--s:calc(var(--i) - var(--f));--d:max(var(--s),calc(-1 * var(--s)));"
      "--on:clamp(0,calc(1 - var(--d)),1);--vis:clamp(0,calc(4 - var(--d)),1);"
      "--sg:clamp(-1,var(--s),1);"
      "--x:calc(var(--s) * var(--step) + var(--sg) * var(--gap));"
      "--sz:calc((var(--nsz) + (200px - var(--nsz)) * var(--on)) * var(--vis));"
      "--pad:calc((200px - var(--sz)) / 2);--shd:var(--slide);"
      "left:calc(50% + var(--x) - 100px);opacity:var(--vis);"
      "transition:left var(--slide) cubic-bezier(.3,.7,.2,1),opacity var(--slide) linear}")
    # the glow pool, the closed frame and the sheet are painted from CSS
    # backgrounds: their URLs then live in profile.css, where they are
    # relative, and a sheet inside a display:none window is never fetched
    a(".bzk-pet img,.bzk-pet > u{position:absolute;top:var(--pad);left:var(--pad);"
      "display:block;background-size:100% 100%;background-repeat:no-repeat;"
      "width:var(--sz);height:var(--sz);"
      "transition:top var(--slide) cubic-bezier(.3,.7,.2,1),"
      "left var(--slide) cubic-bezier(.3,.7,.2,1),"
      "width var(--slide) cubic-bezier(.3,.7,.2,1),"
      "height var(--slide) cubic-bezier(.3,.7,.2,1),opacity .3s linear}")
    # 1: a soft pool of the room's colour under the pet. It is larger than the
    #    pet and blurred far past its edge, so it never reads as an outline.
    a(".bzk-pet .bzk-aura{z-index:1;opacity:calc(.08 + .36 * var(--on));"
      "top:calc(var(--pad) - 16px);left:calc(var(--pad) - 16px);"
      "width:calc(var(--sz) + 32px);height:calc(var(--sz) + 32px);"
      "filter:brightness(0) invert(1) sepia(1) saturate(5) "
      "hue-rotate(var(--grot,200deg)) blur(24px)}")
    # 2: the closed frame - the pet as coloured ASCII. It stays lit under the
    #    sheet, so a slow or missing sheet shows this, never a blank box.
    a(".bzk-pet .bzk-closed{z-index:2;opacity:1}")
    # 2b: fallback when no sprite sheet exists for this pet: the old glyph mask
    a(".bzk-pet .bzk-fake{opacity:calc(1 - var(--on));"
      "filter:saturate(1.2) contrast(1.05) brightness(1.4);"
      "-webkit-mask-image:" + MASK_TILE + ";mask-image:" + MASK_TILE + ";"
      "-webkit-mask-size:var(--cw) var(--ch);mask-size:var(--cw) var(--ch);"
      "-webkit-mask-repeat:repeat;mask-repeat:repeat}")
    # 3: the reveal sheet, stepped on real geometry inside a 200x200 window
    a(".bzk-pet .bzk-win{position:absolute;top:var(--pad);left:var(--pad);"
      "width:var(--sz);height:var(--sz);overflow:hidden;z-index:3;display:none;"
      "opacity:var(--on);"
      "transition:top var(--slide) cubic-bezier(.3,.7,.2,1),"
      "left var(--slide) cubic-bezier(.3,.7,.2,1),"
      "width var(--slide) cubic-bezier(.3,.7,.2,1),"
      "height var(--slide) cubic-bezier(.3,.7,.2,1),opacity .3s linear}")
    a(".bzk-pet .bzk-win i{position:absolute;top:0;left:0;display:block;"
      "background-size:100% 100%;background-repeat:no-repeat;"
      "animation-fill-mode:forwards,forwards;"
      "animation-delay:var(--shd),var(--shd);transition:none}")
    a(".bzk-pet .bzk-sh{animation-name:var(--shn,none)}")
    a("@keyframes bzk-sx{from{left:0}to{left:var(--sxend)}}")
    a("@keyframes bzk-sy{from{top:0}to{top:var(--syend)}}")
    # 3b: the optional closing sheet, played once when a pet loses focus
    a(".bzk-pet .bzk-shc{opacity:calc(1 - var(--on));animation-name:var(--cln,none)}")
    a(".bzk-pet.bzk-hasclose .bzk-win{opacity:1;transition:"
      "top var(--slide) cubic-bezier(.3,.7,.2,1),left var(--slide) cubic-bezier(.3,.7,.2,1),"
      "width var(--slide) cubic-bezier(.3,.7,.2,1),height var(--slide) cubic-bezier(.3,.7,.2,1)}")
    a(".bzk-pet.bzk-hasclose .bzk-sh{opacity:var(--on);transition:none}")
    a("@keyframes bzk-cx{from{left:0}to{left:var(--cxend)}}")
    a("@keyframes bzk-cy{from{top:0}to{top:var(--cyend)}}")
    # 4: the site's own PNG takes over when the reveal finishes
    a(".bzk-pet .bzk-img{z-index:4;opacity:0;animation-name:var(--hon,none);"
      "animation-duration:.15s;animation-timing-function:linear;"
      "animation-delay:calc(var(--shd) + var(--T,0s));animation-fill-mode:forwards}")
    a("@keyframes bzk-hand{from{opacity:0}to{opacity:1}}")
    a(".bzk-pet.bzk-nosheet .bzk-img{opacity:var(--on);animation:none}")
    # 5: a neighbour is not dimmed: it stands behind a scrim of the room's own
    #    dark colour, cut to the pet's shape, so it sinks into the wall
    a('.bzk-pet::after{content:"";position:absolute;top:var(--pad);left:var(--pad);'
      "width:var(--sz);height:var(--sz);z-index:5;pointer-events:none;"
      "background:var(--envdark,#07131b);opacity:calc(.6 * (1 - var(--on)));"
      "-webkit-mask-size:100% 100%;mask-size:100% 100%;"
      "-webkit-mask-repeat:no-repeat;mask-repeat:no-repeat;"
      "transition:top var(--slide) cubic-bezier(.3,.7,.2,1),"
      "left var(--slide) cubic-bezier(.3,.7,.2,1),"
      "width var(--slide) cubic-bezier(.3,.7,.2,1),"
      "height var(--slide) cubic-bezier(.3,.7,.2,1),opacity var(--slide) linear}")
    # the numeral: bare on the wall, no plate, carved, and blurred into the
    # wall as it leaves the centre
    a(".bzk-pet b{position:absolute;left:-40px;right:-40px;"
      "top:calc(var(--pad) - var(--sz) * .40);"
      "font-family:var(--black);font-weight:700;font-size:calc(var(--sz) * .39);"
      "line-height:1;text-align:center;color:#131c22;letter-spacing:.04em;"
      "text-shadow:0 2px 0 color-mix(in srgb,var(--tint) 86%,transparent),"
      "0 -1px 0 rgba(0,0,0,.95),2px 0 0 rgba(0,0,0,.9),-2px 0 0 rgba(0,0,0,.9),"
      "0 5px 11px rgba(0,0,0,.9);"
      "filter:blur(calc((1 - var(--on)) * 1.7px));"
      "transition:top var(--slide) cubic-bezier(.3,.7,.2,1),"
      "font-size var(--slide) cubic-bezier(.3,.7,.2,1),filter var(--slide) linear}")
    # links: the focused pet's link leads to its page, a neighbour's focuses it
    a(".bzk-pet a{position:absolute;top:var(--pad);left:var(--pad);display:block;"
      "text-decoration:none;outline:none;z-index:6;"
      "transition:top var(--slide) linear,left var(--slide) linear}")
    a(".bzk-pet .bzk-sel{width:calc(var(--sz) * var(--vis));"
      "height:calc(var(--sz) * var(--vis))}")
    a(".bzk-pet .bzk-go{z-index:7;width:calc(var(--sz) * var(--on));"
      "height:calc(var(--sz) * var(--on));cursor:pointer}")
    a(PIPS % ".bzk-pet a:focus-visible::after")

    # per-pet geometry: the scrim's silhouette, and the sheet's own grid
    for i, p in enumerate(pets, 1):
        sel = ".bzk-row > div:nth-child(" + str(i) + ")"
        a(sel + "::after{-webkit-mask-image:url(" + p["img"] + ");mask-image:url("
          + p["img"] + ")}")
        a(sel + " .bzk-aura{background-image:url(" + p["img"] + ")}")
        r = p.get("rev")
        if not r:
            a(sel + " .bzk-fake{background-image:url(" + p["img"] + ")}")
            continue
        a(sel + " .bzk-closed{background-image:url(" + r["closed"] + ")}")
        a(sel + " .bzk-sh{background-image:url(" + r["sheet"] + ")}")
        if r.get("close"):
            a(sel + " .bzk-shc{background-image:url(" + r["close"]["file"] + ")}")
        a(sel + "{--T:" + ("%.3f" % r["duration_s"]) + "s;--sxend:calc(-100% * "
          + str(r["cols"] - 1) + ");--syend:calc(-100% * " + str(r["rows"] - 1) + ")}")
        a(sel + " .bzk-sh{width:" + str(r["cols"] * 100) + "%;height:"
          + str(r["rows"] * 100) + "%;animation-duration:calc(var(--T) / "
          + str(r["rows"]) + "),var(--T);animation-timing-function:steps("
          + str(r["cols"]) + ",jump-none),steps(" + str(r["rows"]) + ",jump-none);"
          "animation-iteration-count:" + str(r["rows"]) + ",1}")
        c = r.get("close")
        if c:
            a(sel + "{--cxend:calc(-100% * " + str(c["cols"] - 1)
              + ");--cyend:calc(-100% * " + str(c["rows"] - 1) + ")}")
            a(sel + " .bzk-shc{width:" + str(c["cols"] * 100) + "%;height:"
              + str(c["rows"] * 100) + "%;animation-duration:calc("
              + ("%.3f" % c["duration_s"]) + "s / " + str(c["rows"]) + "),"
              + ("%.3f" % c["duration_s"]) + "s;animation-timing-function:steps("
              + str(c["cols"]) + ",jump-none),steps(" + str(c["rows"]) + ",jump-none);"
              "animation-iteration-count:" + str(c["rows"]) + ",1;animation-delay:0s,0s}")

    # ---- name line ---------------------------------------------------------
    a(".bzk-names{position:absolute;left:0;right:0;top:calc(var(--cy) + 116px);"
      "height:34px;z-index:9;text-align:center}")
    a(".bzk-nm{position:absolute;left:0;right:0;top:0;white-space:nowrap}")
    a(".bzk-nm b{font-family:var(--black);font-weight:700;font-size:27px;line-height:1.2;"
      "color:var(--text);letter-spacing:.02em;text-shadow:0 0 10px rgba(0,0,0,.85);"
      "animation-name:var(--run,none);animation-duration:var(--at,0s);"
      "animation-timing-function:linear}")
    a("@keyframes bzk-type{from,to{opacity:0}}")
    a(".bzk-nm i{display:inline-block;width:12px;height:19px;margin-left:3px;"
      "background:var(--tint);vertical-align:-2px;font-style:normal;"
      "animation:bzk-blink 2s steps(1,end) infinite}")
    a("@keyframes bzk-blink{0%,49.9%{opacity:1}50%,100%{opacity:0}}")
    for i in range(1, MAX_NAME + 1):
        a(".bzk-nm b:nth-child(%d){--k:%d}" % (i, i - 1))
    a(".bzk-nm b{--at:calc(.26s + var(--k,0) * %.4fs)}" % (1.0 / TYPE_CPS))

    # ======================================================================= #
    # REPLACEABLE SECTION: TERMINAL BAR                                       #
    # One flat plate at the foot of the room, on the 7x13 cell grid: a rim     #
    # row, the readout, the nav row, the index row, a rim row. No borders, no  #
    # boxes, no corner marks, no crest, no labels; dim is told apart by ink    #
    # alone. All of its text is IBM Plex Mono, upper case, double size.        #
    # The rim and the ground scatter are generated text, carried in CSS        #
    # custom properties so they cost the paste nothing. Markup: bar_html().    #
    # The rest of the build only needs this to keep emitting, per pet i:       #
    #   * class "bzk-fo nI" on the readout line and on the prev/next pair      #
    #   * class "bzk-ix nI" on the index cell (its <s> is lit through --on2)    #
    # ======================================================================= #
    a(".bzk-bar{position:absolute;left:0;right:0;bottom:0;z-index:3;height:var(--barh);"
      "display:flex;justify-content:center;pointer-events:none;filter:blur(1.2px)}")
    a(".bzk-plate{position:relative;width:var(--barw);height:var(--barh);"
      "pointer-events:auto;font-family:var(--mono);font-weight:400;"
      "font-size:var(--fs2);line-height:var(--ch2);letter-spacing:0;"
      "text-transform:uppercase;white-space:nowrap}")
    # the ground: a very low wash, a touch lighter than the ink, lit from above
    # and feathered about four cells into the room so the plate has no cut edge
    a('.bzk-plate::before{content:"";position:absolute;inset:0;z-index:0;'
      "background:linear-gradient(180deg," + ink(.042) + " 0," + ink(.012) + " 62%,"
      + ink(.026) + " 100%);"
      "-webkit-mask-image:linear-gradient(90deg,transparent 0,#000 28px,"
      "#000 calc(100% - 28px),transparent 100%),"
      "linear-gradient(180deg,transparent 0,#000 26px,#000 100%);"
      "mask-image:linear-gradient(90deg,transparent 0,#000 28px,"
      "#000 calc(100% - 28px),transparent 100%),"
      "linear-gradient(180deg,transparent 0,#000 26px,#000 100%);"
      "-webkit-mask-composite:source-in;mask-composite:intersect}")
    # the faint scatter on the ground
    a(".bzk-plate::after{content:var(--scatter);position:absolute;z-index:1;"
      "left:var(--cw);right:var(--cw);top:var(--rr);bottom:var(--rr);overflow:hidden;"
      "white-space:pre;font-size:var(--fs);line-height:var(--ch);color:" + ink(.085) + ";"
      "pointer-events:none}")
    # the rim: one cell of & @ % glyphs, lit differently on each edge
    a(".bzk-rim{position:absolute;z-index:1;overflow:hidden;white-space:pre;"
      "font-size:var(--fs);line-height:var(--ch);pointer-events:none}")
    a(".bzk-rim::before{content:var(--rim)}")
    a(".bzk-rim.r-t{left:0;right:0;top:0;height:var(--rr);--rim:var(--rimrow);color:"
      + ink(.400) + "}")
    a(".bzk-rim.r-f{left:0;right:0;bottom:0;height:var(--rr);--rim:var(--rimrow);color:"
      + ink(.170) + "}")
    a(".bzk-rim.r-l{left:0;top:0;bottom:0;width:var(--cw);--rim:var(--rimcol);color:"
      + ink(.255) + "}")
    a(".bzk-rim.r-r{right:0;top:0;bottom:0;width:var(--cw);--rim:var(--rimcol);color:"
      + ink(.255) + "}")
    # the three content rows, inset two cells from the rim
    a(".bzk-rw,.bzk-nv,.bzk-ixr{position:absolute;left:calc(2 * var(--cw));"
      "right:calc(2 * var(--cw));z-index:2}")
    a(".bzk-rw{top:var(--rr);height:var(--rt)}")
    a(".bzk-nv{top:calc(var(--rr) + var(--rt));height:var(--rn);text-align:center}")
    a(".bzk-ixr{top:calc(var(--rr) + var(--rt) + var(--rn));height:var(--ri)}")
    # readout: the name on the left, the count on the right. The count gives
    # way first, so the name is never clipped while there is room for it.
    a(".bzk-ro{position:absolute;left:0;right:0;top:0;height:var(--rt);display:flex;"
      "align-items:center;justify-content:space-between;gap:var(--cw2);overflow:hidden}")
    a(".bzk-ro b{font-weight:400;flex:1 0 auto;color:" + ink(.96) + "}")
    a(".bzk-ro em{font-style:normal;flex:0 1 auto;min-width:0;overflow:hidden;color:"
      + ink(.44) + "}")
    # nav row: plain words, separated by single spaces
    a(".bzk-nv{color:" + ink(.62) + "}")
    a(".bzk-slot{position:relative;display:inline-block;color:" + ink(.62) + ";"
      "text-decoration:none;outline:none;line-height:var(--rn);height:var(--rn);"
      "transition:color .14s linear}")
    a(".bzk-slot:hover,.bzk-slot:active,.bzk-slot:focus-visible{color:" + ink(1.0) + "}")
    a(PIPS % ".bzk-slot:focus-visible::after")
    # index row: one block per pet, centred; < and > at the ends
    a(".bzk-strip{position:absolute;left:0;right:0;top:0;height:var(--ri);display:flex;"
      "align-items:center;justify-content:center}")
    a(".bzk-ix{position:relative;display:inline-block;width:var(--cw2);text-align:center;"
      "line-height:var(--ri);height:var(--ri);color:" + ink(.62) + ";"
      "text-decoration:none;outline:none;transition:color .14s linear}")
    a(".bzk-ix:hover{color:" + ink(1.0) + "}")
    a(".bzk-ix s{position:absolute;left:0;right:0;top:0;text-decoration:none;color:"
      + ink(1.0) + ";opacity:var(--on2,0)}")
    a(PIPS % ".bzk-ix:focus-visible::after")
    a(".bzk-navset{position:absolute;left:0;right:0;top:0;height:var(--ri)}")
    a(".bzk-navset a{position:absolute;top:0;width:calc(2 * var(--cw2));height:var(--ri);"
      "line-height:var(--ri);text-align:center;color:" + ink(.62) + ";"
      "text-decoration:none;outline:none;transition:color .14s linear}")
    a(".bzk-navset a:hover{color:" + ink(1.0) + "}")
    a(".bzk-navset .bzk-pv{left:0}")
    a(".bzk-navset .bzk-nx{right:0}")
    a(PIPS % ".bzk-navset a:focus-visible::after")
    a(".bzk-plate{--rimrow:" + RIMROW + ";--rimcol:" + RIMCOL + ";--scatter:" + SCATTER + "}")
    # ====================== end REPLACEABLE: TERMINAL BAR ================== #

    # ---- generic per-pet wiring (pre-written for MAXPETS) -------------------
    a(".bzk-stage{--f:1}")
    for k in range(1, MAXPETS + 1):
        a(".bzk-pa:nth-of-type(%d):target ~ .bzk-stage{--f:%d}" % (k, k))
    for k in range(1, MAXPETS + 1):
        a(".bzk-pet:nth-child(%d){--i:%d}" % (k, k))
    # focus groups: hidden by default, pet 1 shown, any :target resets then one wins
    a(".bzk-fo{visibility:hidden;--run:none;--on2:0}")
    a(".bzk-ix{--on2:0}")
    # a pet that is not focused rests closed; its closing sheet, if it has one,
    # restarts every time --cln goes back from none to a name, which is exactly
    # the moment it loses focus
    a(".bzk-pet{--cln:bzk-cx,bzk-cy}")
    a(".bzk-stage .n1{visibility:visible;--run:bzk-type;--on2:1}")
    # the default pet opens once the boot screen has gone
    a(".bzk-stage .bzk-pet:nth-child(1){--shn:bzk-sx,bzk-sy;--hon:bzk-hand;--cln:none;"
      "--shd:calc(var(--boot) + .45s)}")
    # only the focused pet and its neighbours are allowed to load a full sheet
    a(".bzk-stage .bzk-pet:nth-child(-n+2) .bzk-win{display:block}")
    a(".bzk-pa:target ~ .bzk-stage .bzk-fo{visibility:hidden;--run:none;--on2:0}")
    a(".bzk-pa:target ~ .bzk-stage .bzk-ix{--on2:0}")
    a(".bzk-pa:target ~ .bzk-stage .bzk-pet{--shn:none;--hon:none;"
      "--cln:bzk-cx,bzk-cy;--shd:var(--slide)}")
    a(".bzk-pa:target ~ .bzk-stage .bzk-win{display:none}")
    for k in range(1, MAXPETS + 1):
        a(".bzk-pa:nth-of-type(%d):target ~ .bzk-stage .n%d{visibility:visible;"
          "--run:bzk-type;--on2:1}" % (k, k))
        a(".bzk-pa:nth-of-type(%d):target ~ .bzk-stage .bzk-pet:nth-child(%d){"
          "--shn:bzk-sx,bzk-sy;--hon:bzk-hand;--cln:none}" % (k, k))
        a(",".join(".bzk-pa:nth-of-type(%d):target ~ .bzk-stage .bzk-pet:nth-child(%d) .bzk-win"
                   % (k, j) for j in (k - 1, k, k + 1) if 1 <= j <= MAXPETS)
          + "{display:block}")

    # ---- phone -------------------------------------------------------------
    # single-size bar text, taller nav and index rows so every target is 44px
    a("@media (max-width:560px){#my-overlay{--cw:5.5px;--ch:10px;--fs:9.1667px;"
      "--cw2:7px;--ch2:16px;--fs2:11.667px;"
      "--step:118px;--gap:36px;--nsz:96px;--cy:38%;--blur:3.3px;"
      "--rr:10px;--rt:26px;--rn:44px;--ri:44px;"
      "--barw:min(100vw - 8px," + str(bar_min(7, 5.5)) + "px)}"
      ".bzk-names{top:calc(var(--cy) + 110px)}"
      ".bzk-nm b{font-size:21px}.bzk-nm i{width:9px;height:15px}"
      ".bzk-ix{width:calc(2 * var(--cw2))}"
      ".bzk-navset a{width:calc(3 * var(--cw2))}}")

    # ---- reduced motion ----------------------------------------------------
    a("@media (prefers-reduced-motion:reduce){"
      "#my-overlay *,#my-overlay *::before,#my-overlay *::after{animation:none !important}"
      "#my-overlay{--sw:130%}"
      ".bzk-scenes{opacity:1}"
      "#bzk-boot{opacity:0;visibility:hidden}"
      ".bzk-sc > u{opacity:0}"
      ".bzk-sc::before{left:calc(-10 * var(--cw))}"
      ".bzk-pet{transition:opacity .3s linear,left 0s}"
      ".bzk-pet img,.bzk-pet > u{transition:opacity .3s linear,top 0s,left 0s,width 0s,height 0s}"
      ".bzk-pet b{transition:none}.bzk-pet a{transition:none}"
      ".bzk-pet::after{transition:none}"
      # no playback: the window never gets layout, so no sheet is even fetched
      ".bzk-pet .bzk-win{display:none !important}"
      ".bzk-pet .bzk-img{opacity:var(--on);animation:none}"
      ".bzk-pet .bzk-closed{opacity:calc(1 - var(--on))}"
      ".bzk-sc{transition:none}}")



    return "\n".join(L) + "\n"


def scene_css(pets):
    """content strings for every kind that is actually used."""
    used = []
    for p in pets:
        if p["kind"] not in used:
            used.append(p["kind"])
    out = []
    for k in KINDS:
        if k not in used:
            continue
        far, near, life = SCENES[k](SCENE_W, SCENE_H, SEED + KINDS.index(k) * 1013)
        out.append(".s-%s{--far:%s;--near:%s}" % (k, css_text(far), css_text(near)))
        out.append(".s-%s > i{--life:%s}" % (k, css_text(life[0])))
        out.append(".s-%s > u{--life:%s}" % (k, css_text(life[1])))
    return "\n".join(out) + "\n", used

# --------------------------------------------------------------------------- #
# HTML                                                                        #
# --------------------------------------------------------------------------- #

BOOT = ('<div id="bzk-boot" style="position:absolute;top:0;left:0;width:100%;height:100%;'
        'background:#05070a"><span class="bzk-bs" style="position:absolute;top:50%;left:0;'
        'width:100%;text-align:center;font:10px monospace;letter-spacing:.28em;'
        'color:#677c78">LOADING</span></div>')

OVERLAY_STYLE = ("position:fixed;top:0;left:0;width:100vw;height:100vh;"
                 "z-index:999999999;background:#05070a")


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def num(i):
    return "%02d" % i


# ============================================================================ #
# REPLACEABLE SECTION: SCREEN EFFECTS MARKUP                                   #
# Paired with the screen-effects CSS in build_css(). DOM order does not matter #
# here: every layer carries its own z-index inside the overlay, and the pets   #
# sit above all of them, so nothing is ever drawn over pet art.                #
# ============================================================================ #


def fx_html():
    return ('<div class="bzk-bl b1"></div><div class="bzk-bl b2"></div>'
            '<div class="bzk-bl b3"></div><div class="bzk-bl b4"></div>'
            '<div class="bzk-tx t1"></div><div class="bzk-tx t2"></div>'
            '<div class="bzk-tx t3"></div><div class="bzk-scan"></div>'
            '<div class="bzk-edge e-l"></div><div class="bzk-edge e-r"></div>'
            '<div class="bzk-vig"></div>')


# ============================================================================ #
# REPLACEABLE SECTION: TERMINAL BAR MARKUP                                     #
# Paired with the terminal-bar CSS in build_css(). The rest of the build only  #
# needs this to keep emitting, per pet i (1-based):                            #
#   * one element with class "bzk-fo nI"  - shown only while pet i is focused  #
#     (the readout line, and the prev/next pair)                               #
#   * one element with class "bzk-ix nI"  - the index cell, always visible,    #
#     whose inner <s> is lit through --on2                                     #
# The rim rows, the rim columns and the ground scatter are generated text held #
# in CSS custom properties, so they cost the paste nothing.                    #
# ============================================================================ #


def bar_html(pets):
    n = len(pets)
    slots = list(SLOTS)
    if USER_ID:
        slots.append(("GALLERY", "%s/gallery/?u=%s" % (PREFIX, USER_ID)))
    o = ['<div class="bzk-bar"><div class="bzk-plate">'
         '<div class="bzk-rim r-t"></div><div class="bzk-rim r-f"></div>'
         '<div class="bzk-rim r-l"></div><div class="bzk-rim r-r"></div>'
         '<div class="bzk-rw">']
    for i, p in enumerate(pets, 1):
        o.append('<span class="bzk-ro bzk-fo n%d"><b>%s</b><em>%s/%s</em></span>'
                 % (i, esc(p["name"]), num(i), num(n)))
    o.append('</div><div class="bzk-nv">')
    o.append(" ".join('<a class="bzk-slot" href="%s">%s</a>' % (href, label)
                      for label, href in slots))
    o.append('</div><div class="bzk-ixr"><div class="bzk-strip">')
    for i in range(1, n + 1):
        o.append('<a class="bzk-ix n%d" href="#p%d">░<s>█</s></a>' % (i, i))
    o.append("</div>")
    for i in range(1, n + 1):
        pv = n if i == 1 else i - 1
        nx = 1 if i == n else i + 1
        o.append('<span class="bzk-navset bzk-fo n%d">'
                 '<a class="bzk-pv" href="#p%d">&lt;</a>'
                 '<a class="bzk-nx" href="#p%d">&gt;</a></span>' % (i, pv, nx))
    o.append("</div></div></div>")
    return "".join(o)


def pet_html(i, p, base=""):
    """One pet: glow pool, closed frame, reveal window, real image, two links.

    Only the real image's URL is in the paste; the other three layers are
    painted from profile.css, which keeps them relative and off the ref.
    """
    im, nm = esc(p["img"]), esc(p["name"])
    r = p.get("rev")
    cls = "bzk-pet"
    if not r:
        cls += " bzk-nosheet"
    elif r.get("close"):
        cls += " bzk-hasclose"
    o = ['<div class="%s"><b>%s</b><u class="bzk-aura"></u>' % (cls, num(i))]
    if r:
        # the closed frame stays lit under the sheet, so a sheet that is slow
        # or missing shows the closed pet, never a blank box
        o.append('<u class="bzk-closed"></u><span class="bzk-win">'
                 '<i class="bzk-sh"></i>')
        if r.get("close"):
            o.append('<i class="bzk-shc"></i>')
        o.append("</span>")
    else:
        o.append('<u class="bzk-closed bzk-fake"></u>')
    o.append('<img class="bzk-img" src="%s" alt="%s">'
             '<a class="bzk-sel" href="#p%d"></a>'
             '<a class="bzk-go" href="%s"></a></div>'
             % (im, nm, i, esc(p["href"])))
    return "".join(o)


def body(pets, used, base=""):
    """The paste body. `base` prefixes the reveal sheets (absolute for the paste,
    relative for the local preview); pet images are always absolute."""
    n = len(pets)
    o = []
    o.append('<div id="my-overlay" style="%s">%s' % (OVERLAY_STYLE, BOOT))
    for i, p in enumerate(pets, 1):
        o.append('<span class="bzk-pa k-%s l-%s" id="p%d"></span>'
                 % (p["kind"], p["light"], i))
    o.append('<div class="bzk-stage k-%s l-%s" style="visibility:hidden">'
             % (pets[0]["kind"], pets[0]["light"]))
    # scenes: one layer per kind in use
    o.append('<div class="bzk-scenes">'
             + "".join('<div class="bzk-sc s-%s"><i></i><u></u></div>' % k for k in used)
             + "</div>")
    o.append(fx_html())
    # pets
    o.append('<div class="bzk-row">')
    for i, p in enumerate(pets, 1):
        o.append(pet_html(i, p, base))
    o.append("</div>")
    # name line
    o.append('<div class="bzk-names">')
    for i, p in enumerate(pets, 1):
        letters = "".join("<b>%s</b>" % esc(c) for c in p["name"][:MAX_NAME])
        o.append('<span class="bzk-nm bzk-fo n%d">%s<i></i></span>' % (i, letters))
    o.append("</div>")
    o.append(bar_html(pets))
    o.append("</div></div>")
    return "".join(o)


FILLER = """<h1>Rescreatu - user profile (mock host page)</h1>
<p>This page stands in for the Rescreatu profile page that the overlay covers.
It is long and light so that a leak would be obvious.</p>
<h2>About</h2>
<p>Lorem ipsum filler, repeated so the mock page scrolls.</p>
"""


def preview(bodytext):
    pad = "".join("<p>Host page filler line %d. The overlay must hide all of this.</p>\n"
                  % i for i in range(1, 61))
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>blizzicky preview</title>"
            "<style>body{margin:0;padding:24px;background:#f3f1ea;color:#333;"
            "font:16px/1.5 Georgia,serif}</style></head><body>\n"
            + FILLER + pad +
            "\n<!-- ===== paste body below (link swapped to the local file) ===== -->\n"
            + '<link rel="stylesheet" href="profile.css">\n'
            + bodytext + "\n</body></html>\n")

# --------------------------------------------------------------------------- #
# reveal sheets                                                               #
# --------------------------------------------------------------------------- #

# reveal.py and reveal/ are written by the reveal build step, not by this file.
# Point BZK_REVEAL_DIR somewhere else to build against a different set.
REVEAL_DIR = os.environ.get("BZK_REVEAL_DIR") or os.path.join(HERE, "reveal")
REVEAL_URL = os.environ.get("BZK_REVEAL_URL") or "reveal/"


def reveal_key(url):
    """pets/7/ginger/effect-derp-baby.png -> 7-ginger-effect-derp-baby"""
    parts = [q for q in urllib.parse.urlsplit(url).path.split("/") if q]
    if "pets" in parts:
        parts = parts[parts.index("pets") + 1:]
    if parts:
        parts[-1] = parts[-1].rsplit(".", 1)[0]
    return "-".join(parts)


def _manifest():
    path = os.path.join(REVEAL_DIR, "manifest.json")
    if not os.path.isfile(path):
        return {}, "no reveal/manifest.json"
    try:
        with open(path, encoding="utf-8") as f:
            man = json.load(f)
    except Exception as exc:
        return {}, "reveal/manifest.json did not parse (%s)" % exc
    if isinstance(man, dict):
        for holder in ("sheets", "pets", "keys", "entries"):
            if holder in man and isinstance(man[holder], dict):
                return man[holder], None
    return man if isinstance(man, dict) else {}, None


def attach_reveal(pets, warn):
    """Render a sprite sheet per pet, then read its geometry from the manifest.

    Every number that drives the playback (columns, rows, frames, duration)
    comes from the manifest, never from a constant here. A pet with no sheet
    falls back to its glyph-masked closed frame and its real image, so the
    gallery still works while the sheets are being made.
    """
    mod = None
    if os.environ.get("BZK_SKIP_RENDER"):
        # read the sheets that are already there, render nothing
        pass
    elif os.path.isfile(os.path.join(HERE, "reveal.py")):
        bc = sys.dont_write_bytecode
        try:
            # reveal.py exits hard when numpy is missing, so catch everything,
            # and leave no __pycache__ in the folder that gets published
            sys.dont_write_bytecode = True
            if HERE not in sys.path:
                sys.path.insert(0, HERE)
            import reveal as mod
        except BaseException as exc:
            warn.append("reveal.py did not import (%s: %s)"
                        % (type(exc).__name__, exc))
            mod = None
        finally:
            sys.dont_write_bytecode = bc
    elif not os.environ.get("BZK_SKIP_RENDER"):
        warn.append("reveal.py is not there yet")

    for p in pets:
        p["key"] = reveal_key(p["img"])
        if mod is not None and hasattr(mod, "render"):
            try:
                mod.render(p["img"], REVEAL_DIR)
            except BaseException as exc:
                warn.append("reveal.render failed for %s (%s)" % (p["key"], exc))

    man, why = _manifest()
    if why:
        warn.append(why)
    for p in pets:
        key = p["key"]
        p["rev"] = None
        e = man.get(key)
        sheet, closed = key + ".webp", key + "-closed.webp"
        have = all(os.path.isfile(os.path.join(REVEAL_DIR, f)) for f in (sheet, closed))
        if not isinstance(e, dict) or not have:
            warn.append("no sheet for %s; it falls back to the glyph mask" % key)
            continue
        cols = int(e.get("cols", 8) or 8)
        frames = int(e.get("frames", 0) or 0)
        rows = int(e.get("rows", 0) or 0) or (frames // cols if cols else 0)
        dur = float(e.get("duration_s", 0) or 0)
        if cols < 1 or rows < 1 or dur <= 0:
            warn.append("manifest entry for %s is incomplete; glyph mask instead" % key)
            continue
        rv = {"key": key, "cols": cols, "rows": rows, "frames": frames or cols * rows,
              "duration_s": dur, "sheet": REVEAL_URL + sheet,
              "closed": REVEAL_URL + closed, "close": None}
        # the optional closing sheet is described inside the pet's own entry
        cfile = e.get("close") or (key + "-close.webp")
        if os.path.isfile(os.path.join(REVEAL_DIR, cfile)):
            cframes = int(e.get("close_frames", 0) or 0)
            crows = int(e.get("close_rows", 0) or 0) or (cframes // cols if cols else 0)
            cdur = float(e.get("close_duration_s", 0) or 0)
            if crows >= 1 and cdur > 0:
                rv["close"] = {"cols": cols, "rows": crows, "duration_s": cdur,
                               "file": REVEAL_URL + cfile}
        p["rev"] = rv

# --------------------------------------------------------------------------- #
# main                                                                        #
# --------------------------------------------------------------------------- #


def main(argv=None):
    global REF
    argv = list(sys.argv[1:] if argv is None else argv)
    while argv:
        if argv[0] in ("--ref", "-r") and len(argv) > 1:
            REF = argv[1]
            del argv[:2]
        elif argv[0].startswith("--ref="):
            REF = argv[0].split("=", 1)[1]
            del argv[:1]
        else:
            sys.exit("usage: build.py [--ref REF]   (REF: a branch, tag or commit sha)")

    pets, warn = read_pets(os.path.join(HERE, "pets.txt"))
    if not pets:
        sys.exit("pets.txt has no pets")
    attach_reveal(pets, warn)
    for w in warn:
        print("WARNING:", w)

    css = build_css(pets)
    scenes, used = scene_css(pets)
    css = css + scenes

    base = cdn_base()
    paste = '<link rel="stylesheet" href="%s">\n%s\n' % (cdn_css(), body(pets, used, base))

    for name, text in (("profile.css", css), ("profile.html", paste),
                       ("preview.html", preview(body(pets, used, "")))):
        with open(os.path.join(HERE, name), "w", encoding="utf-8") as f:
            f.write(text)

    chars = len(paste)
    # what one more pet costs the paste: its own markup plus its anchor, name
    # line, readout line, index cell and prev/next pair
    each = (len(body(pets, used, base))
            - (len(body(pets[:-1], used, base)) if len(pets) > 1 else 0))
    art = len(pet_html(len(pets), pets[-1], base))
    print("pets: %d  kinds in use: %s" % (len(pets), ", ".join(used)))
    for i, p in enumerate(pets, 1):
        print("  %s  %-16s %-9s %-5s %-8s %s"
              % (num(i), p["name"], p["kind"], p["light"],
                 "sheet" if p.get("rev") else "fallback", p["href"]))
    print("cdn ref      : %s" % REF)
    print("               %s" % cdn_css())
    print("profile.html : %d characters (limit 35000, target < 12000)" % chars)
    print("               %d characters for each pet (%d of that its own markup)"
          % (each, art))
    print("               about %d characters at 25 pets, %d at %d"
          % (chars + each * (25 - len(pets)),
             chars + each * (MAXPETS - len(pets)), MAXPETS))
    print("profile.css  : %d bytes" % len(css.encode("utf-8")))
    if chars > 30000:
        print("WARNING: paste is close to the 35,000 character limit")


if __name__ == "__main__":
    main()
