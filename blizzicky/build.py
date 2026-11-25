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
SWEEP = "1.4s"           # one pet's wall giving way to the next
SLIDE = ".34s"           # slide between pets
LIFE = "21s"             # one turn of the three captured moments

# ---- the row, as three numbers and nothing else ----------------------
# FOCUS is the focused pet's size and the pet art's own size: 200 px is
# native, so nothing is ever upscaled. GAP is the clear wall either side
# of it before the first neighbour, and STEP the distance between
# neighbours beyond that. NEAR is how big a neighbour is.
FOCUS = "200px"
GAP = "200px"
STEP = "150px"
NEAR = "0.58"
PLATES = "plates/"       # where the captured plates live
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
# the plates                                                                  #
# --------------------------------------------------------------------------- #

PLATE_DIR = os.environ.get("BZK_PLATE_DIR") or os.path.join(HERE, "plates")


def read_plates(warn):
    """The manifest written by the capture: every plate's file name and size,
    and the bar's own geometry in CSS pixels.

    Nothing here invents a number. If the manifest is missing the build still
    runs and says so, and the page comes out with no wall, which is loud.
    """
    path = os.path.join(PLATE_DIR, "manifest.json")
    if not os.path.isfile(path):
        warn.append("no plates/manifest.json - run the capture (bake-work/shoot.py)")
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        warn.append("plates/manifest.json did not parse (%s)" % exc)
        return None


FX_DIR = os.environ.get("BZK_FX_DIR") or os.path.join(HERE, "fx")
FX = "fx/"


def read_fx(warn):
    """The live layers' manifest: the grain tile and the jumps it takes, the
    morph's cell grid and frame count, and the churn tile. Written by
    bake-work/fx.py, which generates all three from the renderer's own
    numbers. Without it the page still builds - it simply has no grain and
    cross-fades between environments instead of morphing."""
    path = os.path.join(FX_DIR, "manifest.json")
    if not os.path.isfile(path):
        warn.append("no fx/manifest.json - run bake-work/fx.py all")
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        warn.append("fx/manifest.json did not parse (%s)" % exc)
        return None


def attach_plates(pets, man, warn):
    for p in pets:
        p["plate"] = None
        if not man:
            continue
        e = man["walls"].get(p["key"])
        if not e:
            warn.append("no plates for %s" % p["key"])
            continue
        p["plate"] = e


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


# one 7x13 character cell: a couple of strokes and a dot, so a masked image
# reads as rows of glyphs rather than as a dot screen. The no-sheet fallback
# is the only thing left that wants it.
MASK_TILE = ("url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
             "width='7' height='13'%3E%3Cg fill='%23fff'%3E"
             "%3Crect x='1' y='3' width='1' height='7'/%3E"
             "%3Crect x='4' y='3' width='1' height='7'/%3E"
             "%3Crect x='1' y='5' width='4' height='1'/%3E"
             "%3Crect x='2' y='8' width='3' height='1'/%3E"
             "%3Crect x='5' y='11' width='1' height='1'/%3E"
             "%3C/g%3E%3C/svg%3E\")")


def ramp(kind):
    """The kind's four-stop ramp. Only the darkest stop is still used: it is
    the scrim a neighbour stands behind so it sinks into the wall."""
    hue, ls, sm = ENV[kind]
    out = []
    for l in ls:
        s = min(1.0, max(0.40, 0.58 * sm))
        r, g, b = colorsys.hls_to_rgb(hue, l, s)
        out.append("#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255)))
    return out


def _box(b, extra=""):
    """One of the bar's own rectangles, in the plate's CSS pixels, scaled by
    --bs so the same numbers serve the phone."""
    return ("left:calc(%gpx * var(--bs));top:calc(%gpx * var(--bs));"
            "width:calc(%gpx * var(--bs));height:calc(%gpx * var(--bs));%s"
            % (b[0], b[1], b[2], b[3], extra))


def bar_css(a, man):
    """The bar is ONE PICTURE the renderer drew, with transparent links over
    the words. Hovering a link shows a LIT COPY of the whole plate clipped to
    that link's own rectangle, which is how the portfolio lights a label: the
    word brightens and not one pixel moves.
    """
    a(".bzk-bar{position:absolute;left:50%;bottom:var(--bbot);z-index:10;"
      "width:calc(var(--bw0) * var(--bs));height:calc(var(--bh0) * var(--bs));"
      "margin-left:calc(var(--bw0) * var(--bs) / -2)}")
    a(".bzk-bp{position:absolute;inset:0;display:block;"
      "background-size:100% 100%;background-repeat:no-repeat;opacity:0;"
      "transition:opacity .9s linear .8s}")
    a(".bzk-hit{position:absolute;inset:0}")
    a(".bzk-hit a{position:absolute;display:block;text-decoration:none;outline:none}")
    a(".bzk-hit a > i{position:absolute;inset:0;display:block;overflow:hidden}")
    a('.bzk-hit a > i::before{content:"";position:absolute;display:block;'
      "width:calc(var(--bw0) * var(--bs));height:calc(var(--bh0) * var(--bs));"
      "background-image:var(--lit);background-size:100% 100%;"
      "background-repeat:no-repeat;opacity:0;transition:opacity .12s linear}")
    a(".bzk-hit a:hover > i::before,.bzk-hit a:active > i::before,"
      ".bzk-hit a:focus-visible > i::before{opacity:1}")
    a(PIPS % ".bzk-hit a:focus-visible::before")
    a(".bzk-nav{display:contents}")
    # the prev/next pair belongs to whichever pet is focused
    a(".bzk-nav.bzk-fo{visibility:hidden}")

    if not man:
        return
    for tag in ("L", "P"):
        g = man["bar"][tag]
        rules = []
        for k, lab in enumerate(g["labels"], 1):
            b = lab["box"]
            rules.append(".bzk-hit .k%d{%s}" % (k, _box(b)))
            rules.append(".bzk-hit .k%d > i::before{left:calc(%gpx * var(--bs));"
                         "top:calc(%gpx * var(--bs))}" % (k, -b[0], -b[1]))
        for c in g["index"]:
            b = c["box"]
            rules.append(".bzk-hit .x%d{%s}" % (c["i"] + 1, _box(b)))
            rules.append(".bzk-hit .x%d > i::before{left:calc(%gpx * var(--bs));"
                         "top:calc(%gpx * var(--bs))}" % (c["i"] + 1, -b[0], -b[1]))
        for cls, b in (("bzk-pv", g["prev"]), ("bzk-nx", g["next"])):
            if not b:
                continue
            rules.append(".bzk-hit .%s{%s}" % (cls, _box(b)))
            rules.append(".bzk-hit .%s > i::before{left:calc(%gpx * var(--bs));"
                         "top:calc(%gpx * var(--bs))}" % (cls, -b[0], -b[1]))
        if tag == "L":
            for r in rules:
                a(r)
        else:
            # a finger needs 44 px whatever the plate says, so every target
            # grows a taller strip of its own; the lit clip does not move
            a("@media (max-width:620px){" + "".join(rules)
              + '.bzk-hit a::after{content:"";position:absolute;left:0;right:0;'
                "top:50%;height:44px;transform:translateY(-50%)}}")


def plate_css(pets, man):
    """Which captured plate each pet's wall and bar are, landscape and
    portrait. Every URL here is relative to profile.css."""
    if not man:
        return ""
    out = []
    for tag, pre, post in (("L", "", ""),
                           ("P", "@media (max-width:620px){", "}")):
        L = []
        # the plate's size in CSS pixels, which is what the wall box is set
        # to: the landscape plate was captured at device scale 1 and the
        # portrait one on a phone's frame at device scale 2
        css = (man.get("css") or {}).get(tag)
        if css:
            L.append("#my-overlay{--pw:%dpx;--ph:%dpx}" % (css[0], css[1]))
        for i, p in enumerate(pets, 1):
            e = p.get("plate")
            if not e:
                continue
            L.append(".bzk-wg.n%d > u{background-image:url(%s%s)}"
                     % (i, PLATES, e["poster"][tag]["file"]))
            for m, f in enumerate(e[tag], 1):
                L.append(".bzk-wg.n%d > i:nth-of-type(%d){background-image:url(%s%s)}"
                         % (i, m, PLATES, f["file"]))
            L.append(".bzk-bp.n%d{background-image:url(%sb-%s-ink-%s.webp)}"
                     % (i, PLATES, tag, p["key"]))
            L.append(".bzk-pa.f%d:target ~ .bzk-stage"
                     "{--lit:url(%sb-%s-lit-%s.webp)}" % (i, PLATES, tag, p["key"]))
        if pets and pets[0].get("plate"):
            L.append(".bzk-stage{--lit:url(%sb-%s-lit-%s.webp)}"
                     % (PLATES, tag, pets[0]["key"]))
        out.append(pre + "\n".join(L) + post)
    # A WINDOW BIGGER THAN THE PLATE is the one case the plate has to be
    # scaled for. The morph is unaffected: its mask is measured against the
    # window, not the plate.
    css = (man.get("css") or {}).get("L")
    if css:
        out.append("@media (min-width:%dpx),(min-height:%dpx){"
                   ".bzk-wg > u,.bzk-wg > i{left:0;top:0;margin:0;"
                   "width:100%%;height:100%%;background-size:cover}}"
                   % (css[0] + 1, css[1] + 1))
    return "\n".join(out) + "\n"


def fx_css(a, fx):
    """The two layers that are alive: the grain, and the morph.

    Nothing here is a picture of the room. The grain is a 256 px speck tile
    and the morph is a one-pixel-per-glyph-cell mask; between them they are
    about 150 kB, and both are played by the compositor.
    """
    if not fx:
        return

    # ---- THE GRAIN ---------------------------------------------------------
    # His renderer redraws its grain every frame. Baked into a still it is
    # the same amplitude and reads as half of it, which is most of why our
    # wall looked clean beside his. So the grain is live: one tile, half its
    # specks lighter and half darker (so the wall's own level does not
    # move), laid over the wall on its own layer and JUMPED - by whole
    # pixels, with transform, sixteen times a second. No repaint, no blend
    # mode, no filter. The layer is oversized by the tile so a jump never
    # pulls an edge into view.
    g = fx.get("grain")
    if g:
        t = g["tile"]
        a(".bzk-grain{position:absolute;inset:-%dpx;z-index:2;pointer-events:none;"
          "background-image:url(%s%s);background-repeat:repeat;"
          "will-change:transform;animation:bzk-grain 1s steps(1,end) infinite}"
          % (t, FX, g["file"]))
        n = len(g["offsets"])
        a("@keyframes bzk-grain{"
          + "".join("%.4g%%{transform:translate3d(%dpx,%dpx,0)}"
                    % (100.0 * i / n, -o[0], -o[1])
                    for i, o in enumerate(g["offsets"])) + "}")

    # ---- THE MORPH ---------------------------------------------------------
    # One environment giving way to the next, as a MASK over the incoming
    # wall: one pixel per glyph cell, generated from the glyph pass's own
    # arrival formula (bake-work/fx.py), blown up with image-rendering
    # pixelated so the front is ragged on exact cell boundaries, and
    # stepped. The outgoing wall is whole underneath the whole time.
    m = fx.get("morph")
    if m:
        nf = m["frames"]
        # THE SHEET IS STRETCHED OVER THE WINDOW, one frame to a windowful,
        # so the sweep is the window's whatever its size, and one frame is
        # exactly 100% of the height. That makes the stepping free: a
        # mask-position percentage is measured against (box - image), which
        # here is -(n - 1) windowfuls, so 0% is the first frame and 100% the
        # last, and steps(n, jump-none) lands on every one between.
        #
        # The price is that a mask cell is the window's width over the
        # sheet's columns rather than a flat 7 px, so the sheet comes in
        # three sizes and the width picks the one whose cell lands nearest
        # 7 x 13. Within a band it is off by at most a seventh of a cell.
        a("#my-overlay{--mn:%d;--mdur:%gs;--msz:100%% calc(%d * 100%%)}"
          % (nf, m["dur_s"], nf))
        a("@keyframes bzk-morph{"
          "from{-webkit-mask-position:0 0%;mask-position:0 0%}"
          "to{-webkit-mask-position:0 100%;mask-position:0 100%}}")
        # --wsweep 0: the incoming wall is WHOLE from the first frame and the
        # mask is the only thing uncovering it. Fading it in as well would
        # make everything behind the front half there. The bar plate keeps
        # its own cross-fade - it is not part of the room.
        bands = m["bands"]
        for bi, b in enumerate(bands):
            pre, post = "", ""
            q = []
            if b.get("min"):
                q.append("(min-width:%dpx)" % b["min"])
            if b.get("max"):
                q.append("(max-width:%dpx)" % b["max"])
            if q:
                pre, post = "@media " + " and ".join(q) + "{", "}"
            r = []
            for d, tag in (("a", "f"), ("b", "b")):
                # --odl/--owd: the place being LEFT holds at full until the
                # front has covered the last of it, then goes in a frame or
                # two under cover. Its ordinary fade starts at 0.8 s, which
                # during a morph would thin the half of the screen the
                # front has not reached yet and let the ground through.
                r.append(".bzk-pa.d%s:target ~ .bzk-stage{--mrun:bzk-morph;"
                         "--wsweep:0s;--odl:%gs;--owd:.15s;"
                         "--mf:url(%s%s);--mb:url(%s%s)}"
                         % (d, m["dur_s"], FX, b["front"][tag],
                            FX, b["band"][tag]))
            a(pre + "".join(r) + post)

        # ---- the crossing cells' own lettering ----------------------------
        # A band of churning characters rides the front, in the INCOMING
        # place's ink. Two masks, on two elements rather than one composite,
        # because they step at different rates: the band at the morph's 24
        # frames over 1.4 s, the lettering on the renderer's own 0.085 s
        # churn tick. The tile is jumped by WHOLE CELLS, so every cell on
        # the screen is handed a different character and nothing slides.
        c = fx.get("churn")
        if c:
            a(".bzk-cb{position:absolute;inset:0;"
              "z-index:1;pointer-events:none;opacity:0;image-rendering:pixelated;"
              "overflow:hidden;"
              "-webkit-mask-repeat:no-repeat;mask-repeat:no-repeat;"
              "-webkit-mask-size:var(--msz);mask-size:var(--msz);"
              "-webkit-mask-image:var(--mb);mask-image:var(--mb);"
              "-webkit-mask-position:0 0;mask-position:0 0}")
            a(".bzk-pa.da:target ~ .bzk-stage .bzk-cb,"
              ".bzk-pa.db:target ~ .bzk-stage .bzk-cb"
              "{opacity:.34;animation:bzk-morph var(--mdur) "
              "steps(var(--mn),jump-none) both,bzk-coff var(--mdur) linear both}")
            a("@keyframes bzk-coff{0%,92%{opacity:.34}100%{opacity:0}}")
            cw2, ch2 = c["px"]
            a(".bzk-cg{position:absolute;inset:-%dpx;display:block;"
              "background:var(--ink,#8fa);image-rendering:auto;"
              "-webkit-mask-image:url(%s%s);mask-image:url(%s%s);"
              "-webkit-mask-size:%dpx %dpx;mask-size:%dpx %dpx;"
              "-webkit-mask-repeat:repeat;mask-repeat:repeat;"
              "will-change:transform;"
              "animation:bzk-churn %gs steps(1,end) infinite}"
              % (max(cw2, ch2), FX, c["file"], FX, c["file"],
                 cw2, ch2, cw2, ch2, c["ticks"] * c["tick_s"]))
            j = c["jumps"]
            a("@keyframes bzk-churn{"
              + "".join("%.4g%%{transform:translate3d(%dpx,%dpx,0)}"
                        % (100.0 * i / len(j), -o[0], -o[1])
                        for i, o in enumerate(j)) + "}")


def build_css(pets, man, fx=None):
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
      "--focus:" + FOCUS + ";--gap:" + GAP + ";--step:" + STEP + ";--near:" + NEAR + ";"
      "--cy:44%;--life:" + LIFE + ";"
      "--bw0:847px;--bh0:130px;--bs:1;--bbot:30px;"
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

    # The kind classes carried the whole live scene machinery. One thing is
    # left for them: --envdark, the darkest stop of the room's ramp, which is
    # the scrim a neighbour stands behind so it sinks into the wall.
    a(".bzk-stage.k-%s{--envdark:%s}" % (pets[0]["kind"], ramp(pets[0]["kind"])[0]))
    for i, p in enumerate(pets, 1):
        a(".bzk-pa.f%d:target ~ .bzk-stage{--envdark:%s}" % (i, ramp(p["kind"])[0]))

    # ======================================================================= #
    # THE WALL                                                               #
    # One layer per pet, each holding the three moments captured from the    #
    # renderer. Only opacity animates, on layers that are already their own  #
    # compositor layers, so a frame of this costs no paint at all.           #
    # ======================================================================= #
    a(".bzk-wall{position:absolute;inset:0;z-index:1;overflow:hidden;"
      "animation:bzk-settle 2.4s ease-out calc(var(--boot) + .45s) both}")
    a("@keyframes bzk-settle{0%,14%{opacity:0}100%{opacity:1}}")
    # A PET'S WALL IS A BOX OF THE PLATE'S OWN SIZE, centred, and the wall
    # above crops it: ONE CSS PIXEL TO ONE CAPTURED PIXEL, so the glyph cell
    # on the page is the 7 x 13 the renderer drew and nothing is resampled.
    # Billy's screen is 2543 CSS px wide; a 1920 plate under `cover` was
    # blown up 1.32x there, which is what took the fine texture out of it.
    # --pw and --ph are the capture's own size, from its manifest.
    #
    # The one coming in takes --sweep; the one going out keeps this rule's
    # own transition, which is slower AND delayed, so the two opacities
    # always sum past 1 and the ground is never seen between them. The mask
    # properties are inert until a morph sets --mf (see THE MORPH below).
    # THE GROUP IS THE WINDOW and the plate inside it is the oversized thing
    # being cropped. It was the other way round until the morph went in, and
    # that was wrong: the mask has to span WHAT IS SEEN. A 2560 plate on a
    # 1440 screen is 56% visible, so a front sweeping the plate spent half a
    # second off the right edge, crossed in 450 ms and then sat still for
    # another half second (measured: the front reached the right edge of the
    # screen at 500 ms and the left at 900 ms of 1400). Anchored to the
    # window instead, the sweep is the window's, at every width.
    a(".bzk-wg{position:absolute;inset:0;"
      "opacity:0;transition:opacity .9s linear .8s;image-rendering:pixelated;"
      "-webkit-mask-repeat:no-repeat;mask-repeat:no-repeat;"
      "-webkit-mask-size:var(--msz);mask-size:var(--msz);"
      "-webkit-mask-position:0 0;mask-position:0 0}")
    # the plate itself: the capture's own size, centred, ONE CSS PIXEL TO ONE
    # CAPTURED PIXEL
    a(".bzk-wg > u,.bzk-wg > i{left:50%;top:50%;"
      "width:var(--pw);height:var(--ph);"
      "margin-left:calc(var(--pw) / -2);margin-top:calc(var(--ph) / -2)}")
    # the poster: a quarter-size copy of the same wall, always loaded, so a
    # jump straight across the index strip has something of the right room
    # underneath the plate that is fading in.
    # image-rendering INHERITS, and the mask above is the only thing here
    # that wants nearest neighbour - the plate itself never does.
    a(".bzk-wg > u{position:absolute;display:block;z-index:0;"
      "background-position:center;background-size:100% 100%;"
      "background-repeat:no-repeat;image-rendering:auto}")
    # the three moments, fourteen seconds apart - far enough for the texture
    # schedule to have swapped one of its three layers, so the wall drifts
    a(".bzk-wg > i{position:absolute;display:block;z-index:1;"
      "background-position:center;background-size:100% 100%;"
      "background-repeat:no-repeat;image-rendering:auto;"
      "will-change:opacity;animation:bzk-life var(--life) linear infinite both}")
    a(".bzk-wg > i:nth-of-type(1){animation-delay:0s}")
    a(".bzk-wg > i:nth-of-type(2){animation-delay:calc(-1 * var(--life) / 3)}")
    a(".bzk-wg > i:nth-of-type(3){animation-delay:calc(-2 * var(--life) / 3)}")
    # each moment holds for a third of the turn and hands over across a
    # twelfth of it; the three always sum to one, so the wall never dips
    a("@keyframes bzk-life{0%{opacity:1}25%{opacity:1}33.333%{opacity:0}"
      "91.667%{opacity:0}100%{opacity:1}}")
    fx_css(a, fx)

    # ---- pets --------------------------------------------------------------
    a(".bzk-row{position:absolute;left:0;right:0;top:var(--cy);height:0;z-index:8}")
    a(".bzk-pet{position:absolute;top:calc(-1 * var(--focus) / 2);"
      "width:var(--focus);height:var(--focus);line-height:0;"
      "--s:calc(var(--i) - var(--f));--d:max(var(--s),calc(-1 * var(--s)));"
      # --vis: the first neighbour either side is whole, the second is a
      # little over half there, and the third is gone. Four neighbours in a
      # line all at full strength read as a contact sheet; the row has to
      # fall away from the one in the middle.
      "--on:clamp(0,calc(1 - var(--d)),1);--vis:clamp(0,calc(2.6 - var(--d)),1);"
      "--sg:clamp(-1,var(--s),1);"
      "--x:calc(var(--s) * var(--step) + var(--sg) * var(--gap));"
      "--sz:calc(var(--focus) * (var(--near) + (1 - var(--near)) * var(--on))"
      " * var(--vis));"
      "--pad:calc((var(--focus) - var(--sz)) / 2);--shd:var(--slide);"
      "left:calc(50% + var(--x) - var(--focus) / 2);opacity:var(--vis);"
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
    # (1: there was a glow pool under the pet here. Billy: "there is a border
    #     around my gun now that's weird?" - and the pet had a dark blob behind
    #     it for the same reason. The room's own light on the pet's place is in
    #     the plate now, where the renderer put it, with no edge anywhere.)
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
    # (the numeral over each pet is gone at Billy's word, 2026-11-25: the count
    #  is in the bar and nowhere else.)
    # links: the focused pet's link leads to its page, a neighbour's focuses it
    a(".bzk-pet a{position:absolute;top:var(--pad);left:var(--pad);display:block;"
      "text-decoration:none;outline:none;z-index:6;"
      "transition:top var(--slide) linear,left var(--slide) linear}")
    # A NEIGHBOUR HAS TWO SELECT LINKS, one on top of the other, and only
    # one of them has any size: the one that matches the side this pet is
    # on. Clicking the pet to the right of the middle is moving FORWARD, so
    # it leads to that pet's `a` anchor and the morph sweeps the way the
    # reel did; the pet to the left leads to its `b` anchor. This is the
    # only way the stylesheet can know a direction, since :target says
    # where you are going and never where you came from.
    a(".bzk-pet{--fwd:clamp(0,var(--s),1);--bwd:clamp(0,calc(-1 * var(--s)),1)}")
    a(".bzk-pet .bzk-sf{width:calc(var(--sz) * var(--vis) * var(--fwd));"
      "height:calc(var(--sz) * var(--vis) * var(--fwd))}")
    a(".bzk-pet .bzk-sb{width:calc(var(--sz) * var(--vis) * var(--bwd));"
      "height:calc(var(--sz) * var(--vis) * var(--bwd))}")
    a(".bzk-pet .bzk-go{z-index:7;width:calc(var(--sz) * var(--on));"
      "height:calc(var(--sz) * var(--on));cursor:pointer}")
    a(PIPS % ".bzk-pet a:focus-visible::after")

    # per-pet geometry: the scrim's silhouette, and the sheet's own grid
    for i, p in enumerate(pets, 1):
        sel = ".bzk-row > div:nth-child(" + str(i) + ")"
        a(sel + "::after{-webkit-mask-image:url(" + p["img"] + ");mask-image:url("
          + p["img"] + ")}")
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
    a(".bzk-names{position:absolute;left:0;right:0;"
      "top:calc(var(--cy) + var(--focus) / 2 + 18px);"
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
    # REPLACEABLE SECTION: TERMINAL BAR (now one captured plate)              #
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
    bar_css(a, man)
    # ====================== end REPLACEABLE: TERMINAL BAR ================== #

    # ---- generic per-pet wiring (pre-written for MAXPETS) -------------------
    # EVERY FOCUS ANCHOR CARRIES ITS OWN fN CLASS rather than being told
    # apart by :nth-of-type, because a pet now has three of them: #pN,
    # #pNa and #pNb. All three mean "pet N focused"; the a and b suffixes
    # say which way the reel moved to get here, which is the one thing the
    # stylesheet cannot work out for itself, and all they change is the
    # direction the morph's front sweeps.
    a(".bzk-stage{--f:1}")
    for k in range(1, MAXPETS + 1):
        a(".bzk-pa.f%d:target ~ .bzk-stage{--f:%d}" % (k, k))
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
    # the wall, the bar plate and the lit copy of the bar, per pet. The one
    # coming in uses --sweep; the one going out keeps .bzk-wg's own slower,
    # delayed transition, so the two never both sit at half and let the
    # ground through.
    a(".bzk-stage .bzk-wg.n1,.bzk-stage .bzk-bp.n1"
      "{opacity:1;transition:opacity var(--sweep) linear}")
    # ...and the moment anything IS targeted, that standing rule has to let
    # go, or the first pet's room is painted under every other pet's and the
    # morph uncovers the new place over SKY instead of over the place being
    # left. Reset every wall here, with the slow delayed fade, so the one
    # going out is still whole while the front crosses it; the focused rule
    # below is six classes to this one's four and takes it back.
    a(".bzk-pa:target ~ .bzk-stage .bzk-wg"
      "{opacity:0;transition:opacity var(--owd,.9s) linear var(--odl,.8s)}")
    a(".bzk-pa:target ~ .bzk-stage .bzk-bp"
      "{opacity:0;transition:opacity var(--sweep) linear}")
    for k in range(1, MAXPETS + 1):
        # The wall that is coming in is also the one the morph's mask runs
        # on. --mrun is `none` unless the anchor said a direction, so an
        # index-strip jump - which has no direction to give - is the plain
        # cross-fade and nothing here changes for it.
        a(".bzk-pa.f%d:target ~ .bzk-stage .bzk-wg.n%d"
          "{opacity:1;transition:opacity var(--wsweep,var(--sweep)) linear;"
          "-webkit-mask-image:var(--mf);mask-image:var(--mf);"
          "animation:var(--mrun,none) var(--mdur) steps(var(--mn),jump-none) both}"
          % (k, k))
        a(".bzk-pa.f%d:target ~ .bzk-stage .bzk-bp.n%d"
          "{opacity:1;transition:opacity var(--sweep) linear}" % (k, k))
    # ONLY THE FOCUSED PET'S WALL loads its three full plates. At 2560x1600
    # a plate is half a megabyte on the wire and 16 MB decoded, so three of
    # them for each of two neighbours was 100 MB of a phone's image memory
    # held against a move that might never come. A neighbour keeps its
    # poster, which is a quarter-size copy of the same wall and a few
    # kilobytes, so a jump still lands on the right room and never on bare
    # ground - and the plate it is standing in for arrives behind the
    # morph, under a front that is still crossing.
    a(".bzk-wg > i{display:none}")
    a(".bzk-stage .bzk-wg:nth-child(1) > i{display:block}")
    for k in range(1, MAXPETS + 1):
        a(".bzk-pa.f%d:target ~ .bzk-stage .bzk-wg:nth-child(%d) > i"
          "{display:block}" % (k, k))
    # the incoming place's ink, for the churning characters on the front
    a(".bzk-stage.k-%s{--ink:%s}" % (pets[0]["kind"], ramp(pets[0]["kind"])[2]))
    for i, p in enumerate(pets, 1):
        a(".bzk-pa.f%d:target ~ .bzk-stage{--ink:%s}" % (i, ramp(p["kind"])[2]))
    # the default pet opens once the boot screen has gone
    a(".bzk-stage .bzk-pet:nth-child(1){--shn:bzk-sx,bzk-sy;--hon:bzk-hand;--cln:none;"
      "--shd:calc(var(--boot) + .45s)}")
    # ONLY THE FOCUSED PET LOADS A SHEET. A sheet is 3200x4000 - 1.3 MB on
    # the wire and 51 MB decoded, measured - and only the focused pet ever
    # plays one. Keeping the neighbours' sheets warm cost 100 MB of a
    # phone's image memory and two megabytes of its data to save a third of
    # a second, and the closed frame is already underneath, lit, for exactly
    # this: a sheet that has not arrived shows the pet as coloured ASCII
    # rather than a blank box.
    a(".bzk-stage .bzk-pet:nth-child(1) .bzk-win{display:block}")
    a(".bzk-pa:target ~ .bzk-stage .bzk-fo{visibility:hidden;--run:none;--on2:0}")
    a(".bzk-pa:target ~ .bzk-stage .bzk-ix{--on2:0}")
    a(".bzk-pa:target ~ .bzk-stage .bzk-pet{--shn:none;--hon:none;"
      "--cln:bzk-cx,bzk-cy;--shd:var(--slide)}")
    a(".bzk-pa:target ~ .bzk-stage .bzk-win{display:none}")
    for k in range(1, MAXPETS + 1):
        a(".bzk-pa.f%d:target ~ .bzk-stage .n%d{visibility:visible;"
          "--run:bzk-type;--on2:1}" % (k, k))
        a(".bzk-pa.f%d:target ~ .bzk-stage .bzk-pet:nth-child(%d){"
          "--shn:bzk-sx,bzk-sy;--hon:bzk-hand;--cln:none}" % (k, k))
        a(".bzk-pa.f%d:target ~ .bzk-stage .bzk-pet:nth-child(%d) "
          ".bzk-win{display:block}" % (k, k))

    # ---- phone -------------------------------------------------------------
    # single-size bar text, taller nav and index rows so every target is 44px
    # The focused pet stays 200 px on a phone - the art is 200 px and must
    # not be resampled - so the row loses the gap instead and the
    # neighbours barely peek in from the edges.
    a("@media (max-width:620px){#my-overlay{"
      "--gap:124px;--step:74px;--near:.42;--cy:40%;--bbot:16px;"
      "--bw0:399px;--bh0:130px;--bs:.88}"
      ".bzk-nm b{font-size:21px}.bzk-nm i{width:9px;height:15px}}")
    a("@media (max-width:374px){#my-overlay{--gap:104px;--step:64px;--bs:.78}}")

    # ---- reduced motion ----------------------------------------------------
    a("@media (prefers-reduced-motion:reduce){"
      "#my-overlay *,#my-overlay *::before,#my-overlay *::after{animation:none !important}"
      ".bzk-wall{opacity:1}"
      ".bzk-wg > i:nth-of-type(1){opacity:1}"
      ".bzk-wg > i:nth-of-type(2),.bzk-wg > i:nth-of-type(3){display:none}"
      "#bzk-boot{opacity:0;visibility:hidden}"
      ".bzk-pet{transition:opacity .3s linear,left 0s}"
      ".bzk-pet img,.bzk-pet > u{transition:opacity .3s linear,top 0s,left 0s,width 0s,height 0s}"
      ".bzk-pet a{transition:none}"
      ".bzk-pet::after{transition:none}"
      # no playback: the window never gets layout, so no sheet is even fetched
      ".bzk-pet .bzk-win{display:none !important}"
      ".bzk-pet .bzk-img{opacity:var(--on);animation:none}"
      ".bzk-pet .bzk-closed{opacity:calc(1 - var(--on))}"
      # the grain stays, it simply stops moving; the morph becomes a short
      # cross-fade and its churning band never appears
      ".bzk-wg{-webkit-mask-image:none !important;mask-image:none !important}"
      # the three transition times this file sets anywhere, all brought to
      # the same 0.3 s - the fade in, the ordinary fade out and the hold
      # the morph asks for - so none of them needs !important to win
      "#my-overlay .bzk-stage{--sweep:.3s;--wsweep:.3s;--owd:.3s;--odl:0s}"
      ".bzk-wg{transition:opacity .3s linear}"
      ".bzk-cb{display:none}"
      "}")



    return "\n".join(L) + "\n"


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
    """There are no screen-effect layers any more. The blur, the textures, the
    scanline, the grain, the side glow, the bloom and the vignette are in the
    plates, drawn by the renderer that invented them."""
    return ""


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


def bar_html(pets, man):
    """The bar is ONE PICTURE per pet, drawn by the renderer, with transparent
    links laid over the words it drew. Nothing here is typeset by the browser.
    """
    n = len(pets)
    o = ['<div class="bzk-bar">']
    for i in range(1, n + 1):
        o.append('<u class="bzk-bp n%d"></u>' % i)
    o.append('<div class="bzk-hit">')
    slots = list(SLOTS)
    if USER_ID:
        slots.append(("GALLERY", "%s/gallery/?u=%s" % (PREFIX, USER_ID)))
    for k, (label, href) in enumerate(slots, 1):
        o.append('<a class="bzk-sl k%d" href="%s" title="%s"><i></i></a>'
                 % (k, href, esc(label)))
    for i in range(1, n + 1):
        o.append('<a class="bzk-ix x%d" href="#p%d"><i></i></a>' % (i, i))
    for i in range(1, n + 1):
        pv = n if i == 1 else i - 1
        nx = 1 if i == n else i + 1
        # `<` is moving backward and `>` forward, wrapping at both ends
        o.append('<span class="bzk-nav bzk-fo n%d">'
                 '<a class="bzk-pv" href="#p%db"><i></i></a>'
                 '<a class="bzk-nx" href="#p%da"><i></i></a></span>' % (i, pv, nx))
    o.append("</div></div>")
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
    o = ['<div class="%s">' % cls]
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
             '<a class="bzk-sf" href="#p%da"></a>'
             '<a class="bzk-sb" href="#p%db"></a>'
             '<a class="bzk-go" href="%s"></a></div>'
             % (im, nm, i, i, esc(p["href"])))
    return "".join(o)


def body(pets, man, base=""):
    """The paste body. `base` prefixes the reveal sheets (absolute for the paste,
    relative for the local preview); pet images are always absolute."""
    n = len(pets)
    o = []
    o.append('<div id="my-overlay" style="%s">%s' % (OVERLAY_STYLE, BOOT))
    # THREE ANCHORS PER PET. #pN is pet N with no direction (the index
    # strip, and any link from outside); #pNa is pet N arrived at moving
    # forward and #pNb moving backward. All three say "pet N focused"
    # through their fN class; the da/db classes only turn the morph's
    # front round.
    # An anchor is as short as it can be made: a one-letter tag, the hook
    # class, its pet's fN and, on two of the three, the direction. What
    # room the pet stands in used to be a class here as well, three times
    # per pet; it is a per-pet rule in the stylesheet now, where it costs
    # the paste nothing. At forty pets that is three thousand characters.
    for i, p in enumerate(pets, 1):
        for sfx, dcls in (("", ""), ("a", " da"), ("b", " db")):
            o.append('<i class="bzk-pa f%d%s" id="p%d%s"></i>'
                     % (i, dcls, i, sfx))
    o.append('<div class="bzk-stage k-%s" style="visibility:hidden">'
             % pets[0]["kind"])
    # the wall: one group per pet, a poster and three captured moments,
    # then the band of churning characters that rides the morph's front,
    # then the grain, which is over all of it and under everything else
    o.append('<div class="bzk-wall">')
    for i, p in enumerate(pets, 1):
        o.append('<div class="bzk-wg n%d"><u></u><i></i><i></i><i></i></div>' % i)
    o.append('<i class="bzk-cb"><u class="bzk-cg"></u></i>')
    o.append('<u class="bzk-grain"></u>')
    o.append("</div>")
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
    o.append(bar_html(pets, man))
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
        # THE REVERSE CLOSING SHEET IS NOT SHIPPED. reveal.py still makes one
        # and it is still correct; the page does not use it. It is close to a
        # megabyte per pet, it only ever plays while a pet is LEAVING the
        # middle - behind the pet coming in, over a wall that is already
        # crossfading - and nobody has ever watched it. The closed frame
        # takes the pet back on its own.
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
    man = read_plates(warn)
    attach_plates(pets, man, warn)
    fx = read_fx(warn)
    for w in warn:
        print("WARNING:", w)

    css = build_css(pets, man, fx) + plate_css(pets, man)

    base = cdn_base()
    paste = '<link rel="stylesheet" href="%s">\n%s\n' % (cdn_css(), body(pets, man, base))

    for name, text in (("profile.css", css), ("profile.html", paste),
                       ("preview.html", preview(body(pets, man, "")))):
        with open(os.path.join(HERE, name), "w", encoding="utf-8") as f:
            f.write(text)

    chars = len(paste)
    # what one more pet costs the paste: its own markup plus its anchor, name
    # line, readout line, index cell and prev/next pair
    each = (len(body(pets, man, base))
            - (len(body(pets[:-1], man, base)) if len(pets) > 1 else 0))
    art = len(pet_html(len(pets), pets[-1], base))
    kinds = []
    for p in pets:
        if p["kind"] not in kinds:
            kinds.append(p["kind"])
    plbytes = 0
    if man:
        for p in pets:
            e = p.get("plate") or {}
            for tag in ("L", "P"):
                plbytes += sum(x["bytes"] for x in e.get(tag, []))
                plbytes += (e.get("poster", {}).get(tag) or {}).get("bytes", 0)
    print("pets: %d  kinds in use: %s" % (len(pets), ", ".join(kinds)))
    print("plates       : %.2f MB of wall, %s"
          % (plbytes / 1e6, "bar from the manifest" if man else "NONE"))
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
