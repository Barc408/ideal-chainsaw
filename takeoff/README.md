# barc-takeoff

Reads a residential plan set and pulls out what a framing takeoff needs:
schedules, material specs, and the scope split between new work, demolition and
existing construction. Internal tool for BARC Builder Group.

Status: **stages 1–3 working, stage 4 (rules engine) not built.** The tool
reports what it read and what it still needs; it does not yet produce a lumber
list.

## Why it works this way

The obvious approach — hand each sheet to a vision model and ask for a lumber
list — is the wrong shape for this problem. Two findings from the first real
plan set drove the design:

**The schedules need no model at all.** Drawings exported from Bluebeam carry a
real vector text layer, and every word has coordinates. Rebuilding a schedule
by clustering words on their baselines is exact, free, instant, and auditable —
each figure traces to a coordinate on a named sheet. It is also strictly more
accurate than reading the rendered image: `pdftotext -layout` on the reference set
silently dropped two rows of the header schedule, and a model reading a
downsampled render can make the same class of error without saying so.

**The geometry mostly isn't there.** Every sheet is stamped *"DO NOT SCALE
DRAWINGS"*, the structural sheets say *"ALL DIMENSIONS REFER TO ARCHITECTURAL
DRAWINGS"*, and the architectural sheets dimension almost nothing about the new
patio structure. Numbers the architect did not draw cannot be recovered by
reading harder. So the tool asks for them instead of inventing them, and
refuses to emit quantities until it has them.

The split that follows: **what the engineer specified** is read from the plans
and is not negotiable; **how BARC builds and buys** lives in
`standards/barc-framing.yaml`; **what neither one states** is asked for.

## Usage

```sh
python -m barc_takeoff PLANS.pdf                        # report
python -m barc_takeoff PLANS.pdf --json                 # machine-readable
python -m barc_takeoff PLANS.pdf --measurements job.yaml
```

Exit status is non-zero when the set cannot support a complete takeoff — a
missing sheet, or an outstanding measurement — so it can gate a workflow rather
than just inform one.

`job.yaml` is a flat map of the measurement keys the report lists:

```yaml
roof_area_sf: 480
ridge_length_ft: 24
rafter_run_ft: 11
roof_perimeter_ft: 68
post_count: 3
post_height_ft: 9
new_wall_lf: 0
wall_height_ft: 8
```

Requires `poppler-utils` (`pdftotext`, `pdfinfo`) and `pyyaml`.

## Pipeline

| Stage | Module | What it does | Reliability |
|---|---|---|---|
| 1 | `pdfdoc`, `index` | Split sheets, read the text layer, identify each sheet, reconcile against the cover-sheet index | Deterministic |
| 2 | `tables`, `schedules`, `keynotes` | Rebuild schedules from coordinates; read keynotes and classify scope | Deterministic |
| 3 | `geometry` | State the measurements needed; offer dimensions actually printed on the sheets as candidates | Human supplies values |
| 4 | — | Apply BARC standards to produce the lumber list | **Not built** |

Stage 3 is isolated on purpose. Its uncertainty is the kind that produces a
wrong lumber order, so it is kept out of stages 1–2 rather than blended into
them.

## What it catches

Run against the reference progress set (23 sheets), it reports:

- **A missing sheet.** The cover index lists 24 sheets; the file has 23. A-0.3
  PROJECT CALCULATIONS is absent — likely the area and coverage figures that
  would feed a takeoff. Reported, not skipped.
- **Five flattened sheets.** The CalGreen sheets and both Simpson Strong-Wall
  detail sheets are raster images with no text layer. Their content was not
  extracted, and the report says so instead of treating them as empty.
- **Scope.** 35 keynotes sorted into new / demolish / existing-to-remain /
  unknown. On a remodel this is what stops the tool pricing the whole house
  instead of the patio. Notes it cannot classify are listed for a decision
  rather than guessed.
- **Every schedule** on S1.0: header (span → size, stud and jack counts),
  ceiling joist, pad, and column connector.

## Known limits

- **The shearwall schedule is extracted but not parsed into per-type values.**
  Cells merged across types cannot be distinguished from six tightly packed
  distinct values without the ruled cell boundaries, which are vector art and
  invisible to text extraction. It is flagged for manual reading rather than
  parsed into something plausible but possibly wrong.
- **Raster sheets need a different tool** — OCR or a vision pass. Not built.
- **The standards file is seeded with plausible defaults, not BARC's numbers.**
  It is marked `review_status: UNCONFIRMED` and every report says so until a
  human changes it.
- **Keynote text carries kerning artefacts** from the CAD export (`MA TCH`,
  `REMOV ED`). Matching ignores whitespace entirely, so classification is
  unaffected, but displayed text looks odd.
- **Only tested against one office's sheets** (Brad Cox Architect / MHA
  Consulting Engineers). Sheet layout conventions vary; `schedules.py` is where
  office-specific knowledge is meant to live.

## Tests

```sh
python -m pytest                                   # unit tests
BARC_TAKEOFF_FIXTURE=/path/to/plans.pdf python -m pytest   # + integration
```

Integration tests skip unless pointed at a plan set. The fixture is a client's
construction documents and is deliberately not committed.
