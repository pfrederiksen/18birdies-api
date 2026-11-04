#!/usr/bin/env python3
"""Offline, stdlib-only client for official 18Birdies account JSON exports.

This is an export-backed client, not a live API or a network implementation.
Only activityData and clubData.playedClubs are imported. See the project README.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from collections.abc import Sequence
from pathlib import Path
import statistics
import stat
import sys
import tempfile
from datetime import UTC, datetime, tzinfo
from typing import Any, TypedDict, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .locking import file_lock
from ._version import __version__

SOURCE = "18birdies"
FORMAT = "18birdies-golf-export-v1"
MAX_BYTES = 64 * 1024 * 1024
MAX_ROUNDS = 10000
MAX_TOTAL_SHOTS = 100000
MAX_JSON_DEPTH = 100
MAX_JSON_NODES = 1000000


def default_store_path() -> Path:
    """Return a user data path; never write inside the installed package."""
    configured = os.environ.get("XDG_DATA_HOME")
    base = Path(configured).expanduser() if configured else Path.home() / ".local" / "share"
    if not base.is_absolute():
        base = Path.home() / ".local" / "share"
    return base / "eighteenbirdies" / "export.json"


DEFAULT_STORE = default_store_path()
ROUND_FIELDS = {
    "id",
    "timestamp",
    "clubId",
    "score",
    "strokes",
    "holeStrokes",
    "stats",
    "roundHandicap",
    "shotEntries",
}
STAT_COUNTS = {
    "aces",
    "doubleEagleOrBetter",
    "eagles",
    "birdies",
    "pars",
    "bogeys",
    "doubleBogeyOrWorse",
    "fairwayLefts",
    "fairwayMiddles",
    "fairwayRights",
    "fairwayShorts",
    "fairwayLongs",
    "fairwayHoleCount",
    "gir",
    "girLefts",
    "girRights",
    "girShorts",
    "girLongs",
    "girNoChances",
    "girHoleCount",
    "putts",
    "puttHoleCount",
}
SG_FIELDS = {"strokeGainOverall", "strokeGainTeeToGreen"}
SHOT_FIELDS = {
    "timestamp",
    "holeNumber",
    "stickTypeAndNumber",
    "startPoint",
    "endPoint",
    "distanceInYards",
}
SCORING_FIELDS = (
    ("aces", "aces", "ACES"),
    ("doubleEagleOrBetter", "double_eagle_or_better", "DOUBLE_EAGLE_OR_BETTER"),
    ("eagles", "eagles", "EAGLES"),
    ("birdies", "birdies", "BIRDIES"),
    ("pars", "pars", "PARS"),
    ("bogeys", "bogeys", "BOGEYS"),
    ("doubleBogeyOrWorse", "double_bogey_or_worse", "DOUBLE_BOGEY_OR_WORSE"),
)
FAIRWAY_DIRECTION_FIELDS = (
    ("fairwayLefts", "left"),
    ("fairwayRights", "right"),
    ("fairwayShorts", "short"),
    ("fairwayLongs", "long"),
)
GIR_DIRECTION_FIELDS = (
    ("girLefts", "left"),
    ("girRights", "right"),
    ("girShorts", "short"),
    ("girLongs", "long"),
    ("girNoChances", "no_chance"),
)
FILTER_COMMANDS = (
    "list",
    "summary",
    "trends",
    "courses",
    "clubs",
    "coverage",
    "handicap",
    "snapshot",
)
YARDAGE_COMMANDS = ("clubs", "snapshot")
_SAFE_OS_ERRORS = {
    "Timed out waiting for the local store lock": "timed out waiting for the local store lock.",
    "Store lock must be a regular file": "store lock must be a regular file.",
}
_SAFE_CLI_ERRORS = {
    "Since date must be YYYY-MM-DD",
    "Until date must be YYYY-MM-DD",
    "Holes filter must be an integer from 0 through 18",
    "Last filter must be a nonnegative integer",
    "Course filter must be a course id",
    "Since date must be on or before until date",
    "Round filters apply to list, summary, trends, courses, clubs, coverage, handicap, and snapshot",
    "Minimum yardage applies to clubs and snapshot",
    "Minimum yardage must be a positive number up to 10000",
    "Round limit must be a nonnegative integer",
    "--export cannot be used with retrieve",
    "--export cannot be used with import",
}


class ShotClub(TypedDict):
    type: str | None
    number: str | None
    loft_angle: int | float | None


class Shot(TypedDict):
    id: str
    hole_number: int | None
    club: ShotClub
    distance_yards: int | float | None
    timestamp_ms: int | None
    start_point: dict[str, Any] | None
    end_point: dict[str, Any] | None


class Hole(TypedDict):
    number: int
    strokes: int | None
    putts: None
    fairway: None
    green_in_regulation: None
    shots: list[Shot]


class CountTotal(TypedDict):
    total: int | None
    rounds: int


class ScoringCounts(TypedDict):
    aces: int | None
    double_eagle_or_better: int | None
    eagles: int | None
    birdies: int | None
    pars: int | None
    bogeys: int | None
    double_bogey_or_worse: int | None


class DirectionCounts(TypedDict):
    left: int | None
    right: int | None
    short: int | None
    long: int | None


class GirDirections(TypedDict):
    left: int | None
    right: int | None
    short: int | None
    long: int | None
    no_chance: int | None


class StrokesGained(TypedDict):
    overall: int | float | None
    tee_to_green: int | float | None
    putting: int | float | None
    sand: int | float | None


class RoundStats(TypedDict):
    putts: int | None
    putts_per_hole: None
    putt_hole_count: None
    fairways_hit: int | None
    fairways_possible: int | None
    gir: int | None
    gir_possible: int | None
    fairway_pct: float | None
    gir_pct: float | None
    scoring: ScoringCounts
    fairway_directions: DirectionCounts
    gir_directions: GirDirections
    strokes_gained: StrokesGained


class RoundCoverage(TypedDict):
    scored_holes: int
    exported_shots: int
    shot_entries_present: bool
    holes_with_shots: int
    scored_holes_without_shots: list[int]
    shot_tracking_complete: None
    shot_count_is_scoring_strokes: bool
    putts_available: bool
    hole_putts_available: bool
    hole_fairways_available: bool
    hole_gir_available: bool
    issues: list[str]


class Round(TypedDict):
    id: str
    source_id: str
    source: str
    course_id: str | None
    course: str | None
    date: str | None
    date_utc: str | None
    timestamp_ms: int | None
    strokes: int | None
    score_to_par: int | None
    hole_slots: int
    holes_scored: int
    hole_score_total: int | None
    round_handicap: str | int | float | None
    holes: list[Hole]
    stats: RoundStats
    shots: list[Shot]
    coverage: RoundCoverage


class ScoreBucket(TypedDict):
    holes_scored: int
    rounds: int
    scored_rounds: int
    average_strokes: float | None
    best_strokes: int | None
    average_score_to_par: float | None
    best_score_to_par: int | None
    scoring: dict[str, CountTotal]
    fairway_directions: dict[str, CountTotal]
    gir_directions: dict[str, CountTotal]


class Summary(TypedDict):
    rounds: int
    scored_holes: int
    exported_shots: int
    scoring_by_holes: list[ScoreBucket]
    fairway_pct: float | None
    gir_pct: float | None
    average_putts_per_round: float | None
    putt_rounds: int
    fairway_rounds: int
    gir_rounds: int
    putts_per_hole: None
    scoring: dict[str, CountTotal]
    fairway_directions: dict[str, CountTotal]
    gir_directions: dict[str, CountTotal]


class EighteenBirdiesError(ValueError):
    """Malformed or unsupported export, missing store, or unknown entity."""


class EntityNotFoundError(EighteenBirdiesError):
    """A round or course is absent. The message is a fixed label with no id."""

    def __init__(self, kind: str) -> None:
        self.kind = kind if kind in {"round", "course"} else "entity"
        super().__init__(f"{self.kind} not found")


class InvalidJsonError(EighteenBirdiesError):
    """The file is not valid UTF-8 JSON."""

    def __init__(self) -> None:
        super().__init__("Export must be valid UTF-8 JSON")


class UnavailableFileError(EighteenBirdiesError):
    """The export or store cannot be read as a regular file."""

    def __init__(self) -> None:
        super().__init__("Cannot read a regular export or store file")


class ReducedArchiveError(EighteenBirdiesError):
    """The export drops rounds that the current store already contains."""


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def _integer(value: Any, low: int = 0, high: int = 1000000) -> bool:
    return type(value) is int and low <= value <= high


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise EighteenBirdiesError("Invalid golf entity identifier")
    return value


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise EighteenBirdiesError("Acquisition time must be an ISO8601 string with timezone")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError()
        return dt.astimezone(UTC).isoformat()
    except ValueError:
        raise EighteenBirdiesError("Acquisition time must be ISO8601 with timezone") from None


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if not _integer(value, 0, 253402300799000):
        raise EighteenBirdiesError("Golf timestamp must be Unix milliseconds")
    try:
        return datetime.fromtimestamp(value / 1000, UTC).isoformat()
    except (ValueError, OSError, OverflowError):
        raise EighteenBirdiesError("Golf timestamp is outside supported dates") from None


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise EighteenBirdiesError("Duplicate JSON object key")
        result[key] = value
    return result


def _check_json_complexity(data: bytes | memoryview) -> None:
    """Bound lexical JSON structure before the decoder allocates its object tree."""
    nodes = depth = 0
    quote, backslash = ord('"'), ord("\\")
    in_string = escaped = in_bare_value = False
    for character in data:
        if in_string:
            if escaped:
                escaped = False
            elif character == backslash:
                escaped = True
            elif character == quote:
                in_string = False
            continue
        if character == quote:
            nodes += 1
            in_string = True
            in_bare_value = False
        elif character in b"[{":
            nodes += 1
            depth += 1
            in_bare_value = False
            if depth > MAX_JSON_DEPTH + 1:
                raise EighteenBirdiesError("Export nesting exceeds supported depth")
        elif character in b"]}":
            depth -= 1
            in_bare_value = False
        elif character in b"\t\n\r ,:":
            in_bare_value = False
        elif not in_bare_value:
            nodes += 1
            in_bare_value = True
        if nodes > MAX_JSON_NODES:
            raise EighteenBirdiesError("Export exceeds the JSON node limit")


def _check_json_bounds(value: Any) -> None:
    """Bound all JSON values/keys before copying or normalizing provider data."""
    nodes = 0
    pending = [(iter((value,)), 0)]
    while pending:
        iterator, depth = pending[-1]
        try:
            item = next(iterator)
        except StopIteration:
            pending.pop()
            continue
        nodes += 1
        if isinstance(item, dict):
            nodes += len(item)
        if nodes > MAX_JSON_NODES:
            raise EighteenBirdiesError("Export exceeds the JSON node limit")
        if isinstance(item, (dict, list)):
            if depth > MAX_JSON_DEPTH:
                raise EighteenBirdiesError("Export nesting exceeds supported depth")
            children = item.values() if isinstance(item, dict) else item
            pending.append((iter(children), depth + 1))
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            if _number(item) is None:
                raise EighteenBirdiesError("JSON number exceeds supported numeric range")


def _read(path: str | Path) -> tuple[dict, bytes]:
    descriptor = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise EighteenBirdiesError("Export must be a regular file")
        if info.st_size <= 0 or info.st_size > MAX_BYTES:
            raise EighteenBirdiesError("Export is empty or exceeds the byte limit")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            data = handle.read(MAX_BYTES + 1)
    except (OSError, ValueError, TypeError):
        raise UnavailableFileError() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not data or len(data) > MAX_BYTES:
        raise EighteenBirdiesError("Export is empty or exceeds the byte limit")
    try:
        decoded = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise InvalidJsonError() from None
    lexical_data = memoryview(data)[3:] if data.startswith(b"\xef\xbb\xbf") else data
    _check_json_complexity(lexical_data)
    try:

        def finite_float(value: str) -> float:
            parsed = float(value)
            if not math.isfinite(parsed):
                raise EighteenBirdiesError("Nonfinite JSON number")
            return parsed

        result = json.loads(
            decoded,
            object_pairs_hook=_object_pairs,
            parse_float=finite_float,
            parse_constant=lambda _: (_ for _ in ()).throw(
                EighteenBirdiesError("Nonfinite JSON number")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise InvalidJsonError() from None
    except ValueError as error:
        if isinstance(error, EighteenBirdiesError):
            raise
        raise EighteenBirdiesError("Unsupported JSON value") from None
    if not isinstance(result, dict):
        raise EighteenBirdiesError("Export root must be an object")
    _check_json_bounds(result)
    return result, data


def _validate_stats(value: Any) -> None:
    """Reject malformed known stat fields. Unknown fields stay in the stored subtree."""
    if value is None:
        return
    if not isinstance(value, dict):
        raise EighteenBirdiesError("Round stats must be an object")
    for key in STAT_COUNTS | SG_FIELDS:
        item = value.get(key)
        if item is not None:
            if key in STAT_COUNTS and not _integer(item):
                raise EighteenBirdiesError("Invalid round stat count")
            if key in SG_FIELDS and _number(item) is None:
                raise EighteenBirdiesError("Invalid strokes-gained value")
    recommended = value.get("recommendedStats", [])
    if not isinstance(recommended, list) or len(recommended) > 100:
        raise EighteenBirdiesError("Invalid recommended stats list")
    seen = set()
    for item in recommended:
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            raise EighteenBirdiesError("Invalid recommended stat")
        if item["type"] in seen:
            raise EighteenBirdiesError("Duplicate recommended stat type")
        seen.add(item["type"])
        if item.get("noStats") is not None and type(item["noStats"]) is not bool:
            raise EighteenBirdiesError("Invalid recommended stat availability flag")
        if item.get("value") is not None and _number(item["value"]) is None:
            raise EighteenBirdiesError("Invalid recommended stat value")


def _validate_shot(value: Any) -> None:
    """Reject malformed known shot fields. Unknown fields stay in the stored subtree."""
    if not isinstance(value, dict):
        raise EighteenBirdiesError("Shot entry must be an object")
    known = {key: value[key] for key in SHOT_FIELDS if key in value}
    _timestamp(known.get("timestamp"))
    if known.get("holeNumber") is not None and not _integer(known["holeNumber"], 1, 18):
        raise EighteenBirdiesError("Shot hole number must be 1 through 18")
    distance = known.get("distanceInYards")
    if distance is not None and (_number(distance) is None or not 0 <= distance <= 10000):
        raise EighteenBirdiesError("Invalid shot distance in yards")
    club = known.get("stickTypeAndNumber")
    if club is not None:
        if not isinstance(club, dict):
            raise EighteenBirdiesError("Invalid shot club")
        for key in ("type", "number"):
            if club.get(key) is not None and (
                not isinstance(club[key], str) or len(club[key]) > 100
            ):
                raise EighteenBirdiesError("Invalid shot club label")
        if club.get("loftAngle") is not None and (
            _number(club["loftAngle"]) is None or not 0 <= club["loftAngle"] <= 90
        ):
            raise EighteenBirdiesError("Invalid shot club loft")
    for key in ("startPoint", "endPoint"):
        point = known.get(key)
        if point is None:
            continue
        if not isinstance(point, dict):
            raise EighteenBirdiesError("Invalid shot coordinates")
        for coordinate, bound in (("latitude", 90), ("longitude", 180)):
            n = point.get(coordinate)
            if n is not None and (_number(n) is None or not -bound <= n <= bound):
                raise EighteenBirdiesError("Invalid shot coordinates")


def _golf(activity: Any, clubs: Any) -> dict:
    if not isinstance(activity, dict) or not isinstance(activity.get("rounds"), list):
        raise EighteenBirdiesError("Missing official activityData.rounds list")
    rounds = activity["rounds"]
    if len(rounds) > MAX_ROUNDS:
        raise EighteenBirdiesError("Export has too many rounds")
    if "roundCount" in activity and (
        not _integer(activity["roundCount"]) or activity["roundCount"] != len(rounds)
    ):
        raise EighteenBirdiesError("Declared round count does not match rounds list")
    if not isinstance(clubs, list) or len(clubs) > MAX_ROUNDS:
        raise EighteenBirdiesError("playedClubs must be a list")
    total_shots = 0
    for raw in rounds:
        if not isinstance(raw, dict):
            raise EighteenBirdiesError("Round must be an object")
        shots = raw.get("shotEntries")
        if shots is not None:
            if not isinstance(shots, list) or len(shots) > 10000:
                raise EighteenBirdiesError("Invalid shot entries list")
            total_shots += len(shots)
            if total_shots > MAX_TOTAL_SHOTS:
                raise EighteenBirdiesError("Export exceeds the total shot limit")
    seen = set()
    for club in clubs:
        if not isinstance(club, dict):
            raise EighteenBirdiesError("Invalid played course record")
        cid = _identifier(club.get("clubId"))
        if cid in seen:
            raise EighteenBirdiesError("Duplicate played course identifier")
        seen.add(cid)
        name = club.get("name")
        if name is not None and (not isinstance(name, str) or len(name) > 1000):
            raise EighteenBirdiesError("Invalid played course name")
    seen = set()
    for raw in rounds:
        if not isinstance(raw, dict):
            raise EighteenBirdiesError("Round must be an object")
        item = {k: raw[k] for k in ROUND_FIELDS if k in raw}
        rid = _identifier(item.get("id"))
        if rid in seen:
            raise EighteenBirdiesError("Duplicate round identifier")
        seen.add(rid)
        _timestamp(item.get("timestamp"))
        club_id = item.get("clubId")
        if club_id is not None:
            if not isinstance(club_id, dict):
                raise EighteenBirdiesError("Round clubId must be an object")
            item["clubId"] = {"id": _identifier(club_id.get("id"))}
        for key in ("strokes", "score"):
            if item.get(key) is not None and not _integer(
                item[key], -1000 if key == "score" else 0, 10000
            ):
                raise EighteenBirdiesError("Invalid round score")
        holes = item.get("holeStrokes", [])
        if not isinstance(holes, list) or len(holes) > 18:
            raise EighteenBirdiesError("holeStrokes must contain at most 18 holes")
        if any(n is not None and not _integer(n, 0, 1000) for n in holes):
            raise EighteenBirdiesError("Invalid per-hole score")
        item["holeStrokes"] = holes
        _validate_stats(item.get("stats"))
        # Handicaps are retained as source strings, never relabeled as GHIN.
        if item.get("roundHandicap") is not None and (
            not isinstance(item["roundHandicap"], (str, int, float))
            or isinstance(item["roundHandicap"], bool)
        ):
            raise EighteenBirdiesError("Invalid source round handicap")
        shots = item.get("shotEntries")
        if shots is None:
            shots = []
        if not isinstance(shots, list) or len(shots) > 10000:
            raise EighteenBirdiesError("Invalid shot entries list")
        for shot in shots:
            _validate_shot(shot)
    return {"activityData": copy.deepcopy(activity), "playedClubs": copy.deepcopy(clubs)}


def _envelope(path: str | Path, acquired_at: str | None = None) -> dict:
    document, data = _read(path)
    root = document.get("myData")
    if not isinstance(root, dict):
        raise EighteenBirdiesError("Expected official account export with myData object")
    club_data = root.get("clubData", {})
    if not isinstance(club_data, dict):
        raise EighteenBirdiesError("clubData must be an object")
    return {
        "format": FORMAT,
        "source": SOURCE,
        "provenance": {
            "source_sha256": hashlib.sha256(data).hexdigest(),
            "source_bytes": len(data),
            "acquired_at": _iso(acquired_at),
            "imported_at": datetime.now(UTC).isoformat(),
            "method": "official_account_export",
            "golf_only": True,
        },
        "golf": _golf(root.get("activityData"), club_data.get("playedClubs", [])),
    }


def _atomic_write(path: Path, data: dict) -> None:
    _check_json_bounds(data)
    try:
        encoded = (
            json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
    except (ValueError, UnicodeError, OverflowError, RecursionError):
        raise EighteenBirdiesError("Store cannot be encoded as supported UTF-8 JSON") from None
    if len(encoded) > MAX_BYTES:
        raise EighteenBirdiesError("Encoded store exceeds the byte limit")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".18birdies-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            if os.name != "nt":
                os.fchmod(handle.fileno(), 0o600)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _mean(values: Sequence[int | float]) -> float | None:
    return round(statistics.mean(values), 3) if values else None


def _ratio(
    numerator: int | float | None, denominator: int | float | None, factor: int = 100
) -> float | None:
    if numerator is None or not denominator or numerator > denominator:
        return None
    return round(factor * numerator / denominator, 3)


def _percentiles(values: list[int | float]) -> tuple[float | None, float | None]:
    """Inclusive sample quartiles. One distance has no spread, so both stay unknown."""
    if len(values) < 2:
        return None, None
    low, _, high = statistics.quantiles(values, n=4, method="inclusive")
    return round(low, 3), round(high, 3)


def _minimum_yards(value: int | float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise EighteenBirdiesError("Minimum yardage must be a positive number up to 10000")
    if not 0 < value <= 10000:
        raise EighteenBirdiesError("Minimum yardage must be a positive number up to 10000")
    return float(value)


def _iso_date(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise EighteenBirdiesError(f"{label} must be YYYY-MM-DD")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise EighteenBirdiesError(f"{label} must be YYYY-MM-DD") from None
    return parsed.date().isoformat()


def _counted_total(values: list[int | None]) -> CountTotal:
    known = [value for value in values if value is not None]
    return {"total": sum(known) if known else None, "rounds": len(known)}


def _normal_stats(raw: dict[str, Any]) -> RoundStats:
    recommended = {item["type"]: item for item in raw.get("recommendedStats", [])}

    def available(key: str, stat_type: str) -> Any:
        recommendation = recommended.get(stat_type, {})
        if recommendation.get("noStats") is True:
            return None
        return raw.get(key)

    def integer_count(key: str, stat_type: str) -> int | None:
        value = available(key, stat_type)
        return value if _integer(value) else None

    def sg(key: str, stat_type: str) -> int | float | None:
        recommendation = recommended.get(stat_type, {})
        if recommendation.get("noStats") is True:
            return None
        number = (
            recommendation.get("value") if recommendation.get("noStats") is False else raw.get(key)
        )
        # The observed 100 markers have no validated semantic meaning.
        if number == 100 or not isinstance(number, (int, float)) or isinstance(number, bool):
            return None
        return number

    def bounded(value: Any, denominator: int | None) -> int | None:
        if not _integer(value):
            return None
        if denominator is not None and value > denominator:
            return None
        return value

    fw_count_raw = raw.get("fairwayHoleCount")
    gir_count_raw = raw.get("girHoleCount")
    fw_count: int | None = fw_count_raw if _integer(fw_count_raw, 1) else None
    gir_count: int | None = gir_count_raw if _integer(gir_count_raw, 1) else None
    fw = available("fairwayMiddles", "FAIRWAY_HIT_PERCENTAGE") if fw_count else None
    gir = available("gir", "GIR_PERCENTAGE") if gir_count else None
    if fw is not None and fw_count is not None and fw > fw_count:
        fw = fw_count = None
    if gir is not None and gir_count is not None and gir > gir_count:
        gir = gir_count = None
    putts = available("putts", "TOTAL_PUTTS")
    putt_recommendation = recommended.get("TOTAL_PUTTS", {})
    if putt_recommendation.get("noStats") is False and putts is None:
        candidate = putt_recommendation.get("value")
        putts = candidate if _integer(candidate) else None
    if putts == 0 and putt_recommendation.get("noStats") is not False:
        putts = None
    putts_count = putts if _integer(putts) else None
    fw_hit = bounded(fw, fw_count)
    gir_hit = bounded(gir, gir_count)

    def direction(key: str, stat_type: str, denominator: int | None) -> int | None:
        if not denominator:
            return None
        value = integer_count(key, stat_type)
        if value is None or value > denominator:
            return None
        return value

    return {
        "putts": putts_count,
        "putts_per_hole": None,
        "putt_hole_count": None,
        "fairways_hit": fw_hit,
        "fairways_possible": fw_count,
        "gir": gir_hit,
        "gir_possible": gir_count,
        "fairway_pct": _ratio(fw_hit, fw_count),
        "gir_pct": _ratio(gir_hit, gir_count),
        "scoring": cast(
            ScoringCounts,
            {name: integer_count(key, stat_type) for key, name, stat_type in SCORING_FIELDS},
        ),
        "fairway_directions": cast(
            DirectionCounts,
            {
                name: direction(key, "FAIRWAY_HIT_PERCENTAGE", fw_count)
                for key, name in FAIRWAY_DIRECTION_FIELDS
            },
        ),
        "gir_directions": cast(
            GirDirections,
            {
                name: direction(key, "GIR_PERCENTAGE", gir_count)
                for key, name in GIR_DIRECTION_FIELDS
            },
        ),
        "strokes_gained": {
            "overall": sg("strokeGainOverall", "SG_OVERALL"),
            "tee_to_green": sg("strokeGainTeeToGreen", "SG_TEE_TO_GREEN"),
            "putting": sg("__absent", "SG_PUTTING"),
            "sand": sg("__absent", "SG_SAND"),
        },
    }


def _normalize_round(
    raw: dict[str, Any],
    courses: dict[str, str | None],
    date_zone: tzinfo = UTC,
) -> Round:
    rid = f"{SOURCE}:round:{raw['id']}"
    club_id = (raw.get("clubId") or {}).get("id")
    cid = club_id if isinstance(club_id, str) and club_id else None
    stamp = _timestamp(raw.get("timestamp"))
    shots: list[Shot] = []
    for index, shot in enumerate(raw.get("shotEntries") or []):
        club = shot.get("stickTypeAndNumber") or {}
        shots.append(
            {
                "id": f"{rid}:shot:{index + 1}",
                "hole_number": shot.get("holeNumber"),
                "club": {
                    "type": club.get("type"),
                    "number": club.get("number"),
                    "loft_angle": club.get("loftAngle"),
                },
                "distance_yards": shot.get("distanceInYards"),
                "timestamp_ms": shot.get("timestamp"),
                "start_point": shot.get("startPoint"),
                "end_point": shot.get("endPoint"),
            }
        )
    holes: list[Hole] = [
        {
            "number": index + 1,
            "strokes": strokes or None,
            "putts": None,
            "fairway": None,
            "green_in_regulation": None,
            "shots": [s for s in shots if s["hole_number"] == index + 1],
        }
        for index, strokes in enumerate(raw.get("holeStrokes", []))
    ]
    scored = [hole for hole in holes if hole["strokes"] is not None]
    missing_shots = [hole["number"] for hole in scored if not hole["shots"]]
    reported_strokes = raw.get("strokes")
    reported = reported_strokes if _integer(reported_strokes, 1, 10000) else None
    scored_strokes = [hole["strokes"] for hole in scored if hole["strokes"] is not None]
    hole_sum = sum(scored_strokes) if scored_strokes else None
    mismatch = reported is not None and hole_sum is not None and reported != hole_sum
    issues = []
    if missing_shots:
        issues.append("scored_holes_without_shots")
    if any(s["hole_number"] is None or s["hole_number"] > len(holes) for s in shots):
        issues.append("shots_without_matching_hole_slot")
    if any(s["distance_yards"] is None for s in shots):
        issues.append("shots_missing_distance")
    if any(s["club"]["type"] is None for s in shots):
        issues.append("shots_missing_club")
    if mismatch:
        issues.append("reported_strokes_differ_from_scored_holes")
    if any(h["strokes"] is None for h in holes):
        issues.append("unscored_hole_slots")
    if not holes:
        issues.append("hole_scores_unavailable")
    stats = _normal_stats(raw.get("stats") or {})
    return {
        "id": rid,
        "source_id": raw["id"],
        "source": SOURCE,
        "course_id": f"{SOURCE}:course:{cid}" if cid else None,
        "course": courses.get(cid) if cid is not None else None,
        "date": datetime.fromisoformat(stamp).astimezone(date_zone).date().isoformat()
        if stamp
        else None,
        "date_utc": stamp[:10] if stamp else None,
        "timestamp_ms": raw.get("timestamp"),
        "strokes": reported,
        "score_to_par": raw.get("score") if _integer(raw.get("score"), -1000, 10000) else None,
        "hole_slots": len(holes),
        "holes_scored": len(scored),
        "hole_score_total": hole_sum,
        "round_handicap": raw.get("roundHandicap"),
        "holes": holes,
        "stats": stats,
        "shots": shots,
        "coverage": {
            "scored_holes": len(scored),
            "exported_shots": len(shots),
            "shot_entries_present": bool(shots),
            "holes_with_shots": sum(bool(h["shots"]) for h in scored),
            "scored_holes_without_shots": missing_shots,
            "shot_tracking_complete": None,
            "shot_count_is_scoring_strokes": False,
            "putts_available": stats["putts"] is not None,
            "hole_putts_available": False,
            "hole_fairways_available": False,
            "hole_gir_available": False,
            "issues": issues,
        },
    }


def redact_coordinates(value: Any) -> Any:
    """Replace shot coordinates with null. Scores and distances stay in place."""
    if isinstance(value, list):
        return [redact_coordinates(item) for item in value]
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if key in {
                "latitude",
                "longitude",
                "start_point",
                "end_point",
                "startPoint",
                "endPoint",
            }:
                redacted[key] = None
            else:
                redacted[key] = redact_coordinates(item)
        return redacted
    return value


_IDENTITY_KEYS = {
    "id",
    "source_id",
    "course_id",
    "course",
    "name",
    "round_handicap",
    "round_ids",
}


def redact_identity(value: Any) -> Any:
    """Null course names, source ids, and handicap strings. Scores stay in place."""
    if isinstance(value, list):
        return [redact_identity(item) for item in value]
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            redacted[key] = None if key in _IDENTITY_KEYS else redact_identity(item)
        return redacted
    return value


def _strokes_gained(rounds: list[Round]) -> dict[str, dict[str, float | int | None]]:
    totals: dict[str, dict[str, float | int | None]] = {}
    for key in ("overall", "tee_to_green", "putting", "sand"):
        values: list[int | float] = []
        for round_ in rounds:
            value = round_["stats"]["strokes_gained"][key]
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(value)
        totals[key] = {"mean": _mean(values), "rounds": len(values)}
    return totals


def _scoring_aggregate(
    rounds: list[Round], section: str, names: tuple[str, ...]
) -> dict[str, CountTotal]:
    totals: dict[str, CountTotal] = {}
    for name in names:
        values: list[int | None] = []
        for round_ in rounds:
            block = cast(dict[str, int | None], cast(dict[str, Any], round_["stats"])[section])
            values.append(block[name])
        totals[name] = _counted_total(values)
    return totals


def _hole_strokes(rounds: list[Round]) -> list[dict[str, int | float | None]]:
    """Average recorded hole strokes. Ties for the high score each count."""
    scores: dict[int, list[int]] = {}
    highest: dict[int, int] = {}
    for round_ in rounds:
        scored = [hole for hole in round_["holes"] if hole["strokes"] is not None]
        if not scored:
            continue
        maximum = max(hole["strokes"] for hole in scored if hole["strokes"] is not None)
        for hole in scored:
            strokes = hole["strokes"]
            if strokes is None:
                continue
            scores.setdefault(hole["number"], []).append(strokes)
            if strokes == maximum:
                highest[hole["number"]] = highest.get(hole["number"], 0) + 1
    return [
        {
            "number": number,
            "rounds": len(values),
            "average_strokes": _mean(values),
            "times_highest_score": highest.get(number, 0),
        }
        for number, values in sorted(scores.items())
    ]


def _date_zone(timezone_name: str) -> tzinfo:
    try:
        return UTC if timezone_name == "UTC" else ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise EighteenBirdiesError("Unknown timezone or unavailable timezone data") from None


class _Rounds:
    def __init__(self, client: EighteenBirdiesClient) -> None:
        self.client = client

    def list(self, limit: int | None = None, rounds: list[Round] | None = None) -> list[Round]:
        return self.client.list_rounds(limit, rounds)

    def get(self, round_id: str) -> Round:
        return self.client.get_round(round_id)


class _Courses:
    def __init__(self, client: EighteenBirdiesClient) -> None:
        self.client = client

    def list(self, rounds: list[Round] | None = None) -> list[dict[str, Any]]:
        return self.client.course_history(rounds)

    def get(self, course_id: str) -> dict[str, Any]:
        for course in self.list():
            if course_id in (course["id"], course["source_id"]):
                return course
        raise EntityNotFoundError("course")


class _Clubs:
    def __init__(self, client: EighteenBirdiesClient) -> None:
        self.client = client

    def smart_distances(
        self,
        rounds: list[Round] | None = None,
        *,
        minimum_yards: int | float | None = None,
    ) -> list[dict[str, Any]]:
        return self.client.club_distances(rounds, minimum_yards=minimum_yards)

    distances = smart_distances


class _Stats:
    def __init__(self, client: EighteenBirdiesClient) -> None:
        self.client = client

    def summary(self, rounds: list[Round] | None = None) -> Summary:
        return self.client.summary(rounds)

    def trends(self, rounds: list[Round] | None = None) -> dict[str, Any]:
        return self.client.trends(rounds)

    def strokes_gained(
        self, round_ids: list[str] | None = None
    ) -> dict[str, dict[str, float | int | None]]:
        rounds = (
            [self.client.get_round(round_id) for round_id in round_ids]
            if round_ids is not None
            else self.client.list_rounds()
        )
        return _strokes_gained(rounds)


class EighteenBirdiesClient:
    """Read an official export directly, or an explicitly imported golf-only store."""

    timezone_name: str
    _envelope: dict[str, Any]
    _rounds: list[Round]
    rounds: _Rounds
    courses: _Courses
    clubs: _Clubs
    stats: _Stats

    def __init__(
        self,
        export_path: str | Path | None = None,
        *,
        store_path: str | Path | None = None,
        timezone_name: str = "UTC",
    ):
        if export_path is not None and store_path is not None:
            raise EighteenBirdiesError("Choose an export or a store, not both")
        if export_path is not None:
            envelope = _envelope(export_path)
        else:
            envelope, _ = _read(store_path or default_store_path())
            if envelope.get("format") != FORMAT or envelope.get("source") != SOURCE:
                raise EighteenBirdiesError("Unsupported private golf store format")
            provenance = envelope.get("provenance")
            if (
                not isinstance(provenance, dict)
                or provenance.get("method") != "official_account_export"
                or provenance.get("golf_only") is not True
            ):
                raise EighteenBirdiesError("Invalid store provenance")
            digest = provenance.get("source_sha256")
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)
            ):
                raise EighteenBirdiesError("Invalid store source digest")
            if not _integer(provenance.get("source_bytes"), 1, MAX_BYTES):
                raise EighteenBirdiesError("Invalid store source size")
            _iso(provenance.get("acquired_at"))
            if _iso(provenance.get("imported_at")) is None:
                raise EighteenBirdiesError("Missing store import timestamp")
            golf = envelope.get("golf")
            if not isinstance(golf, dict):
                raise EighteenBirdiesError("Missing stored golf data")
            envelope["golf"] = _golf(golf.get("activityData"), golf.get("playedClubs"))
        self._initialize(envelope, timezone_name)

    def _initialize(self, envelope: dict, timezone_name: str) -> None:
        date_zone = _date_zone(timezone_name)
        self.timezone_name = timezone_name
        self._envelope = envelope
        golf = envelope["golf"]
        courses = {
            club["clubId"]: club.get("name") if isinstance(club.get("name"), str) else None
            for club in golf["playedClubs"]
        }
        self._rounds = sorted(
            [
                _normalize_round(round_, courses, date_zone)
                for round_ in golf["activityData"]["rounds"]
            ],
            key=lambda round_: (
                round_["timestamp_ms"] if round_["timestamp_ms"] is not None else -1,
                round_["id"],
            ),
            reverse=True,
        )
        self.rounds = _Rounds(self)
        self.courses = _Courses(self)
        self.clubs = _Clubs(self)
        self.stats = _Stats(self)

    @classmethod
    def from_export(cls, path: str | Path, *, timezone_name: str = "UTC") -> EighteenBirdiesClient:
        return cls(export_path=path, timezone_name=timezone_name)

    @classmethod
    def import_export(
        cls,
        path: str | Path,
        *,
        store_path: str | Path | None = None,
        acquired_at: str | None = None,
        timezone_name: str = "UTC",
        new_store: bool = False,
    ) -> EighteenBirdiesClient:
        _date_zone(timezone_name)
        envelope = _envelope(path, acquired_at)
        destination = Path(store_path or DEFAULT_STORE)
        if Path(path).resolve() == destination.resolve():
            raise EighteenBirdiesError("Import destination must differ from original export")
        if new_store and os.path.lexists(destination):
            raise EighteenBirdiesError("New store path already exists; choose another --store")
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = destination.with_name(destination.name + ".lock")
        with file_lock(lock_path):
            if new_store and os.path.lexists(destination):
                raise EighteenBirdiesError("New store path already exists; choose another --store")
            if destination.exists():
                previous = cls(store_path=destination, timezone_name=timezone_name)
                old = previous.provenance
                new = envelope["provenance"]
                if new["source_sha256"] == old["source_sha256"]:
                    if new["acquired_at"] is None or new["acquired_at"] == old["acquired_at"]:
                        return previous
                    if old["acquired_at"] is not None and new["acquired_at"] < old["acquired_at"]:
                        raise EighteenBirdiesError("Acquisition time cannot move backwards")
                    envelope = copy.deepcopy(previous._envelope)
                    envelope["provenance"]["acquired_at"] = new["acquired_at"]
                else:
                    prior_ids = {r["source_id"] for r in previous._rounds}
                    next_ids = {r["id"] for r in envelope["golf"]["activityData"]["rounds"]}
                    if not prior_ids <= next_ids:
                        raise ReducedArchiveError(
                            "Export omits previously imported rounds; prior store preserved. "
                            "Use --store PATH import EXPORT --new-store for a shortened archive."
                        )
            candidate = cls.__new__(cls)
            candidate._initialize(envelope, timezone_name)
            _atomic_write(destination, envelope)
            return candidate

    @property
    def provenance(self) -> dict:
        return copy.deepcopy(self._envelope["provenance"])

    def raw_golf_data(self) -> dict:
        """Return original approved activityData/playedClubs, including future fields."""
        return copy.deepcopy(self._envelope["golf"])

    def list_rounds(
        self, limit: int | None = None, rounds: list[Round] | None = None
    ) -> list[Round]:
        selected = self._rounds if rounds is None else rounds
        if limit is not None and not _integer(limit, 0, MAX_ROUNDS):
            raise EighteenBirdiesError("Round limit must be a nonnegative integer")
        window = selected if limit is None else selected[:limit]
        return copy.deepcopy(window)

    def get_round(self, round_id: str) -> Round:
        for item in self._rounds:
            if round_id in (item["id"], item["source_id"]):
                return copy.deepcopy(item)
        raise EntityNotFoundError("round")

    def select_rounds(
        self,
        *,
        since: str | None = None,
        until: str | None = None,
        course: str | None = None,
        holes: int | None = None,
        last: int | None = None,
    ) -> list[Round] | None:
        """Return a newest-first copy, or None when every filter is unset."""
        since_date = _iso_date(since, "Since date")
        until_date = _iso_date(until, "Until date")
        if holes is not None and not _integer(holes, 0, 18):
            raise EighteenBirdiesError("Holes filter must be an integer from 0 through 18")
        if last is not None and not _integer(last, 0, MAX_ROUNDS):
            raise EighteenBirdiesError("Last filter must be a nonnegative integer")
        if course is not None and (
            not isinstance(course, str) or not course.strip() or len(course) > 300
        ):
            raise EighteenBirdiesError("Course filter must be a course id")
        if (
            since_date is None
            and until_date is None
            and course is None
            and holes is None
            and last is None
        ):
            return None
        if since_date is not None and until_date is not None and since_date > until_date:
            raise EighteenBirdiesError("Since date must be on or before until date")
        namespaced = f"{SOURCE}:course:"
        accepted = {course} if course is not None else set()
        if course is not None:
            accepted.add(
                course.removeprefix(namespaced)
                if course.startswith(namespaced)
                else f"{namespaced}{course}"
            )
        matched: list[Round] = []
        for round_ in self._rounds:
            round_date = round_["date"]
            if since_date is not None or until_date is not None:
                if (
                    round_date is None
                    or (since_date is not None and round_date < since_date)
                    or (until_date is not None and round_date > until_date)
                ):
                    continue
            if course is not None and round_["course_id"] not in accepted:
                continue
            if holes is not None and round_["holes_scored"] != holes:
                continue
            matched.append(round_)
        if last is not None:
            matched = matched[:last]
        return copy.deepcopy(matched)

    def summary(self, rounds: list[Round] | None = None) -> Summary:
        selected = self._rounds if rounds is None else rounds
        scoring_names = tuple(name for _, name, _ in SCORING_FIELDS)
        fairway_names = tuple(name for _, name in FAIRWAY_DIRECTION_FIELDS)
        gir_names = tuple(name for _, name in GIR_DIRECTION_FIELDS)
        buckets: list[ScoreBucket] = []
        for size in sorted({round_["holes_scored"] for round_ in selected}):
            group = [round_ for round_ in selected if round_["holes_scored"] == size]
            consistent = [
                round_
                for round_ in group
                if round_["strokes"] is not None and round_["strokes"] == round_["hole_score_total"]
            ]
            scores = [round_["strokes"] for round_ in consistent if round_["strokes"] is not None]
            relative = [
                round_["score_to_par"]
                for round_ in consistent
                if round_["score_to_par"] is not None
            ]
            buckets.append(
                {
                    "holes_scored": size,
                    "rounds": len(group),
                    "scored_rounds": len(scores),
                    "average_strokes": _mean(scores),
                    "best_strokes": min(scores) if scores else None,
                    "average_score_to_par": _mean(relative),
                    "best_score_to_par": min(relative) if relative else None,
                    "scoring": _scoring_aggregate(group, "scoring", scoring_names),
                    "fairway_directions": _scoring_aggregate(
                        group, "fairway_directions", fairway_names
                    ),
                    "gir_directions": _scoring_aggregate(group, "gir_directions", gir_names),
                }
            )
        fw = [round_["stats"] for round_ in selected if round_["stats"]["fairways_hit"] is not None]
        gir = [round_["stats"] for round_ in selected if round_["stats"]["gir"] is not None]
        putts = [
            round_["stats"]["putts"] for round_ in selected if round_["stats"]["putts"] is not None
        ]
        return {
            "rounds": len(selected),
            "scored_holes": sum(round_["holes_scored"] for round_ in selected),
            "exported_shots": sum(len(round_["shots"]) for round_ in selected),
            "scoring_by_holes": buckets,
            "fairway_pct": _ratio(
                sum(item["fairways_hit"] for item in fw if item["fairways_hit"] is not None),
                sum(
                    item["fairways_possible"]
                    for item in fw
                    if item["fairways_possible"] is not None
                ),
            ),
            "gir_pct": _ratio(
                sum(item["gir"] for item in gir if item["gir"] is not None),
                sum(item["gir_possible"] for item in gir if item["gir_possible"] is not None),
            ),
            "average_putts_per_round": _mean([value for value in putts if value is not None]),
            "putt_rounds": len(putts),
            "fairway_rounds": len(fw),
            "gir_rounds": len(gir),
            "putts_per_hole": None,
            "scoring": _scoring_aggregate(selected, "scoring", scoring_names),
            "fairway_directions": _scoring_aggregate(selected, "fairway_directions", fairway_names),
            "gir_directions": _scoring_aggregate(selected, "gir_directions", gir_names),
        }

    def trends(self, rounds: list[Round] | None = None) -> dict[str, Any]:
        selected = self._rounds if rounds is None else rounds
        groups: dict[str, list[Round]] = {}
        ordered: list[Round] = []
        for round_ in selected:
            ordered.append(round_)
            if round_["date"]:
                groups.setdefault(round_["date"][:7], []).append(round_)
        return {
            "months": [{"month": month, **self.summary(groups[month])} for month in sorted(groups)],
            "last_10": self.summary(ordered[:10]),
            "last_20": self.summary(ordered[:20]),
        }

    def course_history(self, rounds: list[Round] | None = None) -> list[dict[str, Any]]:
        selected = self._rounds if rounds is None else rounds
        groups: dict[str, list[Round]] = {}
        for round_ in selected:
            course_id = round_["course_id"]
            if course_id:
                groups.setdefault(course_id, []).append(round_)
        result = []
        for course_id in sorted(groups):
            grouped = groups[course_id]
            result.append(
                {
                    "id": course_id,
                    "source_id": course_id.removeprefix(f"{SOURCE}:course:"),
                    "name": grouped[0]["course"],
                    "latest_date": grouped[0]["date"],
                    "round_ids": [round_["id"] for round_ in grouped],
                    "hole_strokes": _hole_strokes(grouped),
                    **self.summary(grouped),
                }
            )
        return result

    def club_distances(
        self,
        rounds: list[Round] | None = None,
        *,
        minimum_yards: int | float | None = None,
    ) -> list[dict[str, Any]]:
        selected = self._rounds if rounds is None else rounds
        floor = _minimum_yards(minimum_yards)
        groups: dict[tuple[str | None, str | None, int | float | None], list[Shot]] = {}
        for round_ in selected:
            for shot in round_["shots"]:
                club = shot["club"]
                if club["type"] is None:
                    continue
                groups.setdefault((club["type"], club["number"], club["loft_angle"]), []).append(
                    shot
                )
        result = []
        for (kind, number, loft), shots in sorted(groups.items(), key=lambda pair: str(pair[0])):
            distances = [
                shot["distance_yards"]
                for shot in shots
                if shot["distance_yards"] is not None
                and shot["distance_yards"] > 0
                and (floor is None or shot["distance_yards"] >= floor)
            ]
            known = [distance for distance in distances if distance is not None]
            low, high = _percentiles(known)
            result.append(
                {
                    "club": " ".join(
                        str(part) for part in (kind, number, f"{loft}deg" if loft else None) if part
                    ),
                    "type": kind,
                    "number": number,
                    "loft_angle": loft,
                    "total_shots": len(shots),
                    "distance_samples": len(known),
                    "average_distance": _mean(known),
                    "median_distance": round(statistics.median(known), 3) if known else None,
                    "percentile_25": low,
                    "percentile_75": high,
                    "minimum_yards": floor,
                    "smart_distance": None,
                    "distance_unit": "yards",
                    "method": "recorded_shot_distance",
                    "full_swing_only": None,
                    "includes_partial_shots": None,
                }
            )
        return result

    def handicap_series(self, rounds: list[Round] | None = None) -> dict[str, Any]:
        """Source round-handicap values in newest-first order. This is not GHIN."""
        selected = self._rounds if rounds is None else rounds
        return {
            "source": "round_handicap",
            "ghin": False,
            "rounds": [
                {
                    "id": round_["id"],
                    "date": round_["date"],
                    "round_handicap": round_["round_handicap"],
                }
                for round_ in selected
            ],
        }

    def capabilities(self) -> dict[str, Any]:
        return {
            "source": SOURCE,
            "mode": "official_export",
            "live_api": False,
            "automatic_refresh": False,
            "rounds": True,
            "hole_scores": True,
            "round_stats": True,
            "shots": True,
            "shot_coordinates": True,
            "club_distances": "recorded_shots_only",
            "smart_distances": False,
            "hole_putts": False,
            "hole_fairways": False,
            "hole_gir": False,
            "pace": False,
            "penalties": False,
            "ghin_handicap": False,
            "scoring_distribution": "exported_counts_only",
            "fairway_directions": "exported_counts_only",
            "gir_directions": "exported_counts_only",
            "hole_stroke_averages": "by_course_and_hole_number",
            "source_handicap_series": True,
            "strokes_gained": "available_non_sentinel_export_values_only",
            "shot_tracking_complete": None,
        }

    def coverage(self, rounds: list[Round] | None = None) -> dict[str, Any]:
        selected = self._rounds if rounds is None else rounds
        return {
            "rounds": len(selected),
            "rounds_with_shots": sum(bool(round_["shots"]) for round_ in selected),
            "exported_shots": sum(len(round_["shots"]) for round_ in selected),
            "scored_holes": sum(round_["holes_scored"] for round_ in selected),
            "holes_with_shots": sum(round_["coverage"]["holes_with_shots"] for round_ in selected),
            "rounds_with_tracking_gaps": sum(
                bool(round_["coverage"]["issues"]) for round_ in selected
            ),
            "shot_tracking_complete": None,
            "sg_sentinel_policy": "100_is_unknown",
            "putt_hole_denominator_validated": False,
            "latest_round": copy.deepcopy(selected[0]["coverage"]) if selected else None,
        }

    def snapshot(
        self,
        rounds: list[Round] | None = None,
        *,
        minimum_yards: int | float | None = None,
    ) -> dict[str, Any]:
        selected = self._rounds if rounds is None else rounds
        provenance = self.provenance
        return {
            "source": SOURCE,
            "date_timezone": self.timezone_name,
            "last_fetched": provenance["acquired_at"],
            "imported_at": provenance["imported_at"],
            "provenance": provenance,
            "total_rounds": len(selected),
            "total_shots": sum(len(round_["shots"]) for round_ in selected),
            "recent_rounds": copy.deepcopy(selected[:10]),
            "rounds": copy.deepcopy(selected),
            "clubs": self.club_distances(selected, minimum_yards=minimum_yards),
            "summary": self.summary(selected),
            "trends": self.trends(selected),
            "handicap_series": self.handicap_series(selected),
            "capabilities": self.capabilities(),
            "coverage": self.coverage(selected),
            "strokes_gained": _strokes_gained(selected),
        }


def _cli_error(error: BaseException) -> str:
    """Fixed CLI text. Paths, ids, and provider payloads stay out of the message."""
    from .retrieval import RetrievalError

    if isinstance(error, RetrievalError):
        return f"18Birdies retrieval error: {error}"
    if isinstance(error, ReducedArchiveError):
        return (
            "18Birdies export error: this export omits rounds already in the store. "
            "The existing store was not changed. "
            "Use --store PATH import EXPORT --new-store for a shortened archive."
        )
    if isinstance(error, EntityNotFoundError):
        return f"18Birdies export error: {error.kind} not found."
    if isinstance(error, InvalidJsonError):
        return "18Birdies export error: export is not valid JSON."
    if isinstance(error, UnavailableFileError):
        return "18Birdies export error: cannot read the export or store."
    if isinstance(error, OSError):
        text = _SAFE_OS_ERRORS.get(str(error))
        if text is not None:
            return f"18Birdies export error: {text}"
        return "18Birdies export error: store path is not writable."
    if isinstance(error, EighteenBirdiesError) and str(error) in _SAFE_CLI_ERRORS:
        return f"18Birdies export error: {error}"
    return (
        "18Birdies export error: invalid input, unavailable file, "
        "or unknown entity; see the client documentation."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"18birdies {__version__}")
    parser.add_argument("--store", type=Path, default=default_store_path())
    parser.add_argument(
        "--timezone", default="UTC", help="IANA zone for round dates (default: UTC)"
    )
    parser.add_argument(
        "--export", type=Path, help="Read an official export without writing a store"
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--redact-coordinates",
        action="store_true",
        help="Replace shot coordinates with null in the JSON result",
    )
    parser.add_argument(
        "--redact-identity",
        action="store_true",
        help="Replace course names, source ids, and handicap values with null in the JSON result",
    )
    parser.add_argument("--since", help="Inclusive round date YYYY-MM-DD in the selected timezone")
    parser.add_argument("--until", help="Inclusive round date YYYY-MM-DD in the selected timezone")
    parser.add_argument("--course", help="Course id, namespaced or the source id")
    parser.add_argument("--holes", type=int, help="Scored-hole count, such as 9 or 18")
    parser.add_argument("--last", type=int, help="Keep the newest matching rounds")
    parser.add_argument(
        "--minimum-yards",
        type=float,
        help="For clubs and snapshot, ignore shorter recorded distances",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in (
        "retrieve",
        "import",
        "list",
        "round",
        "summary",
        "trends",
        "courses",
        "clubs",
        "coverage",
        "handicap",
        "capabilities",
        "snapshot",
    ):
        child = sub.add_parser(name)
        child.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        if name == "retrieve":
            child.add_argument("--output", type=Path, required=True)
            child.add_argument("--timeout", type=int, default=300)
            child.add_argument(
                "--method", choices=("direct", "browser", "cloud", "cloud-env"), default="direct"
            )
        if name == "import":
            child.add_argument("path", type=Path)
            child.add_argument(
                "--acquired-at",
                help="Actual official export acquisition time (ISO8601 with timezone)",
            )
            child.add_argument(
                "--new-store",
                action="store_true",
                help="Create a separate store, including for a shortened archive. Fails if it exists.",
            )
        if name == "list":
            child.add_argument("--limit", type=int)
        if name == "round":
            child.add_argument("id")
    args = parser.parse_args(argv)
    try:
        result: Any
        filters_set = any(
            value is not None
            for value in (args.since, args.until, args.course, args.holes, args.last)
        )
        if filters_set and args.command not in FILTER_COMMANDS:
            raise EighteenBirdiesError(
                "Round filters apply to list, summary, trends, courses, clubs, "
                "coverage, handicap, and snapshot"
            )
        if args.minimum_yards is not None and args.command not in YARDAGE_COMMANDS:
            raise EighteenBirdiesError("Minimum yardage applies to clubs and snapshot")
        if args.command == "retrieve":
            from .retrieval import retrieve

            if args.export:
                raise EighteenBirdiesError("--export cannot be used with retrieve")
            result = retrieve(args.output, timeout=args.timeout, method=args.method)
        elif args.command == "import":
            if args.export:
                raise EighteenBirdiesError("--export cannot be used with import")
            client = EighteenBirdiesClient.import_export(
                args.path,
                store_path=args.store,
                acquired_at=args.acquired_at,
                timezone_name=args.timezone,
                new_store=args.new_store,
            )
            result = {
                "source": SOURCE,
                "imported_rounds": len(client.list_rounds()),
                "provenance": client.provenance,
                "coverage": client.coverage(),
            }
        else:
            client = (
                EighteenBirdiesClient(export_path=args.export, timezone_name=args.timezone)
                if args.export
                else EighteenBirdiesClient(store_path=args.store, timezone_name=args.timezone)
            )
            selected = client.select_rounds(
                since=args.since,
                until=args.until,
                course=args.course,
                holes=args.holes,
                last=args.last,
            )
            if args.command == "list":
                result = client.list_rounds(args.limit, selected)
            elif args.command == "round":
                result = client.get_round(args.id)
            else:
                result = {
                    "summary": lambda: client.summary(selected),
                    "trends": lambda: client.trends(selected),
                    "courses": lambda: client.course_history(selected),
                    "clubs": lambda: client.club_distances(
                        selected, minimum_yards=args.minimum_yards
                    ),
                    "coverage": lambda: client.coverage(selected),
                    "handicap": lambda: client.handicap_series(selected),
                    "capabilities": client.capabilities,
                    "snapshot": lambda: client.snapshot(selected, minimum_yards=args.minimum_yards),
                }[args.command]()
        if args.redact_identity:
            result = redact_identity(result)
        if args.redact_coordinates:
            result = redact_coordinates(result)
        if args.command == "retrieve":
            from .retrieval import ARCHIVE_NOTICE

            print(ARCHIVE_NOTICE, file=sys.stderr)
        print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))
        return 0
    except (EighteenBirdiesError, OSError) as error:
        print(_cli_error(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
