"""Stage 3: the measurements a takeoff needs and the drawings may not carry.

Schedules give member sizes; they do not give lengths, areas or counts. Some of
those are dimensioned on the sheets and some are not - this set carries almost
no dimensions for the patio structure and states plainly "DO NOT SCALE
DRAWINGS" and "ALL DIMENSIONS REFER TO ARCHITECTURAL DRAWINGS".

Numbers the architect did not draw cannot be recovered by reading harder. This
module therefore states what is needed, offers whatever dimension strings do
appear on the relevant sheets as candidates, and leaves the value unset until a
person supplies it. A takeoff missing a required measurement does not run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pdfdoc import PlanSet

# 9'-7", 13'-5 1/2", 24'-0"
DIMENSION = re.compile(r"\d+\s*'\s*-?\s*(?:\d+(?:\s+\d+/\d+)?\s*\")?")


@dataclass
class Measurement:
    key: str
    label: str
    unit: str
    why: str
    sheets: tuple[str, ...]
    candidates: list[str] = field(default_factory=list)
    value: float | None = None

    @property
    def satisfied(self) -> bool:
        return self.value is not None


@dataclass
class GeometryRequest:
    measurements: list[Measurement]

    @property
    def outstanding(self) -> list[Measurement]:
        return [m for m in self.measurements if not m.satisfied]

    @property
    def complete(self) -> bool:
        return not self.outstanding

    def apply(self, values: dict[str, float]) -> None:
        for m in self.measurements:
            if m.key in values:
                m.value = float(values[m.key])


# What the framing rules need before they can produce quantities. Each entry
# names the sheets an estimator should read it off.
REQUIRED = (
    ("roof_area_sf", "New roof plane area", "sf",
     "Sets sheathing sheets, underlayment and shingle squares.", ("A-5.1", "S3.0")),
    ("ridge_length_ft", "Ridge length", "ft",
     "Ridge beam stock length; RB1 is called out as one continuous piece.",
     ("S3.0", "A-5.1")),
    ("rafter_run_ft", "Rafter run (ridge to bearing, horizontal)", "ft",
     "With pitch, gives rafter length; drives count against 24\" OC spacing.",
     ("S3.0", "A-7.0")),
    ("roof_perimeter_ft", "Eave + rake perimeter", "ft",
     "Fascia, gutter and eave blocking quantities.", ("A-5.1",)),
    ("post_count", "New post count", "each",
     "Posts, bases, caps and pad footings, per the column schedule.",
     ("S1.0", "S3.0")),
    ("post_height_ft", "Post height, bottom of beam to pad", "ft",
     "Post stock length.", ("A-7.0", "A-6.1")),
    ("new_wall_lf", "New wall length", "lf",
     "Studs, plates and wall sheathing. Zero is a valid answer on this scope.",
     ("A-3.1", "S1.0")),
    ("wall_height_ft", "Wall height, plate to plate", "ft",
     "Stud length and blocking rows.", ("A-7.0",)),
)


def harvest_dimensions(plan: PlanSet, sheet_ids: tuple[str, ...]) -> list[str]:
    """Collect dimension strings printed on the given sheets.

    Offered as candidates only. A dimension on a sheet is not necessarily the
    dimension being asked for, so nothing here is used automatically.
    """
    found: list[str] = []
    for sid in sheet_ids:
        sheet = plan.sheet(sid)
        if sheet is None or sheet.is_raster:
            continue
        # Dimensions are frequently split across words ("9'" then "- 7\""), so
        # match against the reconstructed line rather than single tokens.
        #
        # Only the drawing itself is searched. The right-hand strip carries the
        # schedules and keynotes, whose span limits ("UP TO 10'-8\"") read as
        # dimensions but describe a lookup table, not this building.
        by_line: dict[int, list] = {}
        for w in sheet.drawing_words:
            if w.cx > sheet.width * 0.72:
                continue
            by_line.setdefault(int(w.cy // 6), []).append(w)
        for _, ws in by_line.items():
            line = " ".join(w.text for w in sorted(ws, key=lambda w: w.x))
            for hit in DIMENSION.findall(line):
                hit = hit.strip()
                if hit and hit not in found:
                    found.append(hit)
    return found


def required(plan: PlanSet) -> GeometryRequest:
    measurements = [
        Measurement(
            key=key,
            label=label,
            unit=unit,
            why=why,
            sheets=sheets,
            candidates=harvest_dimensions(plan, sheets)[:12],
        )
        for key, label, unit, why, sheets in REQUIRED
    ]
    return GeometryRequest(measurements=measurements)
