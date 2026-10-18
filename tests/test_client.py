"""Synthetic-only regression coverage for the private official-export client."""

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from eighteenbirdies import client as api


def fixture():
    return {
        "myData": {
            "accountData": {"syntheticSensitiveField": "DO-NOT-COPY"},
            "friendData": {"private": "DO-NOT-COPY"},
            "clubData": {
                "playedClubs": [{"clubId": "course-a", "name": "Synthetic Course"}],
                "followedClubs": [{"private": "DO-NOT-COPY"}],
            },
            "activityData": {
                "roundCount": 2,
                "rounds": [
                    {
                        "id": "round-a",
                        "timestamp": 1762041600000,
                        "clubId": {"id": "course-a"},
                        "strokes": 9,
                        "score": 1,
                        "holeStrokes": [4, 5, 0],
                        "roundHandicap": "12.0",
                        "stats": {
                            "putts": 4,
                            "puttHoleCount": 0,
                            "fairwayHoleCount": 1,
                            "fairwayMiddles": 0,
                            "gir": 1,
                            "girHoleCount": 2,
                            "strokeGainOverall": 100,
                            "strokeGainTeeToGreen": 100,
                            "recommendedStats": [
                                {"type": "SG_PUTTING", "noStats": False, "value": -1.5}
                            ],
                        },
                        "shotEntries": [
                            {
                                "timestamp": 1762041600000,
                                "holeNumber": 1,
                                "distanceInYards": 240,
                                "stickTypeAndNumber": {
                                    "type": "WOOD",
                                    "number": "1",
                                    "loftAngle": 10,
                                },
                                "startPoint": {"latitude": 0, "longitude": 0},
                                "endPoint": {"latitude": 0.001, "longitude": 0},
                            },
                            {
                                "holeNumber": 1,
                                "distanceInYards": 120,
                                "stickTypeAndNumber": {"type": "IRON", "number": "7"},
                            },
                        ],
                    },
                    {
                        "id": "round-b",
                        "timestamp": 1762128000000,
                        "clubId": {"id": "course-a"},
                        "strokes": 36,
                        "score": 0,
                        "holeStrokes": [4] * 9,
                        "stats": {
                            "putts": 0,
                            "fairwayHoleCount": 0,
                            "fairwayMiddles": 0,
                            "gir": 0,
                            "girHoleCount": 0,
                        },
                        "shotEntries": None,
                    },
                ],
            },
        }
    }


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "official.json"
        self.store = Path(self.directory.name) / "private" / "golf.json"
        self.data = fixture()
        self.write()

    def write(self):
        self.path.write_text(json.dumps(self.data), encoding="utf-8")

    def client(self):
        self.write()
        return api.EighteenBirdiesClient.from_export(self.path, timezone_name="America/Los_Angeles")

    def test_source_namespace_order_and_pacific_date(self):
        client = self.client()
        rounds = client.rounds.list()
        self.assertEqual(
            [r["id"] for r in rounds], ["18birdies:round:round-b", "18birdies:round:round-a"]
        )
        item = client.rounds.get("round-a")
        self.assertEqual(item["source"], "18birdies")
        self.assertEqual(item["date"], "2025-11-01")
        self.assertEqual(item["date_utc"], "2025-11-02")
        self.assertEqual(item["timestamp_ms"], 1762041600000)
        self.assertEqual(item["course_id"], "18birdies:course:course-a")
        self.assertEqual(item["course"], "Synthetic Course")

    def test_strokes_are_not_relative_score_or_shot_entries(self):
        item = self.client().get_round("round-a")
        self.assertEqual(item["strokes"], 9)
        self.assertEqual(item["score_to_par"], 1)
        self.assertEqual(len(item["shots"]), 2)
        self.assertEqual(item["coverage"]["shot_count_is_scoring_strokes"], False)
        self.assertIsNone(item["coverage"]["shot_tracking_complete"])

    def test_partial_nine_and_eighteen_are_preserved(self):
        item = self.client().get_round("round-a")
        self.assertEqual(item["hole_slots"], 3)
        self.assertEqual(item["holes_scored"], 2)
        self.assertIsNone(item["holes"][2]["strokes"])
        self.assertIn("unscored_hole_slots", item["coverage"]["issues"])
        self.assertEqual(self.client().get_round("round-b")["holes_scored"], 9)
        self.data["myData"]["activityData"]["rounds"][1].update(holeStrokes=[4] * 18, strokes=72)
        self.assertEqual(self.client().get_round("round-b")["holes_scored"], 18)

    def test_hole_stats_not_invented_from_round_totals(self):
        item = self.client().get_round("round-a")
        self.assertEqual(item["stats"]["putts"], 4)
        for hole in item["holes"]:
            for key in ("putts", "fairway", "green_in_regulation"):
                self.assertIsNone(hole[key])
        self.assertIsNone(item["stats"]["putts_per_hole"])
        self.assertIsNone(item["stats"]["putt_hole_count"])

    def test_zero_denominators_are_unknown_but_real_zero_hit_is_known(self):
        client = self.client()
        self.assertEqual(client.get_round("round-a")["stats"]["fairway_pct"], 0)
        self.assertEqual(client.get_round("round-a")["stats"]["gir_pct"], 50)
        for key in (
            "putts",
            "fairways_hit",
            "fairways_possible",
            "gir",
            "gir_possible",
            "fairway_pct",
            "gir_pct",
        ):
            self.assertIsNone(client.get_round("round-b")["stats"][key])

    def test_sg_sentinel_and_recommended_availability(self):
        stats = self.client().get_round("round-a")["stats"]["strokes_gained"]
        self.assertIsNone(stats["overall"])
        self.assertIsNone(stats["tee_to_green"])
        self.assertEqual(stats["putting"], -1.5)
        raw = self.data["myData"]["activityData"]["rounds"][0]["stats"]
        raw["strokeGainOverall"] = -3
        raw["recommendedStats"] = [{"type": "SG_OVERALL", "noStats": True, "value": -3}]
        self.assertIsNone(self.client().get_round("round-a")["stats"]["strokes_gained"]["overall"])
        raw["recommendedStats"] = [{"type": "SG_OVERALL", "noStats": False, "value": 100}]
        self.assertIsNone(self.client().get_round("round-a")["stats"]["strokes_gained"]["overall"])
        raw["recommendedStats"][0]["value"] = -2
        self.assertEqual(
            self.client().get_round("round-a")["stats"]["strokes_gained"]["overall"], -2
        )

    def test_shots_and_coverage_are_first_class(self):
        item = self.client().get_round("round-a")
        self.assertEqual(item["holes"][0]["shots"], item["shots"])
        self.assertEqual(item["shots"][0]["club"]["type"], "WOOD")
        self.assertEqual(item["shots"][0]["distance_yards"], 240)
        self.assertEqual(item["coverage"]["scored_holes_without_shots"], [2])
        self.assertEqual(item["coverage"]["holes_with_shots"], 1)
        self.assertEqual(self.client().coverage()["rounds_with_shots"], 1)

    def test_missing_shot_fields_and_unmatched_hole_are_flagged(self):
        self.data["myData"]["activityData"]["rounds"][0]["shotEntries"].append({"holeNumber": 18})
        item = self.client().get_round("round-a")
        self.assertIn("shots_without_matching_hole_slot", item["coverage"]["issues"])
        self.assertIn("shots_missing_distance", item["coverage"]["issues"])
        self.assertIn("shots_missing_club", item["coverage"]["issues"])
        self.assertEqual(len(item["shots"]), 3)

    def test_score_mismatch_is_visible_and_excluded_from_score_average(self):
        self.data["myData"]["activityData"]["rounds"][0]["strokes"] = 10
        client = self.client()
        self.assertIn(
            "reported_strokes_differ_from_scored_holes",
            client.get_round("round-a")["coverage"]["issues"],
        )
        self.assertIsNone(client.summary()["scoring_by_holes"][0]["average_strokes"])

    def test_summary_does_not_mix_nine_and_partial(self):
        summary = self.client().summary()
        self.assertEqual(summary["scored_holes"], 11)
        self.assertEqual(summary["exported_shots"], 2)
        self.assertEqual(
            summary["scoring_by_holes"],
            [
                {
                    "holes_scored": 2,
                    "rounds": 1,
                    "scored_rounds": 1,
                    "average_strokes": 9,
                    "best_strokes": 9,
                },
                {
                    "holes_scored": 9,
                    "rounds": 1,
                    "scored_rounds": 1,
                    "average_strokes": 36,
                    "best_strokes": 36,
                },
            ],
        )
        self.assertEqual(summary["fairway_rounds"], 1)
        self.assertEqual(summary["putt_rounds"], 1)
        self.assertEqual(len(self.client().trends()), 1)
        self.assertEqual(self.client().courses.get("course-a")["rounds"], 2)

    def test_clubs_use_actual_recorded_distance_not_smart_distance(self):
        clubs = self.client().clubs.smart_distances()
        self.assertEqual(len(clubs), 2)
        self.assertEqual(sorted(c["average_distance"] for c in clubs), [120, 240])
        self.assertTrue(all(c["smart_distance"] is None for c in clubs))
        self.assertTrue(all(c["distance_samples"] == 1 for c in clubs))

    def test_import_is_private_golf_only_and_preserves_provenance(self):
        client = api.EighteenBirdiesClient.import_export(
            self.path, store_path=self.store, acquired_at="2026-10-18T01:13:00Z"
        )
        saved = self.store.read_text()
        self.assertNotIn("DO-NOT-COPY", saved)
        self.assertNotIn("accountData", saved)
        self.assertNotIn("friendData", saved)
        self.assertNotIn("followedClubs", saved)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.store.parent.stat().st_mode), 0o700)
        self.assertEqual(client.provenance["acquired_at"], "2026-10-18T01:13:00+00:00")
        self.assertEqual(client.provenance["source_bytes"], self.path.stat().st_size)
        self.assertNotEqual(client.provenance["imported_at"], client.snapshot()["last_fetched"])
        self.assertEqual(
            api.EighteenBirdiesClient(store_path=self.store).snapshot(), client.snapshot()
        )

    def test_import_time_never_becomes_acquisition_time(self):
        client = api.EighteenBirdiesClient.import_export(self.path, store_path=self.store)
        self.assertIsNone(client.snapshot()["last_fetched"])
        self.assertIsNotNone(client.snapshot()["imported_at"])

    def test_read_only_export_and_defensive_copies(self):
        client = self.client()
        self.assertFalse(self.store.exists())
        item = client.get_round("round-a")
        item["shots"].clear()
        self.assertEqual(len(client.get_round("round-a")["shots"]), 2)
        p = client.provenance
        p["acquired_at"] = "invalid"
        self.assertIsNone(client.provenance["acquired_at"])

    def test_reimport_replaces_snapshot_and_failure_preserves_old_store(self):
        api.EighteenBirdiesClient.import_export(self.path, store_path=self.store)
        old = self.store.read_bytes()
        self.data["myData"]["activityData"]["rounds"][0]["score"] = 2
        self.write()
        with patch.object(api.os, "replace", side_effect=OSError("synthetic failure")):
            with self.assertRaises(OSError):
                api.EighteenBirdiesClient.import_export(self.path, store_path=self.store)
        self.assertEqual(self.store.read_bytes(), old)
        self.assertEqual(list(self.store.parent.glob(".18birdies-*")), [])
        self.data["myData"]["activityData"]["rounds"].pop()
        self.data["myData"]["activityData"]["roundCount"] = 1
        self.write()
        with self.assertRaises(api.EighteenBirdiesError):
            api.EighteenBirdiesClient.import_export(self.path, store_path=self.store)
        self.assertEqual(self.store.read_bytes(), old)

    def test_entire_approved_subtrees_preserved_including_future_fields(self):
        raw = self.data["myData"]["activityData"]["rounds"][0]
        raw["future_round_field"] = {"preserve": [None, 0, False, "future"]}
        raw["stats"]["future_stat"] = 9
        raw["shotEntries"][0]["future_shot_field"] = True
        raw["shotEntries"][0]["stickTypeAndNumber"]["future_club_field"] = "x"
        self.data["myData"]["activityData"]["future_activity"] = {"v": 1}
        self.data["myData"]["clubData"]["playedClubs"][0]["future_course"] = "x"
        self.write()
        client = api.EighteenBirdiesClient.import_export(self.path, store_path=self.store)
        self.assertEqual(
            client.raw_golf_data(),
            {
                "activityData": self.data["myData"]["activityData"],
                "playedClubs": self.data["myData"]["clubData"]["playedClubs"],
            },
        )
        self.assertNotIn("DO-NOT-COPY", self.store.read_text())
        returned = client.raw_golf_data()
        returned["activityData"]["rounds"].clear()
        self.assertEqual(len(client.raw_golf_data()["activityData"]["rounds"]), 2)

    def test_identical_import_does_not_advance_metadata_or_rewrite(self):
        client = api.EighteenBirdiesClient.import_export(
            self.path, store_path=self.store, acquired_at="2026-10-18T01:13:00Z"
        )
        content = self.store.read_bytes()
        before = self.store.stat().st_mtime_ns
        with patch.object(api, "_atomic_write", side_effect=AssertionError("must not rewrite")):
            repeated = api.EighteenBirdiesClient.import_export(self.path, store_path=self.store)
        self.assertEqual(repeated.provenance, client.provenance)
        self.assertEqual(self.store.read_bytes(), content)
        self.assertEqual(self.store.stat().st_mtime_ns, before)
        reacquired = api.EighteenBirdiesClient.import_export(
            self.path, store_path=self.store, acquired_at="2026-10-19T01:13:00Z"
        )
        self.assertEqual(reacquired.provenance["imported_at"], client.provenance["imported_at"])
        self.assertEqual(reacquired.provenance["acquired_at"], "2026-10-19T01:13:00+00:00")
        with self.assertRaises(api.EighteenBirdiesError):
            api.EighteenBirdiesClient.import_export(
                self.path, store_path=self.store, acquired_at="2026-10-17T01:13:00Z"
            )

    def test_explicit_available_zero_putts_preserved(self):
        raw = self.data["myData"]["activityData"]["rounds"][1]["stats"]
        raw["recommendedStats"] = [{"type": "TOTAL_PUTTS", "noStats": False, "value": 0}]
        self.assertEqual(self.client().get_round("round-b")["stats"]["putts"], 0)

    def test_import_uses_private_regular_lock_and_allows_shot_corrections(self):
        api.EighteenBirdiesClient.import_export(self.path, store_path=self.store)
        lock = self.store.with_name(self.store.name + ".lock")
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)
        self.data["myData"]["activityData"]["rounds"][0]["shotEntries"].pop()
        self.write()
        updated = api.EighteenBirdiesClient.import_export(self.path, store_path=self.store)
        self.assertEqual(len(updated.get_round("round-a")["shots"]), 1)

    def test_bad_new_export_preserves_prior_store(self):
        api.EighteenBirdiesClient.import_export(self.path, store_path=self.store)
        previous = self.store.read_bytes()
        self.path.write_text("{truncated")
        with self.assertRaises(api.EighteenBirdiesError):
            api.EighteenBirdiesClient.import_export(self.path, store_path=self.store)
        self.assertEqual(self.store.read_bytes(), previous)

    def test_invalid_schema_variants(self):
        original = copy.deepcopy(self.data)
        changes = [
            lambda d: d["myData"]["activityData"].update(roundCount=3),
            lambda d: d["myData"]["activityData"]["rounds"][1].update(id="round-a"),
            lambda d: d["myData"]["activityData"]["rounds"][0].update(strokes=True),
            lambda d: d["myData"]["activityData"]["rounds"][0].update(holeStrokes=[-1]),
            lambda d: d["myData"]["activityData"]["rounds"][0].update(holeStrokes=[4] * 19),
            lambda d: d["myData"]["activityData"]["rounds"][0].update(shotEntries={}),
            lambda d: d["myData"]["activityData"]["rounds"][0].update(timestamp="not-ms"),
            lambda d: d["myData"]["activityData"]["rounds"][0]["shotEntries"][0].update(
                distanceInYards=-1
            ),
            lambda d: d["myData"]["activityData"]["rounds"][0]["shotEntries"][0].update(
                holeNumber=0
            ),
            lambda d: d["myData"]["activityData"]["rounds"][0]["shotEntries"][0][
                "startPoint"
            ].update(latitude=91),
            lambda d: d["myData"]["activityData"]["rounds"][0]["stats"].update(putts=-1),
            lambda d: d["myData"]["activityData"]["rounds"][0]["stats"].update(
                recommendedStats=[{"type": "SG_OVERALL", "noStats": "false"}]
            ),
        ]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                self.data = copy.deepcopy(original)
                change(self.data)
                with self.assertRaises(api.EighteenBirdiesError):
                    self.client()

    def test_size_nonfinite_duplicate_keys_and_invalid_json(self):
        for data in (
            '{"myData":{}, "myData":{}}',
            '{"x":NaN}',
            '{"x":Infinity}',
            '{"x":1e999}',
            "[1]",
            "not json",
        ):
            with self.subTest(data=data):
                self.path.write_text(data)
                with self.assertRaises(api.EighteenBirdiesError):
                    api.EighteenBirdiesClient.from_export(self.path)
        self.write()
        with patch.object(api, "MAX_BYTES", 10):
            with self.assertRaises(api.EighteenBirdiesError):
                api.EighteenBirdiesClient.from_export(self.path)

    def test_missing_file_entity_and_invalid_limits(self):
        with self.assertRaises(api.EighteenBirdiesError):
            api.EighteenBirdiesClient(store_path=self.store)
        client = self.client()
        with self.assertRaises(api.EighteenBirdiesError):
            client.get_round("missing")
        with self.assertRaises(api.EighteenBirdiesError):
            client.courses.get("missing")
        for value in (-1, True, "2"):
            with self.assertRaises(api.EighteenBirdiesError):
                client.list_rounds(value)
        self.assertEqual(client.list_rounds(0), [])

    def test_unsupported_nesting_is_a_domain_error(self):
        nested = {}
        for _ in range(130):
            nested = {"next": nested}
        self.data["myData"]["activityData"]["future_field"] = nested
        with self.assertRaises(api.EighteenBirdiesError):
            self.client()

    def test_acquisition_time_requires_timezone_and_source_is_not_overwritten(self):
        for value in ("2026-10-18", "invalid", 12):
            with self.assertRaises(api.EighteenBirdiesError):
                api.EighteenBirdiesClient.import_export(
                    self.path, store_path=self.store, acquired_at=value
                )
        original = self.path.read_bytes()
        with self.assertRaises(api.EighteenBirdiesError):
            api.EighteenBirdiesClient.import_export(self.path, store_path=self.path)
        self.assertEqual(self.path.read_bytes(), original)

    def test_empty_export_supported_without_invented_values(self):
        self.data["myData"]["activityData"] = {"roundCount": 0, "rounds": []}
        client = self.client()
        self.assertEqual(client.list_rounds(), [])
        self.assertIsNone(client.summary()["fairway_pct"])
        self.assertEqual(client.clubs.smart_distances(), [])
        self.assertIsNone(client.coverage()["latest_round"])
        self.assertEqual(client.stats.strokes_gained()["overall"], {"mean": None, "rounds": 0})

    def test_capabilities_do_not_claim_live_feed_or_unavailable_stats(self):
        capabilities = self.client().capabilities()
        for key in (
            "live_api",
            "automatic_refresh",
            "smart_distances",
            "hole_putts",
            "hole_fairways",
            "hole_gir",
            "ghin_handicap",
        ):
            self.assertFalse(capabilities[key])

    def test_cli_json_and_redacted_errors(self):
        for command in (
            "list",
            "summary",
            "trends",
            "courses",
            "clubs",
            "capabilities",
            "snapshot",
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = api.main(["--export", str(self.path), command, "--json"])
            self.assertEqual(code, 0)
            json.loads(output.getvalue())
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            code = api.main(["--export", str(self.path), "round", "private-identifier", "--json"])
        self.assertEqual(code, 2)
        self.assertNotIn("private-identifier", error.getvalue())
        self.assertNotIn(str(self.path), error.getvalue())

    def test_unicode_store_is_utf8_and_remains_readable_within_cap(self):
        api.EighteenBirdiesClient.import_export(self.path, store_path=self.store)
        self.data["myData"]["activityData"]["future_text"] = "\u00e9" * 3500
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        self.assertLess(self.path.stat().st_size, 10000)
        with patch.object(api, "MAX_BYTES", 10000):
            client = api.EighteenBirdiesClient.import_export(self.path, store_path=self.store)
            reopened = api.EighteenBirdiesClient(store_path=self.store)
        self.assertEqual(client.raw_golf_data(), reopened.raw_golf_data())
        self.assertLessEqual(self.store.stat().st_size, 10000)
        self.assertNotIn(b"\\u00e9", self.store.read_bytes())

    def test_encoded_store_overhead_rejected_before_replacement(self):
        api.EighteenBirdiesClient.import_export(self.path, store_path=self.store)
        old = self.store.read_bytes()
        root = self.data["myData"]
        self.data = {
            "myData": {
                "activityData": root["activityData"],
                "clubData": {"playedClubs": root["clubData"]["playedClubs"]},
            }
        }
        self.data["myData"]["activityData"]["future_text"] = "x" * 4000
        self.path.write_text(json.dumps(self.data, separators=(",", ":")), encoding="utf-8")
        cap = self.path.stat().st_size
        self.assertLess(len(old), cap)
        with patch.object(api, "MAX_BYTES", cap):
            with patch.object(api.os, "replace") as replace:
                with self.assertRaisesRegex(api.EighteenBirdiesError, "Encoded store"):
                    api.EighteenBirdiesClient.import_export(self.path, store_path=self.store)
                replace.assert_not_called()
        self.assertEqual(self.store.read_bytes(), old)

    def test_candidate_normalization_failure_preserves_previous_store(self):
        api.EighteenBirdiesClient.import_export(self.path, store_path=self.store)
        old = self.store.read_bytes()
        self.data["myData"]["activityData"]["future_marker"] = "reject-normalization"
        self.write()
        initialize = api.EighteenBirdiesClient._initialize

        def reject(candidate, envelope, *arguments):
            if envelope["golf"]["activityData"].get("future_marker") == "reject-normalization":
                raise api.EighteenBirdiesError("Synthetic normalization failure")
            return initialize(candidate, envelope, *arguments)

        with patch.object(api.EighteenBirdiesClient, "_initialize", new=reject):
            with patch.object(api, "_atomic_write") as write:
                with self.assertRaisesRegex(api.EighteenBirdiesError, "normalization"):
                    api.EighteenBirdiesClient.import_export(self.path, store_path=self.store)
                write.assert_not_called()
        self.assertEqual(self.store.read_bytes(), old)

    def test_resource_budgets_reject_before_any_golf_deep_copy(self):
        cases = (
            ("MAX_TOTAL_SHOTS", 1, "total shot"),
            ("MAX_ROUNDS", 1, "too many rounds"),
            ("MAX_JSON_NODES", 10, "node limit"),
            ("MAX_JSON_DEPTH", 1, "nesting"),
        )
        for constant, cap, message in cases:
            with self.subTest(constant=constant):
                with patch.object(api, constant, cap):
                    with patch.object(
                        api.copy, "deepcopy", side_effect=AssertionError("Unexpected copy")
                    ):
                        with self.assertRaisesRegex(api.EighteenBirdiesError, message):
                            self.client()
        with patch.object(api, "MAX_TOTAL_SHOTS", 2):
            self.assertEqual(self.client().coverage()["exported_shots"], 2)
            self.assertEqual(self.client().coverage()["exported_shots"], 2)

    def test_aggregate_shot_budget_rejects_large_multi_round_export(self):
        rounds = self.data["myData"]["activityData"]["rounds"]
        rounds[:] = [
            {"id": f"synthetic-{index}", "shotEntries": [{}] * 10000} for index in range(11)
        ]
        self.data["myData"]["activityData"]["roundCount"] = len(rounds)
        self.write()
        with patch.object(api.copy, "deepcopy", side_effect=AssertionError("Unexpected copy")):
            with self.assertRaisesRegex(api.EighteenBirdiesError, "total shot"):
                api.EighteenBirdiesClient.from_export(self.path)

    def test_huge_integer_is_domain_error_and_sanitized_cli_error(self):
        self.data["myData"]["activityData"]["rounds"][0]["stats"]["strokeGainOverall"] = 10**400
        self.write()
        with self.assertRaises(api.EighteenBirdiesError):
            api.EighteenBirdiesClient.from_export(self.path)
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            self.assertEqual(api.main(["--export", str(self.path), "summary", "--json"]), 2)
        self.assertNotIn("Traceback", error.getvalue())
        self.assertNotIn(str(10**400), error.getvalue())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO inputs are POSIX-specific")
    def test_fifo_input_rejected_without_waiting_for_a_writer(self):
        import subprocess
        import sys

        fifo = Path(self.directory.name) / "untrusted-input"
        os.mkfifo(fifo)
        module = api.__name__
        directory = Path(api.__file__).parent
        if module.endswith(".client"):
            directory = directory.parent
        code = (
            "import importlib, sys; sys.path.insert(0, sys.argv[1]); "
            "raise SystemExit(importlib.import_module(sys.argv[2]).main(sys.argv[3:]))"
        )
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                str(directory),
                module,
                "--export",
                str(fifo),
                "summary",
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(str(fifo), result.stderr)

    def test_trends_and_courses_group_in_one_round_pass(self):
        class CountedRounds(list):
            iterations = 0

            def __iter__(self):
                self.iterations += 1
                return super().__iter__()

        client = self.client()
        template = client.get_round("round-b")
        rounds = []
        for index in range(50):
            item = copy.deepcopy(template)
            item.update(
                id=f"round-{index}",
                course_id=f"18birdies:course:{index}",
                date=f"{2000 + index:04d}-01-01",
            )
            rounds.append(item)
        client._rounds = CountedRounds(rounds)
        self.assertEqual(len(client.trends()), 50)
        self.assertEqual(client._rounds.iterations, 1)
        client._rounds.iterations = 0
        self.assertEqual(len(client.course_history()), 50)
        self.assertEqual(client._rounds.iterations, 1)

    def test_json_complexity_rejected_before_decoder_allocation(self):
        self.path.write_text('{"items":[' + ",".join(["{}"] * 20) + "]}", encoding="utf-8")
        with patch.object(api, "MAX_JSON_NODES", 10):
            with patch.object(api.json, "loads", side_effect=AssertionError("Unexpected decode")):
                with self.assertRaisesRegex(api.EighteenBirdiesError, "node limit"):
                    api.EighteenBirdiesClient.from_export(self.path)
        self.path.write_text('{"item":' + "[" * 10 + "0" + "]" * 10 + "}", encoding="utf-8")
        with patch.object(api, "MAX_JSON_DEPTH", 3):
            with patch.object(api.json, "loads", side_effect=AssertionError("Unexpected decode")):
                with self.assertRaisesRegex(api.EighteenBirdiesError, "nesting"):
                    api.EighteenBirdiesClient.from_export(self.path)

    def test_json_complexity_guard_ignores_escaped_string_contents(self):
        text = 'quoted"slash\\' + "[" * 200 + "}" * 200
        self.data["myData"]["activityData"]["future_text"] = text
        self.write()
        with patch.object(api, "MAX_JSON_DEPTH", 12):
            client = api.EighteenBirdiesClient.from_export(self.path)
        self.assertEqual(client.raw_golf_data()["activityData"]["future_text"], text)

    def test_utf16_and_utf32_inputs_are_rejected_without_decoder_autodetection(self):
        text = json.dumps(self.data)
        for encoding in ("utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be"):
            with self.subTest(encoding=encoding):
                self.path.write_bytes(text.encode(encoding))
                with self.assertRaises(api.EighteenBirdiesError):
                    api.EighteenBirdiesClient.from_export(self.path)
                error = io.StringIO()
                with contextlib.redirect_stderr(error):
                    self.assertEqual(api.main(["--export", str(self.path), "summary", "--json"]), 2)
                self.assertNotIn("Traceback", error.getvalue())

    def test_utf8_signature_is_supported(self):
        self.path.write_bytes(json.dumps(self.data).encode("utf-8-sig"))
        self.assertEqual(api.EighteenBirdiesClient.from_export(self.path).coverage()["rounds"], 2)


if __name__ == "__main__":
    unittest.main()
