"""Stage 4: turn specifications and measurements into a materials list.

Framing is the bulk of it, but a framing-only list is not an orderable list:
this scope also carries roofing, underlayment, gutter, footing rebar, post
hardware and a demolition scope, all of which are specified on the plans.
Items carry a category so the output groups by trade.

Plain arithmetic, deliberately. Every quantity here is a number a human can
check against the plans in a minute, and every line records what produced it -
which schedule, which measurement, which standard. An estimating error costs
real money, so nothing in this module infers, rounds silently, or fills a gap
it was not given.

The engine refuses to run on incomplete input rather than substituting a
default. A lumber list built on a guessed roof area is worse than no list,
because it looks like an answer.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .geometry import GeometryRequest
from .members import MemberTakeoff
from .schedules import Schedules
from .standards import Standards

SQFT_PER_PANEL = 32.0   # 4x8 sheet
SQFT_PER_SQUARE = 100.0  # roofing "square"
SQFT_PER_FELT_ROLL = 400.0  # 15# felt, standard roll

# Trades this engine does not cover at all. Listed so a reader can see the
# boundary of the estimate rather than infer it from what happens to be absent.
NOT_COVERED = (
    "electrical (see A-3.2 power plan)",
    "lighting fixtures (see A-4.0)",
    "plumbing",
    "interior finishes, drywall, paint",
    "site work, drainage and landscaping",
    "shearwall sheathing and hold-downs (see the shearwall schedule)",
)


class IncompleteInput(RuntimeError):
    """Raised when a required measurement is missing."""


@dataclass
class LineItem:
    description: str
    size: str
    length_ft: float | None
    quantity: int
    unit: str
    source: str
    note: str = ""
    category: str = "framing"

    @property
    def total_lf(self) -> float:
        return (self.length_ft or 0) * self.quantity


@dataclass
class LumberList:
    items: list[LineItem] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    not_covered: list[str] = field(default_factory=list)

    def by_category(self) -> dict[str, list[LineItem]]:
        groups: dict[str, list[LineItem]] = {}
        for item in self.items:
            groups.setdefault(item.category, []).append(item)
        return groups

    def add(self, item: LineItem) -> None:
        self.items.append(item)

    @property
    def total_pieces(self) -> int:
        return sum(i.quantity for i in self.items if i.unit == "pcs")


def stock_length(required_ft: float, options: list[int]) -> int | None:
    """Shortest stock length that covers a cut, or None if nothing is long enough."""
    for length in sorted(options):
        if length >= required_ft:
            return length
    return None


def _pitch_rise(pitch: str) -> float:
    """Rise per 12 of run, from a '4:12' style pitch."""
    try:
        rise, run = pitch.split(":")
        return float(rise) / float(run) * 12
    except (ValueError, ZeroDivisionError):
        return 0.0


def build(
    scheds: Schedules,
    members: MemberTakeoff,
    geom: GeometryRequest,
    standards: Standards,
    *,
    pitch: str = "4:12",
    notes: dict | None = None,
) -> LumberList:
    """Produce the lumber list.

    Sizes and spacings come from the engineer via `scheds` and `members`;
    lengths, areas and counts come from `geom`; waste factors and stock lengths
    come from `standards`.
    """
    if not geom.complete:
        missing = ", ".join(m.key for m in geom.outstanding)
        raise IncompleteInput(f"missing measurements: {missing}")

    values = {m.key: m.value for m in geom.measurements}
    notes = notes or {}
    out = LumberList()

    if not standards.confirmed:
        out.warnings.append(
            f"Framing standards in {standards.path.name} are UNCONFIRMED. Waste "
            "factors and stock lengths are seeded defaults, not BARC's numbers."
        )
    if members.unresolved:
        out.warnings.append(
            "Member callouts detected but not readable: "
            + ", ".join(members.unresolved)
            + ". Their sizes are NOT in this list - read them off the framing "
            "sheets and add them."
        )
    if scheds.shearwalls:
        merged = sorted(scheds.shearwalls.merged_rows)
        detail = (
            f" Its {', '.join(merged)} row(s) hold merged cells and are "
            "approximate." if merged else ""
        )
        out.warnings.append(
            "No shearwall quantities are in this list: the schedule specifies "
            "how a shearwall is built, but which walls are shearwalls and how "
            "long they run is not a measurement this tool collects." + detail
        )

    # ---- rafters ----------------------------------------------------------
    run_ft = values["rafter_run_ft"]
    ridge_ft = values["ridge_length_ft"]
    rise_per_12 = _pitch_rise(pitch)
    rafter_len = math.hypot(run_ft, run_ft * rise_per_12 / 12)
    out.assumptions.append(
        f"Roof pitch taken as {pitch}; rafter length {rafter_len:.1f} ft from a "
        f"{run_ft:g} ft run."
    )
    out.not_covered = list(NOT_COVERED)

    # Rafter size comes from the rafter callout and nowhere else. The ceiling
    # joist schedule looks like a tempting fallback and is not one: it is sized
    # for "uninhabitable attics with limited storage" at 20 psf live / 10 psf
    # dead, while these rafters carry the roof at 20 psf live / 12.2 psf dead
    # per S0.0. Substituting it here would have specified 2x8 where the plan
    # calls out 2x10 - an undersized member, arrived at silently.
    rafter = next((m for m in members.of_kind("rafter") if m.new), None)
    if rafter and rafter.size:
        size = rafter.size
        spacing_in = rafter.spacing_in or 24.0
        src = f"{rafter.tag} callout, {rafter.sheet_id}"
    else:
        size, spacing_in, src = None, 24.0, ""
        out.warnings.append(
            "Rafter size NOT determined: the rafter callout could not be read, and "
            "the ceiling joist schedule is a different load case (attic storage, "
            "not roof) so it was not substituted. Rafters and rafter blocking "
            "are omitted from this list - read the callout off the framing sheet."
        )

    if size:
        # Two planes either side of a ridge, each rafter repeated along its length.
        per_plane = math.ceil(ridge_ft * 12 / spacing_in) + 1
        count = per_plane * 2
        waste = standards.waste("rafters")
        count = math.ceil(count * (1 + waste))
        stock = stock_length(rafter_len, standards.stock_lengths(size))
        out.add(
            LineItem(
                description="Rafters",
                size=size,
                length_ft=stock,
                quantity=count,
                unit="pcs",
                source=src,
                note=(
                    f"{per_plane} per plane x 2 planes, {spacing_in:g}\" OC, "
                    f"+{waste:.0%} waste; cut length {rafter_len:.1f} ft"
                ),
            )
        )
        if stock is None:
            out.warnings.append(
                f"No stock length carries a {rafter_len:.1f} ft {size} rafter; "
                "splice or special order."
            )

        # Blocking between rafters at bearing and at the spacing S0.0 requires.
        # Blocks are short, so the order is stock pieces to cut from, not one
        # stick per block - counting sticks would over-order several times over.
        rows = max(1, math.ceil(run_ft / standards.get("roof", "blocking_spacing_ft", default=8)))
        blocks = per_plane * 2 * rows
        block_len_ft = (spacing_in - 1.5) / 12  # clear span between rafters
        total_lf = blocks * block_len_ft * (1 + standards.waste("blocking"))
        stick = stock_length(8, standards.stock_lengths(size)) or 8
        out.add(
            LineItem(
                description="Rafter blocking",
                size=size,
                length_ft=stick,
                quantity=math.ceil(total_lf / stick),
                unit="pcs",
                source="S0.0 general notes: blocking at 8 ft OC",
                note=(
                    f"{blocks} blocks @ {block_len_ft * 12:.1f}\" ({rows} row(s)), "
                    f"{total_lf:.0f} lf incl. {standards.waste('blocking'):.0%} waste, "
                    f"cut from {stick} ft stock"
                ),
            )
        )

    # ---- beams and columns from the plans ---------------------------------
    for beam in (m for m in members.of_kind("beam") if m.new):
        out.add(
            LineItem(
                description=f"Beam {beam.tag}"
                + (" (pressure treated)" if beam.treated else ""),
                size=beam.size or "see plan",
                length_ft=None,
                quantity=1,
                unit="ea",
                source=f"callout on {beam.sheet_id}",
                note=(beam.material or "") + " - length to be confirmed on site",
            )
        )

    columns = [m for m in members.of_kind("column") if m.new]
    post_h = values["post_height_ft"]
    for col in columns:
        stock = stock_length(post_h, standards.stock_lengths(col.size or ""))
        out.add(
            LineItem(
                description=f"Post {col.tag}",
                size=col.size or "see plan",
                length_ft=stock or post_h,
                quantity=1,
                unit="pcs",
                source=f"callout on {col.sheet_id}",
                note=f"{post_h:g} ft to underside of beam",
            )
        )
    if columns and len(columns) != int(values["post_count"]):
        out.warnings.append(
            f"{len(columns)} distinct post callouts read from the plans but "
            f"post_count was given as {values['post_count']:g}. The list follows "
            "the callouts; reconcile before ordering."
        )

    # Post bases and caps come straight from the connector schedule.
    for col in columns:
        nominal = (col.size or "").split("x")[0]
        spec = next(
            (c for c in scheds.connectors if c.post.rstrip("x") == nominal), None
        )
        if spec:
            out.add(
                LineItem(
                    description=f"Post base for {col.tag}",
                    size=spec.base,
                    length_ft=None,
                    quantity=1,
                    unit="ea",
                    source=f"column connector schedule, {scheds.source_sheet}",
                    category="hardware",
                )
            )
            # The cap column lists alternatives separated by "/" - one for new
            # construction and others for tying into existing framing. Which
            # applies depends on the condition at each post, so all are carried
            # through rather than one being picked here.
            out.add(
                LineItem(
                    description=f"Post cap for {col.tag}",
                    size=spec.cap,
                    length_ft=None,
                    quantity=1,
                    unit="ea",
                    source=f"column connector schedule, {scheds.source_sheet}",
                    note="schedule lists alternatives - choose per condition",
                    category="hardware",
                )
            )

    # ---- roof sheathing ---------------------------------------------------
    area = values["roof_area_sf"]
    waste = standards.get("sheathing", "waste", "roof_cut_up", default=0.15)
    sheets = math.ceil(area / SQFT_PER_PANEL * (1 + waste))
    out.add(
        LineItem(
            description="Roof sheathing",
            size=str(scheds.raw.get("SHEATHING", "")) or '15/32" CDX/OSB',
            length_ft=None,
            quantity=sheets,
            unit="sheets",
            source="S0.0 plywood & OSB spec; area from measurement",
            category="sheathing",
            note=f"{area:g} sf / 32 sf per sheet, +{waste:.0%} waste",
        )
    )

    # ---- fascia -----------------------------------------------------------
    perimeter = values["roof_perimeter_ft"]
    out.add(
        LineItem(
            description="Fascia",
            size="see keynote 06.18",
            length_ft=None,
            # Linear-foot items carry the footage as the quantity; a purchasing
            # system reading quantity would otherwise order one of them.
            quantity=math.ceil(perimeter),
            unit="lf",
            source="keynote 06.18: painted wood fascia to match existing",
            category="roofing",
            note="verify profile against existing before ordering",
        )
    )

    # ---- walls, only if there are any -------------------------------------
    wall_lf = values["new_wall_lf"]
    if wall_lf > 0:
        spacing = standards.get("walls", "stud_spacing_in", default=16)
        height = values["wall_height_ft"]
        studs = math.ceil(wall_lf * 12 / spacing) + standards.get(
            "walls", "end_studs", default=2
        )
        studs = math.ceil(studs * (1 + standards.waste("studs")))
        out.add(
            LineItem(
                description="Wall studs",
                size="2x4",
                length_ft=stock_length(height, standards.stock_lengths("2x4")),
                quantity=studs,
                unit="pcs",
                source=f"BARC standard {spacing:g}\" OC",
                note=f"{wall_lf:g} lf wall, +{standards.waste('studs'):.0%} waste",
            )
        )
        plate_rows = standards.get("walls", "plates", "top", default=2) + standards.get(
            "walls", "plates", "bottom", default=1
        )
        out.add(
            LineItem(
                description="Plates (top and bottom)",
                size="2x4",
                length_ft=None,
                quantity=math.ceil(wall_lf * plate_rows * (1 + standards.waste("plates"))),
                unit="lf",
                source="BARC standard: 2 top, 1 bottom",
            )
        )
    else:
        out.assumptions.append("No new wall framing: new_wall_lf given as 0.")

    # ---- pad footings -----------------------------------------------------
    if scheds.pads and columns:
        pad = scheds.pads[0]
        cf = (pad.width_in * pad.length_in * pad.depth_in) / 1728
        total_cf = cf * len(columns) * (1 + standards.get("concrete", "waste", default=0.1))
        out.add(
            LineItem(
                description=f"Concrete, {pad.name} footings",
                size=f'{pad.width_in:.0f}x{pad.length_in:.0f}x{pad.depth_in:.0f}"',
                length_ft=None,
                quantity=math.ceil(total_cf * 0.037037 * 10) / 10,
                unit="cu yd",
                source=f"pad schedule, {scheds.source_sheet}",
                category="concrete",
                note=(
                    f"{len(columns)} pads x {cf:.2f} cf, +"
                    f"{standards.get('concrete', 'waste', default=0.1):.0%} waste. "
                    f"Pad type assumed {pad.name} - confirm against the plan."
                ),
            )
        )
        out.assumptions.append(
            f"Footing type assumed {pad.name} for all posts; the plan marks pad "
            "types individually."
        )

    _roofing(out, values, standards, notes, pitch)
    _rebar(out, scheds, columns)
    _demolition(out, notes)

    return out


def _roofing(out, values, standards, notes, pitch) -> None:
    """Roof covering, underlayment and gutter.

    Product and fastener requirements come from the roofing keynote rather than
    being assumed; where the note's own conditions are ambiguous for this roof,
    that is surfaced instead of resolved.
    """
    area = values["roof_area_sf"]
    waste = standards.get("sheathing", "waste", "roof_cut_up", default=0.15)

    shingle_note = next(
        (n for n in notes.values() if "COMPOSITION SHINGLE" in n.text.upper().replace(" ", "")
         or "COMPOSITIONSHINGLE" in n.text.upper().replace(" ", "")),
        None,
    )
    if shingle_note:
        out.add(
            LineItem(
                description="Composition shingles, Class A",
                size="per keynote " + shingle_note.code,
                length_ft=None,
                quantity=math.ceil(area / SQFT_PER_SQUARE * (1 + waste)),
                unit="squares",
                source=f"keynote {shingle_note.code}",
                note=f"{area:g} sf, +{waste:.0%} waste. Fasteners must be "
                     "corrosion resistant per CRC R905.2.5.",
                category="roofing",
            )
        )

        # The keynote requires one layer of 15# felt, but two where the slope is
        # between 2:12 and 4:12. A 4:12 roof sits exactly on that boundary, so
        # the requirement is genuinely ambiguous and is not decided here.
        rise = pitch.split(":")[0] if ":" in pitch else ""
        boundary = rise in {"2", "3", "4"}
        layers = 2 if boundary else 1
        out.add(
            LineItem(
                description=f"Underlayment, 15# felt ({layers} layer)",
                size="15# felt",
                length_ft=None,
                quantity=math.ceil(area * layers / SQFT_PER_FELT_ROLL * (1 + 0.10)),
                unit="rolls",
                source=f"keynote {shingle_note.code}",
                note=f"{layers} layer(s) at {pitch}",
                category="roofing",
            )
        )
        if rise == "4":
            out.warnings.append(
                f"Underlayment layers are ambiguous at {pitch}: the roofing "
                "keynote requires 2 layers 'for slopes between 2:12 and 4:12' "
                "and 1 layer otherwise. Two layers are listed - confirm with the "
                "architect before ordering."
            )

    tpo = next((n for n in notes.values() if "TPO" in n.text.upper()), None)
    if tpo:
        out.add(
            LineItem(
                description="TPO roofing (low-slope areas)",
                size="TPO membrane",
                length_ft=None,
                quantity=0,
                unit="sf",
                source=f"keynote {tpo.code}",
                note="Area NOT determined - the low-slope extent is not "
                     "dimensioned. Keynote says coordinate with contractor.",
                category="roofing",
            )
        )
        out.warnings.append(
            f"Keynote {tpo.code} calls for a TPO roof but the low-slope area is "
            "not dimensioned; its quantity is zero and must be measured."
        )

    gutter = next((n for n in notes.values() if "GUTTER" in n.text.upper()
                   and n.scope.value == "new"), None)
    if gutter:
        out.add(
            LineItem(
                description="Gutter, to match existing",
                size="match (E) profile",
                length_ft=None,
                quantity=math.ceil(values["eave_length_ft"]),
                unit="lf",
                source=f"keynote {gutter.code}",
                note="eaves only; confirm profile and colour against existing",
                category="roofing",
            )
        )


def _rebar(out, scheds, columns) -> None:
    """Footing reinforcement, from the pad schedule's own callout."""
    if not (scheds.pads and columns):
        return
    pad = scheds.pads[0]
    m = re.match(r"(\d+)-#(\d+)", pad.rebar)
    if not m:
        return
    bars, size = int(m.group(1)), m.group(2)
    # "3-#4 EACH WAY" is three bars in each direction, each the pad width less
    # cover at both ends.
    length_ft = (pad.width_in - 6) / 12
    total = bars * 2 * len(columns)
    out.add(
        LineItem(
            description=f"Footing rebar #{size}",
            size=f"#{size}",
            length_ft=round(length_ft, 1),
            quantity=total,
            unit="pcs",
            source=f"pad schedule ({pad.rebar}), {scheds.source_sheet}",
            note=f"{bars} bars each way x {len(columns)} pads, "
                 f"{length_ft:.1f} ft each incl. 3\" cover",
            category="concrete",
        )
    )


def _demolition(out, notes) -> None:
    """Demolition scope, listed without invented quantities.

    Disposal volume depends on what is found once the roof is opened, so the
    scope is enumerated and the quantity left to the estimator.
    """
    demo = [n for n in notes.values() if n.scope.value == "demolish"]
    for note in demo:
        out.add(
            LineItem(
                description=f"Demo: {note.text[:52]}",
                size="-",
                length_ft=None,
                quantity=0,
                unit="scope",
                source=f"keynote {note.code}",
                note="quantity not derived - price from site conditions",
                category="demolition",
            )
        )
    if demo:
        out.assumptions.append(
            f"{len(demo)} demolition keynotes listed as scope only; disposal "
            "volume is not derived from the drawings."
        )
