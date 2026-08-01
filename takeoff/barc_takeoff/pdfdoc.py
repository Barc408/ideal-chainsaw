"""Read a plan set PDF into sheets of coordinate-tagged words.

Everything downstream depends on word positions, not reading order. On a 36x24
CAD sheet, text that looks adjacent in a linear extraction is often on opposite
sides of the drawing, so `pdftotext -layout` silently interleaves unrelated
content. `-bbox-layout` keeps the geometry, and we rebuild structure from it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

NS = "{http://www.w3.org/1999/xhtml}"

# Sheet identifiers as they appear in the title block: A-3.1, S1.0, BFCB.
SHEET_ID = re.compile(r"(?:[A-Z]{1,2}-?\d\.\d|BFCB)")

# A sheet whose drawing area carries fewer than this many words is almost
# certainly a flattened raster scan. In the reference set that catches the CalGreen
# sheets and the Simpson Strong-Wall detail sheets, whose tables are images.
RASTER_WORD_THRESHOLD = 120


class MissingDependency(RuntimeError):
    pass


@dataclass(frozen=True)
class Word:
    text: str
    x: float
    x2: float
    y: float
    y2: float

    @property
    def cx(self) -> float:
        return (self.x + self.x2) / 2

    @property
    def cy(self) -> float:
        return (self.y + self.y2) / 2

    @property
    def height(self) -> float:
        return self.y2 - self.y


@dataclass
class Sheet:
    page: int
    width: float
    height: float
    words: list[Word]
    sheet_id: str | None = None
    title: str | None = None
    _title_block_x: float | None = field(default=None, repr=False)

    @property
    def title_block_x(self) -> float:
        """Left edge of the right-hand title block strip.

        Detected rather than assumed. Guessing from sheet width cuts into the
        schedules, which run to within 15pt of the block on the structural
        sheets, dropping their last column.
        """
        if self._title_block_x is None:
            self._title_block_x = self._detect_title_block()
        return self._title_block_x

    def _gutters(self, *, start: float = 0.80, min_width: int = 4) -> list[tuple[int, int]]:
        """Vertical bands in the right of the sheet containing no text.

        The frame's rule lines sit in these bands. Rules themselves are vector
        art and invisible to text extraction, but the whitespace around them is
        unambiguous.
        """
        x0, x1 = int(self.width * start), int(self.width)
        cover = [0] * (x1 - x0 + 2)
        for w in self.words:
            a, b = max(int(w.x), x0), min(int(w.x2), x1)
            for x in range(a, b + 1):
                cover[x - x0] += 1

        runs, run_start = [], None
        for i, c in enumerate(cover):
            if c == 0 and run_start is None:
                run_start = i
            elif c != 0 and run_start is not None:
                # Whitespace before the first text in the window is the margin
                # of the scan, not a separator between two blocks.
                if i - run_start >= min_width and run_start > 0:
                    runs.append((x0 + run_start, x0 + i))
                run_start = None
        if run_start is not None and len(cover) - run_start >= min_width:
            runs.append((x0 + run_start, x1))
        return runs

    def _detect_title_block(self) -> float:
        """Left edge of the title block: the far side of the last frame gutter.

        Content and title block are separated by a ruled gutter, so the
        boundary is read off the whitespace rather than guessed from sheet
        width. The trailing gutter at the paper's edge is not a separator and
        is discarded.
        """
        gutters = [g for g in self._gutters() if g[1] < self.width - 2]
        if gutters:
            return float(gutters[-1][1])
        return self.width * 0.888

    @property
    def drawing_words(self) -> list[Word]:
        """Words outside the title block - the actual content of the sheet."""
        return [w for w in self.words if w.cx < self.title_block_x]

    @property
    def is_raster(self) -> bool:
        """True when the sheet's content is images, not extractable text.

        Such sheets are not failures - they simply cannot be mined by this
        stage, and must be flagged rather than treated as empty.
        """
        return len(self.drawing_words) < RASTER_WORD_THRESHOLD

    def words_in(self, x0: float, x1: float, y0: float, y1: float) -> list[Word]:
        return [w for w in self.words if x0 <= w.cx <= x1 and y0 <= w.cy <= y1]

    def find_phrase(self, phrase: str, *, tol: float = 6.0) -> list[Word] | None:
        """Locate a run of words forming `phrase` on a single baseline."""
        parts = phrase.upper().split()
        for i, w in enumerate(self.words):
            if w.text.upper() != parts[0]:
                continue
            run, j, k = [w], i + 1, 1
            while k < len(parts) and j < len(self.words):
                cand = self.words[j]
                if cand.text.upper() == parts[k] and abs(cand.y - w.y) < tol:
                    run.append(cand)
                    k += 1
                j += 1
            if k == len(parts):
                return run
        return None


@dataclass
class PlanSet:
    path: Path
    sheets: list[Sheet] = field(default_factory=list)

    def sheet(self, sheet_id: str) -> Sheet | None:
        return next((s for s in self.sheets if s.sheet_id == sheet_id), None)

    @property
    def raster_sheets(self) -> list[Sheet]:
        return [s for s in self.sheets if s.is_raster]


def _require_pdftotext() -> None:
    if shutil.which("pdftotext") is None:
        raise MissingDependency(
            "pdftotext not found. Install poppler-utils "
            "(apt-get install poppler-utils / brew install poppler)."
        )


def page_count(pdf: Path) -> int:
    """Read the real page count.

    Worth doing explicitly: upload tooling and file metadata have both been
    observed reporting a wrong count for this very set.
    """
    _require_pdftotext()
    out = subprocess.run(
        ["pdfinfo", str(pdf)], capture_output=True, text=True, check=True
    ).stdout
    m = re.search(r"^Pages:\s+(\d+)$", out, re.M)
    if not m:
        raise RuntimeError(f"could not determine page count of {pdf}")
    return int(m.group(1))


def _extract_page(pdf: Path, page: int) -> Sheet:
    xml = subprocess.run(
        ["pdftotext", "-bbox-layout", "-f", str(page), "-l", str(page), str(pdf), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    root = ET.fromstring(xml)
    pg = root.find(f".//{NS}page")
    words = [
        Word(
            text=(w.text or "").strip(),
            x=float(w.get("xMin")),
            x2=float(w.get("xMax")),
            y=float(w.get("yMin")),
            y2=float(w.get("yMax")),
        )
        for w in root.iter(NS + "word")
        if (w.text or "").strip()
    ]
    return Sheet(
        page=page,
        width=float(pg.get("width")) if pg is not None else 0.0,
        height=float(pg.get("height")) if pg is not None else 0.0,
        words=words,
    )


def _identify(sheet: Sheet) -> tuple[str | None, str | None]:
    """Pull the sheet number and title out of the bottom-right title block.

    The sheet number is set in the largest type on the sheet, which makes size
    a more reliable discriminator than position alone - drawings frequently
    contain matching strings as cross-references (e.g. a detail bubble
    pointing at A-6.1).
    """
    corner = [
        w
        for w in sheet.words
        if w.cx > sheet.title_block_x and w.cy > sheet.height * 0.80
    ]
    ids = [w for w in corner if SHEET_ID.fullmatch(w.text)]
    if not ids:
        return None, None
    num = max(ids, key=lambda w: w.height)

    # The title sits directly above the number, in the same strip, and is set
    # noticeably larger than the field labels around it ("DATE", "LEVEL",
    # "COMBO"). Selecting by type size rather than blacklisting those words
    # keeps this working on sheets that use different labels.
    above = [w for w in corner if w.y2 < num.y and num.y - w.y2 < 120]
    if not above:
        return num.text, None
    tallest = max(w.height for w in above)
    title_words = [w for w in above if w.height >= tallest * 0.8]

    lines: dict[int, list[Word]] = {}
    for w in title_words:
        lines.setdefault(int(w.cy // 12), []).append(w)
    text = [
        " ".join(w.text for w in sorted(ln, key=lambda w: w.x))
        for _, ln in sorted(lines.items())
    ]
    title = " ".join(text).strip() or None
    return num.text, title


def _assign_title_blocks(sheets: list[Sheet]) -> None:
    for sheet in sheets:
        sheet._title_block_x = sheet._detect_title_block()


def load(pdf: str | Path) -> PlanSet:
    """Load every page of a plan set."""
    pdf = Path(pdf)
    if not pdf.exists():
        raise FileNotFoundError(pdf)
    _require_pdftotext()
    n = page_count(pdf)
    sheets = [_extract_page(pdf, p) for p in range(1, n + 1)]
    # Title blocks first: identifying a sheet means reading its title block.
    _assign_title_blocks(sheets)
    for sheet in sheets:
        sheet.sheet_id, sheet.title = _identify(sheet)
    return PlanSet(path=pdf, sheets=sheets)
