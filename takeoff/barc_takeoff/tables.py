"""Rebuild schedule tables from word coordinates.

Schedules on these sheets are drawn, not tagged - there is no table structure in
the PDF, just words at positions inside ruled boxes whose rules are vector art.
Rows come back by clustering on the baseline; cells by splitting each row where
the horizontal gap between words exceeds normal inter-word spacing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pdfdoc import Sheet, Word

# Words closer than this are the same cell; wider gaps are column boundaries.
# Inter-word spacing in this type is ~4-6pt, column gutters are 20pt+.
CELL_GAP = 14.0

# Words within this vertical distance share a baseline.
ROW_TOL = 5.0


@dataclass
class Table:
    title: str
    sheet_id: str | None
    rows: list[list[str]] = field(default_factory=list)

    @property
    def header(self) -> list[str]:
        return self.rows[0] if self.rows else []

    @property
    def body(self) -> list[list[str]]:
        return self.rows[1:] if len(self.rows) > 1 else []

    def as_text(self) -> str:
        return "\n".join("  |  ".join(r) for r in self.rows)


def _group_rows(words: list[Word]) -> list[list[Word]]:
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w.y, w.x)):
        for r in rows:
            if abs(r[0].y - w.y) < ROW_TOL:
                r.append(w)
                break
        else:
            rows.append([w])
    return rows


def _split_cells(row: list[Word]) -> list[str]:
    row = sorted(row, key=lambda w: w.x)
    cells: list[list[Word]] = [[row[0]]]
    for prev, cur in zip(row, row[1:]):
        if cur.x - prev.x2 > CELL_GAP:
            cells.append([cur])
        else:
            cells[-1].append(cur)
    return [" ".join(w.text for w in c) for c in cells]


def extract(
    sheet: Sheet,
    title: str,
    *,
    height: float = 100.0,
    width: float = 130.0,
    stop_titles: tuple[str, ...] = (),
) -> Table | None:
    """Extract the table drawn beneath `title` on `sheet`.

    `height` is how far below the heading to read, `width` how far either side.
    The window is clipped at the title block so office details - address, phone,
    project name - cannot bleed into the last column.
    """
    head = sheet.find_phrase(title)
    if not head:
        return None

    x0 = min(w.x for w in head) - width
    x1 = min(max(w.x2 for w in head) + width, sheet.title_block_x)
    ytop = max(w.y2 for w in head)

    words = [w for w in sheet.words_in(x0, x1, ytop, ytop + height)]
    if not words:
        return None

    rows = []
    for row in _group_rows(words):
        cells = _split_cells(row)
        joined = " ".join(cells).upper()
        # A neighbouring schedule's heading marks the end of this one.
        if any(s.upper() in joined for s in stop_titles):
            break
        rows.append(cells)

    return Table(title=title, sheet_id=sheet.sheet_id, rows=rows) if rows else None


# Stacked fractions are drawn as separate runs on slightly different baselines,
# so "15/32" arrives as the tokens "15" and "32". Denominators in framing specs
# are always powers of two, which makes the recombination unambiguous.
_FRACTION = re.compile(r"\b(\d{1,2}) (2|4|8|16|32)\b")


def normalise_fractions(text: str) -> str:
    return _FRACTION.sub(r"\1/\2", text)


@dataclass
class Matrix:
    """A schedule keyed across the top - shearwall types A..F, say."""

    title: str
    sheet_id: str | None
    columns: list[str]
    rows: dict[str, dict[str, str]] = field(default_factory=dict)
    #: Cells merged across columns cannot be recovered reliably, so a matrix is
    #: always presented for confirmation rather than consumed silently.
    needs_review: bool = True

    def as_text(self) -> str:
        out = ["property".ljust(22) + "  ".join(c.ljust(16) for c in self.columns)]
        for label, vals in self.rows.items():
            out.append(
                label[:21].ljust(22)
                + "  ".join((vals.get(c, "") or "-").ljust(16) for c in self.columns)
            )
        return "\n".join(out)


def extract_matrix(
    sheet: Sheet,
    title: str,
    *,
    key_row: str = "TYPE",
    height: float = 180.0,
    width: float = 200.0,
) -> Matrix | None:
    """Extract a schedule whose categories run across the top.

    Column positions are taken from the key row and every value below is
    assigned to its nearest column centre. Splitting such a table on horizontal
    gaps alone fails: adjacent columns can carry values like `10d @ 6" OC` that
    sit closer together than the gap threshold, silently merging two types.
    """
    head = sheet.find_phrase(title)
    if not head:
        return None
    x0 = min(w.x for w in head) - width
    x1 = min(max(w.x2 for w in head) + width, sheet.title_block_x)
    ytop = max(w.y2 for w in head)
    words = sheet.words_in(x0, x1, ytop, ytop + height)
    if not words:
        return None

    grouped = _group_rows(words)
    keys = [r for r in grouped if any(w.text.upper() == key_row for w in r)]
    if not keys:
        return None
    key = sorted(keys[0], key=lambda w: w.x)
    labels = [w for w in key if w.text.upper() != key_row]
    if not labels:
        return None

    centres = {w.text: w.cx for w in labels}
    ordered = sorted(centres.values())
    # Column pitch sets the boundary of the label column. Measuring from the
    # first heading's left edge instead clips wide values that start well left
    # of the letter centred above them.
    pitch = (
        min(b - a for a, b in zip(ordered, ordered[1:])) if len(ordered) > 1 else 60.0
    )
    label_edge = ordered[0] - pitch / 2

    matrix = Matrix(title=title, sheet_id=sheet.sheet_id, columns=list(centres))
    current: str | None = None
    for row in grouped:
        if row is keys[0]:
            continue
        row = sorted(row, key=lambda w: w.x)
        label = normalise_fractions(
            " ".join(w.text for w in row if w.cx < label_edge)
        ).strip()
        values = [w for w in row if w.cx >= label_edge]

        if label:
            current = label if label not in matrix.rows else f"{label} (cont)"
            matrix.rows.setdefault(current, {})
        if current is None or not values:
            continue

        # Assign each word to the column it sits under. Cells merged across
        # columns in the drawing - "2x @ ALL PANEL EDGES" covering types A
        # through C - cannot be told apart from a row of six tightly packed
        # distinct values without the ruled cell boundaries, which are vector
        # art and invisible here. Per-word assignment is exact for the
        # quantitative rows (nailing, spacing, anchor bolts) and splits the
        # prose rows, so the result is flagged rather than trusted blindly.
        for w in values:
            col = min(centres, key=lambda c: abs(centres[c] - w.cx))
            prev = matrix.rows[current].get(col, "")
            matrix.rows[current][col] = normalise_fractions(
                f"{prev} {w.text}".strip()
            )
    return matrix
