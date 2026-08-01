"""Tests for the rules engine - the part that produces orderable quantities."""

from __future__ import annotations

import pytest

from barc_takeoff.geometry import GeometryRequest, Measurement
from barc_takeoff.lumber import IncompleteInput, build, stock_length
from barc_takeoff.members import Member, MemberTakeoff
from barc_takeoff.schedules import ConnectorSpec, JoistSpec, PadSpec, Schedules
from barc_takeoff.standards import Standards

KEYS = (
    "roof_area_sf", "ridge_length_ft", "rafter_run_ft", "roof_perimeter_ft",
    "post_count", "post_height_ft", "new_wall_lf", "wall_height_ft",
)


def make_geom(**overrides) -> GeometryRequest:
    base = {
        "roof_area_sf": 480, "ridge_length_ft": 24, "rafter_run_ft": 11,
        "roof_perimeter_ft": 68, "post_count": 3, "post_height_ft": 9,
        "new_wall_lf": 0, "wall_height_ft": 8,
    }
    base.update(overrides)
    return GeometryRequest(
        measurements=[
            Measurement(key=k, label=k, unit="", why="", sheets=(), value=base[k])
            for k in KEYS
        ]
    )


@pytest.fixture
def standards():
    return Standards(
        path=type("P", (), {"name": "test.yaml"})(),
        data={
            "review_status": "CONFIRMED",
            "walls": {"stud_spacing_in": 16, "end_studs": 2,
                      "plates": {"top": 2, "bottom": 1}},
            "roof": {"blocking_spacing_ft": 8},
            "sheathing": {"waste": {"roof_cut_up": 0.15}},
            "waste": {"rafters": 0.07, "blocking": 0.15, "studs": 0.05, "plates": 0.10},
            "concrete": {"waste": 0.10},
            "stock_lengths_ft": {
                "2x4": [8, 10, 12, 14, 16], "2x10": [10, 12, 14, 16, 20],
                "4x6": [8, 10, 12, 16], "6x6": [8, 10, 12, 16],
            },
        },
    )


@pytest.fixture
def scheds():
    return Schedules(
        joists=[JoistSpec("C", 162, "UP TO 13'-6\"", "2x8", '24" OC', "U28")],
        pads=[PadSpec("PAD 1", 18, 18, 18, "3-#4 EACH WAY")],
        connectors=[ConnectorSpec("4x", "PB4x", "LPC4Z"),
                    ConnectorSpec("6x", "PB6x", "LPC6Z")],
        source_sheet="S1.0",
    )


def rafter_member(size="2x10", spacing=24.0):
    return Member("RR", "rafter", size, None, False, False, "", "S3.0", spacing)


def column(size):
    return Member(f"COL {size}", "column", size, None, False, False, "", "S3.0")


class TestStockLength:
    def test_picks_shortest_that_covers(self):
        assert stock_length(11.6, [8, 10, 12, 14, 16]) == 12

    def test_exact_fit(self):
        assert stock_length(12, [8, 10, 12]) == 12

    def test_none_when_nothing_long_enough(self):
        assert stock_length(24, [8, 10, 12]) is None


class TestRefusesIncompleteInput:
    def test_raises_rather_than_defaulting(self, scheds, standards):
        geom = make_geom()
        geom.measurements[0].value = None
        with pytest.raises(IncompleteInput, match="roof_area_sf"):
            build(scheds, MemberTakeoff(), geom, standards)


class TestRafters:
    def test_sized_from_the_rafter_callout(self, scheds, standards):
        mem = MemberTakeoff(members=[rafter_member("2x10")])
        result = build(scheds, mem, make_geom(), standards)
        rafters = next(i for i in result.items if i.description == "Rafters")
        assert rafters.size == "2x10"
        assert "RR callout" in rafters.source

    def test_never_falls_back_to_the_ceiling_joist_schedule(self, scheds, standards):
        """The joist schedule is attic-storage loading, not roof loading.

        Substituting it here would specify 2x8 where the plan calls out 2x10.
        """
        result = build(scheds, MemberTakeoff(unresolved=["RR"]), make_geom(), standards)
        assert not any(i.description == "Rafters" for i in result.items)
        assert any("Rafter size NOT determined" in w for w in result.warnings)

    def test_spacing_comes_from_the_callout_not_a_default(self, scheds, standards):
        """The callout reads RR 2x10 @ 24" OC; 16" OC would need more rafters."""
        wide = build(scheds, MemberTakeoff(members=[rafter_member(spacing=24)]),
                     make_geom(), standards)
        tight = build(scheds, MemberTakeoff(members=[rafter_member(spacing=16)]),
                      make_geom(), standards)
        w = next(i for i in wide.items if i.description == "Rafters")
        t = next(i for i in tight.items if i.description == "Rafters")
        assert t.quantity > w.quantity

    def test_count_covers_both_roof_planes(self, scheds, standards):
        mem = MemberTakeoff(members=[rafter_member()])
        result = build(scheds, mem, make_geom(ridge_length_ft=24), standards)
        rafters = next(i for i in result.items if i.description == "Rafters")
        # 24ft / 24in = 12 bays -> 13 per plane, x2 planes, +7% waste.
        assert rafters.quantity == 28

    def test_length_accounts_for_pitch(self, scheds, standards):
        mem = MemberTakeoff(members=[rafter_member()])
        flat = build(scheds, mem, make_geom(), standards, pitch="0:12")
        steep = build(scheds, mem, make_geom(), standards, pitch="12:12")
        f = next(i for i in flat.items if i.description == "Rafters")
        s = next(i for i in steep.items if i.description == "Rafters")
        assert s.length_ft > f.length_ft

    def test_blocking_is_ordered_as_stock_not_one_stick_per_block(
        self, scheds, standards
    ):
        """52 short blocks come out of a dozen sticks, not 52 of them."""
        mem = MemberTakeoff(members=[rafter_member()])
        result = build(scheds, mem, make_geom(), standards)
        blocking = next(i for i in result.items if i.description == "Rafter blocking")
        assert blocking.quantity < 20


class TestPostsAndFootings:
    def test_posts_come_from_callouts(self, scheds, standards):
        mem = MemberTakeoff(members=[column("4x6"), column("6x6")])
        result = build(scheds, mem, make_geom(post_count=2), standards)
        posts = [i for i in result.items if i.description.startswith("Post COL")]
        assert {p.size for p in posts} == {"4x6", "6x6"}

    def test_post_bases_come_from_the_connector_schedule(self, scheds, standards):
        mem = MemberTakeoff(members=[column("4x6")])
        result = build(scheds, mem, make_geom(post_count=1), standards)
        base = next(i for i in result.items if "Post base" in i.description)
        assert base.size == "PB4x"

    def test_callout_count_disagreeing_with_measurement_is_flagged(
        self, scheds, standards
    ):
        mem = MemberTakeoff(members=[column("4x6")])
        result = build(scheds, mem, make_geom(post_count=5), standards)
        assert any("post_count was given as 5" in w for w in result.warnings)

    def test_footing_volume_scales_with_post_count(self, scheds, standards):
        one = build(scheds, MemberTakeoff(members=[column("4x6")]),
                    make_geom(post_count=1), standards)
        two = build(scheds, MemberTakeoff(members=[column("4x6"), column("6x6")]),
                    make_geom(post_count=2), standards)
        a = next(i for i in one.items if "Concrete" in i.description)
        b = next(i for i in two.items if "Concrete" in i.description)
        assert b.quantity > a.quantity


class TestSheathingAndWalls:
    def test_sheathing_includes_waste(self, scheds, standards):
        result = build(scheds, MemberTakeoff(), make_geom(roof_area_sf=480), standards)
        sheets = next(i for i in result.items if i.description == "Roof sheathing")
        assert sheets.quantity == 18  # 480/32 = 15, +15% -> 17.25 -> 18

    def test_no_wall_framing_when_no_new_walls(self, scheds, standards):
        result = build(scheds, MemberTakeoff(), make_geom(new_wall_lf=0), standards)
        assert not any("stud" in i.description.lower() for i in result.items)
        assert any("No new wall framing" in a for a in result.assumptions)

    def test_wall_framing_when_there_are_walls(self, scheds, standards):
        result = build(scheds, MemberTakeoff(), make_geom(new_wall_lf=20), standards)
        studs = next(i for i in result.items if i.description == "Wall studs")
        # 20ft at 16" OC = 15 bays + 2 end studs = 17, +5% -> 18
        assert studs.quantity == 18
        assert studs.length_ft == 8


class TestHonesty:
    def test_unconfirmed_standards_are_surfaced(self, scheds, standards):
        standards.data["review_status"] = "UNCONFIRMED"
        result = build(scheds, MemberTakeoff(), make_geom(), standards)
        assert any("UNCONFIRMED" in w for w in result.warnings)

    def test_unreadable_members_are_surfaced(self, scheds, standards):
        mem = MemberTakeoff(unresolved=["RB1", "PB1"])
        result = build(scheds, mem, make_geom(), standards)
        assert any("RB1" in w and "PB1" in w for w in result.warnings)

    def test_treated_beams_are_labelled(self, scheds, standards):
        beam = Member("PB2", "beam", "5.5x7.5", "GLU-LAM", False, True, "", "S3.0")
        result = build(scheds, MemberTakeoff(members=[beam]), make_geom(), standards)
        assert any("pressure treated" in i.description for i in result.items)

    def test_every_line_records_where_it_came_from(self, scheds, standards):
        mem = MemberTakeoff(members=[rafter_member(), column("4x6")])
        result = build(scheds, mem, make_geom(post_count=1), standards)
        assert all(i.source for i in result.items)
