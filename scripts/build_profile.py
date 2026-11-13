#!/usr/bin/env python3
"""Build the SVG artwork used by the profile README.

GitHub renders README images without access to web fonts, so every piece of
text is converted to outlines here. The look follows the portfolio site:
obsidian background, amber filaments, Archivo for display type and
JetBrains Mono for labels (both fonts are OFL-licensed).

    pip install fonttools
    python scripts/build_profile.py

Fonts are downloaded once into scripts/.cache/ (git-ignored).
"""

from __future__ import annotations

import math
import random
import urllib.request
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "profile"
CACHE = Path(__file__).resolve().parent / ".cache"

FONT_URLS = {
    "Archivo.ttf": "https://github.com/google/fonts/raw/main/ofl/archivo/Archivo%5Bwdth%2Cwght%5D.ttf",
    "JetBrainsMono.ttf": "https://github.com/google/fonts/raw/main/ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf",
}

# Palette — taken from the portfolio's "sacred" theme.
VOID = "#060402"
AMBER = "#FF8C00"
GLOW = "#FFB03D"
GOLD = "#FFD700"
CORE = "#FFF6E0"
CREAM = "#F4F1EB"
DIM = "#D9D0BF"
MUTED = "#9A907E"

W = 1200  # every artboard is 1200 units wide
PAD = 72  # left/right text margin
PLATE_H = 260


def fmt(v: float) -> str:
    s = f"{v:.1f}"
    if s.endswith(".0"):
        s = s[:-2]
    return "0" if s == "-0" else s


# --------------------------------------------------------------------------
# Type
# --------------------------------------------------------------------------


def font_file(name: str) -> Path:
    path = CACHE / name
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        print(f"downloading {name}")
        urllib.request.urlretrieve(FONT_URLS[name], path)
    return path


class Face:
    """One static instance of a variable font: glyph outlines, advances and kerning."""

    def __init__(self, key: str, name: str, **axes: float):
        self.key = key  # prefix for glyph ids inside an SVG
        self.font = instancer.instantiateVariableFont(TTFont(font_file(name)), axes)
        self.cmap = self.font.getBestCmap()
        self.glyphs = self.font.getGlyphSet()
        self.hmtx = self.font["hmtx"]
        self.upm = self.font["head"].unitsPerEm
        self.kern_lookups = self._find_kern_lookups()

    def _find_kern_lookups(self):
        if "GPOS" not in self.font:
            return []
        table = self.font["GPOS"].table
        indices = set()
        for record in table.FeatureList.FeatureRecord:
            if record.FeatureTag == "kern":
                indices.update(record.Feature.LookupListIndex)
        lookups = []
        for i in sorted(indices):
            subtables = []
            for st in table.LookupList.Lookup[i].SubTable:
                if st.LookupType == 9:  # extension wrapper
                    st = st.ExtSubTable
                if st.LookupType == 2:  # pair adjustment
                    subtables.append(st)
            lookups.append(subtables)
        return lookups

    def kern(self, left: str, right: str) -> float:
        total = 0.0
        for subtables in self.kern_lookups:
            for st in subtables:
                coverage = st.Coverage.glyphs
                if left not in coverage:
                    continue
                if st.Format == 1:
                    pairs = st.PairSet[coverage.index(left)].PairValueRecord
                    hit = next((p for p in pairs if p.SecondGlyph == right), None)
                    if hit is None:
                        continue
                    value = hit.Value1
                else:
                    c1 = st.ClassDef1.classDefs.get(left, 0)
                    c2 = st.ClassDef2.classDefs.get(right, 0)
                    value = st.Class1Record[c1].Class2Record[c2].Value1
                total += (getattr(value, "XAdvance", 0) or 0) if value else 0
                break  # first applicable subtable wins within a lookup
        return total

    def layout(self, text: str, tracking: float = 0.0):
        """Place glyphs along a baseline, in font units. Returns (placements, width)."""
        x, prev, placed = 0.0, None, []
        for ch in text:
            glyph = self.cmap.get(ord(ch))
            if glyph is None:
                raise KeyError(f"{ch!r} is missing from the font")
            if prev:
                x += self.kern(prev, glyph)
            placed.append((glyph, x))
            x += self.hmtx[glyph][0] + tracking * self.upm
            prev = glyph
        return placed, (x - tracking * self.upm if placed else 0.0)

    def outline(self, glyph: str) -> str:
        pen = SVGPathPen(self.glyphs, ntos=fmt)
        self.glyphs[glyph].draw(pen)
        return pen.getCommands()


# --------------------------------------------------------------------------
# Artboard
# --------------------------------------------------------------------------

PULSE_CSS = (
    f".p{{fill:none;stroke:{CORE};stroke-linecap:round;stroke-dasharray:24 976;"
    "animation:run linear infinite}"
    "@keyframes run{0%{stroke-dashoffset:1000;opacity:0}8%{opacity:1}92%{opacity:1}"
    "100%{stroke-dashoffset:0;opacity:0}}"
    "@media (prefers-reduced-motion:reduce){.p{animation:none;opacity:0}}"
)


class Board:
    """An SVG card: obsidian background, hairline border, outlined text."""

    def __init__(self, height: int, label: str):
        self.height = height
        self.label = label
        self.defs: list[str] = []
        self.body: list[str] = []
        self.glyphs: dict[str, str] = {}

    def define(self, svg: str) -> None:
        self.defs.append(svg)

    def draw(self, svg: str) -> None:
        self.body.append(svg)

    def blur(self, fid: str, deviation: float) -> None:
        self.define(
            f'<filter id="{fid}" filterUnits="userSpaceOnUse" x="0" y="0" width="{W}" height="{self.height}">'
            f'<feGaussianBlur stdDeviation="{deviation}"/></filter>'
        )

    def text(self, face: Face, text, x, y, size, fill, tracking=0.0, anchor="start") -> None:
        """Draw `text` as outlines with its baseline at (x, y); each glyph is defined once."""
        placed, width = face.layout(text, tracking)
        scale = size / face.upm
        if anchor == "middle":
            x -= width * scale / 2
        elif anchor == "end":
            x -= width * scale
        uses = []
        for glyph, gx in placed:
            gid = f"{face.key}{face.font.getGlyphID(glyph)}"
            if gid not in self.glyphs:
                self.glyphs[gid] = face.outline(glyph)
            if self.glyphs[gid]:  # spaces have no outline
                uses.append(f'<use href="#{gid}" x="{fmt(gx)}"/>' if gx else f'<use href="#{gid}"/>')
        self.draw(
            f'<g fill="{fill}" transform="translate({fmt(x)} {fmt(y)}) scale({scale:.5g} {-scale:.5g})">'
            f'{"".join(uses)}</g>'
        )

    def render(self) -> str:
        h = self.height
        glyphs = "".join(f'<path id="{gid}" d="{d}"/>' for gid, d in self.glyphs.items() if d)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" role="img" aria-label="{self.label}">'
            f"<style>{PULSE_CSS}</style>"
            f'<defs><clipPath id="frame"><rect width="{W}" height="{h}" rx="18"/></clipPath>'
            f'{"".join(self.defs)}{glyphs}</defs>'
            f'<g clip-path="url(#frame)"><rect width="{W}" height="{h}" fill="{VOID}"/>{"".join(self.body)}</g>'
            f'<rect x=".5" y=".5" width="{W - 1}" height="{h - 1}" rx="17.5" fill="none" '
            f'stroke="{AMBER}" stroke-opacity=".24"/>'
            "</svg>\n"
        )


# --------------------------------------------------------------------------
# Filaments
# --------------------------------------------------------------------------


def smooth(points) -> str:
    """Catmull-Rom spline through `points`, written as cubic Béziers."""
    n = len(points)
    out = [f"M{fmt(points[0][0])} {fmt(points[0][1])}"]
    for i in range(n - 1):
        p0, p1 = points[max(i - 1, 0)], points[i]
        p2, p3 = points[i + 1], points[min(i + 2, n - 1)]
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        if i == 0:
            c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
            out.append(f"C{fmt(c1[0])} {fmt(c1[1])} {fmt(c2[0])} {fmt(c2[1])} {fmt(p2[0])} {fmt(p2[1])}")
        else:  # the first control point mirrors the previous one
            out.append(f"S{fmt(c2[0])} {fmt(c2[1])} {fmt(p2[0])} {fmt(p2[1])}")
    return "".join(out)


def sample(fn, x0: int, x1: int, step: int):
    return [(x, fn(x)) for x in range(x0, x1 + 1, step)]


def ease(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def strand(sid: str, d: str) -> str:
    # pathLength normalises dash patterns so every pulse travels its whole path
    return f'<path id="{sid}" pathLength="1000" d="{d}"/>'


def pulse(ref: str, duration: float, delay: float, width: float = 1.8) -> str:
    return (
        f'<use href="#{ref}" class="p" stroke-width="{fmt(width)}" '
        f'style="animation-duration:{fmt(duration)}s;animation-delay:-{fmt(delay)}s"/>'
    )


def cable(board: Board, prefix, rng, *, count, x0, x1, cy, amp, radius, pulses, step=40, spread=None) -> str:
    """A braided bundle of strands — the same wave maths as the site's canvas scene.

    Registers the strand paths on `board` and returns the drawing.
    """
    soft, lines, cores = [], [], []
    for i in range(count):
        offset = (rng.random() - 0.5) * 2 * radius
        freq = rng.uniform(0.0022, 0.0038)
        phase = i / count * math.pi * 4 + rng.random() * 0.6
        width = 0.7 + rng.random() * 1.2
        alpha = 0.26 + rng.random() * 0.5

        def y(x, offset=offset, freq=freq, phase=phase):
            k = spread((x - x0) / (x1 - x0)) if spread else 1.0
            return (
                cy
                + math.sin(x * freq + phase) * amp * 0.65
                + math.cos(x * 0.0011) * amp * 0.35
                + math.sin(x * 0.0072 + phase) * offset * k
                + math.sin(x * 0.0135 + phase * 2) * 1.6
            )

        sid = f"{prefix}{i}"
        board.define(strand(sid, smooth(sample(y, x0, x1, step))))
        if i % 10 == 0:  # a few bright strands carry the glow
            soft.append(f'<use href="#{sid}"/>')
            cores.append(f'<use href="#{sid}" stroke-width="{fmt(width + 0.8)}"/>')
        else:
            lines.append(f'<use href="#{sid}" stroke-width="{fmt(width)}" stroke-opacity="{alpha:.2f}"/>')
    moving = [pulse(f"{prefix}{i}", rng.uniform(7, 13), rng.uniform(0, 13)) for i in rng.sample(range(count), pulses)]
    return (
        f'<g fill="none" stroke-linecap="round">'
        f'<g stroke="{GOLD}" stroke-width="7" stroke-opacity=".5" filter="url(#soft)">{"".join(soft)}</g>'
        f'<g stroke="{AMBER}">{"".join(lines)}</g>'
        f'<g stroke="{CORE}" stroke-opacity=".85">{"".join(cores)}</g>'
        f'{"".join(moving)}</g>'
    )


# --------------------------------------------------------------------------
# Artboards
# --------------------------------------------------------------------------


def hero(display: Face, body: Face, mono: Face) -> Board:
    board = Board(500, "Nguyen Minh Huy — Fullstack Developer, Ho Chi Minh City")
    cy = 402
    board.blur("soft", 6)
    board.define(
        f'<radialGradient id="halo" cx="50%" cy="50%" r="50%">'
        f'<stop offset="0" stop-color="{GOLD}" stop-opacity=".17"/>'
        f'<stop offset=".5" stop-color="{AMBER}" stop-opacity=".05"/>'
        f'<stop offset="1" stop-color="{AMBER}" stop-opacity="0"/></radialGradient>'
    )
    board.draw(f'<ellipse cx="{W * 0.56:.0f}" cy="{cy}" rx="760" ry="230" fill="url(#halo)"/>')
    board.draw(
        cable(
            board, "s", random.Random(1212),
            count=42, x0=-20, x1=W + 20, cy=cy, amp=24, radius=27, pulses=7,
            spread=lambda t: 0.5 + 0.9 * t,
        )
    )
    board.text(mono, "FULLSTACK DEVELOPER — HO CHI MINH CITY, VN", PAD, 104, 16, GLOW, tracking=0.18)
    board.text(display, "Nguyen Minh Huy", PAD - 4, 208, 100, CREAM, tracking=-0.025)
    board.text(body, "I build fullstack web systems with .NET and Next.js,", PAD, 268, 25, DIM)
    board.text(body, "and wire AI into products that are already running.", PAD, 304, 25, DIM)
    return board


def plate(display, body, mono, *, label, title, summary, aria) -> Board:
    """A project title card: label, title and summary on the left; the motif goes on the right."""
    board = Board(PLATE_H, aria)
    board.blur("soft", 5)
    board.text(mono, label, PAD, 70, 14.5, GLOW, tracking=0.18)
    board.text(display, title, PAD - 3, 148, 66, CREAM, tracking=-0.025)
    for i, line in enumerate(summary):
        board.text(body, line, PAD, 192 + i * 32, 23, DIM)
    return board


def tag(board: Board, mono: Face, text: str, x: float, y: float, fill: str = MUTED, anchor: str = "start"):
    board.text(mono, text, x, y, 13, fill, tracking=0.16, anchor=anchor)


def unagi(display, body, mono) -> Board:
    """Two responders — assistant and staff — twisting into one connection."""
    board = plate(
        display, body, mono,
        label="01 — CLIENT WORK · UNAGI.VN",
        title="UNAGI",
        summary=["Real-time chat & RAG assistant", "for a Japanese food site"],
        aria="UNAGI — real-time chat and RAG assistant for a Japanese food site",
    )
    cy, x0, x1 = 128, 676, W + 20
    period, far, near = 132, 46, 13

    def path(sign):
        def y(x):
            u = x - x0
            reach = near + (far - near) * math.exp(-u / 90)  # settle towards the shared axis
            angle = 2 * math.pi * (u * u / (u + 80)) / period  # twist starts slowly, then runs steady
            return cy + sign * reach * math.cos(angle)

        return smooth(sample(y, x0, x1, 6))

    board.define(strand("ua", path(-1)))
    board.define(strand("ub", path(1)))
    board.draw(
        f'<g fill="none" stroke-linecap="round">'
        f'<g stroke="{GOLD}" stroke-width="6" stroke-opacity=".35" filter="url(#soft)"><use href="#ua"/></g>'
        f'<use href="#ua" stroke="{AMBER}" stroke-width="2"/>'
        f'<use href="#ub" stroke="{DIM}" stroke-width="2" stroke-opacity=".85"/>'
        + pulse("ua", 6.5, 1.5, 2.6)
        + pulse("ub", 8.0, 5.0, 2.6)
        + "</g>"
        f'<circle cx="{x0}" cy="{cy - far}" r="4.5" fill="{AMBER}"/>'
        f'<circle cx="{x0}" cy="{cy + far}" r="4.5" fill="{DIM}"/>'
    )
    tag(board, mono, "ASSISTANT", x0 + 16, cy - far - 14, GLOW)
    tag(board, mono, "STAFF", x0 + 16, cy + far + 24, DIM)
    tag(board, mono, "ONE SIGNALR CONNECTION", W - PAD, cy + 82, anchor="end")
    return board


def bddwriter(display, body, mono) -> Board:
    """One user story becomes many scenarios, which become test code."""
    board = plate(
        display, body, mono,
        label="02 — FINAL-YEAR THESIS · 2025",
        title="BDDWriter",
        summary=["Gherkin scenarios and Cypress code", "from self-hosted LLMs"],
        aria="BDDWriter — Gherkin scenarios and Cypress code from self-hosted LLMs",
    )
    cy, xa, xb, xc, x1 = 128, 676, 872, 1068, W + 20
    fan = 7
    board.define(strand("b0", f"M{xa} {cy}H{xb}"))
    uses = []
    for i in range(fan):
        target = cy + (i - (fan - 1) / 2) * 15

        def y(x, target=target):
            return cy + (target - cy) * ease((x - xb) / (xc - xb))

        board.define(strand(f"b{i + 1}", smooth(sample(y, xb, x1, 14))))
        uses.append(f'<use href="#b{i + 1}" stroke-opacity="{0.9 if i == fan // 2 else 0.55}"/>')
    node = f'fill="{VOID}" stroke="{GLOW}" stroke-width="2"'
    board.draw(
        f'<g fill="none" stroke-linecap="round">'
        f'<g stroke="{GOLD}" stroke-width="6" stroke-opacity=".35" filter="url(#soft)"><use href="#b0"/></g>'
        f'<g stroke="{AMBER}" stroke-width="1.6"><use href="#b0" stroke-width="2"/>{"".join(uses)}</g>'
        + pulse("b0", 4.2, 1.0, 2.6)
        + pulse("b1", 7.5, 2.0, 2.2)
        + pulse("b4", 6.0, 4.4, 2.2)
        + pulse("b6", 8.5, 0.6, 2.2)
        + "</g>"
        f'<circle cx="{xa}" cy="{cy}" r="6" {node}/><circle cx="{xb}" cy="{cy}" r="6" {node}/>'
    )
    tag(board, mono, "USER STORY", xa - 6, cy - 70, GLOW)
    tag(board, mono, "GHERKIN", xb - 6, cy - 70, GLOW)
    tag(board, mono, "CYPRESS", xc - 6, cy - 70, GLOW)
    tag(board, mono, "1 STORY → MANY SCENARIOS → RUNNABLE TESTS", W - PAD, cy + 82, anchor="end")
    return board


def portfolio(display, body, mono) -> Board:
    """The site's own scene: a braided cable of light."""
    board = plate(
        display, body, mono,
        label="03 — PERSONAL · OPEN SOURCE",
        title="Portfolio",
        summary=["Personal site built around", "a canvas-rendered scene"],
        aria="Portfolio — personal site built with Next.js and GSAP",
    )
    cy = 128
    board.define(
        '<linearGradient id="reveal" gradientUnits="userSpaceOnUse" x1="610" x2="820" y1="0" y2="0">'
        '<stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="#fff"/></linearGradient>'
        f'<mask id="emerge" maskUnits="userSpaceOnUse" x="600" y="0" width="640" height="{PLATE_H}">'
        f'<rect x="600" width="640" height="{PLATE_H}" fill="url(#reveal)"/></mask>'
    )
    lines = cable(
        board, "f", random.Random(28),
        count=26, x0=610, x1=W + 20, cy=cy, amp=9, radius=30, pulses=5, step=30,
    )
    board.draw(f'<g mask="url(#emerge)">{lines}</g>')
    tag(board, mono, "CANVAS 2D · GSAP SCROLLTRIGGER · LENIS", W - PAD, cy + 82, anchor="end")
    return board


def main() -> None:
    display = Face("d", "Archivo.ttf", wght=700, wdth=110)
    body = Face("b", "Archivo.ttf", wght=400, wdth=100)
    mono = Face("m", "JetBrainsMono.ttf", wght=500)
    OUT.mkdir(parents=True, exist_ok=True)
    boards = {
        "hero.svg": hero,
        "work-unagi.svg": unagi,
        "work-bddwriter.svg": bddwriter,
        "work-portfolio.svg": portfolio,
    }
    for name, build in boards.items():
        svg = build(display, body, mono).render()
        (OUT / name).write_text(svg, encoding="utf-8", newline="\n")
        print(f"{name:22} {len(svg) / 1024:6.1f} KB")


if __name__ == "__main__":
    main()
