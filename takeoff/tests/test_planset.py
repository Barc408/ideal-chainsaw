"""Integration tests against a real plan set.

The fixture is a client's construction documents and is deliberately not stored
in the repository. Point BARC_TAKEOFF_FIXTURE at a copy to run these.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from barc_takeoff import geometry, index, keynotes, members, pdfdoc, schedules

FIXTURE = os.environ.get("BARC_TAKEOFF_FIXTURE")

pytestmark = pytest.mark.skipif(
    not FIXTURE or not Path(FIXTURE).exists(),
    reason="set BARC_TAKEOFF_FIXTURE to a plan set PDF to run integration tests",
)


@pytest.fixture(scope="module")
def plan():
    return pdfdoc.load(FIXTURE)


class TestSheetIdentification:
    def test_reads_every_page(self, plan):
        assert len(plan.sheets) == 23

    def test_page_count_is_read_from_the_file(self, plan):
        """Upload metadata reported 75 pages for this file; the file says 23."""
        assert pdfdoc.page_count(plan.path) == len(plan.sheets)

    def test_identifies_sheet_numbers(self, plan):
        ids = [s.sheet_id for s in plan.sheets]
        assert ids[0] == "A-0.0"
        assert ids[-1] == "S3.0"
        assert "S1.0" in ids

    def test_titles_exclude_field_labels(self, plan):
        """Title selection is by type size, so 'DATE' and 'LEVEL' stay out."""
        assert plan.sheet("S1.0").title == "1ST STORY & FOUNDATION"
        assert plan.sheet("A-4.0").title == "CEILING & LIGHTING PLAN"

    def test_flags_flattened_sheets(self, plan):
        raster = {s.sheet_id for s in plan.raster_sheets}
        assert {"A-0.1", "A-0.2", "S0.2", "S0.3"} <= raster
        assert "S1.0" not in raster


class TestIndexReconciliation:
    def test_detects_the_missing_sheet(self, plan):
        recon = index.reconcile(plan)
        assert [e.sheet_id for e in recon.missing] == ["A-0.3"]
        assert not recon.ok

    def test_names_the_untitled_raster_page_from_index_order(self, plan):
        index.reconcile(plan)
        assert plan.sheets[1].sheet_id == "BFCB"

    def test_index_lists_more_than_the_file_holds(self, plan):
        recon = index.reconcile(plan)
        assert len(recon.listed) == 24
        assert len(recon.present) == 23


@pytest.fixture(scope="module")
def scheds(plan):
    return schedules.read(plan)


@pytest.fixture(scope="module")
def notes(plan):
    return keynotes.read(plan)


class TestSchedules:
    def test_header_schedule_keeps_every_row(self, scheds):
        """Plain -layout extraction drops the 10\" and 12\" rows entirely."""
        assert len(scheds.headers) == 4
        assert [h.depth for h in scheds.headers] == ['6"', '8"', '10"', '12"']

    def test_header_stud_and_jack_counts(self, scheds):
        assert [(h.studs, h.jacks) for h in scheds.headers] == [
            (1, 1), (2, 1), (3, 2), (3, 2)
        ]

    def test_joist_schedule(self, scheds):
        assert [j.size for j in scheds.joists] == ["2x4", "2x6", "2x8", "2x10"]
        assert all(j.spacing == '24" OC' for j in scheds.joists)

    def test_pad_schedule_dimensions(self, scheds):
        assert len(scheds.pads) == 4
        assert scheds.pads[0].width_in == 18
        assert scheds.pads[-1].depth_in == 18

    def test_connectors_exclude_title_block_bleed(self, scheds):
        """A too-wide window pulls 'ADDITION/' and 'REMODEL' into the cap column."""
        caps = " ".join(c.cap for c in scheds.connectors)
        assert "ADDITION" not in caps and "REMODEL" not in caps

    def test_shearwall_matrix_is_flagged_for_review(self, scheds):
        assert scheds.shearwalls is not None
        assert scheds.shearwalls.needs_review


class TestKeynotes:
    def test_reads_keynotes(self, notes):
        assert len(notes) > 25
        assert "07.04" in notes

    def test_separates_demolition_from_retention(self, notes):
        assert notes["07.03"].scope is keynotes.Scope.EXISTING_REMAIN
        assert notes["07.04"].scope is keynotes.Scope.DEMOLISH

    def test_last_note_does_not_absorb_the_legend_below_it(self, notes):
        """Unbounded, 07.31 ran into 'ROOF LEGEND: EXISTING ROOF TO REMAIN'."""
        assert "LEGEND" not in notes["07.31"].text
        assert notes["07.31"].scope is not keynotes.Scope.EXISTING_REMAIN


class TestGeometry:
    def test_nothing_is_satisfied_without_input(self, plan):
        req = geometry.required(plan)
        assert not req.complete
        assert len(req.outstanding) == len(req.measurements)

    def test_candidates_exclude_schedule_span_limits(self, plan):
        """'UP TO 10'-8\"' is a lookup bound, not a dimension of this building."""
        req = geometry.required(plan)
        ridge = next(m for m in req.measurements if m.key == "ridge_length_ft")
        assert "10'-8\"" not in ridge.candidates

    def test_supplying_values_satisfies_them(self, plan):
        req = geometry.required(plan)
        req.apply({m.key: 10 for m in req.measurements})
        assert req.complete


@pytest.fixture(scope="module")
def mem(plan):
    return members.read(plan)


class TestMemberCallouts:
    def test_reads_tagged_beams(self, mem):
        beams = {m.tag for m in mem.of_kind("beam")}
        assert "PB2" in beams

    def test_size_survives_case_folding(self, mem):
        """Upper-casing the line first turns 5.5x7.5 into 5.5X7.5 and stops matching."""
        pb2 = next(m for m in mem.of_kind("beam") if m.tag == "PB2")
        assert pb2.size == "5.5x7.5"
        assert pb2.material == "GLU-LAM"

    def test_treatment_is_read_from_the_continuation_line(self, mem):
        """'EXTERIOR TREATED' sits below the callout; treated glulam is a
        different product at a different price."""
        pb2 = next(m for m in mem.of_kind("beam") if m.tag == "PB2")
        assert pb2.treated

    def test_reads_columns(self, mem):
        sizes = {m.size for m in mem.of_kind("column")}
        assert {"4x6", "6x6"} <= sizes

    def test_reads_rotated_callouts(self, mem):
        """RB1 is set on a rotated leader, reading bottom-to-top."""
        rb1 = next(m for m in mem.of_kind("beam") if m.tag == "RB1")
        assert rb1.size == "5.5x11.875"
        assert rb1.material == "GLU-LAM"

    def test_reads_the_rafter_callout_with_its_spacing(self, mem):
        """RR 2x10 @ 24" OC - rotated, and its short tokens ("OC", "24")
        measure wider than tall, so aspect ratio alone does not find it."""
        rr = next(m for m in mem.of_kind("rafter") if m.new)
        assert rr.size == "2x10"
        assert rr.spacing_in == 24

    def test_existing_rafters_are_not_read_as_new_members(self, mem):
        """RFTR appears only as "(E) RFTR UNDERNEATH" on this set."""
        assert all(m.tag != "RFTR" for m in mem.members)

    def test_nothing_is_left_unresolved_on_this_set(self, mem):
        assert mem.unresolved == []
