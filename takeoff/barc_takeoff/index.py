"""Reconcile the cover-sheet index against the pages actually in the file.

A plan set is a promise about its own contents. When a sheet listed in the
index is not in the PDF, every downstream quantity silently loses whatever that
sheet carried. The reference progress set lists 24 sheets and contains 23 - A-0.3
PROJECT CALCULATIONS is absent - which is exactly the class of gap that must
stop a takeoff rather than quietly shrink it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .pdfdoc import SHEET_ID, PlanSet, Sheet, Word


@dataclass(frozen=True)
class IndexEntry:
    sheet_id: str
    title: str


@dataclass
class Reconciliation:
    listed: list[IndexEntry]
    present: list[str]
    missing: list[IndexEntry]      # indexed but not in the file
    unlisted: list[str]            # in the file but not indexed
    unidentified: list[int]        # pages with no readable sheet number

    @property
    def ok(self) -> bool:
        return not (self.missing or self.unlisted or self.unidentified)

    def summary(self) -> str:
        if self.ok:
            return f"index reconciled: {len(self.present)} sheets, all accounted for"
        bits = []
        if self.missing:
            bits.append(
                "missing from file: "
                + ", ".join(f"{e.sheet_id} ({e.title})" for e in self.missing)
            )
        if self.unlisted:
            bits.append("not in index: " + ", ".join(self.unlisted))
        if self.unidentified:
            bits.append(
                "unidentified pages: " + ", ".join(str(p) for p in self.unidentified)
            )
        return "; ".join(bits)


def parse_index(cover: Sheet) -> list[IndexEntry]:
    """Read the SHEET INDEX table off the cover sheet.

    The index is a two-column list (number, title) sitting under a "SHEET INDEX"
    heading in the title-block strip. Rows are recovered by clustering on the
    baseline, and the sheet number is whichever token on the row looks like one.
    """
    head = cover.find_phrase("SHEET INDEX")
    if not head:
        return []
    hx = min(w.x for w in head)
    hy = max(w.y2 for w in head)

    # The heading is centred over the table, so the sheet-number column starts
    # to its left. Reach far enough left to catch it, but not so far as to pull
    # in the floor-area table that shares this band of the cover sheet.
    region = [
        w
        for w in cover.words
        if hx - 150 < w.x < hx + 260 and hy < w.y < hy + 460
    ]

    rows: list[list[Word]] = []
    for w in sorted(region, key=lambda w: (w.y, w.x)):
        for r in rows:
            if abs(r[0].y - w.y) < 5:
                r.append(w)
                break
        else:
            rows.append([w])

    entries: list[IndexEntry] = []
    for row in rows:
        row.sort(key=lambda w: w.x)
        texts = [w.text for w in row]
        if any(t.upper() in {"REVISIONS", "DESCRIPTION"} for t in texts):
            break
        ids = [t for t in texts if SHEET_ID.fullmatch(t)]
        if not ids:
            continue
        sheet_id = ids[0]
        rest = " ".join(t for t in texts if t != sheet_id).strip()
        # Discipline headers ("ARCHITECTURAL", "STRUCTURAL") have no title text,
        # and stray project-address fragments land in this strip too.
        if not rest or re.fullmatch(r"[\d\W]+", rest):
            continue
        entries.append(IndexEntry(sheet_id=sheet_id, title=rest))
    return entries


def infer_unidentified(plan: PlanSet, listed: list[IndexEntry]) -> dict[int, str]:
    """Name pages whose sheet number could not be read, using index order.

    Flattened raster sheets carry no text at all, so they have no readable
    number - but the index is ordered, so a nameless page bracketed by two
    known neighbours can be identified when exactly one indexed sheet is
    unaccounted for in that gap. Anything less certain is left unnamed rather
    than guessed at.
    """
    order = [e.sheet_id for e in listed]
    if not order:
        return {}
    known = {s.page: s.sheet_id for s in plan.sheets if s.sheet_id}
    unplaced = [e.sheet_id for e in listed if e.sheet_id not in set(known.values())]
    inferred: dict[int, str] = {}

    for sheet in plan.sheets:
        if sheet.sheet_id:
            continue
        before = [known[p] for p in sorted(known) if p < sheet.page]
        after = [known[p] for p in sorted(known) if p > sheet.page]
        lo = order.index(before[-1]) if before and before[-1] in order else -1
        hi = order.index(after[0]) if after and after[0] in order else len(order)
        gap = [s for s in order[lo + 1 : hi] if s in unplaced]
        pages_in_gap = [
            s.page
            for s in plan.sheets
            if not s.sheet_id
            and (not before or s.page > max(p for p in known if known[p] == before[-1]))
            and (not after or s.page < min(p for p in known if known[p] == after[0]))
        ]
        if len(gap) == 1 and len(pages_in_gap) == 1:
            inferred[sheet.page] = gap[0]
    return inferred


def reconcile(plan: PlanSet) -> Reconciliation:
    cover = plan.sheets[0] if plan.sheets else None
    listed = parse_index(cover) if cover else []

    inferred = infer_unidentified(plan, listed)
    for sheet in plan.sheets:
        if not sheet.sheet_id and sheet.page in inferred:
            sheet.sheet_id = inferred[sheet.page]
            sheet.title = next(
                (e.title for e in listed if e.sheet_id == sheet.sheet_id), None
            )

    present = [s.sheet_id for s in plan.sheets if s.sheet_id]
    unidentified = [s.page for s in plan.sheets if not s.sheet_id]

    present_set = set(present)
    listed_set = {e.sheet_id for e in listed}
    return Reconciliation(
        listed=listed,
        present=present,
        missing=[e for e in listed if e.sheet_id not in present_set],
        unlisted=sorted(present_set - listed_set),
        unidentified=unidentified,
    )
