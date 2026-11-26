#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
blizzicky LITE - Rescreatu profile builder, built to a weight budget.

The heavy build looked right and was slow on a Mac and on a strong PC. This
one puts weight and draw cost first:

  * SIX ROOM LOOPS, ONE PER ENVIRONMENT KIND, not one per pet. A pet's
    `kind` in pets.txt picks its room, so twenty pets still ship six videos.
  * EACH LOOP IS A TRUE PING-PONG: frames 0..N-1 then N-2..1, so the wrap
    pair is one source step like every other pair. No crossfade, no cut.
    rooms/rooms.json carries the measured proof.
  * ONE WALL LAYER. No stills-per-pet, no grain, no churn band, no mask
    sprite. A room change is a 0.5 s opacity crossfade, and a room that is
    neither current nor fading out is display:none, which is the only thing
    that stops a video decoding.
  * COMPOSITOR-ONLY MOTION. The row, the pets and the reveal move with
    transform and opacity. Nothing animates left/top/width/height, there is
    no backdrop-filter, no blend mode, no CSS mask, and no animated filter.
  * NOTHING IS PRELOADED WHOLESALE. A pet's reveal sheet and bar plates are
    fetched while it is the focused pet's NEIGHBOUR, off a 1 px element, so
    they are already there when the move happens.

    python3 build.py [--pets FILE] [--ref REF]

No JavaScript is emitted anywhere. profile.html is the text to paste into
the Rescreatu profile box; profile.css is served beside it from the CDN and
all of its own URLs are relative. preview.html is the same page with every
URL local, for `python3 -m http.server` in this folder.
"""

import colorsys
import json
import math
import os
import re
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))

CDN_REPO = "https://cdn.jsdelivr.net/gh/BillyTheArtist/rescreatu-images"
CDN_DIR = "/blizzicky/"
REF = "main"                      # --ref REF

USER_ID = None                    # Rescreatu user id for the GALLERY slot

MAXPETS = 40
KINDS = ["sky", "water", "cathedral", "garden", "void", "geode"]
LABEL = {"sky": "OPEN SKY", "water": "DEEP WATER", "cathedral": "RELIQUARY",
         "garden": "OVERGROWTH", "void": "THE VOID", "geode": "GEODE"}

# Night palette. Nothing else in the file invents a colour.
INK = "#05070a"
TEXT = "#c6d6d0"
DIM = "#677c78"
LINE = "#28373c"
TINT = "#9fd8bd"
ACCENT = "#ff4b2b"

# ---- the row, as three numbers and nothing else --------------------------
FOCUS = 200       # the focused pet, and the pet art's own size: never upscaled
GAP = 200         # clear wall either side of the focused pet
STEP = 150        # distance between neighbours beyond that
NEAR = 0.58       # a neighbour's scale
WINDOW = 3        # pets this far from the focus are drawn; the rest are none

SLIDE = ".34s"    # the row's move
FADE = ".5s"      # the room crossfade
BOOT = ".18s"     # LOADING hold before its .3s fade

TYPE_CPS = 34.0
MAX_NAME = 20

# the kind's four-stop ramp, as in the heavy build (hue, lightness, sat mult)
ENV = {
    "sky":       (0.58, [0.46, 0.58, 0.76, 0.98], 0.80),
    "water":     (0.50, [0.30, 0.46, 0.70, 0.96], 1.15),
    "cathedral": (0.09, [0.34, 0.52, 0.74, 0.99], 0.85),
    "garden":    (0.29, [0.30, 0.48, 0.70, 0.95], 1.10),
    "void":      (0.72, [0.26, 0.44, 0.68, 1.00], 0.95),
    "geode":     (0.85, [0.30, 0.50, 0.76, 1.00], 1.20),
}

PREFIX = "https://www.rescreatu.com"
SLOTS = [("HATCHERY", PREFIX + "/hatchery/"),
         ("INVENTORY", PREFIX + "/inventory/"),
         ("EXPLORE", PREFIX + "/explore/"),
         ("SHOPS", PREFIX + "/shops/"),
         ("MERCHANT", PREFIX + "/usershops/"),
         ("RANCHER", PREFIX + "/ranchershop/")]

# ---- the reveal sheet, re-rendered small --------------------------------- #
# MEASURED, 2026-11-25: at the heavy build's 64 frames / scale 1 a sheet is
# 511 KB and the worst of the six 832 KB, and one sheet is what a move
# fetches. 16 frames at scale 1 with a lossy alpha channel lands every one
# of the six at or under 120 KB, which is what keeps a move under 150 KB.
# The frame itself stays 200 px: the pet art is 200 px and resampling it is
# the one thing this build will not do.
RV_FRAMES = 16
RV_FPS = 12.0
RV_QUALITY = 32
RV_ALPHA_Q = 20

ROOM_DIR = "rooms/"
PLATE_DIR = "plates/"
REVEAL_DIR = "reveal/"


def cdn_base(ref=None):
    return CDN_REPO + "@" + (ref or REF) + CDN_DIR


# --------------------------------------------------------------------------- #
# pets.txt                                                                    #
# --------------------------------------------------------------------------- #

def read_pets(path):
    """placeholder | name | image url | kind | light

    The lite build is placeholder-only on purpose: the heavy build's live
    petpage fetch and its image-measured `kind` are unchanged in
    ../blizzicky/build.py and nothing here needs the network.
    """
    pets, warn = [], []
    for raw in open(path, encoding="utf-8"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not line.lower().startswith("placeholder"):
            raise ValueError("lite build takes placeholder lines only: " + line)
        bits = [b.strip() for b in line.split("|")]
        if len(bits) < 5:
            raise ValueError("bad placeholder line: " + line)
        _, name, img, kind, light = bits[:5]
        pets.append({"name": name, "img": img, "kind": kind, "light": light})
    for i, p in enumerate(pets, 1):
        if p["kind"] not in KINDS:
            raise ValueError("unknown kind %r" % p["kind"])
        if p["light"] not in ("left", "right"):
            raise ValueError("light must be left or right")
        p["href"] = "#p%d" % i
        p["key"] = reveal_key(p["img"])
        p["rel"] = local_rel(p["img"])
    return pets, warn


def reveal_key(url):
    """pets/7/ginger/effect-derp-baby.png -> 7-ginger-effect-derp-baby"""
    parts = [q for q in urllib.parse.urlsplit(url).path.split("/") if q]
    if "pets" in parts:
        parts = parts[parts.index("pets") + 1:]
    if parts:
        parts[-1] = parts[-1].rsplit(".", 1)[0]
    return "-".join(parts)


def local_rel(url):
    """Where preview.html finds its own copy of a pet image."""
    path = urllib.parse.urlsplit(url).path.lstrip("/")
    return "pets/" + path.split("pets/", 1)[-1] if "pets/" in path else "pets/" + path


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


def hue_sat(hexcol):
    v = hexcol.lstrip("#")
    r, g, b = (int(v[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    h, l, sat = colorsys.rgb_to_hls(r, g, b)
    return h * 360.0, sat, l


# grayscale(1) sepia(1) sends (v,v,v) to hue 38.5 at HLS saturation 0.242,
# so the chain only has to turn the wheel by the difference and multiply up.
SEPIA_HUE, SEPIA_SAT = 38.5, 0.242


def tint(kind, sat_cap=0.45):
    """KEPT FROM THE HEAVY BUILD, and measured to cost nothing here: it is a
    static filter on a 200 px layer, it never animates, and it is not on
    anything larger than a pet. Rotation in degrees and the saturate
    multiplier; brightness and contrast are flat in the rule."""
    h, sat, _ = hue_sat(ramp(kind)[1])
    return round(h - SEPIA_HUE, 1), round(min(sat, sat_cap) / SEPIA_SAT, 3)


# --------------------------------------------------------------------------- #
# the plates manifest (bar geometry and the per-pet bar pictures)             #
# --------------------------------------------------------------------------- #

def read_plates(warn):
    path = os.path.join(HERE, PLATE_DIR, "manifest.json")
    if not os.path.isfile(path):
        warn.append("no plates/manifest.json - the bar ships without a plate")
        return {}
    man = json.load(open(path, encoding="utf-8"))
    return man


def attach_plates(pets, man, warn):
    """Each pet's own bar picture, plain and lit, landscape and portrait.
    The files are the heavy build's: one captured plate per pet state."""
    for p in pets:
        p["bar"] = {}
        for tag in ("L", "P"):
            got = {}
            for state in ("ink", "lit"):
                f = "b-%s-%s-%s.webp" % (tag, state, p["key"])
                if os.path.isfile(os.path.join(HERE, PLATE_DIR, f)):
                    got[state] = PLATE_DIR + f
            if len(got) == 2:
                p["bar"][tag] = got
        if not p["bar"]:
            warn.append("no bar plate for %s" % p["key"])


# --------------------------------------------------------------------------- #
# the rooms                                                                   #
# --------------------------------------------------------------------------- #

def read_rooms(warn):
    """One loop and one poster per kind, with the loop proof that made them."""
    path = os.path.join(HERE, ROOM_DIR, "rooms.json")
    man = {}
    if os.path.isfile(path):
        man = json.load(open(path, encoding="utf-8"))
    out = {}
    for k in KINDS:
        mp4, pos = "room-%s.mp4" % k, "poster-%s.webp" % k
        if not os.path.isfile(os.path.join(HERE, ROOM_DIR, mp4)):
            warn.append("no loop for the %s room" % k)
            continue
        e = dict(man.get(k) or {})
        e["mp4"] = ROOM_DIR + mp4
        e["poster"] = ROOM_DIR + pos if os.path.isfile(
            os.path.join(HERE, ROOM_DIR, pos)) else None
        e["mp4_bytes"] = os.path.getsize(os.path.join(HERE, ROOM_DIR, mp4))
        if e["poster"]:
            e["poster_bytes"] = os.path.getsize(os.path.join(HERE, e["poster"]))
        if e.get("loop") not in ("OK", "SEAM"):
            warn.append("the %s loop has no ping-pong proof in rooms.json" % k)
        out[k] = e
    return out


def room_order(pets, rooms):
    """The first pet's room first in the DOM, then the rest in KINDS order."""
    first = pets[0]["kind"]
    return ([first] if first in rooms else []) + \
           [k for k in KINDS if k in rooms and k != first]


# --------------------------------------------------------------------------- #
# the reveal sheets                                                           #
# --------------------------------------------------------------------------- #

def squeeze(path, quality, alpha_q):
    """Re-encode a sheet with a LOSSY ALPHA CHANNEL.

    MEASURED: the quality dial alone saturates - at scale 1, 24 frames, the
    worst of the six is 237 KB at q25 and still 181 KB at q8 - because
    reveal.py's save leaves the alpha channel lossless and half the sheet is
    transparent. alpha_quality is the dial that actually moves it.
    """
    try:
        from PIL import Image
    except Exception:
        return os.path.getsize(path)
    im = Image.open(path).convert("RGBA")
    im.save(path, "WEBP", quality=quality, alpha_quality=alpha_q, method=6)
    return os.path.getsize(path)


def attach_reveal(pets, warn):
    mod = None
    if os.environ.get("BZK_SKIP_RENDER"):
        pass
    elif os.path.isfile(os.path.join(HERE, "reveal.py")):
        bc = sys.dont_write_bytecode
        try:
            sys.dont_write_bytecode = True
            if HERE not in sys.path:
                sys.path.insert(0, HERE)
            import reveal as mod
        except BaseException as exc:
            warn.append("reveal.py did not import (%s: %s)" % (type(exc).__name__, exc))
            mod = None
        finally:
            sys.dont_write_bytecode = bc
    else:
        warn.append("reveal.py is not there")

    rdir = os.path.join(HERE, REVEAL_DIR)
    mp = os.path.join(rdir, "manifest.json")
    man = {}
    if os.path.isfile(mp):
        m = json.load(open(mp, encoding="utf-8"))
        man = dict(m.get("keys", m))
    done = set()
    for p in pets:
        if mod is None or p["key"] in done:
            continue
        done.add(p["key"])
        sheet = os.path.join(rdir, p["key"] + ".webp")
        if os.path.exists(sheet) and os.environ.get("BZK_KEEP_SHEETS"):
            continue
        try:
            # reveal.render() returns the manifest entry and writes no
            # manifest of its own (render_all does, and it ships the heavy
            # build's 64-frame defaults), so the entry is kept here.
            e = mod.render(p["img"], rdir, frames=RV_FRAMES, scale=1,
                           quality=RV_QUALITY, close=False,
                           run=RV_FRAMES / RV_FPS, force=True)
            e["sheet_bytes"] = squeeze(sheet, RV_QUALITY, RV_ALPHA_Q)
            man[p["key"]] = e
        except BaseException as exc:
            warn.append("reveal.render failed for %s (%s)" % (p["key"], exc))
    if man:
        os.makedirs(rdir, exist_ok=True)
        json.dump({"keys": man}, open(mp, "w", encoding="utf-8"), indent=1,
                  sort_keys=True)
    for p in pets:
        p["rev"] = None
        e = man.get(p["key"])
        sheet, closed = p["key"] + ".webp", p["key"] + "-closed.webp"
        have = all(os.path.isfile(os.path.join(rdir, f)) for f in (sheet, closed))
        if not isinstance(e, dict) or not have:
            warn.append("no sheet for %s - it falls back to closed frame + image"
                        % p["key"])
            continue
        cols = int(e.get("cols") or 8)
        frames = int(e.get("frames") or 0)
        rows = int(e.get("rows") or 0) or (frames // cols if cols else 0)
        dur = float(e.get("duration_s") or 0)
        if cols < 1 or rows < 1 or dur <= 0:
            warn.append("manifest entry for %s is incomplete" % p["key"])
            continue
        p["rev"] = {"cols": cols, "rows": rows, "frames": frames or cols * rows,
                    "duration_s": dur,
                    "sheet": REVEAL_DIR + sheet, "closed": REVEAL_DIR + closed,
                    "sheet_bytes": os.path.getsize(os.path.join(rdir, sheet)),
                    "closed_bytes": os.path.getsize(os.path.join(rdir, closed))}


# --------------------------------------------------------------------------- #
# the stylesheet                                                              #
# --------------------------------------------------------------------------- #

PIPS = ('%s{content:"";position:absolute;inset:-6px;'
        "background-image:linear-gradient(var(--tint),var(--tint)),"
        "linear-gradient(var(--tint),var(--tint)),"
        "linear-gradient(var(--tint),var(--tint)),"
        "linear-gradient(var(--tint),var(--tint));"
        "background-size:5px 5px;background-repeat:no-repeat;"
        "background-position:left top,right top,left bottom,right bottom}")


def dist(i, f, n):
    """Signed CIRCULAR distance from the focus, so the row wraps the way the
    prev/next links do: at twenty pets, pet 20 stands to the left of pet 1."""
    d = i - f
    if d > n // 2:
        d -= n
    if d < -(n // 2):
        d += n
    return d


def _box(b, px, extra=""):
    """One of the bar's own rectangles, AS A FRACTION OF THE BAR. In pixels
    these only ever lined up at the widths where the plate and the window
    happened to agree; a percentage tracks the drawing at every width."""
    w, h = px
    return ("left:%.4f%%;top:%.4f%%;width:%.4f%%;height:%.4f%%;%s"
            % (100.0 * b[0] / w, 100.0 * b[1] / h,
               100.0 * b[2] / w, 100.0 * b[3] / h, extra))


def _clip(b, px):
    """The lit copy of the WHOLE plate inside one hit rectangle, in
    percentages of that rectangle, so it scales with it."""
    w, h = px
    return ("width:%.4f%%;height:%.4f%%;left:%.4f%%;top:%.4f%%"
            % (100.0 * w / b[2], 100.0 * h / b[3],
               -100.0 * b[0] / b[2], -100.0 * b[1] / b[3]))


def bar_css(a, man):
    a(".bzk-bar{position:absolute;left:50%;bottom:var(--bbot);z-index:10;"
      "width:min(calc(var(--bw0) * var(--bs)),calc(100vw - 2 * var(--bpad)));"
      "aspect-ratio:var(--bar);height:auto;transform:translateX(-50%)}")
    # ONE PLATE AT A TIME, AND display:none FOR THE REST. opacity:0 still
    # decodes; display:none does not, and the neighbours' plates are already
    # in the cache because the prefetch element asked for them.
    a(".bzk-bp{position:absolute;inset:0;display:none;"
      "background-size:100% 100%;background-repeat:no-repeat}")
    a(".bzk-hit{position:absolute;inset:0}")
    a(".bzk-hit a{position:absolute;display:block;text-decoration:none;outline:none}")
    a(".bzk-hit a > i{position:absolute;inset:0;display:block;overflow:hidden}")
    # The link's name, and nothing a sighted reader ever meets. It cannot be
    # a title attribute: the host's filter cuts the word in half and leaves
    # the tail showing over the bar.
    a(".bzk-hit a > i::after{content:var(--nm,\"\");position:absolute;left:0;top:0;"
      "font-size:0;color:transparent;white-space:nowrap}")
    a('.bzk-hit a > i::before{content:"";position:absolute;display:block;'
      "background-image:var(--lit);background-size:100% 100%;"
      "background-repeat:no-repeat;opacity:0;transition:opacity .12s linear}")
    a(".bzk-hit a:hover > i::before,.bzk-hit a:active > i::before,"
      ".bzk-hit a:focus-visible > i::before{opacity:1}")
    a(PIPS % ".bzk-hit a:focus-visible::before")
    a(".bzk-nav{display:contents}")
    a(".bzk-nav.bzk-fo{visibility:hidden}")
    if not man:
        return
    for tag in ("L", "P"):
        g = man["bar"][tag]
        px = g["px"]
        rules = ["#my-overlay{--bar:%g / %g;--bw0:%gpx;--bh0:%gpx}"
                 % (px[0], px[1], px[0], px[1])]
        for k, lab in enumerate(g["labels"], 1):
            b = lab["box"]
            rules.append(".bzk-hit .k%d{%s;--nm:\"%s\"}"
                         % (k, _box(b, px), lab["t"].replace('"', "")))
            rules.append(".bzk-hit .k%d > i::before{%s}" % (k, _clip(b, px)))
        for c in g["index"]:
            b = c["box"]
            rules.append(".bzk-hit .x%d{%s;--nm:\"%d\"}"
                         % (c["i"] + 1, _box(b, px), c["i"] + 1))
            rules.append(".bzk-hit .x%d > i::before{%s}" % (c["i"] + 1, _clip(b, px)))
        for cls, b, nm in (("bzk-pv", g["prev"], "Previous"),
                           ("bzk-nx", g["next"], "Next")):
            if not b:
                continue
            rules.append(".bzk-hit .%s{%s;--nm:\"%s\"}" % (cls, _box(b, px), nm))
            rules.append(".bzk-hit .%s > i::before{%s}" % (cls, _clip(b, px)))
        if tag == "L":
            for r in rules:
                a(r)
        else:
            # NO 44 PX FINGER STRIP. The portrait plate stacks its rows 26
            # plate pixels apart - about 22 on a 375 screen - so a 44 px
            # strip spanned two rows and the row drawn later took the tap.
            # Each target is exactly the word the renderer drew.
            a("@media (max-width:620px){" + "".join(rules) + "}")


def build_css(pets, man, rooms, order):
    L = []
    a = L.append
    n = len(pets)

    for w, f in ((400, "ibm-plex-mono-latin-400-normal"),
                 (500, "ibm-plex-mono-latin-500-normal")):
        a('@font-face{font-family:"BZKMono";font-weight:%d;font-style:normal;'
          'font-display:swap;src:url("fonts/%s.woff2") format("woff2")}' % (w, f))
    a('@font-face{font-family:"BZKBlack";font-weight:700;font-style:normal;'
      'font-display:swap;src:url("fonts/unifrakturcook-latin-700-normal.woff2") '
      'format("woff2")}')

    a("#my-overlay{--ink:%s;--text:%s;--dim:%s;--line:%s;--tint:%s;--accent:%s;"
      "--mono:\"BZKMono\",ui-monospace,monospace;--black:\"BZKBlack\",serif;"
      "--focus:%dpx;--sz:%dpx;--gap:%dpx;--step:%dpx;--near:%.3f;--cy:44%%;"
      "--slide:%s;--fade:%s;--boot:%s;--bbot:26px;--bpad:16px;--bs:.78;"
      "position:fixed;top:0;left:0;width:100vw;height:100vh;"
      "z-index:999999999;background:var(--ink);overflow:hidden;"
      "font-family:var(--mono);color:var(--text);"
      "-webkit-font-smoothing:antialiased}"
      % (INK, TEXT, DIM, LINE, TINT, ACCENT, FOCUS, FOCUS, GAP, STEP, NEAR,
         SLIDE, FADE, BOOT))
    a("#my-overlay *{box-sizing:border-box;margin:0;padding:0;border:0}")
    # the LOADING gate the inline style put up, taken down from here
    a("#bzk-boot{z-index:60;animation:bzk-off .3s linear var(--boot) forwards}")
    a("@keyframes bzk-off{to{opacity:0;visibility:hidden}}")
    a(".bzk-stage{position:absolute;inset:0}")
    a(".bzk-pa{position:absolute;width:0;height:0}")

    # ---- the wall: six loops, one per kind ---------------------------------
    a(".bzk-wall{position:absolute;inset:0;z-index:0}")
    # A ROOM THAT IS NEITHER CURRENT NOR FADING OUT IS display:none.
    # MEASURED: opacity:0 does NOT stop a video decoding and display:none
    # does, which is the whole reason there are six videos and not one per
    # pet - every hidden autoplay video still downloads, whatever is done
    # to it. transition-behavior:allow-discrete keeps the outgoing room
    # displayed for the whole 0.5 s fade, and @starting-style gives the
    # incoming one an opacity to come from.
    a(".bzk-rm{position:absolute;inset:0;display:none;opacity:0;"
      "background-position:center center;background-size:cover;"
      "background-repeat:no-repeat;background-color:var(--ink);"
      "transition:opacity var(--fade) linear,display var(--fade);"
      "transition-behavior:allow-discrete}")
    # THE ID IS LOAD-BEARING. The inline gate in the paste is
    # `.bzk-wall>div>video{display:none}` at (0,1,2) and it beats a plain
    # `.bzk-rm > video` at (0,1,1) whatever the source order, so every
    # room was its poster and no video was ever on screen. MEASURED and
    # fixed, 2026-11-25. #my-overlay puts this at (1,1,1).
    a("#my-overlay .bzk-rm > video{position:absolute;inset:0;display:block;"
      "width:100%;height:100%;object-fit:cover;object-position:center center}")
    for k in order:
        e = rooms[k]
        if e.get("poster"):
            a(".bzk-rm.m-%s{background-image:url(%s)}" % (k, e["poster"]))

    def roomrule(sel, kind):
        a("%s .bzk-rm.m-%s{display:block;opacity:1}" % (sel, kind))
        a("@starting-style{%s .bzk-rm.m-%s{opacity:0}}" % (sel, kind))

    roomrule(".bzk-stage", pets[0]["kind"])
    a(".bzk-pa:target ~ .bzk-stage .bzk-rm{display:none;opacity:0}")
    seen = set()
    for i, p in enumerate(pets, 1):
        roomrule(".bzk-pa.f%d:target ~ .bzk-stage" % i, p["kind"])
        seen.add(p["kind"])

    # ---- the row ----------------------------------------------------------
    a(".bzk-row{position:absolute;left:50%;top:var(--cy);width:0;height:0;"
      "z-index:5}")
    # EVERYTHING HERE MOVES ON transform AND opacity. The heavy build
    # animated left/top/width/height on every pet, every move; this one
    # never changes a box, so a move is a compositor job.
    a(".bzk-pet{position:absolute;left:calc(var(--sz) / -2);"
      "top:calc(var(--sz) / -2);width:var(--sz);height:var(--sz);display:none;"
      "transform:translateX(var(--x,0px)) scale(var(--s,1));"
      "opacity:var(--o,1);will-change:transform;"
      "transition:transform var(--slide) cubic-bezier(.3,.7,.2,1),"
      "opacity var(--slide) linear}")
    a(".bzk-pet > u,.bzk-pet > span,.bzk-pet > img{position:absolute;"
      "top:0;left:0;width:var(--sz);height:var(--sz);display:block}")
    a(".bzk-closed{z-index:2;background-size:100% 100%;"
      "background-repeat:no-repeat;image-rendering:pixelated}")
    # the reveal: two nested transform steppers, rows outside, columns in
    a(".bzk-win{z-index:3;overflow:hidden;display:none}")
    a(".bzk-sr{position:absolute;top:0;left:0;width:var(--sz);"
      "height:calc(var(--sz) * var(--rr,1));"
      "animation-name:var(--rvy,none);animation-fill-mode:forwards;"
      "animation-delay:var(--rvd,0s)}")
    a(".bzk-sh{position:absolute;top:0;left:0;"
      "width:calc(var(--sz) * var(--rc,1));"
      "height:calc(var(--sz) * var(--rr,1));"
      "background-size:100% 100%;background-repeat:no-repeat;"
      "animation-name:var(--rvx,none);animation-fill-mode:forwards;"
      "animation-delay:var(--rvd,0s)}")
    a("@keyframes bzk-rvx{from{transform:translateX(0)}"
      "to{transform:translateX(var(--rxe))}}")
    a("@keyframes bzk-rvy{from{transform:translateY(0)}"
      "to{transform:translateY(var(--rye))}}")
    # the site's own PNG takes over when the reveal finishes
    a(".bzk-img{z-index:4;opacity:1;animation-name:var(--hon,none);"
      "animation-duration:.15s;animation-timing-function:linear;"
      "animation-delay:calc(var(--rvd,0s) + var(--T,0s));"
      "animation-fill-mode:forwards}")
    a("@keyframes bzk-hand{from{opacity:0}to{opacity:1}}")
    a(".bzk-pet a{position:absolute;top:0;left:0;width:var(--sz);"
      "height:var(--sz);z-index:6;display:block;cursor:pointer}")
    a(PIPS % ".bzk-pet a:focus-visible::after")

    # ======================================================================= #
    # REPLACEABLE SECTION: AN UNFOCUSED PET'S COLOUR                          #
    # Billy: "the non focused pets ... desaturated as well in the color of    #
    # the current environment treatment". The pet's own layer is taken to     #
    # grey and put back into ONE colour by a filter chain, and the colour is  #
    # the ROOM's - --trot and --tsat come from whichever pet is focused, so a #
    # neighbour wears the wall it is standing on and not its own. The chain   #
    # never animates and is never on anything bigger than a 200 px pet.       #
    # ======================================================================= #
    a(".bzk-pet{--tf:grayscale(1) sepia(1) saturate(var(--tsat,1)) "
      "hue-rotate(calc(var(--trot,0) * 1deg)) brightness(.82) contrast(.88);"
      "--flt:var(--tf)}")
    a(".bzk-pet > img,.bzk-pet > u{filter:var(--flt)}")
    # ============ end REPLACEABLE: AN UNFOCUSED PET'S COLOUR =============== #

    # ======================================================================= #
    # REPLACEABLE SECTION: THE NAME                                           #
    # One element per pet carrying "bzk-fo nI", --run set to a name while     #
    # focused and none otherwise, so a move restarts the typing. Nothing      #
    # else in the file refers to .bzk-names, .bzk-nm or bzk-type.             #
    # ======================================================================= #
    a(".bzk-names{position:absolute;left:0;right:0;"
      "top:calc(var(--cy) + var(--focus) / 2 + 18px);height:34px;z-index:9;"
      "text-align:center}")
    a(".bzk-nm{position:absolute;left:0;right:0;top:0;white-space:nowrap}")
    a(".bzk-nm b{font-family:var(--black);font-weight:700;font-size:27px;"
      "line-height:1.2;color:var(--text);letter-spacing:.02em;"
      "text-shadow:0 0 10px rgba(0,0,0,.85);"
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
    # ========================= end REPLACEABLE: THE NAME =================== #

    bar_css(a, man)

    # ---- the prefetch element --------------------------------------------- #
    # A 1 px element whose background is the NEIGHBOURS' sheet, bar plates
    # and room poster. It has a box, so the browser fetches them; by the
    # time the move happens they are in the cache, which is why no move
    # fetches more than one file and the bar never flashes.
    a(".bzk-pf{position:absolute;left:0;top:0;width:1px;height:1px;"
      "opacity:0;z-index:0;pointer-events:none;background-repeat:no-repeat;"
      "background-size:1px 1px}")

    # ---- per-pet wiring ---------------------------------------------------- #
    for i, p in enumerate(pets, 1):
        sel = ".bzk-pet:nth-child(%d)" % i
        r = p.get("rev")
        if r:
            a("%s > u{background-image:url(%s)}" % (sel, r["closed"]))
            a("%s .bzk-sh{background-image:url(%s)}" % (sel, r["sheet"]))
            a("%s{--rc:%d;--rr:%d;--T:%.3fs;"
              "--rxe:calc(var(--sz) * %d * -1);--rye:calc(var(--sz) * %d * -1)}"
              % (sel, r["cols"], r["rows"], r["duration_s"],
                 r["cols"] - 1, r["rows"] - 1))
            a("%s .bzk-sh{animation-duration:calc(var(--T) / %d);"
              "animation-timing-function:steps(%d,jump-none);"
              "animation-iteration-count:%d}"
              % (sel, r["rows"], r["cols"], r["rows"]))
            a("%s .bzk-sr{animation-duration:var(--T);"
              "animation-timing-function:steps(%d,jump-none);"
              "animation-iteration-count:1}" % (sel, r["rows"]))
        else:
            # no sheet: the pet simply appears - closed frame, then the image
            a("%s{--T:0s}" % sel)
        if p["bar"].get("L"):
            a(".bzk-bp.n%d{background-image:url(%s)}" % (i, p["bar"]["L"]["ink"]))
        if p["bar"].get("P"):
            a("@media (max-width:620px){.bzk-bp.n%d{background-image:url(%s)}}"
              % (i, p["bar"]["P"]["ink"]))

    # the focused pet's lit plate, for the hover copy
    if pets[0]["bar"].get("L"):
        a(".bzk-stage{--lit:url(%s)}" % pets[0]["bar"]["L"]["lit"])
    for i, p in enumerate(pets, 1):
        if p["bar"].get("L"):
            a(".bzk-pa.f%d:target ~ .bzk-stage{--lit:url(%s)}"
              % (i, p["bar"]["L"]["lit"]))
        if p["bar"].get("P"):
            a("@media (max-width:620px){.bzk-pa.f%d:target ~ .bzk-stage"
              "{--lit:url(%s)}}" % (i, p["bar"]["P"]["lit"]))
    if pets[0]["bar"].get("P"):
        a("@media (max-width:620px){.bzk-stage{--lit:url(%s)}}"
          % pets[0]["bar"]["P"]["lit"])

    # ---- the state machine ------------------------------------------------- #
    # THREE ANCHORS PER PET. #pN is pet N with no direction (the index strip,
    # and any link from outside); #pNa is pet N arrived at moving forward and
    # #pNb moving backward. All three say "pet N focused" through fN.
    def focusrules(sel, f):
        """Everything that is true while pet f is the focused pet."""
        # THE WINDOW IS CLAMPED TO HALF THE ROW. At six pets an unclamped
        # window of three puts the pet three places forward and the pet
        # three places back on the SAME pet, and the row comes out two
        # left of centre and three right. floor((n-1)/2) is the widest
        # window in which every pet has one place of its own.
        w = min(WINDOW, (n - 1) // 2)
        for i in range(1, n + 1):
            d = dist(i, f, n)
            if abs(d) > w:
                continue
            if d == 0:
                x, sc = 0, 1.0
            else:
                x = (1 if d > 0 else -1) * (GAP + (abs(d) - 1) * STEP)
                sc = NEAR
            a("%s .bzk-pet:nth-child(%d){display:block;--x:%dpx;--s:%.3f}"
              % (sel, i, x, sc))
        a("%s .bzk-pet:nth-child(%d){--flt:none;--hon:bzk-hand;"
          "--rvx:bzk-rvx;--rvy:bzk-rvy}" % (sel, f))
        a("%s .bzk-pet:nth-child(%d) > img{opacity:0}" % (sel, f))
        a("%s .bzk-pet:nth-child(%d) .bzk-win{display:block}" % (sel, f))
        a("%s .n%d{visibility:visible;--run:bzk-type}" % (sel, f))
        a("%s .bzk-bp.n%d{display:block}" % (sel, f))
        trot, tsat = tint(pets[f - 1]["kind"])
        a("%s{--trot:%s;--tsat:%s}" % (sel, trot, tsat))
        # the prefetch list for this state: both neighbours' sheets, bar
        # plates and room poster
        pv, nx = ((f - 2) % n) + 1, (f % n) + 1
        urls = []
        for q in (pets[pv - 1], pets[nx - 1]):
            if q.get("rev"):
                urls.append(q["rev"]["sheet"])
            for tag in ("L",):
                if q["bar"].get(tag):
                    urls.append(q["bar"][tag]["ink"])
                    urls.append(q["bar"][tag]["lit"])
            e = rooms.get(q["kind"]) or {}
            if e.get("poster"):
                urls.append(e["poster"])
        seenu, keep = set(), []
        for u in urls:
            if u not in seenu:
                seenu.add(u); keep.append(u)
        if keep:
            a("%s .bzk-pf{background-image:%s}"
              % (sel, ",".join("url(%s)" % u for u in keep)))

    a(".bzk-fo{visibility:hidden;--run:none}")
    focusrules(".bzk-stage", 1)
    a(".bzk-pa:target ~ .bzk-stage .bzk-pet{display:none;--flt:var(--tf);"
      "--hon:none;--rvx:none;--rvy:none}")
    a(".bzk-pa:target ~ .bzk-stage .bzk-pet > img{opacity:1}")
    a(".bzk-pa:target ~ .bzk-stage .bzk-win{display:none}")
    a(".bzk-pa:target ~ .bzk-stage .bzk-fo{visibility:hidden;--run:none}")
    a(".bzk-pa:target ~ .bzk-stage .bzk-bp{display:none}")
    a(".bzk-pa:target ~ .bzk-stage .bzk-pf{background-image:none}")
    for f in range(1, n + 1):
        focusrules(".bzk-pa.f%d:target ~ .bzk-stage" % f, f)
    # the reveal only waits for the boot screen on the very first paint
    a(".bzk-stage .bzk-pet{--rvd:var(--boot)}")
    a(".bzk-pa:target ~ .bzk-stage .bzk-pet{--rvd:0s}")

    # ---- the phone -------------------------------------------------------- #
    # The focused pet stays 200 px: the art is 200 px and must not be
    # resampled, so the row loses the gap instead and the neighbours barely
    # peek in from the edges. The loop is landscape and object-fit:cover
    # crops it to the phone's frame, which is why there is no portrait
    # capture and no second set of megabytes on the worst connection.
    a("@media (max-width:620px){#my-overlay{--gap:124px;--step:74px;"
      "--near:.42;--cy:40%;--bbot:16px;--bw0:399px;--bh0:130px;--bs:.88}"
      ".bzk-nm b{font-size:21px}.bzk-nm i{width:9px;height:15px}}")
    a("@media (max-width:374px){#my-overlay{--gap:104px;--step:64px;--bs:.78}}")
    a("@media (min-width:621px) and (max-width:1100px){"
      "#my-overlay{--gap:150px;--step:104px;--near:.50}}")
    a("@media (min-width:1101px) and (max-width:1320px){"
      "#my-overlay{--gap:174px;--step:126px;--near:.54}}")
    a("@media (min-width:621px){#my-overlay{--bs:1}}")

    # ---- reduced motion --------------------------------------------------- #
    a("@media (prefers-reduced-motion:reduce){"
      "#my-overlay *,#my-overlay *::before,#my-overlay *::after"
      "{animation:none !important;transition:none !important}"
      "#bzk-boot{opacity:0;visibility:hidden}"
      ".bzk-pet > img{opacity:1 !important}"
      ".bzk-win{display:none !important}"
      "#my-overlay .bzk-rm > video{display:none !important}}")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- #
# the paste                                                                   #
# --------------------------------------------------------------------------- #

def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


BOOT_HTML = (
    '<div id="bzk-boot" style="position:absolute;top:0;left:0;width:100%;'
    'height:100%;background:#05070a"><span style="position:absolute;top:50%;'
    'left:0;width:100%;text-align:center;font:10px monospace;'
    'letter-spacing:.28em;color:#677c78">LOADING</span></div>')

OVERLAY_STYLE = ("position:fixed;top:0;left:0;width:100vw;height:100vh;"
                 "z-index:999999999;background:#05070a")


def names_html(pets):
    o = ['<div class="bzk-names">']
    for i, p in enumerate(pets, 1):
        letters = "".join("<b>%s</b>" % esc(c) for c in p["name"][:MAX_NAME])
        o.append('<span class="bzk-nm bzk-fo n%d">%s<i></i></span>' % (i, letters))
    o.append("</div>")
    return "".join(o)


def bar_html(pets, man):
    # THE INDEX STRIP IS ONLY AS LONG AS THE PLATE DREW IT. The bar is one
    # captured picture and the capture had six index cells in it, so a
    # twenty-pet page gets six index links and not twenty zero-sized ones
    # that a finger can never land on. Everything past the sixth pet is
    # reached with prev/next or by clicking the pet. Recapturing the bar
    # plates at twenty is a capture job, not a build one.
    n = len(pets)
    nix = len((((man or {}).get("bar") or {}).get("L") or {}).get("index") or [])
    nix = min(n, nix) if nix else n
    o = ['<div class="bzk-bar">']
    for i in range(1, n + 1):
        o.append('<u class="bzk-bp n%d"></u>' % i)
    o.append('<div class="bzk-hit">')
    slots = list(SLOTS)
    if USER_ID:
        slots.append(("GALLERY", "%s/gallery/?u=%s" % (PREFIX, USER_ID)))
    for k, (label, href) in enumerate(slots, 1):
        o.append('<a class="bzk-sl k%d" href="%s"><i></i></a>' % (k, href))
    for i in range(1, nix + 1):
        o.append('<a class="bzk-ix x%d" href="#p%d"><i></i></a>' % (i, i))
    for i in range(1, n + 1):
        pv = n if i == 1 else i - 1
        nx = 1 if i == n else i + 1
        o.append('<span class="bzk-nav bzk-fo n%d">'
                 '<a class="bzk-pv" href="#p%db"><i></i></a>'
                 '<a class="bzk-nx" href="#p%da"><i></i></a></span>'
                 % (i, pv, nx))
    o.append("</div></div>")
    return "".join(o)


def pet_html(i, p, local):
    src = p["rel"] if local else p["img"]
    o = ['<div class="bzk-pet"><u class="bzk-closed"></u>']
    if p.get("rev"):
        o.append('<span class="bzk-win"><i class="bzk-sr">'
                 '<b class="bzk-sh"></b></i></span>')
    o.append('<img class="bzk-img" src="%s" alt="%s">'
             '<a class="bzk-sf" href="#p%da"></a>'
             '<a class="bzk-sb" href="#p%db"></a></div>'
             % (esc(src), esc(p["name"]), i, i))
    return "".join(o)


def body(pets, man, rooms, order, local=False):
    n = len(pets)
    base = "" if local else cdn_base()
    o = []
    # THE GATE, INLINE AND FIRST. The stylesheet is a separate request, and
    # in the gap before it applies the loops are display:block by the
    # browser's own default and autoplay has already started pulling them.
    # An inline rule is parsed before the video elements below it exist.
    o.append("<style>.bzk-wall>div>video{display:none}</style>")
    o.append('<div id="my-overlay" style="%s">%s' % (OVERLAY_STYLE, BOOT_HTML))
    for i, p in enumerate(pets, 1):
        for sfx, dcls in (("", ""), ("a", " da"), ("b", " db")):
            o.append('<i class="bzk-pa f%d%s" id="p%d%s"></i>' % (i, dcls, i, sfx))
    o.append('<div class="bzk-stage">')
    o.append('<div class="bzk-wall">')
    for k in order:
        o.append('<div class="bzk-rm m-%s"><video src="%s%s" autoplay loop '
                 'muted playsinline></video></div>' % (k, base, rooms[k]["mp4"]))
    o.append("</div>")
    o.append('<div class="bzk-row">')
    for i, p in enumerate(pets, 1):
        o.append(pet_html(i, p, local))
    o.append("</div>")
    o.append(names_html(pets))
    o.append(bar_html(pets, man))
    o.append('<u class="bzk-pf"></u>')
    o.append("</div></div>")
    return "".join(o)


FILLER = """<h1>Rescreatu - user profile (mock host page)</h1>
<p>This page stands in for the Rescreatu profile page that the overlay covers.
It is long and light so that a leak would be obvious.</p>
"""


def preview(bodytext, tag=""):
    pad = "".join("<p>Host page filler line %d. The overlay must hide all of "
                  "this.</p>\n" % i for i in range(1, 61))
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>blizzicky lite preview</title>"
            "<style>body{margin:0;padding:24px;background:#f3f1ea;color:#333;"
            "font:16px/1.5 Georgia,serif}</style></head><body>\n"
            + FILLER + pad
            + "\n<!-- ==== paste body below (all urls local) ==== -->\n"
            + '<link rel="stylesheet" href="profile%s.css">\n' % tag
            + bodytext + "\n</body></html>\n")


# --------------------------------------------------------------------------- #
# the allow-list                                                              #
# --------------------------------------------------------------------------- #

# WHAT THE HOST HAS BEEN PROVEN TO KEEP. `title` is not here and never will
# be: the site's filter cut it inside the word and left `tle="HATCHERY">`
# showing as text over the bar.
OK_TAGS = ("link", "div", "span", "a", "b", "i", "u", "img", "video",
           "source", "style")
OK_ATTRS = ("class", "id", "href", "src", "alt", "style", "rel", "as", "type",
            "autoplay", "loop", "muted", "playsinline", "width")
ATTR_ON = {"rel": ("link",), "as": ("link",), "type": ("link", "source"),
           "autoplay": ("video",), "loop": ("video",), "muted": ("video",),
           "playsinline": ("video",)}
TAG_RE = re.compile(r"<\s*([a-zA-Z][-\w]*)([^>]*)>")
ATTR_RE = re.compile(
    r"""([a-zA-Z_:][-\w:.]*)\s*(?:=\s*(?:"[^"]*"|'[^']*'|[^\s"'>]+))?""")


def check_paste(paste):
    tags, attrs, bad = {}, {}, []
    for m in TAG_RE.finditer(paste):
        t = m.group(1).lower()
        tags[t] = tags.get(t, 0) + 1
        if t not in OK_TAGS:
            bad.append("tag <%s>" % t)
        for at in ATTR_RE.findall(m.group(2)):
            at = at.lower()
            attrs[at] = attrs.get(at, 0) + 1
            if at.startswith("data-"):
                continue
            if at not in OK_ATTRS:
                bad.append("attribute %s= on <%s>" % (at, t))
            elif at in ATTR_ON and t not in ATTR_ON[at]:
                bad.append("attribute %s= on <%s>" % (at, t))
    def show(d):
        return ", ".join("%s x%d" % (k, v) for k, v in sorted(d.items()))
    print("paste tags   : %s" % show(tags))
    print("paste attrs  : %s" % show(attrs))
    if bad:
        seen, uniq = set(), []
        for b in bad:
            if b not in seen:
                seen.add(b); uniq.append(b)
        sys.exit("PASTE REJECTED, outside the allow-list: " + "; ".join(uniq))


# --------------------------------------------------------------------------- #
# main                                                                        #
# --------------------------------------------------------------------------- #

def main(argv=None):
    global REF
    argv = list(sys.argv[1:] if argv is None else argv)
    petfile = os.path.join(HERE, "pets.txt")
    # --tag -20 writes profile-20.html / profile-20.css / preview-20.html,
    # so the twenty-pet scale build sits beside the one that ships without
    # overwriting it.
    tag = ""
    while argv:
        if argv[0] in ("--ref", "-r") and len(argv) > 1:
            REF = argv[1]; del argv[:2]
        elif argv[0].startswith("--ref="):
            REF = argv[0].split("=", 1)[1]; del argv[:1]
        elif argv[0] in ("--pets", "-p") and len(argv) > 1:
            petfile = argv[1]; del argv[:2]
        elif argv[0].startswith("--pets="):
            petfile = argv[0].split("=", 1)[1]; del argv[:1]
        elif argv[0] == "--tag" and len(argv) > 1:
            tag = argv[1]; del argv[:2]
        elif argv[0].startswith("--tag="):
            tag = argv[0].split("=", 1)[1]; del argv[:1]
        else:
            sys.exit("usage: build.py [--pets FILE] [--ref REF] [--tag TAG]")

    pets, warn = read_pets(petfile)
    if not pets:
        sys.exit("no pets")
    attach_reveal(pets, warn)
    man = read_plates(warn)
    attach_plates(pets, man, warn)
    rooms = read_rooms(warn)
    if not rooms:
        sys.exit("no room loops in " + ROOM_DIR)
    order = room_order(pets, rooms)
    for w in warn:
        print("WARNING:", w)

    css = build_css(pets, man, rooms, order)
    paste = '<link rel="stylesheet" href="%sprofile%s.css">\n%s\n' \
            % (cdn_base(), tag, body(pets, man, rooms, order, local=False))
    check_paste(paste)

    for name, text in (("profile%s.css" % tag, css),
                       ("profile%s.html" % tag, paste),
                       ("preview%s.html" % tag,
                        preview(body(pets, man, rooms, order, local=True), tag))):
        with open(os.path.join(HERE, name), "w", encoding="utf-8") as f:
            f.write(text)

    vid = sum(rooms[k]["mp4_bytes"] for k in order)
    pos = sum(rooms[k].get("poster_bytes", 0) for k in order)
    sheets = {}
    for p in pets:
        if p.get("rev"):
            sheets[p["key"]] = (p["rev"]["sheet_bytes"], p["rev"]["closed_bytes"])
    bar = 0
    for f in sorted({p["key"]: p for p in pets}):
        q = [p for p in pets if p["key"] == f][0]
        for tg in ("L", "P"):
            for st in ("ink", "lit"):
                u = (q["bar"].get(tg) or {}).get(st)
                if u:
                    bar += os.path.getsize(os.path.join(HERE, u))
    print("pets: %d   rooms shipped: %d (%s)"
          % (len(pets), len(order), ", ".join(order)))
    for k in order:
        e = rooms[k]
        print("  room %-10s %7.1f KB  poster %5.1f KB  %s frames  "
              "median step %s  wrap %s  worst step %s (%sx median)  %s"
              % (k, e["mp4_bytes"] / 1024.0, e.get("poster_bytes", 0) / 1024.0,
                 e.get("frames"), e.get("median_adj"), e.get("wrap_diff"),
                 e.get("max_adj_diff"), e.get("pop_x"), e.get("loop")))
    for k, (s, c) in sorted(sheets.items()):
        print("  sheet %-30s %6.1f KB  closed %5.1f KB" % (k, s / 1024.0, c / 1024.0))
    print("loops        : %.2f MB  posters %.0f KB  bar plates %.0f KB  "
          "sheets %.0f KB" % (vid / 1e6, pos / 1024.0, bar / 1024.0,
                              sum(s for s, _ in sheets.values()) / 1024.0))
    print("cdn ref      : %s" % REF)
    chars = len(paste)
    each = (len(body(pets, man, rooms, order)) -
            (len(body(pets[:-1], man, rooms, order)) if len(pets) > 1 else 0))
    print("profile%s.html : %d characters (limit 35000)" % (tag, chars))
    print("               %d characters for each pet; about %d at 20, %d at %d"
          % (each, chars + each * (20 - len(pets)),
             chars + each * (MAXPETS - len(pets)), MAXPETS))
    print("profile%s.css  : %d bytes" % (tag, len(css.encode("utf-8"))))
    if chars > 30000:
        print("WARNING: paste is close to the 35,000 character limit")


if __name__ == "__main__":
    main()
