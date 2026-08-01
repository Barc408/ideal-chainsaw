"""Tests for scale calibration and grid reading."""

from __future__ import annotations

import pytest

from barc_takeoff.pdfdoc import Sheet, Word
from barc_takeoff.scaling import Grid, GridLine, Scale, distance_ft


class TestScale:
    def test_converts_points_to_feet(self):
        assert Scale(18.0, "test").to_feet(559.0) == pytest.approx(31.06, abs=0.01)

    def test_describes_the_drawing_scale(self):
        """18 pt/ft is a quarter inch of paper per foot."""
        assert '1/4" = 1\'-0"' in Scale(18.0, "test").describe()
        assert '1/8" = 1\'-0"' in Scale(9.0, "test").describe()

    def test_distance_between_two_points(self):
        s = Scale(18.0, "test")
        assert distance_ft(s, 0, 0, 180, 0) == pytest.approx(10.0)
        assert distance_ft(s, 0, 0, 108, 144) == pytest.approx(10.0)


class TestGrid:
    @pytest.fixture
    def grid(self):
        return Grid(
            sheet_id="S3.0",
            lines=[
                GridLine("1", 432, "x"),
                GridLine("3", 800, "x"),
                GridLine("7", 1625, "x"),
                GridLine("A", 100, "y"),
                GridLine("C", 300, "y"),
            ],
        )

    def test_bays_are_reported_in_order(self, grid):
        bays = grid.bays("x", Scale(18.0, "t"))
        assert [(a, b) for a, b, _ in bays] == [("1", "3"), ("3", "7")]

    def test_bay_distances(self, grid):
        bays = dict(((a, b), d) for a, b, d in grid.bays("x", Scale(18.0, "t")))
        assert bays[("1", "3")] == pytest.approx((800 - 432) / 18, abs=0.01)

    def test_span_covers_first_to_last(self, grid):
        assert grid.span("x", Scale(18.0, "t")) == pytest.approx(
            (1625 - 432) / 18, abs=0.01
        )

    def test_span_needs_two_lines(self):
        assert Grid("X", [GridLine("1", 10, "x")]).span("x", Scale(18.0, "t")) is None


class TestGridDetection:
    """Grid bubbles are found by alignment, not by being the first digit seen.

    Single digits appear throughout a drawing - in dimensions, detail bubbles
    and schedules. What marks the grid is that its bubbles share a baseline.
    """

    def test_picks_the_aligned_row_over_scattered_digits(self):
        from barc_takeoff.scaling import read_grid
        from barc_takeoff.pdfdoc import PlanSet

        # Enough body text that the sheet is not mistaken for a raster scan.
        words = [Word("note", 200 + (i % 20) * 30, 224 + (i % 20) * 30,
                      400 + (i // 20) * 14, 410 + (i // 20) * 14)
                 for i in range(140)]
        # A scattered decoy digit well away from the grid row.
        words.append(Word("3", 500, 510, 800, 812))
        # The real grid row: four digits sharing a baseline near the top.
        for i, x in enumerate(["1", "2", "3", "4"]):
            words.append(Word(x, 400 + i * 100, 410 + i * 100, 120, 132))
        sheet = Sheet(page=1, width=2592, height=1728, words=words, sheet_id="S3.0")
        plan = PlanSet(path=None, sheets=[sheet])

        grid = read_grid(plan, "S3.0")
        xs = grid.axis("x")
        assert [g.label for g in xs] == ["1", "2", "3", "4"]
        # The decoy at x=500 must not have displaced the real "3" at x=600.
        assert dict((g.label, g.position_pt) for g in xs)["3"] == pytest.approx(605)
