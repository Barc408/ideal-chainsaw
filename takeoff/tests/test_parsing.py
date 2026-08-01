"""Unit tests for the pure parsing logic - no PDF required."""

from __future__ import annotations

import pytest

from barc_takeoff.keynotes import Scope, classify
from barc_takeoff.pdfdoc import Sheet, Word
from barc_takeoff.schedules import (
    HeaderSpec,
    JoistSpec,
    Schedules,
    feet_inches_to_inches,
)
from barc_takeoff.tables import normalise_fractions


class TestScopeClassification:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("(E) ROOF FINISH AND FRAMING TO REMAIN.", Scope.EXISTING_REMAIN),
            ("(E) ROOF FINISH AND FRAMING TO BE REMOVED.", Scope.DEMOLISH),
            ("(E) WOOD POST TO BE REMOVED", Scope.DEMOLISH),
            ("PROVIDE VENTILATION AND ACCESS OPENING", Scope.NEW),
            ("GUTTER TO MATCH (E) IN STYLE AND COLOR.", Scope.NEW),
            ("OUTLINE OF POST BELOW.", Scope.UNKNOWN),
        ],
    )
    def test_classifies(self, text, expected):
        assert classify(text) is expected

    def test_demolition_of_existing_beats_retention(self):
        """'(E) ... TO BE REMOVED' contains neither trap: it is demolition."""
        assert classify("(E) GUTTERS TO BE REMOVED.") is Scope.DEMOLISH

    def test_matching_ignores_kerning_artefacts(self):
        """The CAD text layer splits words: 'REMOV ED', 'MA TCH', 'TY PICAL'."""
        assert classify("(E) WOOD FASCIA TO BE REMOV ED") is Scope.DEMOLISH
        assert classify("PAINTED WOOD FASCIA TO MA TCH (E)") is Scope.NEW


class TestFeetInches:
    @pytest.mark.parametrize(
        "text,inches",
        [("UP TO 3'", 36), ("UP TO 7'-3\"", 87), ("16'-4\"", 196), ("10'-8\"", 128)],
    )
    def test_converts(self, text, inches):
        assert feet_inches_to_inches(text) == inches

    def test_returns_none_when_absent(self):
        assert feet_inches_to_inches("REFER TO TYPICAL HEADER DETAIL") is None


class TestFractions:
    def test_rejoins_stacked_fraction(self):
        """'15/32' is drawn as two runs on different baselines."""
        assert normalise_fractions('15 32 " STRUCTURAL I') == '15/32 " STRUCTURAL I'

    def test_leaves_unrelated_numbers_alone(self):
        assert normalise_fractions("PAD 1 18 24") == "PAD 1 18 24"


class TestScheduleLookups:
    @pytest.fixture
    def scheds(self):
        return Schedules(
            headers=[
                HeaderSpec(36, "UP TO 3'", "2-2x, 4x/6x", '6"', 1, 1),
                HeaderSpec(48, "3'-1\" TO 4'", "2-2x, 4x/6x", '8"', 2, 1),
                HeaderSpec(72, "4'-1\" TO 6'", "2-2x, 4x/6x", '10"', 3, 2),
                HeaderSpec(96, "6'-1\" TO 8'", "2-2x, 4x/6x", '12"', 3, 2),
            ],
            joists=[
                JoistSpec("A", 87, "UP TO 7'-3\"", "2x4", '24" OC', "U24"),
                JoistSpec("C", 162, "UP TO 13'-6\"", "2x8", '24" OC', "U28"),
            ],
        )

    def test_picks_smallest_sufficient_header(self, scheds):
        assert scheds.header_for(60).depth == '10"'
        assert scheds.header_for(60).jacks == 2

    def test_boundary_span_uses_that_row(self, scheds):
        """A 4'-0\" opening is exactly the top of the second row, not the third."""
        assert scheds.header_for(48).depth == '8"'

    def test_returns_none_beyond_schedule(self, scheds):
        assert scheds.header_for(200) is None

    def test_joist_lookup(self, scheds):
        assert scheds.joist_for(144).size == "2x8"


class TestTitleBlockDetection:
    def _sheet(self, words):
        return Sheet(page=1, width=2592, height=1728, words=words)

    def test_finds_gutter_before_title_block(self):
        """Content and title block are separated by a ruled, empty band."""
        content = [Word("CONTENT", x, x + 50, 100, 110) for x in range(2100, 2340, 50)]
        block = [Word("MHA", x, x + 50, 100, 110) for x in range(2400, 2540, 50)]
        sheet = self._sheet(content + block)
        assert sheet.title_block_x == 2400

    def test_ignores_leading_whitespace_in_the_scan_window(self):
        """Blank space before the first text is the scan margin, not a gutter."""
        words = [Word("X", x, x + 10, 100, 110) for x in range(2200, 2590, 10)]
        sheet = self._sheet(words)
        assert sheet.title_block_x == pytest.approx(2592 * 0.888)

    def test_falls_back_when_no_gutter(self):
        words = [Word("X", x, x + 10, 100, 110) for x in range(2070, 2590, 10)]
        sheet = self._sheet(words)
        assert sheet.title_block_x == pytest.approx(2592 * 0.888)
