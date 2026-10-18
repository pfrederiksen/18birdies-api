# API Reference

## Entry Points

```python
from eighteenbirdies import (
    EighteenBirdiesClient,
    EighteenBirdiesError,
    default_store_path,
)

client = EighteenBirdiesClient.from_export("examples/synthetic-export.json")
client = EighteenBirdiesClient.import_export(
    "examples/synthetic-export.json", store_path="local-golf.json",
    acquired_at=None, timezone_name="UTC",
)
client = EighteenBirdiesClient(store_path="local-golf.json", timezone_name="UTC")
```

Passing both `export_path` and `store_path` to the constructor is an error.
`from_export` is read-only. `import_export` persists a golf-only full snapshot.
`EighteenBirdiesError` derives from `ValueError`; storage failures may raise
`OSError`. No method contacts the network. Result dictionaries/lists and
`client.provenance` are independent deep copies.

| Direct method | Resource-style equivalent |
| --- | --- |
| `list_rounds(limit=None)` | `rounds.list(limit=None)` |
| `get_round(id)` | `rounds.get(id)` |
| `summary()` | `stats.summary()` |
| `trends()` | `stats.trends()` |
| `course_history()` | `courses.list()` |
| `club_distances()` | `clubs.distances()` |

Additional methods: `courses.get(id)`, `stats.strokes_gained(round_ids=None)`,
`capabilities()`, `coverage()`, `snapshot()`, and `raw_golf_data()`.
`clubs.smart_distances()` is a compatibility alias for measured aggregates;
every `smart_distance` remains null.

## Rounds And Holes

Rounds are newest first, unknown timestamps last. IDs use
`18birdies:round:<source-id>` and courses use `18birdies:course:<source-id>`.
Both namespaced and raw IDs work with get methods.

Round keys:

```text
id, source_id, source, course_id, course,
date, date_utc, timestamp_ms,
strokes, score_to_par, hole_slots, holes_scored, hole_score_total,
round_handicap, holes, stats, shots, coverage
```

`source` is `18birdies`. `date` uses the selected timezone (default UTC).
`strokes` is the export's scoring-stroke total; `score_to_par` is its `score`
field. `round_handicap` retains the source representation and is not GHIN.
Missing values remain null. Zero/missing hole scores become null; scored-hole
counts do not imply completion. There is no default of 18 holes.

Each hole has `number`, `strokes`, `putts`, `fairway`, `green_in_regulation`, and
`shots`. The supported schema has no per-hole putting/fairway/GIR fields; those
remain null rather than distributing round totals across holes.

## Shots And Distances

Each shot has `id`, `hole_number`, `club: {type, number, loft_angle}`,
`distance_yards`, `timestamp_ms`, `start_point`, and `end_point`. Points retain
latitude/longitude when present. The export provides no independent shot ID;
ordinal shot IDs identify entries within an unchanged export, not stable events
across reordered exports. Reconcile corrections by round and provenance.

Club aggregates group recorded shots by club type, number, and loft. They
provide `club`, `type`, `number`, `loft_angle`, `total_shots`, `distance_samples`,
`average_distance`, `median_distance`, `smart_distance`, `distance_unit`,
`method`, `full_swing_only`, and `includes_partial_shots`. Only positive measured
distances contribute samples. `distance_unit` is yards and `method` is
`recorded_shot_distance`. Smart/full-swing/carry classification is unavailable.

## Stats And Summaries

Round stats keys are `putts`, `putts_per_hole`, `putt_hole_count`,
`fairways_hit`, `fairways_possible`, `gir`, `gir_possible`, `fairway_pct`,
`gir_pct`, and `strokes_gained: {overall, tee_to_green, putting, sand}`.

Zero denominators are unknown. A zero numerator with a positive denominator
remains zero. `noStats: true` suppresses a corresponding stat. Zero putts are
known only with explicit `TOTAL_PUTTS` availability. Putt-hole denominators
remain unvalidated; putts per hole stays null. Recommended SG values require
`noStats: false`; primary non-sentinel values are supported. SG=100 is always
unknown until its meaning is established independently.

Summaries group scoring by scored-hole count, excluding round totals that
disagree with the hole sum. They expose contributing round counts for putting,
fairway and GIR aggregates. Trends group these summaries by calendar month in
the selected timezone. Course history includes round IDs and the same scoring
breakdown. SG aggregation returns `{mean, rounds}` for each supported category.

## Capabilities, Coverage And Snapshots

Capabilities describe parser support, not proof that a particular round has
data. Live API, automatic refresh, smart distances, per-hole putting/fairway/GIR,
pace, penalties, and GHIN handicap are not supported.

Round coverage identifies scored holes without shot entries, missing club or
distance data, unmatched shot-hole assignments, unscored slots, and score-total
disagreements. Scoring strokes and exported shots are separate counts.
`shot_tracking_complete` stays null. Aggregate coverage includes round/hole/shot
counts and latest-round diagnostics.

`snapshot()` returns `source`, `date_timezone`, `last_fetched`, `imported_at`,
`provenance`, `total_rounds`, `total_shots`, `recent_rounds` (up to 10), `rounds`,
`clubs`, `summary`, `trends`, `capabilities`, `coverage`, and `strokes_gained`.
`last_fetched` means actual acquisition time or null, never local sync time.
`total_shots` counts exported shot entries, not scoring strokes.

`raw_golf_data()` returns `{activityData, playedClubs}` with the exact approved
source subtrees, including unknown fields and original zero/null values. The
normalized interface exposes known fields only.

## Import Validation And Persistence

Accepted input has `myData.activityData.rounds` and optional
`myData.clubData.playedClubs`. If `roundCount` is present, it must match the
rounds list. Known fields are validated; unknown fields inside the retained
subtrees are preserved for future parsing. Unrelated account categories are
excluded. The original full source's hash/size remain in provenance, not its
unrelated personal content.

Limits: 64 MiB for both input and the complete encoded store, 10,000
rounds/courses, 18 scorecard slots per round, 10,000 shot entries per round,
100,000 total shot entries, 100 levels of JSON nesting, and 1,000,000 JSON nodes
(containers, keys, and values). Structural complexity is checked before JSON
decoding, then exact bounds and numeric ranges are checked on the parsed tree;
aggregate shot/round limits are checked before raw golf copies or normalization.
Only regular UTF-8 files are accepted (an optional UTF-8 signature is supported);
UTF-16/32 autodetection is disabled. Nonblocking open and file-size checks
precede reading. Invalid JSON/types,
duplicate object keys/entity IDs/recommended stat types, nonfinite numbers,
invalid counts, and out-of-range coordinates/distances are rejected. Positive
timestamps are Unix milliseconds, not guessed seconds. Missing data remains
unknown. Unsupported new schemas should be validated before use.

Imports serialize via a regular-file local advisory lock. They write a private
temporary file, fsync, and atomically replace the store. The candidate is fully
normalized and the complete UTF-8 store is encoded and size-checked before any
replacement. Unicode is retained as UTF-8 rather than expanded ASCII escapes;
encoding, size, or normalization failures preserve the previous bytes.
Identical hashes do not
rewrite bytes or advance freshness. Explicit later actual acquisition times
can update acquisition metadata; backwards times for identical hashes fail.
New snapshots omitting existing round IDs fail rather than erasing history.
Edits/deleted shot entries inside existing rounds are accepted. The previous
store survives validation/write/replacement errors. An existing corrupt store
is not silently overwritten. Use a separate store for intentional reduced
archives; no deletion override is currently provided.
