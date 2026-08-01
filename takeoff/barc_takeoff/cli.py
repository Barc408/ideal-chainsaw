"""Command line entry point.

    python -m barc_takeoff PLANS.pdf
    python -m barc_takeoff PLANS.pdf --measurements job.yaml
    python -m barc_takeoff PLANS.pdf --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from . import (
    geometry,
    index,
    keynotes,
    lumber,
    members,
    pdfdoc,
    report,
    schedules,
    standards,
)


def _as_dict(plan, recon, scheds, notes, geom, std, mem, lst):
    return {
        "plan_set": plan.path.name,
        "pages": len(plan.sheets),
        "sheets": [
            {
                "page": s.page,
                "sheet_id": s.sheet_id,
                "title": s.title,
                "raster": s.is_raster,
            }
            for s in plan.sheets
        ],
        "index": {
            "listed": len(recon.listed),
            "present": len(recon.present),
            "missing": [{"sheet_id": e.sheet_id, "title": e.title} for e in recon.missing],
            "ok": recon.ok,
        },
        "schedules": {
            "source_sheet": scheds.source_sheet,
            "headers": [vars(h) for h in scheds.headers],
            "joists": [vars(j) for j in scheds.joists],
            "pads": [vars(p) for p in scheds.pads],
            "connectors": [vars(c) for c in scheds.connectors],
            "shearwalls_need_review": bool(
                scheds.shearwalls and scheds.shearwalls.needs_review
            ),
        },
        "keynotes": [
            {"code": n.code, "text": n.text, "scope": n.scope.value, "sheet": n.sheet_id}
            for n in notes.values()
        ],
        "measurements": [
            {
                "key": m.key,
                "label": m.label,
                "unit": m.unit,
                "why": m.why,
                "sheets": list(m.sheets),
                "candidates": m.candidates,
                "value": m.value,
                "satisfied": m.satisfied,
            }
            for m in geom.measurements
        ],
        "members": [
            {
                "tag": m.tag,
                "kind": m.kind,
                "size": m.size,
                "material": m.material,
                "existing": m.existing,
                "treated": m.treated,
                "sheet": m.sheet_id,
            }
            for m in mem.members
        ],
        "members_unresolved": mem.unresolved,
        "lumber_list": None
        if lst is None
        else {
            "items": [
                {
                    "description": i.description,
                    "size": i.size,
                    "length_ft": i.length_ft,
                    "quantity": i.quantity,
                    "unit": i.unit,
                    "source": i.source,
                    "note": i.note,
                }
                for i in lst.items
            ],
            "assumptions": lst.assumptions,
            "warnings": lst.warnings,
        },
        "blocked_on": [m.key for m in geom.outstanding],
        "standards_confirmed": std.confirmed,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="barc-takeoff",
        description="Read a plan set and report what a framing takeoff needs.",
    )
    ap.add_argument("pdf", type=Path, help="plan set PDF")
    ap.add_argument(
        "--measurements",
        type=Path,
        help="YAML of measurement key: value pairs from the estimator",
    )
    ap.add_argument("--standards", type=Path, help="override framing standards file")
    ap.add_argument("--schedule-sheet", default="S1.0", help="sheet to read schedules from")
    ap.add_argument(
        "--pitch",
        default=None,
        help="roof pitch for rafter length; read off the roof plan when omitted",
    )
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = ap.parse_args(argv)

    try:
        plan = pdfdoc.load(args.pdf)
    except pdfdoc.MissingDependency as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"error: no such file: {args.pdf}", file=sys.stderr)
        return 2

    recon = index.reconcile(plan)
    scheds = schedules.read(plan, args.schedule_sheet)
    notes = keynotes.read(plan)
    geom = geometry.required(plan)
    mem = members.read(plan)
    std = standards.load(args.standards)

    if args.measurements:
        with open(args.measurements) as fh:
            geom.apply(yaml.safe_load(fh) or {})

    detected, tally = geometry.detect_pitch(plan)
    pitch = args.pitch or detected or "4:12"

    lumber_list = None
    if geom.complete:
        try:
            lumber_list = lumber.build(
                scheds, mem, geom, std, pitch=pitch, notes=notes
            )
        except lumber.IncompleteInput as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    if args.json:
        print(
            json.dumps(
                _as_dict(plan, recon, scheds, notes, geom, std, mem, lumber_list),
                indent=2,
            )
        )
    else:
        print(
            report.render(
                plan, recon, scheds, notes, geom, std, mem, lumber_list
            )
        )

    # Non-zero when the set cannot support a complete takeoff, so this can gate
    # a workflow rather than just inform one.
    return 0 if (recon.ok and geom.complete) else 1


if __name__ == "__main__":
    raise SystemExit(main())
