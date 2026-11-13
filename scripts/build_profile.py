#!/usr/bin/env python3
"""Build the SVG artwork used by the profile README.

GitHub renders README images without access to web fonts, so every piece of
text is converted to outlines here. The look follows the portfolio site:
obsidian background, amber filaments, Archivo for display type and
JetBrains Mono for labels (both fonts are OFL-licensed).

Every board comes in a wide version and a narrow one for phones; the README
picks between them with <picture>.

    pip install fonttools
    python scripts/build_profile.py                      # everything
    python scripts/build_profile.py --only contributions # what the daily workflow runs

The contribution calendar is read from the GitHub GraphQL API with the token
in GITHUB_TOKEN / GH_TOKEN, or from `gh auth token` when neither is set.
Fonts are downloaded once into scripts/.cache/ (git-ignored).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import random
import subprocess
import urllib.request
from html import escape
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "profile"
CACHE = Path(__file__).resolve().parent / ".cache"
LOGIN = os.environ.get("PROFILE_LOGIN", "Hutt1212")

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

WIDE, NARROW = 1200, 600

# Same groups and wording as the portfolio site (lib/translations.ts).
STACK = [
    ("Core", ["C#", "ASP.NET Core", "TypeScript", "React", "Next.js", "Node.js", "NestJS"]),
    ("AI & integration", ["SignalR", "Semantic Kernel", "RAG / Vector search", "Python", "LoRA / Fine-tuning"]),
    ("Data", ["SQL Server", "PostgreSQL", "MongoDB", "Redis"]),
    ("Ops & tooling", ["Docker", "CI/CD", "Nginx", "Tailwind CSS", "GSAP"]),
]


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

    def measure(self, text: str, size: float, tracking: float = 0.0) -> float:
        return self.layout(text, tracking)[1] * size / self.upm

    def outline(self, glyph: str) -> str:
        pen = SVGPathPen(self.glyphs, ntos=fmt)
        self.glyphs[glyph].draw(pen)
        return pen.getCommands()


# --------------------------------------------------------------------------
# Artboard
# --------------------------------------------------------------------------

BASE_CSS = (
    f".p{{fill:none;stroke:{CORE};stroke-linecap:round;stroke-dasharray:24 976;"
    "animation:run linear infinite}"
    "@keyframes run{0%{stroke-dashoffset:1000;opacity:0}8%{opacity:1}92%{opacity:1}"
    "100%{stroke-dashoffset:0;opacity:0}}"
    "@keyframes in{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}"
    "@keyframes fade{to{opacity:1}}"
    "@media (prefers-reduced-motion:reduce){.p{animation:none;opacity:0}}"
)


class Board:
    """An SVG card: obsidian background, hairline border, outlined text."""

    def __init__(self, width: int, height: float, label: str, css: str = ""):
        self.width = width
        self.height = round(height)
        self.label = label
        self.css = css
        self.defs: list[str] = []
        self.body: list[str] = []
        self.glyphs: dict[str, str] = {}

    def define(self, svg: str) -> None:
        self.defs.append(svg)

    def draw(self, svg: str) -> None:
        self.body.append(svg)

    def blur(self, fid: str, deviation: float) -> None:
        self.define(
            f'<filter id="{fid}" filterUnits="userSpaceOnUse" x="0" y="0" '
            f'width="{self.width}" height="{self.height}">'
            f'<feGaussianBlur stdDeviation="{deviation}"/></filter>'
        )

    def run(self, face: Face, text, x, y, size, fill, tracking=0.0, anchor="start") -> str:
        """`text` as outlines with its baseline at (x, y); each glyph is defined once per board."""
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
        return (
            f'<g fill="{fill}" transform="translate({fmt(x)} {fmt(y)}) scale({scale:.5g} {-scale:.5g})">'
            f'{"".join(uses)}</g>'
        )

    def text(self, *args, **kwargs) -> None:
        self.draw(self.run(*args, **kwargs))

    def render(self) -> str:
        w, h = self.width, self.height
        glyphs = "".join(f'<path id="{gid}" d="{d}"/>' for gid, d in self.glyphs.items() if d)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" role="img" aria-label="{escape(self.label)}">'
            f"<style>{BASE_CSS}{self.css}</style>"
            f'<defs><clipPath id="frame"><rect width="{w}" height="{h}" rx="18"/></clipPath>'
            f'{"".join(self.defs)}{glyphs}</defs>'
            f'<g clip-path="url(#frame)"><rect width="{w}" height="{h}" fill="{VOID}"/>{"".join(self.body)}</g>'
            f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="17.5" fill="none" '
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


def strand(sid: str, d: str) -> str:
    # pathLength normalises dash patterns so every pulse travels its whole path
    return f'<path id="{sid}" pathLength="1000" d="{d}"/>'


def pulse(ref: str, duration: float, delay: float, width: float = 1.8) -> str:
    return (
        f'<use href="#{ref}" class="p" stroke-width="{fmt(width)}" '
        f'style="animation-duration:{fmt(duration)}s;animation-delay:-{fmt(delay)}s"/>'
    )


def cable(board: Board, prefix, rng, *, count, x0, x1, cy, amp, radius, pulses, step=40, spread=None) -> str:
    """A braided bundle of strands — the same wave maths as the site's canvas scene."""
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
# Hero — role and one-line introduction
# --------------------------------------------------------------------------


def hero(f, narrow: bool) -> Board:
    display, body, mono = f
    if narrow:
        width, pad, height, cy = NARROW, 36, 420, 338
        label, name, lines = (12.5, 62), (58, 132), (21, 180, 30)
        copy = ["I build fullstack web systems", "with .NET and Next.js, and wire AI", "into products that are already running."]
    else:
        width, pad, height, cy = WIDE, 72, 500, 402
        label, name, lines = (16, 104), (100, 208), (25, 268, 36)
        copy = ["I build fullstack web systems with .NET and Next.js,", "and wire AI into products that are already running."]
    board = Board(width, height, "Nguyen Minh Huy — Fullstack Developer, Ho Chi Minh City")
    board.blur("soft", 6)
    board.define(
        f'<radialGradient id="halo" cx="50%" cy="50%" r="50%">'
        f'<stop offset="0" stop-color="{GOLD}" stop-opacity=".17"/>'
        f'<stop offset=".5" stop-color="{AMBER}" stop-opacity=".05"/>'
        f'<stop offset="1" stop-color="{AMBER}" stop-opacity="0"/></radialGradient>'
    )
    board.draw(
        f'<ellipse cx="{width * 0.56:.0f}" cy="{cy}" rx="{width * 0.63:.0f}" ry="{height * 0.46:.0f}" fill="url(#halo)"/>'
    )
    board.draw(
        cable(
            board, "s", random.Random(1212),
            count=42 if not narrow else 30, x0=-20, x1=width + 20, cy=cy,
            amp=24 if not narrow else 18, radius=27 if not narrow else 22, pulses=7 if not narrow else 5,
            spread=lambda t: 0.5 + 0.9 * t,
        )
    )
    board.text(mono, "FULLSTACK DEVELOPER — HO CHI MINH CITY, VN", pad, label[1], label[0], GLOW, tracking=0.18)
    board.text(display, "Nguyen Minh Huy", pad - name[0] * 0.04, name[1], name[0], CREAM, tracking=-0.025)
    size, top, gap = lines
    for i, line in enumerate(copy):
        board.text(body, line, pad, top + i * gap, size, DIM)
    return board


# --------------------------------------------------------------------------
# Tech stack — chips with a light running across them
# --------------------------------------------------------------------------

TECH_CSS = (
    ".k{opacity:0;animation:in .7s cubic-bezier(.2,.7,.2,1) forwards}"
    ".k rect{animation:ring 9s ease-in-out infinite}"
    f"@keyframes ring{{0%,9%,100%{{stroke:{AMBER};stroke-opacity:.3;fill:#100A05}}"
    f"4%{{stroke:{GOLD};stroke-opacity:1;fill:#2A1604}}}}"
    "@media (prefers-reduced-motion:reduce){.k{animation:none;opacity:1}.k rect{animation:none}}"
)


def tech(f, narrow: bool) -> Board:
    display, body, mono = f
    if narrow:
        width, pad, size, chip_h, head = NARROW, 32, 20, 44, 58
    else:
        width, pad, size, chip_h, head = WIDE, 72, 18, 40, 70
    chip_x0 = pad if narrow else pad + 232
    chip_x1 = width - pad
    inset, gap = (18, 10) if narrow else (16, 9)

    # Lay everything out first so the board height is known.
    rows, y = [], head + 34
    for gi, (group, items) in enumerate(STACK):
        top = y
        cy = y + (40 if narrow else 24)  # first chip row
        x, chips = chip_x0, []
        for item in items:
            w = body.measure(item, size) + 2 * inset
            if x + w > chip_x1 and x > chip_x0:
                x, cy = chip_x0, cy + chip_h + gap
            chips.append((item, x, cy, w))
            x += w + gap
        y = cy + chip_h + 24
        rows.append((gi, group, top, chips))
    height = y + (12 if narrow else 20)

    board = Board(
        width, height,
        "Tech stack — " + "; ".join(f"{g}: {', '.join(items)}" for g, items in STACK),
        TECH_CSS,
    )
    board.blur("soft", 4)
    board.text(mono, "TECH STACK", pad, head, 14.5 if not narrow else 12.5, GLOW, tracking=0.18)
    if not narrow:
        board.text(mono, "WHAT I BUILD WITH, DAY TO DAY", width - pad, head, 13, MUTED, tracking=0.16, anchor="end")

    n = 0
    for gi, group, top, chips in rows:
        rid = f"r{gi}"
        board.define(strand(rid, f"M{pad} {top}H{width - pad}"))
        board.draw(
            f'<use href="#{rid}" fill="none" stroke="{AMBER}" stroke-opacity=".16"/>'
            + pulse(rid, 9 + gi * 1.3, gi * 2.1, 1.6)
        )
        label_y = top + 28 if narrow else chips[0][2] + chip_h / 2 + 5
        board.text(mono, f"0{gi + 1}", pad, label_y, 13, MUTED, tracking=0.12)
        board.text(mono, group.upper(), pad + 34, label_y, 13 if not narrow else 12.5, GLOW, tracking=0.16)
        for item, x, cy, w in chips:
            board.draw(
                f'<g class="k" style="animation-delay:{0.15 + n * 0.05:.2f}s">'
                f'<rect x="{fmt(x)}" y="{fmt(cy)}" width="{fmt(w)}" height="{chip_h}" rx="{chip_h / 2:g}" '
                f'fill="#100A05" stroke="{AMBER}" stroke-opacity=".3" style="animation-delay:{1.2 + n * 0.32:.2f}s"/>'
                + board.run(body, item, x + inset, cy + chip_h / 2 + size * 0.36, size, DIM)
                + "</g>"
            )
            n += 1
    return board


# --------------------------------------------------------------------------
# Contributions — calendar, weekly filament and a light sweeping the grid
# --------------------------------------------------------------------------

LEVELS = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2, "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}


def fetch_calendar(login: str) -> dict:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        token = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip()
    query = (
        "query($login:String!){user(login:$login){contributionsCollection{contributionCalendar{"
        "totalContributions weeks{contributionDays{date contributionCount contributionLevel}}}}}}"
    )
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": {"login": login}}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json", "User-Agent": "profile-build"},
    )
    with urllib.request.urlopen(request) as response:
        payload = json.load(response)
    if "errors" in payload:
        raise RuntimeError(payload["errors"])
    return payload["data"]["user"]["contributionsCollection"]["contributionCalendar"]


def stats(calendar: dict) -> dict:
    days = [d for w in calendar["weeks"] for d in w["contributionDays"]]
    longest = current = 0
    for d in days:
        current = current + 1 if d["contributionCount"] else 0
        longest = max(longest, current)
    return {
        "total": calendar["totalContributions"],
        "active": sum(1 for d in days if d["contributionCount"]),
        "longest": longest,
        "best": max(d["contributionCount"] for d in days),
    }


def contributions_css(sweep: float) -> str:
    return (
        ".col{opacity:0;animation:in .5s ease-out forwards}"
        ".l0{fill:#17110B}.l1{fill:#4A2806}.l2{fill:#8F4C05}.l3{fill:#E58200}.l4{fill:#FFC64A}"
        ".draw{stroke-dasharray:1000;stroke-dashoffset:1000;animation:draw 2.6s cubic-bezier(.3,.6,.2,1) .3s forwards}"
        ".area{opacity:0;animation:fade 1.4s ease-out 1.6s forwards}"
        ".sw{animation:sweep 6.5s cubic-bezier(.45,0,.55,1) 1.8s infinite}"
        ".hot{animation:tw 2.8s ease-in-out infinite}"
        "@keyframes draw{to{stroke-dashoffset:0}}"
        f"@keyframes sweep{{0%{{transform:translateX(0)}}70%,100%{{transform:translateX({sweep:.0f}px)}}}}"
        "@keyframes tw{0%,100%{opacity:.35}50%{opacity:1}}"
        "@media (prefers-reduced-motion:reduce){.col,.area{animation:none;opacity:1}"
        ".draw{animation:none;stroke-dashoffset:0}.sw{animation:none;opacity:0}.hot{animation:none}}"
    )


def contributions(f, calendar: dict, narrow: bool) -> Board:
    display, body, mono = f
    s = stats(calendar)
    figures = [
        (f"{s['total']:,}", "CONTRIBUTIONS"),
        (f"{s['active']}", "ACTIVE DAYS"),
        (f"{s['longest']}", "LONGEST STREAK, DAYS"),
        (f"{s['best']}", "BEST DAY"),
    ]
    if narrow:
        weeks = calendar["weeks"][-26:]
        width, pad = NARROW, 32
        head, chart_top, chart_h = 58, 236, 64
        slots = [(pad + (i % 2) * 270, 120 + (i // 2) * 74) for i in range(4)]
        value_size, caption_size = 36, 11
    else:
        weeks = calendar["weeks"]
        width, pad = WIDE, 72
        head, chart_top, chart_h = 70, 196, 76
        slots = [(pad + i * 262, 138) for i in range(4)]
        value_size, caption_size = 48, 12

    pitch = (width - 2 * pad + 4) / len(weeks)
    cell = pitch - 4
    grid_top = chart_top + chart_h + 26
    grid_bottom = grid_top + 7 * pitch - 4
    height = grid_bottom + 58
    sweep_w = 260 if not narrow else 160
    board = Board(
        width, height,
        f"GitHub contributions for @{LOGIN}: {s['total']:,} in the last year, {s['active']} active days, "
        f"longest streak {s['longest']} days, best day {s['best']}",
        contributions_css(width - 2 * pad + 2 * sweep_w),
    )
    board.blur("soft", 5)
    board.blur("halo", 3)

    # Header and figures
    title = "CONTRIBUTIONS — LAST 12 MONTHS" if not narrow else "CONTRIBUTIONS · 12 MONTHS"
    board.text(mono, title, pad, head, 14.5 if not narrow else 12.5, GLOW, tracking=0.18)
    more = mono.measure("MORE", 11, 0.12)
    sx = width - pad - more - 10 - (5 * 18 - 5)
    board.draw("".join(
        f'<rect x="{fmt(sx + i * 18)}" y="{fmt(head - 11)}" width="13" height="13" rx="2.5" class="l{i}"/>'
        for i in range(5)
    ))
    board.text(mono, "LESS", sx - 10, head, 11, MUTED, tracking=0.12, anchor="end")
    board.text(mono, "MORE", width - pad, head, 11, MUTED, tracking=0.12, anchor="end")
    for i, ((value, caption), (x, y)) in enumerate(zip(figures, slots)):
        board.draw(
            f'<g class="col" style="animation-delay:{0.1 + i * 0.12:.2f}s">'
            + board.run(display, value, x - value_size * 0.03, y, value_size, CREAM, tracking=-0.02)
            + board.run(mono, caption, x, y + 26, caption_size, MUTED, tracking=0.16)
            + "</g>"
        )

    # Weekly totals as a filament that draws itself in, then carries a pulse
    totals = [sum(d["contributionCount"] for d in w["contributionDays"]) for w in weeks]
    peak = max(totals) or 1
    xs = [pad + j * pitch + cell / 2 for j in range(len(weeks))]
    base = chart_top + chart_h
    points = [(x, base - (t / peak) ** 0.75 * chart_h) for x, t in zip(xs, totals)]
    line = smooth(points)
    board.define(strand("wk", line))
    board.define(
        '<linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{AMBER}" stop-opacity=".28"/>'
        f'<stop offset="1" stop-color="{AMBER}" stop-opacity="0"/></linearGradient>'
        '<linearGradient id="band" x1="0" y1="0" x2="1" y2="0">'
        f'<stop offset="0" stop-color="{CORE}" stop-opacity="0"/>'
        f'<stop offset=".5" stop-color="{CORE}" stop-opacity=".9"/>'
        f'<stop offset="1" stop-color="{CORE}" stop-opacity="0"/></linearGradient>'
    )
    board.draw(
        f'<path class="area" fill="url(#fill)" d="{line}L{fmt(xs[-1])} {fmt(base)}L{fmt(xs[0])} {fmt(base)}Z"/>'
        f'<g fill="none" stroke-linecap="round">'
        f'<use href="#wk" class="draw" stroke="{GOLD}" stroke-width="6" stroke-opacity=".45" filter="url(#soft)"/>'
        f'<use href="#wk" class="draw" stroke="{GLOW}" stroke-width="2"/>'
        + pulse("wk", 6.5, 0, 2.8)
        + pulse("wk", 6.5, 3.25, 2.8)
        + "</g>"
    )
    best_week = max(range(len(totals)), key=totals.__getitem__)
    bx, by = points[best_week]
    board.draw(
        f'<g class="area"><circle cx="{fmt(bx)}" cy="{fmt(by)}" r="9" fill="{GOLD}" fill-opacity=".25" class="hot"/>'
        f'<circle cx="{fmt(bx)}" cy="{fmt(by)}" r="3.5" fill="{CORE}"/></g>'
    )

    # Calendar grid: columns fade in left to right; active days catch a sweeping light
    columns, active, hot = [], [], []
    for j, week in enumerate(weeks):
        cells = []
        for day in week["contributionDays"]:
            date = dt.date.fromisoformat(day["date"])
            row = (date.weekday() + 1) % 7  # Sunday first, like GitHub
            x, y = pad + j * pitch, grid_top + row * pitch
            rect = f'x="{fmt(x)}" y="{fmt(y)}" width="{fmt(cell)}" height="{fmt(cell)}" rx="3"'
            level = LEVELS[day["contributionLevel"]]
            cells.append(f'<rect {rect} class="l{level}"/>')
            if level:
                active.append(f"<rect {rect}/>")
            if level == 4:
                hot.append(f'<rect {rect} style="animation-delay:-{(j * 0.37) % 2.8:.2f}s"/>')
        columns.append(f'<g class="col" style="animation-delay:{0.5 + j * 0.024:.3f}s">{"".join(cells)}</g>')
    board.define(f'<mask id="lit" maskUnits="userSpaceOnUse" x="0" y="0" width="{width}" height="{board.height}">'
                 f'<g fill="#fff">{"".join(active)}</g></mask>')
    board.draw(f'<g class="hot" fill="{GOLD}" fill-opacity=".55" filter="url(#halo)">{"".join(hot)}</g>')
    board.draw("".join(columns))
    board.draw(
        f'<g mask="url(#lit)"><rect class="sw" x="{fmt(pad - sweep_w)}" y="{fmt(grid_top)}" '
        f'width="{sweep_w}" height="{fmt(grid_bottom - grid_top)}" fill="url(#band)"/></g>'
    )

    # Month labels under the first column of each month
    last = -9
    for j, week in enumerate(weeks):
        first = dt.date.fromisoformat(week["contributionDays"][0]["date"])
        prev = dt.date.fromisoformat(weeks[j - 1]["contributionDays"][0]["date"]) if j else None
        if (prev is None or prev.month != first.month) and j - last >= 3 and j <= len(weeks) - 2:
            label = (first if prev is None else first.replace(day=1) if first.day <= 7 else first).strftime("%b")
            board.text(mono, label.upper(), pad + j * pitch, grid_bottom + 26, 11.5, MUTED, tracking=0.12)
            last = j

    return board


# --------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", choices=["all", "contributions"], default="all")
    args = parser.parse_args()

    faces = (
        Face("d", "Archivo.ttf", wght=700, wdth=110),
        Face("b", "Archivo.ttf", wght=400, wdth=100),
        Face("m", "JetBrainsMono.ttf", wght=500),
    )
    calendar = fetch_calendar(LOGIN)
    boards = {"contributions": lambda narrow: contributions(faces, calendar, narrow)}
    if args.only == "all":
        boards = {
            "hero": lambda narrow: hero(faces, narrow),
            "tech": lambda narrow: tech(faces, narrow),
            **boards,
        }
    OUT.mkdir(parents=True, exist_ok=True)
    for name, build in boards.items():
        for narrow in (False, True):
            file = f"{name}-narrow.svg" if narrow else f"{name}.svg"
            svg = build(narrow).render()
            (OUT / file).write_text(svg, encoding="utf-8", newline="\n")
            print(f"{file:26} {len(svg) / 1024:6.1f} KB")


if __name__ == "__main__":
    main()
