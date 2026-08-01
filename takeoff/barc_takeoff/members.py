"""Structural members called out on the framing sheets.

Beams, columns and rafters are specified as annotations on the plan rather than
in a schedule - "PB2 5.5x7.5 24F-V4 GLU-LAM, EXTERIOR TREATED" printed beside
the member it labels. Where such a callout can be read in full it is the
engineer's specification and is used as-is.

Not all of them can be. Some labels are set on rotated leaders or split across
several lines, which leaves the tag readable but its specification scattered.
Those are reported as detected-but-unparsed so they are chased on the sheet,
never inferred from a similar-looking member nearby.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pdfdoc import PlanSet, Sheet

# Sheets carrying framing callouts, in the order they are read.
FRAMING_SHEETS = ("S3.0", "S2.0", "S1.0")

# Tags that label a structural member. Prefixes: PB porch/plate beam, RB ridge
# beam, GB grade beam, FB floor beam, HDR header.
BEAM_TAG = re.compile(r"^(PB|RB|GB|FB|HDR)\d+$", re.I)

# 5.5x7.5, 3.5x5.25, 4x6, 2x10
SIZE = re.compile(r"\b(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)\b")

MATERIALS = ("GLU-LAM", "GLULAM", "PSL", "LVL", "LSL", "STEEL")

_DIM = r"\d+(?:\.\d+)?x\d+(?:\.\d+)?"
_BEAM = re.compile(rf"\b((?:PB|RB|GB|FB|HDR)\d+)\s+({_DIM})", re.I)
_COLUMN = re.compile(rf"\bCOL\s+({_DIM})", re.I)
_TRIMMER = re.compile(rf"\bTRIMMER\s+({_DIM})", re.I)
_RAFTER = re.compile(r"\bRFTR\s+(\d+x\d+)(?:\s*@\s*(\d+)\s*\"?\s*OC)?", re.I)


@dataclass(frozen=True)
class Member:
    tag: str
    kind: str                  # beam | column | rafter | trimmer
    size: str | None
    material: str | None
    existing: bool
    treated: bool
    note: str
    sheet_id: str | None

    @property
    def new(self) -> bool:
        return not self.existing


@dataclass
class MemberTakeoff:
    members: list[Member] = field(default_factory=list)
    #: Tags seen on the sheets whose specification could not be read.
    unresolved: list[str] = field(default_factory=list)

    @property
    def new_members(self) -> list[Member]:
        return [m for m in self.members if m.new]

    def of_kind(self, kind: str) -> list[Member]:
        return [m for m in self.members if m.kind == kind]


def _lines(sheet: Sheet, tol: int = 5) -> list[str]:
    buckets: dict[int, list] = {}
    for w in sheet.drawing_words:
        buckets.setdefault(int(w.cy // tol), []).append(w)
    return [
        " ".join(w.text for w in sorted(ws, key=lambda w: w.x))
        for _, ws in sorted(buckets.items())
    ]


def _material(text: str) -> str | None:
    up = text.upper()
    for m in MATERIALS:
        if m in up:
            return "GLU-LAM" if m == "GLULAM" else m
    return None


def _parse_line(line: str, sheet_id: str | None, below: str = "") -> list[Member]:
    """Pull member callouts out of one reconstructed line.

    Lines here are drawing annotations, not table rows, so several unrelated
    callouts can share a baseline; each is matched independently.

    The line immediately below is read for material and treatment only. A
    callout is routinely set over two lines - "PB2 5.5x7.5 24F-V4 GLU-LAM" with
    "EXTERIOR TREATED" beneath it - and treated glulam is a different product at
    a different price, so dropping the qualifier would quietly order the wrong
    beam. Sizes and tags are never taken from the continuation line.
    """
    found: list[Member] = []
    up = line.upper()
    context = f"{line}\n{below}".upper()
    existing = "(E)" in up
    treated = "TREATED" in context

    # Tagged beams: PB2 5.5x7.5 24F-V4 GLU-LAM. Matching is case-insensitive
    # against the original line - upper-casing first would turn the "x" in
    # "5.5x7.5" into "X" and stop the size pattern matching.
    for m in re.finditer(_BEAM, line):
        tail = up[m.end() : m.end() + 60]
        found.append(
            Member(
                tag=m.group(1).upper(),
                kind="beam",
                size=m.group(2),
                material=_material(tail) or _material(context),
                existing=existing,
                treated=treated,
                note=line.strip()[:110],
                sheet_id=sheet_id,
            )
        )

    # Columns: COL 4x6
    for m in re.finditer(_COLUMN, line):
        found.append(
            Member(
                tag=f"COL {m.group(1)}",
                kind="column",
                size=m.group(1),
                material=_material(context),
                existing=existing,
                treated=treated,
                note=line.strip()[:110],
                sheet_id=sheet_id,
            )
        )

    # Trimmers: TRIMMER 3.5x5.25 PSL 1.8E
    for m in re.finditer(_TRIMMER, line):
        found.append(
            Member(
                tag="TRIMMER",
                kind="trimmer",
                size=m.group(1),
                material=_material(up[m.end() : m.end() + 40]),
                existing=existing,
                treated=treated,
                note=line.strip()[:110],
                sheet_id=sheet_id,
            )
        )

    # Rafters: RFTR 2x10 @ 24" OC
    for m in re.finditer(_RAFTER, line):
        found.append(
            Member(
                tag="RFTR",
                kind="rafter",
                size=m.group(1),
                material=None,
                existing=existing,
                treated=treated,
                note=line.strip()[:110],
                sheet_id=sheet_id,
            )
        )
    return found


def read(plan: PlanSet) -> MemberTakeoff:
    out = MemberTakeoff()
    seen: set[tuple] = set()
    anchors_found: set[str] = set()
    anchors_parsed: set[str] = set()

    for sheet_id in FRAMING_SHEETS:
        sheet = plan.sheet(sheet_id)
        if sheet is None or sheet.is_raster:
            continue

        for word in sheet.drawing_words:
            token = word.text.upper().strip(".,")
            if BEAM_TAG.match(token) or token in {"RFTR", "TRIMMER"}:
                anchors_found.add(token)

        lines = _lines(sheet)
        for i, line in enumerate(lines):
            below = lines[i + 1] if i + 1 < len(lines) else ""
            for member in _parse_line(line, sheet_id, below):
                # The same callout is printed on several framing sheets; a
                # member is counted once per distinct specification.
                key = (member.tag, member.size, member.material, member.existing, member.treated)
                if key in seen:
                    continue
                seen.add(key)
                out.members.append(member)
                anchors_parsed.add(member.tag.split()[0].upper())

    out.unresolved = sorted(anchors_found - anchors_parsed)
    return out
