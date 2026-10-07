#!/usr/bin/env python3
"""AT-SUIT brand generator.

Builds every logo, app icon and social image from geometry so the pack is
reproducible:  python3 make_brand.py

Geometry  : shapely (gear = circle - 8 scoops - inner circle - C opening)
Lettering : Saira ExtraBold / Bold (SIL OFL 1.1) outlines read with fontTools,
            converted to polygons, so the SVGs are pure paths (no font needed).
Rendering : Playwright Chromium (SVG -> PNG), Pillow (.ico / .bmp).
"""
import json, math, os, shutil, subprocess, sys
from functools import reduce

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from shapely import affinity
from shapely.geometry import Point, Polygon, MultiPolygon, box
from shapely.ops import unary_union

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.environ.get("ATSUIT_FONT_DIR", os.path.join(HERE, "fonts"))
FONT_800 = os.path.join(FONT_DIR, "saira-800.ttf")
FONT_700 = os.path.join(FONT_DIR, "saira-700.ttf")
SHARE = "/mnt/project-files/branding"

# ----------------------------------------------------------------- colours --
COLOURS = {
    "orange":   {"name": "AT-SUIT Orange", "hex": "#FF7A1A",
                 "use": "The gear. Primary accent: buttons, active tabs, highlights, links on dark."},
    "black":    {"name": "Outline Black", "hex": "#000000",
                 "use": "Outer outline of the gear and the letters. Never removed."},
    "white":    {"name": "Letter White", "hex": "#FFFFFF",
                 "use": "Letters and the thin band inside the gear outline."},
    "navy":     {"name": "Night Navy", "hex": "#0B1020",
                 "use": "Brand background: banners, social images, splash screens, the logo plate."},
    "ui_dark":  {"name": "App Dark", "hex": "#0E1116",
                 "use": "AT-SUIT app UI background (dark theme)."},
    "orange_dark":  {"name": "Ember", "hex": "#C85400",
                     "use": "Orange pressed / hover-dark state, orange text on white (AA large)."},
    "orange_light": {"name": "Glow", "hex": "#FFB27A",
                     "use": "Orange tints, subtle highlights on dark UI."},
}
FAMILY = [
    ("AT GROUP", "#0A9EFC", "Parent brand"),
    ("AT SOLUTIONS", "#FDD600", "CCTV & network systems"),
    ("AT NET", "#9B0AFF", "Networking"),
    ("AT EVENTS", "#0AFF2A", "Events"),
    ("AT HANDYMAN", "#FC05E2", "Handyman services (sampled)"),
    ("AT SUIT", "#FF7A1A", "Room control software (this brand)"),
]
ORANGE = COLOURS["orange"]["hex"]
NAVY = COLOURS["navy"]["hex"]

# --------------------------------------------------------------- geometry --
# Unit: R = 1 = outer radius of the gear body (white layer).  Measured from
# the AT GROUP icon: scoop centre 1.075R out, radius 0.225R; inner hole 0.70R;
# C opening half-height 0.42R; black outline 0.026R; white band 0.024R.
G = dict(R=1.0, scoop_d=1.075, scoop_r=0.225, inner=0.68, open_h=0.42,
         black=0.027, white=0.024, round=0.012, res=96)


def gear_body(g=G):
    R = g["R"]
    shape = Point(0, 0).buffer(R, g["res"])
    for k in range(8):                      # every 45 deg, 0 deg = right (cut away anyway)
        a = math.radians(k * 45)
        c = Point(g["scoop_d"] * math.cos(a), g["scoop_d"] * math.sin(a))
        shape = shape.difference(c.buffer(g["scoop_r"], g["res"]))
    shape = shape.difference(Point(0, 0).buffer(g["inner"], g["res"]))
    shape = shape.difference(box(0, -g["open_h"], 2 * R, g["open_h"]))
    r = g["round"]                          # soften the sharp tooth corners slightly
    if r:
        shape = shape.buffer(r, 32).buffer(-2 * r, 32).buffer(r, 32)
    return shape


# ------------------------------------------------------------------ fonts --
class FlattenPen(BasePen):
    def __init__(self, glyphset, steps=12):
        super().__init__(glyphset)
        self.contours, self.cur, self.steps = [], [], steps

    def _moveTo(self, p):
        self.cur = [p]

    def _lineTo(self, p):
        self.cur.append(p)

    def _curveToOne(self, p1, p2, p3):
        p0 = self.cur[-1]
        for i in range(1, self.steps + 1):
            t = i / self.steps; u = 1 - t
            self.cur.append((u**3*p0[0] + 3*u*u*t*p1[0] + 3*u*t*t*p2[0] + t**3*p3[0],
                             u**3*p0[1] + 3*u*u*t*p1[1] + 3*u*t*t*p2[1] + t**3*p3[1]))

    def _qCurveToOne(self, p1, p2):
        p0 = self.cur[-1]
        for i in range(1, self.steps + 1):
            t = i / self.steps; u = 1 - t
            self.cur.append((u*u*p0[0] + 2*u*t*p1[0] + t*t*p2[0],
                             u*u*p0[1] + 2*u*t*p1[1] + t*t*p2[1]))

    def _closePath(self):
        if len(self.cur) > 2:
            self.contours.append(self.cur)
        self.cur = []

    _endPath = _closePath


class Font:
    def __init__(self, path):
        self.tt = TTFont(path)
        self.gs = self.tt.getGlyphSet()
        self.cmap = self.tt.getBestCmap()
        self.cap = self.tt["OS/2"].sCapHeight
        self.cache = {}

    def glyph(self, ch):
        """Glyph as a shapely polygon, y down, baseline at 0, cap height = 1."""
        if ch in self.cache:
            return self.cache[ch]
        pen = FlattenPen(self.gs)
        self.gs[self.cmap[ord(ch)]].draw(pen)
        polys = [Polygon(c).buffer(0) for c in pen.contours]
        # non-zero winding: contours with the same orientation as the biggest are fills
        signed = []
        for c in pen.contours:
            a = 0.0
            for (x1, y1), (x2, y2) in zip(c, c[1:] + c[:1]):
                a += x1 * y2 - x2 * y1
            signed.append(a)
        ref = signed[max(range(len(signed)), key=lambda i: abs(signed[i]))]
        fills = [p for p, s in zip(polys, signed) if s * ref > 0]
        holes = [p for p, s in zip(polys, signed) if s * ref < 0]
        g = unary_union(fills)
        if holes:
            g = g.difference(unary_union(holes))
        g = affinity.scale(g, 1 / self.cap, -1 / self.cap, origin=(0, 0))
        self.cache[ch] = g
        return g


def set_word(font, text, gap, space=0.45):
    """Lay letters out left to right; each pair is spaced so the nearest
    distance between the letter fills equals `gap` (optical spacing)."""
    placed, x_right, prev = [], 0.0, None
    for ch in text:
        if ch == " ":
            x_right += space; prev = None; continue
        g = font.glyph(ch)
        minx = g.bounds[0]
        g = affinity.translate(g, x_right - minx, 0)
        if prev is not None:
            # slide left/right until distance == gap (bisection)
            lo, hi = -1.0, 1.0
            for _ in range(40):
                mid = (lo + hi) / 2
                d = affinity.translate(g, mid, 0).distance(prev)
                if affinity.translate(g, mid, 0).intersects(prev) or d < gap:
                    lo = mid
                else:
                    hi = mid
            g = affinity.translate(g, hi, 0)
        else:
            g = affinity.translate(g, 0 if not placed else 0, 0)
        placed.append(g)
        prev = g
        x_right = g.bounds[2]
    return unary_union(placed)


# ------------------------------------------------------------------- SVG --
def path_d(geom, s=1.0, dx=0.0, dy=0.0, nd=2):
    if geom.is_empty:
        return ""
    polys = [geom] if isinstance(geom, Polygon) else list(getattr(geom, "geoms", []))
    out = []
    for p in polys:
        if not isinstance(p, Polygon):
            continue
        for ring in [p.exterior, *p.interiors]:
            pts = list(ring.coords)[:-1]
            seg = ["M%s %s" % (round(pts[0][0]*s+dx, nd), round(pts[0][1]*s+dy, nd))]
            seg += ["L%s %s" % (round(x*s+dx, nd), round(y*s+dy, nd)) for x, y in pts[1:]]
            out.append("".join(seg) + "Z")
    return "".join(out)


def svg_doc(w, h, layers, bg=None, title="AT-SUIT"):
    """layers: list of (geom, fill). Geometry already in pixel units."""
    body = []
    if bg:
        body.append('<rect width="%s" height="%s" fill="%s"/>' % (w, h, bg))
    for geom, fill in layers:
        d = path_d(geom)
        if d:
            body.append('<path fill="%s" fill-rule="evenodd" d="%s"/>' % (fill, d))
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%s" height="%s" '
            'viewBox="0 0 %s %s"><title>%s</title>%s</svg>\n'
            % (w, h, w, h, title, "".join(body)))


def fit(layers, w, h, pad, bg=None, align="center"):
    """Scale/translate a list of (geom, fill) in unit space into a w x h box."""
    allg = unary_union([g for g, _ in layers])
    minx, miny, maxx, maxy = allg.bounds
    s = min((w - 2 * pad) / (maxx - minx), (h - 2 * pad) / (maxy - miny))
    dx = (w - (maxx - minx) * s) / 2 - minx * s
    dy = (h - (maxy - miny) * s) / 2 - miny * s
    out = [(affinity.affine_transform(g, [s, 0, 0, s, dx, dy]), f) for g, f in layers]
    return out


# ---------------------------------------------------------------- lockups --
F800 = Font(FONT_800)
F700 = Font(FONT_700)

CAP = 0.70          # AT cap height in units of R (both icon and wordmark, as AT GROUP)
LETTER_LINE = 0.026 # letter outline as a fraction of R  (~0.037 cap)
AT_GAP = 0.13      # optical gap AT (in cap units)
AT_LEFT = -0.50     # left edge of the A, in R
WORD_X = 0.96       # where the product word starts, in R
WORD_GAP = 0.13


def at_letters(font=F800, cap=CAP, left=AT_LEFT):
    at = set_word(font, "AT", AT_GAP)
    at = affinity.scale(at, cap, cap, origin=(0, 0))
    minx, miny, maxx, maxy = at.bounds
    return affinity.translate(at, left - minx, -(miny + maxy) / 2)


def word_letters(text, x0=WORD_X, cap=CAP, font=F800):
    w = set_word(font, text, WORD_GAP, space=0.40)
    w = affinity.scale(w, cap, cap, origin=(0, 0))
    minx, miny, maxx, maxy = w.bounds
    return affinity.translate(w, x0 - minx, -(miny + maxy) / 2)


def full_colour(gear, letters, colour=ORANGE, black=G["black"], white=G["white"],
                lline=LETTER_LINE):
    return [
        (gear.buffer(black, 48), "#000000"),
        (gear, "#FFFFFF"),
        (gear.buffer(-white, 48), colour),
        (letters.buffer(lline, 48), "#000000"),
        (letters, "#FFFFFF"),
    ]


def one_colour(gear, letters, black=G["black"], lline=LETTER_LINE, knock=0.03):
    """Solid silhouette: outer outline + body as one shape, letters knocked
    clear of the gear by a small gap."""
    body = gear.buffer(black, 48)
    let = letters.buffer(lline, 48)
    body = body.difference(let.buffer(knock, 48))
    return unary_union([body, let])


def small_icon():
    """Simplified mark for <= 32 px: chunkier gear (no white band), heavy
    outline, and an oversized bold AT that may overlap the ring so it stays
    readable at 16 px."""
    g = dict(G, inner=0.58, open_h=0.30, scoop_r=0.25, scoop_d=1.10, round=0.03)
    gear = gear_body(g)
    at = at_letters(F800, cap=0.98, left=-0.62)
    return [
        (gear.buffer(0.08, 32), "#000000"),
        (gear, ORANGE),
        (at.buffer(0.075, 32), "#000000"),
        (at, "#FFFFFF"),
    ]


# -------------------------------------------------------------- rendering --
JOBS = []   # (svg_path, png_path, width, height, background or None)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def emit(name, layers, w, h, pad, bg=None, png=True, png_w=None):
    lay = fit(layers, w, h, pad)
    svg = svg_doc(w, h, lay, bg=bg, title="AT-SUIT")
    p = os.path.join(HERE, name + ".svg")
    write(p, svg)
    if png:
        pw = png_w or w
        JOBS.append((p, os.path.join(HERE, name + ".png"), pw, round(h * pw / w)))
    return lay


def render_all():
    js = os.path.join(HERE, ".render.js")
    write(js, r"""
const { chromium } = require('/home/claude/AT-SUIT/node-app/node_modules/playwright');
const fs = require('fs');
(async () => {
  const jobs = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' });
  const p = await b.newPage();
  for (const [svg, png, w, h, html] of jobs) {
    await p.setViewportSize({ width: w, height: h });
    if (html) { await p.goto('file://' + svg); }
    else {
      const src = fs.readFileSync(svg, 'utf8');
      await p.setContent(`<html><body style="margin:0;background:transparent">
        <img src="data:image/svg+xml;base64,${Buffer.from(src).toString('base64')}"
             style="display:block;width:${w}px;height:${h}px"></body></html>`);
    }
    await p.screenshot({ path: png, omitBackground: true, clip: { x: 0, y: 0, width: w, height: h } });
  }
  await b.close();
})();
""")
    jf = os.path.join(HERE, ".jobs.json")
    write(jf, json.dumps([list(j) + [False] if len(j) == 4 else list(j) for j in JOBS]))
    subprocess.run(["node", js, jf], check=True)
    os.remove(js); os.remove(jf)
    JOBS.clear()


def pdf_from_html(html, pdf):
    js = os.path.join(HERE, ".pdf.js")
    write(js, r"""
const { chromium } = require('/home/claude/AT-SUIT/node-app/node_modules/playwright');
(async () => {
  const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' });
  const p = await b.newPage();
  await p.goto('file://' + process.argv[2], { waitUntil: 'networkidle' });
  await p.emulateMedia({ media: 'print' });
  await p.pdf({ path: process.argv[3], format: 'A4', landscape: true, printBackground: true,
                margin: { top: '0', bottom: '0', left: '0', right: '0' }, preferCSSPageSize: true });
  await b.close();
})();
""")
    subprocess.run(["node", js, html, pdf], check=True)
    os.remove(js)


# ------------------------------------------------------------------- main --
def main():
    gear = gear_body()
    at = at_letters()
    suit = word_letters("SUIT")

    icon = full_colour(gear, at)
    wordmark = full_colour(gear, unary_union([at, suit]))
    # stacked: icon above "AT SUIT"
    stack_word = word_letters("AT SUIT", x0=0, cap=0.42)
    sw = stack_word.bounds
    stack_word = affinity.translate(stack_word, -(sw[0] + sw[2]) / 2, 1.0 + 0.10 + 0.21 + 0.06)
    stacked = full_colour(gear, at) + [
        (stack_word.buffer(LETTER_LINE * 0.8, 48), "#000000"), (stack_word, "#FFFFFF")]

    L = "logo/"
    emit(L + "atsuit-icon", icon, 1024, 1024, 24)
    emit(L + "atsuit-wordmark", wordmark, 2000, 760, 30)
    emit(L + "atsuit-stacked", stacked, 1200, 1400, 40)
    emit(L + "atsuit-icon-on-dark", icon, 1024, 1024, 96, bg=NAVY)
    emit(L + "atsuit-icon-on-light", icon, 1024, 1024, 96, bg="#FFFFFF")
    emit(L + "atsuit-wordmark-on-dark", wordmark, 2000, 860, 110, bg=NAVY)
    emit(L + "atsuit-wordmark-on-light", wordmark, 2000, 860, 110, bg="#FFFFFF")
    emit(L + "atsuit-stacked-on-dark", stacked, 1200, 1400, 110, bg=NAVY)
    for col, nm in (("#000000", "black"), ("#FFFFFF", "white")):
        emit(L + "atsuit-icon-%s" % nm, [(one_colour(gear, at), col)], 1024, 1024, 24)
        emit(L + "atsuit-wordmark-%s" % nm,
             [(one_colour(gear, unary_union([at, suit])), col)], 2000, 760, 30)
        emit(L + "atsuit-stacked-%s" % nm,
             [(unary_union([one_colour(gear, at), stack_word.buffer(LETTER_LINE * 0.8)]), col)],
             1200, 1400, 40)
    # gear only (for loaders / watermarks)
    emit(L + "atsuit-gear", full_colour(gear, Polygon()), 1024, 1024, 24)

    # ---- app icons
    A = "app/"
    emit(A + "favicon", icon, 64, 64, 1, png=False)
    emit(A + "icon-1024", icon, 1024, 1024, 24)
    emit(A + "icon-512", icon, 512, 512, 12)
    emit(A + "icon-192", icon, 192, 192, 5)
    emit(A + "icon-small", small_icon(), 64, 64, 1, png=False)
    JOBS.append((os.path.join(HERE, A + "icon-512.svg"), os.path.join(HERE, A + "icon.png"), 512, 512))
    # apple touch: navy plate (iOS fills transparency with black)
    emit(A + "apple-touch-icon", icon, 180, 180, 16, bg=NAVY)
    for s in (16, 24, 32):
        JOBS.append((os.path.join(HERE, A + "icon-small.svg"), os.path.join(HERE, A + "_s%d.png" % s), s, s))
    for s in (48, 64, 128, 256):
        JOBS.append((os.path.join(HERE, A + "icon-1024.svg"), os.path.join(HERE, A + "_s%d.png" % s), s, s))
    # tray icons (simplified) + @2x
    JOBS.append((os.path.join(HERE, A + "icon-small.svg"), os.path.join(HERE, A + "tray-16.png"), 16, 16))
    JOBS.append((os.path.join(HERE, A + "icon-small.svg"), os.path.join(HERE, A + "tray-32.png"), 32, 32))

    # ---- manual illustrations (SVG only, referenced by manual/index.html)
    manual_art(gear, at, suit, wordmark)

    # ---- social
    S = "social/"
    social(S, gear, wordmark)
    render_all()

    # ---- .ico + bmp via Pillow
    from PIL import Image
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = {s: Image.open(os.path.join(HERE, A + "_s%d.png" % s)).convert("RGBA") for s in sizes}
    big = imgs[256]
    big.save(os.path.join(HERE, A + "icon.ico"), sizes=[(s, s) for s in sizes],
             append_images=[imgs[s] for s in sizes if s != 256])
    fav = [16, 32, 48]
    imgs[48].save(os.path.join(HERE, A + "favicon.ico"), sizes=[(s, s) for s in fav],
                  append_images=[imgs[16], imgs[32]])
    for s in sizes:
        os.remove(os.path.join(HERE, A + "_s%d.png" % s))
    # small-size favicon.svg uses the simplified mark? keep the full mark (scales);
    # browsers render favicon.svg at 16-32 px, so use the simplified one there.
    shutil.copy(os.path.join(HERE, A + "icon-small.svg"), os.path.join(HERE, A + "favicon.svg"))

    # ---- colours.json
    write(os.path.join(HERE, "colours.json"), json.dumps({
        "brand": "AT-SUIT",
        "colours": {k: dict(v, rgb=hex_rgb(v["hex"])) for k, v in COLOURS.items()},
        "family": [{"brand": b, "hex": h, "rgb": hex_rgb(h), "note": n} for b, h, n in FAMILY],
        "fonts": {"logo": "Saira ExtraBold 800 (outlined in the logo files)",
                  "ui": "Saira 700/800 for headings, system UI font for body"},
    }, indent=2) + "\n")

    # ---- manual
    man = os.path.join(HERE, "manual", "index.html")
    if os.path.exists(man):
        pdf_from_html(man, os.path.join(HERE, "manual", "AT-SUIT-brand-guide.pdf"))

    # ---- copy fonts licence + share
    if os.path.isdir(os.path.dirname(SHARE)) and "--no-share" not in sys.argv:
        if os.path.exists(SHARE):
            shutil.rmtree(SHARE)
        shutil.copytree(HERE, SHARE, ignore=shutil.ignore_patterns("__pycache__", ".*"))
    print("done")


def manual_art(gear, at, suit, wordmark):
    M = "manual/img/"
    b, w = G["black"], G["white"]
    # anatomy: each layer on its own
    emit(M + "layer-black", [(gear.buffer(b, 48), "#000000")], 400, 400, 12, png=False)
    emit(M + "layer-white", [(gear.buffer(b, 48), "#D9DEE7"), (gear, "#FFFFFF")], 400, 400, 12, png=False)
    emit(M + "layer-orange", [(gear.buffer(b, 48), "#D9DEE7"), (gear.buffer(-w, 48), ORANGE)], 400, 400, 12, png=False)
    emit(M + "layer-letters", [(gear.buffer(b, 48), "#E7EAF0"), (at.buffer(LETTER_LINE, 48), "#000000"),
                               (at, "#FFFFFF")], 400, 400, 12, png=False)
    # clear space: x = height of the A
    allw = unary_union([g for g, _ in wordmark]).bounds
    x = CAP
    frame = box(allw[0] - x, allw[1] - x, allw[2] + x, allw[3] + x)
    def dashed_rect(geom_box, step=0.08, thick=0.012):
        x0, y0, x1, y1 = geom_box.bounds
        parts = []
        for (ax, ay, bx_, by) in ((x0, y0, x1, y0), (x0, y1, x1, y1), (x0, y0, x0, y1), (x1, y0, x1, y1)):
            L = math.hypot(bx_ - ax, by - ay); n = int(L / step)
            for i in range(0, n, 2):
                t0, t1 = i / n, min((i + 1) / n, 1)
                parts.append(box(min(ax + (bx_-ax)*t0, ax + (bx_-ax)*t1) - thick, min(ay + (by-ay)*t0, ay + (by-ay)*t1) - thick,
                                 max(ax + (bx_-ax)*t0, ax + (bx_-ax)*t1) + thick, max(ay + (by-ay)*t0, ay + (by-ay)*t1) + thick))
        return unary_union(parts)
    a_box = at.bounds  # the A spans full cap height
    xmark = []
    for (cx, cy) in ((allw[0] - x / 2, 0), (allw[2] + x / 2, 0), ((allw[0] + allw[2]) / 2, allw[1] - x / 2),
                     ((allw[0] + allw[2]) / 2, allw[3] + x / 2)):
        xmark.append(box(cx - x / 2 + 0.03, cy - x / 2 + 0.03, cx + x / 2 - 0.03, cy + x / 2 - 0.03))
    emit(M + "clearspace", [(frame, "#FFF4EC"), (unary_union(xmark), "#FFD9BD"),
                            (dashed_rect(frame), ORANGE),
                            (dashed_rect(box(*unary_union([g for g, _ in wordmark]).bounds)), "#9AA3B2")]
         + wordmark, 1600, 760, 10, png=False)
    # unit illustration: an A with its height bar
    A = at.intersection(box(a_box[0] - 1, a_box[1] - 1, a_box[0] + 0.62, a_box[3] + 1))
    ab = A.bounds
    bar = box(ab[2] + 0.12, ab[1], ab[2] + 0.15, ab[3])
    ticks = unary_union([box(ab[2] + 0.07, ab[1] - 0.015, ab[2] + 0.20, ab[1] + 0.015),
                         box(ab[2] + 0.07, ab[3] - 0.015, ab[2] + 0.20, ab[3] + 0.015)])
    emit(M + "unit-a", [(A.buffer(LETTER_LINE, 48), "#000000"), (A, "#FFFFFF"),
                        (unary_union([bar, ticks]), ORANGE)], 300, 300, 30, png=False)
    # don'ts
    emit(M + "dont-recolour", full_colour(gear, at, colour="#0A9EFC"), 400, 400, 12, png=False)
    emit(M + "dont-no-outline", [(gear, ORANGE), (at, "#FFFFFF")], 400, 400, 12, png=False)
    emit(M + "dont-no-band", [(gear.buffer(b, 48), "#000000"), (gear, ORANGE),
                              (at.buffer(LETTER_LINE, 48), "#000000"), (at, "#FFFFFF")], 400, 400, 12, png=False)
    emit(M + "dont-orange-letters", [(gear.buffer(b, 48), "#000000"), (gear, "#FFFFFF"),
                                     (gear.buffer(-w, 48), ORANGE), (at.buffer(LETTER_LINE, 48), "#000000"),
                                     (at, ORANGE)], 400, 400, 12, png=False)
    # family gear chips (colour reference only)
    for brand, hx, _ in FAMILY:
        emit(M + "family-%s" % brand.lower().replace(" ", "-"), full_colour(gear, Polygon(), colour=hx),
             200, 200, 6, png=False)
    # small icon preview
    emit(M + "small-icon", small_icon(), 400, 400, 12, png=False)


def hex_rgb(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def social(S, gear, wordmark):
    tag = word_plain("ONE APP FOR EVERY ROOM", F700)
    tag2 = word_plain("TIMERS · CHAT · CAPTIONS · PRESENTERS · SCREENS", F700)
    for name, w, h, frac, th in (("github-social-preview", 1280, 640, 0.56, 30),
                                 ("readme-banner", 1600, 400, 0.36, 24)):
        allg = unary_union([g for g, _ in wordmark])
        bx = allg.bounds
        target_w = w * frac
        s = target_w / (bx[2] - bx[0])
        wm_h = (bx[3] - bx[1]) * s
        gap1, gap2 = th * 1.0, th * 0.55
        block = wm_h + gap1 + th + gap2 + th * 0.62
        top = (h - block) / 2
        lay = [(affinity.affine_transform(g, [s, 0, 0, s, (w - target_w) / 2 - bx[0] * s, top - bx[1] * s]), f)
               for g, f in wordmark]
        def place(t, y, cap, col):
            b = t.bounds; sc = cap / (b[3] - b[1])
            tw = (b[2] - b[0]) * sc
            return (affinity.affine_transform(t, [sc, 0, 0, sc, (w - tw) / 2 - b[0] * sc, y - b[1] * sc]), col)
        y1 = top + wm_h + gap1
        lay.append(place(tag, y1, th, "#FFFFFF"))
        lay.append(place(tag2, y1 + th + gap2, th * 0.62, ORANGE))
        svg = social_svg(w, h, lay)
        p = os.path.join(HERE, S + name + ".svg")
        write(p, svg)
        JOBS.append((p, os.path.join(HERE, S + name + ".png"), w, h))


def word_plain(text, font):
    return set_word(font, text, 0.12, space=0.32)


def social_svg(w, h, layers):
    """Navy background with a soft orange glow and a faint circuit-line motif,
    echoing the AT Solutions Facebook banner."""
    lines = []
    import random
    rnd = random.Random(7)
    for i in range(16):
        y = rnd.uniform(0.05, 0.95) * h
        x0 = w * rnd.uniform(0.70, 0.86)
        x1 = x0 + rnd.uniform(40, 120)
        dy = rnd.choice([-1, 1]) * rnd.uniform(20, 70)
        lines.append('<path d="M%.0f %.0fH%.0fl%.0f %.0fH%.0f" />' % (w + 10, y, x1, -abs(dy), dy, x0))
        lines.append('<circle cx="%.0f" cy="%.0f" r="5"/>' % (x0, y + dy))
    for i in range(10):
        y = rnd.uniform(0.05, 0.95) * h
        x0 = w * rnd.uniform(0.14, 0.30)
        x1 = x0 - rnd.uniform(40, 120)
        dy = rnd.choice([-1, 1]) * rnd.uniform(20, 70)
        lines.append('<path d="M-10 %.0fH%.0fl%.0f %.0fH%.0f" />' % (y, x1, abs(dy), dy, x0))
        lines.append('<circle cx="%.0f" cy="%.0f" r="5"/>' % (x0, y + dy))
    body = []
    for geom, fill in layers:
        body.append('<path fill="%s" fill-rule="evenodd" d="%s"/>' % (fill, path_d(geom)))
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            '<title>AT-SUIT</title><defs>'
            '<radialGradient id="g1" cx="0.92" cy="0.95" r="0.75"><stop offset="0" stop-color="#FF7A1A" stop-opacity="0.38"/>'
            '<stop offset="1" stop-color="#FF7A1A" stop-opacity="0"/></radialGradient>'
            '<radialGradient id="g2" cx="0.05" cy="0.0" r="0.6"><stop offset="0" stop-color="#2A3A8C" stop-opacity="0.55"/>'
            '<stop offset="1" stop-color="#2A3A8C" stop-opacity="0"/></radialGradient>'
            '<linearGradient id="fade" x1="0" x2="1"><stop offset="0" stop-color="#fff" stop-opacity="0.9"/>'
            '<stop offset="0.35" stop-color="#fff" stop-opacity="0"/><stop offset="0.65" stop-color="#fff" stop-opacity="0"/>'
            '<stop offset="1" stop-color="#fff" stop-opacity="0.9"/></linearGradient>'
            '<mask id="m"><rect width="{w}" height="{h}" fill="url(#fade)"/></mask></defs>'
            '<rect width="{w}" height="{h}" fill="#0B1020"/><rect width="{w}" height="{h}" fill="url(#g2)"/>'
            '<rect width="{w}" height="{h}" fill="url(#g1)"/>'
            '<g mask="url(#m)" fill="none" stroke="#FF9A4D" stroke-opacity="0.35" stroke-width="2">{lines}</g>'
            '{body}</svg>\n').format(w=w, h=h, lines="".join(lines), body="".join(body))


if __name__ == "__main__":
    main()
