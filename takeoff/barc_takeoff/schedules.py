"""Typed access to the schedules that drive a framing takeoff.

Each entry says where to look and how far to read. Extraction stays generic;
this module is the part that changes when an office lays its sheets out
differently, which keeps office-specific knowledge in one place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pdfdoc import PlanSet, Sheet
from .tables import Matrix, Table, extract, extract_matrix

# Every schedule heading on the structural sheets. Any one of them terminates
# the table above it, so they double as stop markers for each other.
STRUCTURAL_SCHEDULES = (
    "CEILING JOIST SCHEDULE",
    "HEADER SCHEDULE",
    "SHEARWALL SCHEDULE",
    "COLUMN CONNECTOR SCHEDULE",
    "COLUMN SCHEDULE",
    "PAD SCHEDULE",
)
_STOP = STRUCTURAL_SCHEDULES + ("SYMBOL LEGEND", "LINETYPE/LINEWEIGHT LEGEND", "NOTES")


@dataclass(frozen=True)
class HeaderSpec:
    """One row of the header schedule: opening span -> framing to build it."""

    max_span_in: float
    span_label: str
    width: str
    depth: str
    studs: int
    jacks: int


@dataclass(frozen=True)
class JoistSpec:
    type: str
    max_span_in: float
    span_label: str
    size: str
    spacing: str
    hanger: str


@dataclass(frozen=True)
class PadSpec:
    name: str
    width_in: float
    length_in: float
    depth_in: float
    rebar: str


@dataclass(frozen=True)
class ConnectorSpec:
    post: str
    base: str
    cap: str


@dataclass
class Schedules:
    headers: list[HeaderSpec] = field(default_factory=list)
    joists: list[JoistSpec] = field(default_factory=list)
    pads: list[PadSpec] = field(default_factory=list)
    connectors: list[ConnectorSpec] = field(default_factory=list)
    shearwalls: Matrix | None = None
    raw: dict[str, Table] = field(default_factory=dict)
    source_sheet: str | None = None

    def header_for(self, span_in: float) -> HeaderSpec | None:
        """Smallest header that carries `span_in`."""
        for spec in sorted(self.headers, key=lambda h: h.max_span_in):
            if span_in <= spec.max_span_in:
                return spec
        return None

    def joist_for(self, span_in: float) -> JoistSpec | None:
        for spec in sorted(self.joists, key=lambda j: j.max_span_in):
            if span_in <= spec.max_span_in:
                return spec
        return None


# 6'-1", 7'-3", 3', 16'-4" -> inches
_FEET_INCHES = re.compile(r"(\d+)\s*'(?:\s*-?\s*(\d+)\s*\")?")


def feet_inches_to_inches(text: str) -> float | None:
    m = _FEET_INCHES.search(text)
    if not m:
        return None
    feet = int(m.group(1))
    inches = int(m.group(2)) if m.group(2) else 0
    return feet * 12 + inches


def _stops(exclude: str) -> tuple[str, ...]:
    return tuple(s for s in _STOP if s != exclude)


def _parse_headers(t: Table) -> list[HeaderSpec]:
    out = []
    for row in t.body:
        if len(row) < 5 or "TO" in row[0].upper() and len(row) < 5:
            continue
        span = row[0]
        # "UP TO 3'" has one bound; "3'-1\" TO 4'" has two and the upper governs.
        bounds = _FEET_INCHES.findall(span)
        if not bounds:
            continue
        feet, inches = bounds[-1]
        limit = int(feet) * 12 + (int(inches) if inches else 0)
        try:
            studs, jacks = int(row[-2]), int(row[-1])
        except ValueError:
            continue
        out.append(
            HeaderSpec(
                max_span_in=limit,
                span_label=span,
                width=row[1],
                depth=row[2],
                studs=studs,
                jacks=jacks,
            )
        )
    return out


def _parse_joists(t: Table) -> list[JoistSpec]:
    out = []
    for row in t.body:
        if len(row) < 5 or len(row[0]) != 1 or not row[0].isalpha():
            continue
        limit = feet_inches_to_inches(row[1])
        if limit is None:
            continue
        out.append(
            JoistSpec(
                type=row[0],
                max_span_in=limit,
                span_label=row[1],
                size=row[2],
                spacing=row[3],
                hanger=row[4] if len(row) > 4 else "",
            )
        )
    return out


_PAD_DIMS = re.compile(r'(\d+)"?x(\d+)"?x(\d+)"?D?', re.I)


def _parse_pads(t: Table) -> list[PadSpec]:
    out = []
    for row in t.body:
        if len(row) < 2:
            continue
        m = _PAD_DIMS.search(row[0])
        if not m:
            continue
        out.append(
            PadSpec(
                name=row[0][: m.start()].strip() or row[0],
                width_in=float(m.group(1)),
                length_in=float(m.group(2)),
                depth_in=float(m.group(3)),
                rebar=row[1],
            )
        )
    return out


def _parse_connectors(t: Table) -> list[ConnectorSpec]:
    out = []
    for row in t.body:
        if len(row) < 3:
            continue
        out.append(ConnectorSpec(post=row[0], base=row[1], cap=row[2]))
    return out


def read(plan: PlanSet, sheet_id: str = "S1.0") -> Schedules:
    """Read every schedule off one structural sheet.

    S1.0, S2.0 and S3.0 repeat the same schedule block, so any of them will do;
    reading one and noting which avoids triple-counting.
    """
    sheet = plan.sheet(sheet_id)
    if sheet is None or sheet.is_raster:
        return Schedules(source_sheet=None)

    out = Schedules(source_sheet=sheet_id)
    windows = {
        "HEADER SCHEDULE": 95.0,
        "CEILING JOIST SCHEDULE": 95.0,
        "PAD SCHEDULE": 115.0,
        "COLUMN CONNECTOR SCHEDULE": 95.0,
    }
    for title, height in windows.items():
        table = extract(sheet, title, height=height, stop_titles=_stops(title))
        if table:
            out.raw[title] = table

    if t := out.raw.get("HEADER SCHEDULE"):
        out.headers = _parse_headers(t)
    if t := out.raw.get("CEILING JOIST SCHEDULE"):
        out.joists = _parse_joists(t)
    if t := out.raw.get("PAD SCHEDULE"):
        out.pads = _parse_pads(t)
    if t := out.raw.get("COLUMN CONNECTOR SCHEDULE"):
        out.connectors = _parse_connectors(t)

    out.shearwalls = extract_matrix(sheet, "SHEARWALL SCHEDULE", height=175)
    return out
