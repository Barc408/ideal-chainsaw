"""Render what was read off a plan set, and what is still needed.

The report is deliberately explicit about provenance. Every extracted figure
names the sheet it came from, and everything unread - raster sheets, missing
sheets, undimensioned geometry - is listed rather than omitted, because a
takeoff that quietly drops a sheet looks exactly like one that covered it.
"""

from __future__ import annotations

from .geometry import GeometryRequest
from .index import Reconciliation
from .keynotes import Keynote, Scope
from .lumber import LumberList
from .members import MemberTakeoff
from .pdfdoc import PlanSet
from .schedules import Schedules
from .standards import Standards

RULE = "=" * 78


def _h(title: str) -> str:
    return f"\n{RULE}\n{title}\n{RULE}"


def render(
    plan: PlanSet,
    recon: Reconciliation,
    scheds: Schedules,
    notes: dict[str, Keynote],
    geom: GeometryRequest,
    standards: Standards,
    members: MemberTakeoff | None = None,
    lumber_list: LumberList | None = None,
) -> str:
    out: list[str] = []
    out.append(f"PLAN SET: {plan.path.name}")
    out.append(f"{len(plan.sheets)} pages")

    # ---- sheets -----------------------------------------------------------
    out.append(_h("1. SHEETS"))
    out.append(recon.summary())
    if recon.missing:
        out.append(
            "  ! A sheet listed in the index is absent. Anything it carried is "
            "not in this takeoff."
        )
    raster = plan.raster_sheets
    if raster:
        out.append(
            f"\n  {len(raster)} sheet(s) are flattened images with no readable text; "
            "their content was not extracted:"
        )
        for s in raster:
            out.append(f"    - {s.sheet_id or f'page {s.page}'}: {s.title or ''}")

    # ---- schedules --------------------------------------------------------
    out.append(_h(f"2. SCHEDULES (from {scheds.source_sheet or 'n/a'})"))
    if scheds.headers:
        out.append("\nHeader schedule - span to framing:")
        out.append(f"  {'span':<14}{'width':<14}{'depth':<8}{'studs':<7}jacks")
        for h in scheds.headers:
            out.append(
                f"  {h.span_label:<14}{h.width:<14}{h.depth:<8}{h.studs:<7}{h.jacks}"
            )
    if scheds.joists:
        out.append("\nCeiling joist schedule:")
        out.append(f"  {'type':<6}{'max span':<14}{'size':<8}{'spacing':<10}hanger")
        for j in scheds.joists:
            out.append(
                f"  {j.type:<6}{j.span_label:<14}{j.size:<8}{j.spacing:<10}{j.hanger}"
            )
    if scheds.pads:
        out.append("\nPad schedule:")
        for p in scheds.pads:
            cf = (p.width_in * p.length_in * p.depth_in) / 1728
            out.append(
                f"  {p.name:<8}{p.width_in:.0f}x{p.length_in:.0f}x{p.depth_in:.0f}in"
                f"   {cf:5.2f} cf   {p.rebar}"
            )
    if scheds.connectors:
        out.append("\nColumn connectors:")
        for c in scheds.connectors:
            out.append(f"  {c.post:<12}base {c.base:<14}cap {c.cap}")
    if scheds.shearwalls:
        sw = scheds.shearwalls
        exact = sw.exact_rows()
        out.append(
            f"\nShearwall schedule - {len(exact)} row(s) parsed per type, "
            f"{len(sw.merged_rows)} approximate:"
        )
        out.append("  " + "property".ljust(22) + "".join(c.ljust(16) for c in sw.columns))
        for label, vals in exact.items():
            out.append(
                "  " + label[:21].ljust(22)
                + "".join((vals.get(c, "") or "-")[:15].ljust(16) for c in sw.columns)
            )
        if sw.merged_rows:
            out.append(
                "\n  ! " + ", ".join(sorted(sw.merged_rows)) + " hold cells merged "
                "across types. A merged cell's\n    span cannot be recovered "
                f"exactly; read those rows off {sw.sheet_id} directly."
            )

    # ---- members ----------------------------------------------------------
    if members is not None:
        out.append(_h("2b. MEMBERS CALLED OUT ON THE FRAMING SHEETS"))
        for m in members.members:
            state = "(E)" if m.existing else "new"
            extra = " pressure treated" if m.treated else ""
            out.append(
                f"  {state:<5}{m.kind:<9}{m.tag:<12}{str(m.size or '-'):<12}"
                f"{m.material or '':<9}{extra}   [{m.sheet_id}]"
            )
        if members.unresolved:
            out.append(
                "\n  ! Detected but unreadable (rotated or split annotations): "
                + ", ".join(members.unresolved)
            )
            out.append(
                "    Their specifications are not in this takeoff. Read them off "
                "the sheet."
            )

    # ---- scope ------------------------------------------------------------
    out.append(_h("3. SCOPE (from keynotes)"))
    buckets: dict[Scope, list[Keynote]] = {}
    for note in notes.values():
        buckets.setdefault(note.scope, []).append(note)
    for scope in (Scope.NEW, Scope.DEMOLISH, Scope.EXISTING_REMAIN, Scope.UNKNOWN):
        items = buckets.get(scope, [])
        out.append(f"\n{scope.value}: {len(items)}")
        if scope in (Scope.NEW, Scope.DEMOLISH, Scope.UNKNOWN):
            for n in items:
                out.append(f"    {n.code}  {n.text[:66]}")
    if buckets.get(Scope.UNKNOWN):
        out.append(
            "\n  ! 'unknown' notes were not classified automatically and need a "
            "scope decision."
        )

    # ---- geometry ---------------------------------------------------------
    out.append(_h("4. MEASUREMENTS REQUIRED"))
    out.append(
        "These are not on the sheets in usable form. The sheets carry "
        '"DO NOT SCALE\nDRAWINGS", so they must be measured or field-verified '
        "before quantities exist.\n"
    )
    for m in geom.measurements:
        status = f"{m.value:g} {m.unit}" if m.satisfied else "NEEDED"
        out.append(f"  [{status:>9}]  {m.label}  ({m.unit})")
        out.append(f"               {m.why}")
        out.append(f"               see {', '.join(m.sheets)}")
        if m.candidates and not m.satisfied:
            out.append(
                f"               dimensions printed on those sheets: "
                f"{', '.join(m.candidates[:8])}"
            )
        out.append("")

    # ---- lumber list ------------------------------------------------------
    out.append(_h("5. LUMBER LIST"))
    if lumber_list is None:
        out.append(
            f"NOT PRODUCED - {len(geom.outstanding)} of {len(geom.measurements)} "
            "measurements outstanding."
        )
        out.append(
            "\nQuantities are withheld rather than estimated. Supply the "
            "measurements above\n(--measurements file.yaml) and re-run."
        )
    else:
        for category, items in lumber_list.by_category().items():
            out.append(f"\n  {category.upper()}")
            out.append(
                f"  {'item':<44}{'size':<18}{'len':>5}{'qty':>7}  unit"
            )
            out.append("  " + "-" * 82)
            for it in items:
                length = f"{it.length_ft:g}" if it.length_ft else "-"
                qty = f"{it.quantity:g}"
                out.append(
                    f"  {it.description[:43]:<44}{str(it.size)[:17]:<18}"
                    f"{length:>5}{qty:>7}  {it.unit}"
                )
        out.append("\n  Provenance:")
        for it in lumber_list.items:
            out.append(f"    {it.description[:28]:<30} <- {it.source}")
            if it.note:
                out.append(f"    {'':<30}    {it.note}")

        if lumber_list.assumptions:
            out.append("\n  Assumptions:")
            for a in lumber_list.assumptions:
                out.append(f"    - {a}")
        if lumber_list.warnings:
            out.append("\n  NOT INCLUDED / UNVERIFIED:")
            for w in lumber_list.warnings:
                out.append(f"    ! {w}")
        if lumber_list.not_covered:
            out.append("\n  OUT OF SCOPE for this tool (price separately):")
            for n in lumber_list.not_covered:
                out.append(f"    - {n}")

    if not standards.confirmed:
        out.append(
            f"\n! Framing standards at {standards.path.name} are marked "
            "UNCONFIRMED.\n  Waste factors and stock lengths are seeded "
            "defaults, not BARC's actual numbers."
        )
    return "\n".join(out)
