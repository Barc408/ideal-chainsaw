"""Measure real distances off the drawings.

The sheets say "DO NOT SCALE DRAWINGS". That is a professional disclaimer about
print distortion and drafting tolerance, and it is the architect's position -
but it is not a statement that the geometry is missing. These are vector
drawings: the linework carries true coordinates, and at a known scale those
coordinates are lengths.

What this module does and does not do matters.

It establishes the scale, and it does so by measurement rather than assumption:
the elevation datums are labelled at known heights, so the distance between two
of them in points divided by their difference in feet is the scale. On the
reference set three independent datum pairs agree at 18.00 pt/ft exactly, and
the shared column grid spans an identical 1193 pt on the architect's and the
engineer's sheets, confirming both offices plotted at the same scale.

It does not try to decide which linework constitutes a given member. That was
attempted and abandoned: matching a beam to the nearest line of the right
orientation returned a 3 ft tick mark for a porch beam, and matching a beam to
a pair of lines its own width apart produced a false positive at 4.88" for a
member specified at 5.5". A measurement system that confidently returns a wrong
length is worse than one that returns nothing, so identification is left to a
person and this module gives them a calibrated ruler and the grid to use it
against.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .pdfdoc import PlanSet, Sheet

# Elevation datums, with the height each represents in feet. Read off the
# labels themselves rather than hardcoded where possible.
DATUM = re.compile(r"^(Roof|T\.O\.P\.|Floor|Patio|Grade)$")

# 14'-6 1/2", 9'-0", 0'-1"
HEIGHT = re.compile(r"(\d+)'\s*-?\s*(\d+)?\s*(?:(\d+)/(\d+))?\s*\"?")


@dataclass
class Scale:
    points_per_foot: float
    method: str
    #: How many independent checks agreed, and how far apart they were.
    agreement: str = ""
    verified: bool = False

    def to_feet(self, points: float) -> float:
        return points / self.points_per_foot

    def describe(self) -> str:
        # pt/ft -> inches of paper per foot -> the "1/n" the title block states.
        denom = 72.0 / self.points_per_foot
        return (
            f'{self.points_per_foot:.2f} pt/ft (1/{denom:.0f}" = 1\'-0") '
            f"via {self.method}"
            + (f"; {self.agreement}" if self.agreement else "")
        )


@dataclass
class GridLine:
    label: str
    position_pt: float
    axis: str  # "x" for numbered lines, "y" for lettered


@dataclass
class Grid:
    sheet_id: str | None
    lines: list[GridLine] = field(default_factory=list)

    def axis(self, axis: str) -> list[GridLine]:
        return sorted(
            (g for g in self.lines if g.axis == axis), key=lambda g: g.position_pt
        )

    def bays(self, axis: str, scale: Scale) -> list[tuple[str, str, float]]:
        """Centre-to-centre spacing of adjacent grid lines, in feet."""
        seq = self.axis(axis)
        return [
            (a.label, b.label, scale.to_feet(b.position_pt - a.position_pt))
            for a, b in zip(seq, seq[1:])
        ]

    def span(self, axis: str, scale: Scale) -> float | None:
        seq = self.axis(axis)
        if len(seq) < 2:
            return None
        return scale.to_feet(seq[-1].position_pt - seq[0].position_pt)


def _height_ft(text: str) -> float | None:
    m = HEIGHT.search(text)
    if not m:
        return None
    ft = int(m.group(1))
    inches = int(m.group(2)) if m.group(2) else 0
    if m.group(3) and m.group(4):
        inches += int(m.group(3)) / int(m.group(4))
    return ft + inches / 12


def calibrate(plan: PlanSet, elevation_sheet: str = "A-6.1") -> Scale:
    """Derive pt/ft from labelled elevation datums.

    Each datum label sits beside a horizontal reference line at a stated
    height, so any two of them give the scale. Pairs are cross-checked against
    each other because some labels are nudged off their own line to stop them
    colliding - on the reference set the Floor and Grade labels are displaced,
    and taking either alone would give a scale 10-20% wrong.
    """
    sheet = plan.sheet(elevation_sheet)
    if sheet is None or sheet.is_raster:
        return Scale(18.0, "assumed 1/4\" = 1'-0\" (no elevation sheet to verify)")

    # The height is printed on the line directly beneath its datum label.
    found: list[tuple[float, float, float]] = []  # (elevation ft, y, x)
    for w in sheet.words:
        if not DATUM.match(w.text):
            continue
        below = [
            v
            for v in sheet.words
            if 4 < v.cy - w.cy < 22 and v.x2 > w.x - 40 and v.x < w.x2 + 40
        ]
        text = " ".join(v.text for v in sorted(below, key=lambda v: v.x))
        ft = _height_ft(text)
        if ft is not None:
            found.append((ft, w.cy, w.cx))

    # An elevation sheet carries several elevations, each with its own datum
    # stack at a similar x. Pairing across two stacks compares unrelated points,
    # so pairs are formed only within a stack - and only where the label order
    # down the page matches the elevation order, which rejects a pairing that
    # has strayed into a neighbouring stack.
    ratios: list[float] = []
    for i, (e1, y1, x1) in enumerate(found):
        for e2, y2, x2 in found[i + 1 :]:
            if abs(x1 - x2) > 40:
                continue
            de, dy = e1 - e2, y1 - y2
            # Higher elevation must sit higher on the page (smaller y).
            if abs(de) <= 0.5 or abs(dy) <= 5 or (de > 0) == (dy > 0):
                continue
            ratios.append(abs(dy) / abs(de))

    if not ratios:
        return Scale(18.0, "assumed 1/4\" = 1'-0\" (no datum pair readable)")

    # Displaced labels produce outliers, so take the value the most pairs agree
    # on rather than an average, which the outliers would drag.
    buckets: dict[float, int] = {}
    for r in ratios:
        key = round(r, 1)
        buckets[key] = buckets.get(key, 0) + 1
    best, votes = max(buckets.items(), key=lambda kv: kv[1])
    tight = [r for r in ratios if abs(r - best) < 0.05]
    value = sum(tight) / len(tight)

    return Scale(
        points_per_foot=value,
        method=f"labelled elevation datums on {elevation_sheet}",
        agreement=f"{len(tight)} of {len(ratios)} datum pairs agree within 0.05 pt/ft",
        verified=len(tight) >= 2,
    )


def cross_check(plan: PlanSet, scale: Scale, sheet_ids: tuple[str, ...]) -> str:
    """Confirm sheets share a scale by comparing their common column grid.

    The architect's and the engineer's sheets are separate drawings that happen
    to describe the same building. If their shared grid spans the same distance
    in points, they were plotted at the same scale and one calibration serves
    both.
    """
    spans: dict[str, float] = {}
    for sid in sheet_ids:
        grid = read_grid(plan, sid)
        seq = grid.axis("x")
        if len(seq) >= 2:
            spans[sid] = seq[-1].position_pt - seq[0].position_pt
    if len(spans) < 2:
        return ""
    lo, hi = min(spans.values()), max(spans.values())
    if hi - lo < 2:
        return (
            f"grid span identical across {', '.join(spans)} ({hi:.0f} pt = "
            f"{scale.to_feet(hi):.1f} ft) - one scale serves all"
        )
    return (
        f"WARNING: grid span differs across sheets ({lo:.0f}-{hi:.0f} pt); "
        "they are not at a common scale"
    )


def read_grid(plan: PlanSet, sheet_id: str) -> Grid:
    """Locate the numbered and lettered column grid.

    Grid bubbles sit outside the plan: numbers along the top, letters down the
    side. They are the reference an estimator already uses to describe a
    location, which makes them the right thing to report distances against.
    """
    sheet = plan.sheet(sheet_id)
    grid = Grid(sheet_id=sheet_id)
    if sheet is None or sheet.is_raster:
        return grid

    # Single digits and letters occur all over a drawing - in dimensions, in
    # detail bubbles, in schedules. What distinguishes the grid is that its
    # bubbles line up: the numbers share a y, the letters share an x. So the
    # right row is the one holding the most distinct labels, not the first
    # match encountered.
    numbers = [w for w in sheet.drawing_words if re.fullmatch(r"[1-9]", w.text)]
    letters = [w for w in sheet.drawing_words if re.fullmatch(r"[A-H]", w.text)]

    for words, axis, along, across in (
        (numbers, "x", lambda w: w.cx, lambda w: w.cy),
        (letters, "y", lambda w: w.cy, lambda w: w.cx),
    ):
        bands: list[list] = []
        for w in sorted(words, key=across):
            if bands and abs(across(bands[-1][-1]) - across(w)) < 12:
                bands[-1].append(w)
            else:
                bands.append([w])
        if not bands:
            continue
        band = max(bands, key=lambda b: len({w.text for w in b}))
        if len({w.text for w in band}) < 3:
            continue
        placed: dict[str, float] = {}
        for w in sorted(band, key=along):
            placed.setdefault(w.text, along(w))
        for label, pos in placed.items():
            grid.lines.append(GridLine(label=label, position_pt=pos, axis=axis))
    return grid


def distance_ft(scale: Scale, x0: float, y0: float, x1: float, y1: float) -> float:
    """Straight-line distance between two points on a sheet, in feet."""
    return scale.to_feet(math.hypot(x1 - x0, y1 - y0))
