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
    "examples/synthetic-export.json",
    store_path="local-golf.json",
    acquired_at=None,
    timezone_name="UTC",
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
`capabilities()`, `coverage()`, `snapshot()`, `select_rounds()`,
`handicap_series()`, and `raw_golf_data()`.
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

`select_rounds(since, until, course, holes, last)` returns a newest-first copy,
or `None` when every argument is unset. Dates are inclusive `YYYY-MM-DD` values
in the selected timezone. A round with no date is excluded from a date window.
`course` matches the namespaced course id or the source id. `holes` matches
`holes_scored`. `last` keeps that many newest matches. Pass the returned list
to `summary`, `trends`, `course_history`, `club_distances`, `coverage`,
`handicap_series`, and `snapshot`. The same filters are global CLI options and
apply to `list`, `summary`, `trends`, `courses`, `clubs`, `coverage`,
`handicap`, and `snapshot`.

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
`recorded_shot_distance`. `percentile_25` and `percentile_75` are inclusive
sample quartiles. Both stay null when fewer than two distances qualify.
`minimum_yards` is null unless the caller sets one. A positive minimum drops
shorter distances from the average, median, quartiles, and `distance_samples`.
`total_shots` still counts every recorded shot for that club. Smart,
full-swing, and carry classification stay unavailable, and
`includes_partial_shots` stays null.

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

Scoring counts are `aces`, `double_eagle_or_better`, `eagles`, `birdies`,
`pars`, `bogeys`, and `double_bogey_or_worse`. A missing field stays null. A
present zero stays zero. `noStats: true` on `ACES`, `DOUBLE_EAGLE_OR_BETTER`,
`EAGLES`, `BIRDIES`, `PARS`, `BOGEYS`, or `DOUBLE_BOGEY_OR_WORSE` hides that
count. Fairway miss counts are `left`, `right`, `short`, and `long`. Green miss
counts add `no_chance`. They use the fairway or green denominator and the same
`FAIRWAY_HIT_PERCENTAGE` or `GIR_PERCENTAGE` availability flag. A count above
its denominator is unknown. An impossible fairway-hit or green count still
clears that denominator.

Summaries group scoring by scored-hole count, excluding round totals that
disagree with the hole sum. Each bucket includes average and best strokes and,
for those same consistent rounds, average and best `score_to_par`. They also
include scoring and miss-direction totals as `{total, rounds}`. A total stays
null when no selected round has that count. The summary repeats those totals
across the whole selection. Putting, fairway, and GIR aggregates still expose
their contributing round counts. Nine-hole and eighteen-hole stroke averages
stay in separate buckets.

`trends()` returns `{months, last_10, last_20}`. `months` is the previous
calendar-month list, each item still spread from `summary()` plus `month`.
`last_10` and `last_20` are summaries of the newest matching rounds, including
rounds that have no date when they fall in that newest-first window. Course
history includes round IDs, the same scoring breakdown, and `hole_strokes`:
average recorded strokes by hole number, the number of rounds that scored that
hole, and how often that hole tied for the highest score in the round. Unscored
slots are omitted. This is the player's own hole strokes, not par, putts,
fairway, or green results. `handicap_series()` returns
`{source: "round_handicap", ghin: false, rounds: [{id, date, round_handicap}]}`
in newest-first order, with nulls left null. SG aggregation returns
`{mean, rounds}` for each supported category.

## Capabilities, Coverage And Snapshots

Capabilities describe parser support, not proof that a particular round has
data. Live API, automatic refresh, smart distances, per-hole putting/fairway/GIR,
pace, penalties, and GHIN handicap are not supported. Scoring counts, miss
directions, hole-number stroke averages, and the source handicap series are
supported only as exported or directly aggregated values.

Round coverage identifies scored holes without shot entries, missing club or
distance data, unmatched shot-hole assignments, unscored slots, and score-total
disagreements. Scoring strokes and exported shots are separate counts.
`shot_tracking_complete` stays null. Aggregate coverage includes round/hole/shot
counts and latest-round diagnostics.

The CLI flag `--redact-coordinates` replaces `latitude`, `longitude`,
`start_point`, `end_point`, `startPoint`, and `endPoint` with null in that
command's JSON. `--redact-identity` replaces `id`, `source_id`, `course_id`,
`course`, `name`, `round_handicap`, and `round_ids` with null. Both are global
and come before the subcommand. Scores and distances remain. The stored subtree
is unchanged.

CLI failures that can be named without private data say `round not found`,
`export is not valid JSON`, or `store path is not writable`. Other validation
failures keep one generic message. None of them echo paths, identifiers, or
file contents.

`snapshot()` returns `source`, `date_timezone`, `last_fetched`, `imported_at`,
`provenance`, `total_rounds`, `total_shots`, `recent_rounds` (up to 10), `rounds`,
`clubs`, `summary`, `trends`, `handicap_series`, `capabilities`, `coverage`, and
`strokes_gained`. A selected round list limits the round-derived fields.
Provenance still describes the whole store.
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
Pass `new_store=True` (CLI: `--store PATH import EXPORT --new-store`) to write a
shortened archive to a different file. `--store` is global and comes before
`import`. That call fails if the destination already exists, including as a
symlink, and it does not modify the original store. Edits/deleted shot entries inside existing rounds are accepted. The previous
store survives validation/write/replacement errors. An existing corrupt store
is not silently overwritten. Use a separate store for intentional reduced
archives; no deletion override is currently provided.
