"""Keynotes, and the scope decisions they encode.

On a remodel the keynotes are what separate work to be priced from work that is
merely drawn. This set marks existing construction to remain, existing
construction to be demolished, and new work, and a takeoff that ignores the
distinction prices the whole house instead of the patio.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .pdfdoc import PlanSet, Sheet

# Keynote codes are CSI-style pairs: 03.10, 06.51, 22.29.
CODE = re.compile(r"^\d{2}\.\d{2}$")

# Headings of blocks that sit below the keynote legend and must not be read
# into the last note.
_LEGEND = re.compile(r"\b[A-Z ]*LEGEND\b|\bGENERAL NOTES\b", re.I)


class Scope(str, Enum):
    NEW = "new"
    EXISTING_REMAIN = "existing-remain"
    DEMOLISH = "demolish"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Keynote:
    code: str
    text: str
    sheet_id: str | None
    scope: Scope

    @property
    def billable(self) -> bool:
        """Whether this note implies material to buy."""
        return self.scope in (Scope.NEW, Scope.DEMOLISH)


def _squash(text: str) -> str:
    """Strip all whitespace for matching.

    The CAD text layer carries kerning artefacts - "MA TCH", "CLA SS",
    "V ENTILA TION" - so phrase matching has to ignore spacing entirely.
    """
    return re.sub(r"\s+", "", text).upper()


def classify(text: str) -> Scope:
    s = _squash(text)
    # Order matters: "(E) ROOF TO BE REMOVED" is demolition, not retention.
    if "TOBEREMOVED" in s or "TOBEDEMOLISHED" in s or "TOBEDEMO" in s:
        return Scope.DEMOLISH
    if "TOREMAIN" in s:
        return Scope.EXISTING_REMAIN
    if s.startswith("(N)") or "PROVIDE" in s or "INSTALL" in s or "NEW" in s:
        return Scope.NEW
    # "... TO MATCH (E) IN STYLE AND COLOR" describes new material specified to
    # match what is there, not existing material being kept.
    if "TOMATCH(E)" in s:
        return Scope.NEW
    if s.startswith("(E)"):
        return Scope.EXISTING_REMAIN
    return Scope.UNKNOWN


def read_sheet(sheet: Sheet) -> list[Keynote]:
    """Read the keynote legend from one sheet.

    Entries are found by their code rather than by the block's outline: the
    legend sits beside other note columns whose text would otherwise bleed in,
    but only keynotes are numbered this way.
    """
    head = sheet.find_phrase("KEYNOTES")
    if not head:
        return []
    hy = max(w.y2 for w in head)
    hx = min(w.x for w in head)

    region = [
        w
        for w in sheet.words
        if hx - 120 < w.x < sheet.title_block_x and hy < w.cy < sheet.height
    ]
    codes = sorted(
        (w for w in region if CODE.match(w.text)), key=lambda w: (w.y, w.x)
    )
    if not codes:
        return []

    # The note text is the column to the right of the codes.
    code_x = min(w.x for w in codes)
    text_x = min((w.x2 for w in codes), default=code_x) + 4

    notes = []
    for i, code in enumerate(codes):
        y_end = codes[i + 1].y - 1 if i + 1 < len(codes) else sheet.height
        body = [
            w
            for w in region
            if w.x >= text_x and code.y - 3 <= w.cy < y_end and w.x < sheet.title_block_x
        ]
        body.sort(key=lambda w: (w.y, w.x))
        text = re.sub(r"\s+", " ", " ".join(w.text for w in body)).strip()
        # The final note on a sheet has no next code to bound it, so it runs on
        # into whatever sits below - usually a legend, whose own "EXISTING ROOF
        # TO REMAIN" wording would otherwise flip the note's scope.
        text = _LEGEND.split(text, maxsplit=1)[0].strip(" .:,")
        if not text:
            continue
        notes.append(
            Keynote(
                code=code.text,
                text=text,
                sheet_id=sheet.sheet_id,
                scope=classify(text),
            )
        )
    return notes


def read(plan: PlanSet) -> dict[str, Keynote]:
    """Collect keynotes across the set, keyed by code.

    The same code carries the same meaning set-wide, so later sheets confirm
    rather than replace. Where a code appears with genuinely different text the
    longer version is kept, since truncation is the usual cause.
    """
    merged: dict[str, Keynote] = {}
    for sheet in plan.sheets:
        if sheet.is_raster:
            continue
        for note in read_sheet(sheet):
            existing = merged.get(note.code)
            if existing is None or len(note.text) > len(existing.text):
                merged[note.code] = note
    return dict(sorted(merged.items()))
