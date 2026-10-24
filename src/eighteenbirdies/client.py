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
from pathlib import Path
import statistics
import stat
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any
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


class EighteenBirdiesError(ValueError):
    """Malformed or unsupported export, missing store, or unknown entity."""


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
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        raise EighteenBirdiesError("Acquisition time must be ISO8601 with timezone") from None


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if not _integer(value, 0, 253402300799000):
        raise EighteenBirdiesError("Golf timestamp must be Unix milliseconds")
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()
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
        raise EighteenBirdiesError("Cannot read a regular export or store file") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not data or len(data) > MAX_BYTES:
        raise EighteenBirdiesError("Export is empty or exceeds the byte limit")
    try:
        decoded = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise EighteenBirdiesError("Export must be valid UTF-8 JSON") from None
    lexical_data = memoryview(data)[3:] if data.startswith(b"\xef\xbb\xbf") else data
    _check_json_complexity(lexical_data)
    try:

        def finite_float(value):
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
        raise EighteenBirdiesError("Export must be valid UTF-8 JSON") from None
    except ValueError as error:
        if isinstance(error, EighteenBirdiesError):
            raise
        raise EighteenBirdiesError("Unsupported JSON value") from None
    if not isinstance(result, dict):
        raise EighteenBirdiesError("Export root must be an object")
    _check_json_bounds(result)
    return result, data


def _stats(value: Any) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise EighteenBirdiesError("Round stats must be an object")
    result = {}
    for key in STAT_COUNTS | SG_FIELDS:
        item = value.get(key)
        if item is not None:
            if key in STAT_COUNTS and not _integer(item):
                raise EighteenBirdiesError("Invalid round stat count")
            if key in SG_FIELDS and _number(item) is None:
                raise EighteenBirdiesError("Invalid strokes-gained value")
            result[key] = item
    recommended = value.get("recommendedStats", [])
    if not isinstance(recommended, list) or len(recommended) > 100:
        raise EighteenBirdiesError("Invalid recommended stats list")
    result["recommendedStats"] = []
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
        result["recommendedStats"].append(
            {k: item[k] for k in ("type", "noStats", "value") if k in item}
        )
    return value


def _shot(value: Any) -> dict:
    if not isinstance(value, dict):
        raise EighteenBirdiesError("Shot entry must be an object")
    result = {k: value[k] for k in SHOT_FIELDS if k in value}
    _timestamp(result.get("timestamp"))
    if result.get("holeNumber") is not None and not _integer(result["holeNumber"], 1, 18):
        raise EighteenBirdiesError("Shot hole number must be 1 through 18")
    distance = result.get("distanceInYards")
    if distance is not None and (_number(distance) is None or not 0 <= distance <= 10000):
        raise EighteenBirdiesError("Invalid shot distance in yards")
    club = result.get("stickTypeAndNumber")
    if club is not None:
        if not isinstance(club, dict):
            raise EighteenBirdiesError("Invalid shot club")
        result["stickTypeAndNumber"] = {
            k: club[k] for k in ("type", "number", "loftAngle") if k in club
        }
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
        point = result.get(key)
        if point is None:
            continue
        if not isinstance(point, dict):
            raise EighteenBirdiesError("Invalid shot coordinates")
        result[key] = {k: point[k] for k in ("latitude", "longitude") if k in point}
        for coordinate, bound in (("latitude", 90), ("longitude", 180)):
            n = point.get(coordinate)
            if n is not None and (_number(n) is None or not -bound <= n <= bound):
                raise EighteenBirdiesError("Invalid shot coordinates")
    return value


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
        cid = item.get("clubId")
        if cid is not None:
            if not isinstance(cid, dict):
                raise EighteenBirdiesError("Round clubId must be an object")
            item["clubId"] = {"id": _identifier(cid.get("id"))}
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
        item["stats"] = _stats(item.get("stats"))
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
            _shot(shot)
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
            "imported_at": datetime.now(timezone.utc).isoformat(),
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


def _mean(values: list) -> float | None:
    return round(statistics.mean(values), 3) if values else None


def _ratio(numerator: Any, denominator: Any, factor: int = 100) -> float | None:
    if numerator is None or not denominator or numerator > denominator:
        return None
    return round(factor * numerator / denominator, 3)


def _normal_stats(raw: dict) -> dict:
    recommended = {v["type"]: v for v in raw.get("recommendedStats", [])}

    def available(key: str, stat_type: str) -> int | float | None:
        recommendation = recommended.get(stat_type, {})
        if recommendation.get("noStats") is True:
            return None
        return raw.get(key)

    def sg(key: str, stat_type: str) -> int | float | None:
        recommendation = recommended.get(stat_type, {})
        if recommendation.get("noStats") is True:
            return None
        n = recommendation.get("value") if recommendation.get("noStats") is False else raw.get(key)
        # The observed 100 markers have no validated semantic meaning.
        return n if n != 100 else None

    fw_count = raw.get("fairwayHoleCount") or None
    gir_count = raw.get("girHoleCount") or None
    fw = available("fairwayMiddles", "FAIRWAY_HIT_PERCENTAGE") if fw_count else None
    gir = available("gir", "GIR_PERCENTAGE") if gir_count else None
    if fw is not None and fw > fw_count:
        fw = fw_count = None
    if gir is not None and gir > gir_count:
        gir = gir_count = None
    putts = available("putts", "TOTAL_PUTTS")
    putt_recommendation = recommended.get("TOTAL_PUTTS", {})
    if putt_recommendation.get("noStats") is False and putts is None:
        candidate = putt_recommendation.get("value")
        putts = candidate if _integer(candidate) else None
    if putts == 0 and putt_recommendation.get("noStats") is not False:
        putts = None
    return {
        "putts": putts,
        "putts_per_hole": None,
        "putt_hole_count": None,
        "fairways_hit": fw,
        "fairways_possible": fw_count,
        "gir": gir,
        "gir_possible": gir_count,
        "fairway_pct": _ratio(fw, fw_count),
        "gir_pct": _ratio(gir, gir_count),
        "strokes_gained": {
            "overall": sg("strokeGainOverall", "SG_OVERALL"),
            "tee_to_green": sg("strokeGainTeeToGreen", "SG_TEE_TO_GREEN"),
            "putting": sg("__absent", "SG_PUTTING"),
            "sand": sg("__absent", "SG_SAND"),
        },
    }


def _normalize_round(raw: dict, courses: dict, date_zone=timezone.utc) -> dict:
    rid = f"{SOURCE}:round:{raw['id']}"
    cid = (raw.get("clubId") or {}).get("id")
    stamp = _timestamp(raw.get("timestamp"))
    shots = []
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
    holes = [
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
    scored = [h for h in holes if h["strokes"] is not None]
    missing_shots = [h["number"] for h in scored if not h["shots"]]
    reported = raw.get("strokes") or None
    hole_sum = sum(h["strokes"] for h in scored) if scored else None
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
        "course": courses.get(cid),
        "date": datetime.fromisoformat(stamp).astimezone(date_zone).date().isoformat()
        if stamp
        else None,
        "date_utc": stamp[:10] if stamp else None,
        "timestamp_ms": raw.get("timestamp"),
        "strokes": reported,
        "score_to_par": raw.get("score"),
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


def _date_zone(timezone_name: str):
    try:
        return timezone.utc if timezone_name == "UTC" else ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise EighteenBirdiesError("Unknown timezone or unavailable timezone data") from None


class _Rounds:
    def __init__(self, client):
        self.client = client

    def list(self, limit: int | None = None) -> list[dict]:
        return self.client.list_rounds(limit)

    def get(self, round_id: str) -> dict:
        return self.client.get_round(round_id)


class _Courses:
    def __init__(self, client):
        self.client = client

    def list(self) -> list[dict]:
        return self.client.course_history()

    def get(self, course_id: str) -> dict:
        for course in self.list():
            if course_id in (course["id"], course["source_id"]):
                return course
        raise EighteenBirdiesError("Course not found")


class _Clubs:
    def __init__(self, client):
        self.client = client

    def smart_distances(self) -> list[dict]:
        return self.client.club_distances()

    distances = smart_distances


class _Stats:
    def __init__(self, client):
        self.client = client

    def summary(self) -> dict:
        return self.client.summary()

    def trends(self) -> list[dict]:
        return self.client.trends()

    def strokes_gained(self, round_ids: list[str] | None = None) -> dict:
        rounds = (
            [self.client.get_round(r) for r in round_ids]
            if round_ids is not None
            else self.client.list_rounds()
        )
        return {
            key: {
                "mean": _mean(
                    [
                        r["stats"]["strokes_gained"][key]
                        for r in rounds
                        if r["stats"]["strokes_gained"][key] is not None
                    ]
                ),
                "rounds": sum(r["stats"]["strokes_gained"][key] is not None for r in rounds),
            }
            for key in ("overall", "tee_to_green", "putting", "sand")
        }


class EighteenBirdiesClient:
    """Read an official export directly, or an explicitly imported golf-only store."""

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
        courses = {c["clubId"]: c.get("name") for c in golf["playedClubs"]}
        self._rounds = sorted(
            [_normalize_round(r, courses, date_zone) for r in golf["activityData"]["rounds"]],
            key=lambda r: (r["timestamp_ms"] if r["timestamp_ms"] is not None else -1, r["id"]),
            reverse=True,
        )
        self.rounds, self.courses, self.clubs, self.stats = (
            _Rounds(self),
            _Courses(self),
            _Clubs(self),
            _Stats(self),
        )

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
    ) -> EighteenBirdiesClient:
        _date_zone(timezone_name)
        envelope = _envelope(path, acquired_at)
        destination = Path(store_path or DEFAULT_STORE)
        if Path(path).resolve() == destination.resolve():
            raise EighteenBirdiesError("Import destination must differ from original export")
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = destination.with_name(destination.name + ".lock")
        with file_lock(lock_path):
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
                        raise EighteenBirdiesError(
                            "Export omits previously imported rounds; prior store preserved"
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

    def list_rounds(self, limit: int | None = None) -> list[dict]:
        if limit is not None and not _integer(limit, 0, MAX_ROUNDS):
            raise EighteenBirdiesError("Round limit must be a nonnegative integer")
        return copy.deepcopy(self._rounds if limit is None else self._rounds[:limit])

    def get_round(self, round_id: str) -> dict:
        for item in self._rounds:
            if round_id in (item["id"], item["source_id"]):
                return copy.deepcopy(item)
        raise EighteenBirdiesError("Round not found")

    def summary(self, rounds: list[dict] | None = None) -> dict:
        rounds = self._rounds if rounds is None else rounds
        buckets = []
        for size in sorted({r["holes_scored"] for r in rounds}):
            group = [r for r in rounds if r["holes_scored"] == size]
            scores = [
                r["strokes"]
                for r in group
                if r["strokes"] is not None and r["strokes"] == r["hole_score_total"]
            ]
            buckets.append(
                {
                    "holes_scored": size,
                    "rounds": len(group),
                    "scored_rounds": len(scores),
                    "average_strokes": _mean(scores),
                    "best_strokes": min(scores) if scores else None,
                }
            )
        fw = [r["stats"] for r in rounds if r["stats"]["fairways_hit"] is not None]
        gir = [r["stats"] for r in rounds if r["stats"]["gir"] is not None]
        putts = [r["stats"]["putts"] for r in rounds if r["stats"]["putts"] is not None]
        return {
            "rounds": len(rounds),
            "scored_holes": sum(r["holes_scored"] for r in rounds),
            "exported_shots": sum(len(r["shots"]) for r in rounds),
            "scoring_by_holes": buckets,
            "fairway_pct": _ratio(
                sum(s["fairways_hit"] for s in fw), sum(s["fairways_possible"] for s in fw)
            ),
            "gir_pct": _ratio(sum(s["gir"] for s in gir), sum(s["gir_possible"] for s in gir)),
            "average_putts_per_round": _mean(putts),
            "putt_rounds": len(putts),
            "fairway_rounds": len(fw),
            "gir_rounds": len(gir),
            "putts_per_hole": None,
        }

    def trends(self) -> list[dict]:
        groups: dict[str, list[dict]] = {}
        for round_ in self._rounds:
            if round_["date"]:
                groups.setdefault(round_["date"][:7], []).append(round_)
        return [{"month": month, **self.summary(groups[month])} for month in sorted(groups)]

    def course_history(self) -> list[dict]:
        groups: dict[str, list[dict]] = {}
        for round_ in self._rounds:
            if round_["course_id"]:
                groups.setdefault(round_["course_id"], []).append(round_)
        result = []
        for cid in sorted(groups):
            rounds = groups[cid]
            result.append(
                {
                    "id": cid,
                    "source_id": cid.removeprefix(f"{SOURCE}:course:"),
                    "name": rounds[0]["course"],
                    "latest_date": rounds[0]["date"],
                    "round_ids": [r["id"] for r in rounds],
                    **self.summary(rounds),
                }
            )
        return result

    def club_distances(self) -> list[dict]:
        groups: dict[tuple, list] = {}
        for round_ in self._rounds:
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
                s["distance_yards"]
                for s in shots
                if s["distance_yards"] is not None and s["distance_yards"] > 0
            ]
            result.append(
                {
                    "club": " ".join(
                        str(n) for n in (kind, number, f"{loft}deg" if loft else None) if n
                    ),
                    "type": kind,
                    "number": number,
                    "loft_angle": loft,
                    "total_shots": len(shots),
                    "distance_samples": len(distances),
                    "average_distance": _mean(distances),
                    "median_distance": round(statistics.median(distances), 3)
                    if distances
                    else None,
                    "smart_distance": None,
                    "distance_unit": "yards",
                    "method": "recorded_shot_distance",
                    "full_swing_only": None,
                    "includes_partial_shots": None,
                }
            )
        return result

    def capabilities(self) -> dict:
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
            "strokes_gained": "available_non_sentinel_export_values_only",
            "shot_tracking_complete": None,
        }

    def coverage(self) -> dict:
        rounds = self._rounds
        return {
            "rounds": len(rounds),
            "rounds_with_shots": sum(bool(r["shots"]) for r in rounds),
            "exported_shots": sum(len(r["shots"]) for r in rounds),
            "scored_holes": sum(r["holes_scored"] for r in rounds),
            "holes_with_shots": sum(r["coverage"]["holes_with_shots"] for r in rounds),
            "rounds_with_tracking_gaps": sum(bool(r["coverage"]["issues"]) for r in rounds),
            "shot_tracking_complete": None,
            "sg_sentinel_policy": "100_is_unknown",
            "putt_hole_denominator_validated": False,
            "latest_round": copy.deepcopy(rounds[0]["coverage"]) if rounds else None,
        }

    def snapshot(self) -> dict:
        provenance = self.provenance
        return {
            "source": SOURCE,
            "date_timezone": self.timezone_name,
            "last_fetched": provenance["acquired_at"],
            "imported_at": provenance["imported_at"],
            "provenance": provenance,
            "total_rounds": len(self._rounds),
            "total_shots": sum(len(r["shots"]) for r in self._rounds),
            "recent_rounds": self.list_rounds(10),
            "rounds": self.list_rounds(),
            "clubs": self.club_distances(),
            "summary": self.summary(),
            "trends": self.trends(),
            "capabilities": self.capabilities(),
            "coverage": self.coverage(),
            "strokes_gained": self.stats.strokes_gained(),
        }


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
        if name == "list":
            child.add_argument("--limit", type=int)
        if name == "round":
            child.add_argument("id")
    args = parser.parse_args(argv)
    try:
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
            if args.command == "list":
                result = client.list_rounds(args.limit)
            elif args.command == "round":
                result = client.get_round(args.id)
            else:
                result = {
                    "summary": client.summary,
                    "trends": client.trends,
                    "courses": client.course_history,
                    "clubs": client.club_distances,
                    "capabilities": client.capabilities,
                    "snapshot": client.snapshot,
                }[args.command]()
        print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))
        return 0
    except (EighteenBirdiesError, OSError) as error:
        from .retrieval import RetrievalError

        if isinstance(error, RetrievalError):
            print(f"18Birdies retrieval error: {error}", file=sys.stderr)
            return 2
        # Never echo provider JSON, identifiers, paths, or exception payloads.
        print(
            "18Birdies export error: invalid input, unavailable file, or unknown entity; see the client documentation.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
