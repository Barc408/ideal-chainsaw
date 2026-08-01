# barc-takeoff

Reads a residential plan set and pulls out what a takeoff needs: schedules,
member callouts, material specs, and the scope split between new work,
demolition and existing construction. Produces a materials list grouped by
trade — framing, hardware, sheathing, roofing, concrete, demolition. Internal
tool for BARC Builder Group.

Status: **all four stages working.** Given the measurements the drawings omit,
it produces a lumber list with provenance on every line. The framing standards
it prices against are seeded defaults and still need review.

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

**Rotated text is readable, but not by aspect ratio.** Callouts on vertical
leaders stack one word per box, reading bottom-to-top. The tempting test — a
rotated word's box is taller than it is wide — fails on short tokens: rotated
`OC` and `24` still measure wider than tall, which is precisely how the rafter
callout got missed on the first pass. The reliable signal is that every box in
a rotated column shares the same width, because that width is the line height
regardless of character count.

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
eave_length_ft: 48
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
| 2b | `members` | Read beam, column and rafter callouts annotated on the framing sheets | Deterministic |
| 3 | `geometry` | State the measurements needed; offer dimensions actually printed on the sheets as candidates | Human supplies values |
| 4 | `lumber` | Apply BARC standards to produce the materials list | Arithmetic, fully traced |

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
- **Member callouts** off the framing sheets, including ones set on rotated
  leaders. `PB2 5.5x7.5 24F-V4 GLU-LAM` with its *pressure treated* qualifier;
  `RB1 5.5x11.875 24F-V4 GLU-LAM` read bottom-to-top off a vertical leader; the
  rafters as `RR 2x10 @ 24" OC`; the 4x6 / 6x6 / 4x12 columns and the PSL
  trimmer. Nothing on this set is left unresolved.

## What it refuses to do

Every one of these is a place where a plausible guess would have produced a
worse outcome than an admission:

- **It will not size rafters from the ceiling joist schedule.** That schedule
  is for *uninhabitable attics with limited storage* at 20 psf live / 10 psf
  dead; these rafters carry the roof at 20/12.2 per S0.0. An early version
  substituted it and specified 2x8 where the plan calls out 2x10 — an
  undersized member, arrived at silently. It now omits rafters and says why.
- **It will not run on partial measurements.** A lumber list built on a guessed
  roof area is worse than no list, because it looks like an answer.
- **It will not invent specs for callouts it cannot read.** Anything whose tag
  is visible but whose specification is not gets named as unresolved rather
  than inferred from a similar member nearby. (On this set that list is now
  empty, but the mechanism is what keeps the next set honest.)
- **It will not treat `(E) RFTR` as new work.** On these sheets `RFTR` labels
  the *existing* rafters underneath; the new roof rafters are tagged `RR`.
  Reading the wrong tag would have priced rafters that are already built.

## Scope of the estimate

The list covers framing, post hardware, sheathing, roof covering, footing
concrete and rebar, and enumerates the demolition scope. It states its own
boundary rather than leaving it to be inferred — every run prints the trades it
does **not** cover (electrical, plumbing, interior finishes, site work,
shearwall sheathing and hold-downs).

Note that on a roof-replacement scope like the reference set, a roof-heavy list
is the correct answer, not a truncated one: the keynotes show the house itself
marked "NO WORK". On a set with more scope, the same rules produce wall framing,
studs, plates and insulation — those paths run when `new_wall_lf` is non-zero.

## Known limits

- **The shearwall schedule parses per type except for its merged rows.** A cell
  merged across types is told apart from a row of six tightly packed values by
  whether a word straddles a column boundary: merged text flows across the
  boundary it covers, per-column values stay centred in their own cell. On the
  reference set that resolves 11 rows exactly — nailing, plate fastening,
  framing angles, anchor bolts, MASA spacing — and flags 3 (framing, sheathing,
  roof nails) whose merge span cannot be recovered exactly.
- **TPO area is not derived.** The low-slope extent is not dimensioned, so the
  line is listed at zero with a warning rather than estimated.
- **Underlayment layers are ambiguous at 4:12.** The roofing keynote requires
  two layers "for slopes between 2:12 and 4:12" and one otherwise; a 4:12 roof
  sits exactly on that boundary. Two layers are listed and the ambiguity is
  flagged rather than silently resolved.
- **Demolition is scope, not quantity.** Disposal volume depends on what is
  found once the roof is opened, so the notes are enumerated and quantities
  left to the estimator.
- **Raster sheets need a different tool** — OCR or a vision pass. Not built.
- **The standards file is seeded with plausible defaults, not BARC's numbers.**
  It is marked `review_status: UNCONFIRMED` and every report says so until a
  human changes it.
- **Keynote text carries kerning artefacts** from the CAD export (`MA TCH`,
  `REMOV ED`). Matching ignores whitespace entirely, so classification is
  unaffected, but displayed text looks odd.
- **Roof pitch is read off the roof plan** (`4:12` here, with `2:12` and `1:12`
  also present on other planes); `--pitch` overrides. Only the dominant pitch is
  applied, so a multi-slope roof needs checking.
- **Beam lengths are not computed** — beams are listed one-per-callout with
  length to be confirmed, since the plans do not dimension them.
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
