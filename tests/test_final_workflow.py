from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zoneinfo import ZoneInfo

from src.deadline import DeadlineSettings, build_deadline_plan
from src.final_workflow import FinalReviewerAgent, merge_live_entries
from src.final_workflow import FinalPredictionWorkflow
from jra_scraper.config import ScrapeConfig


JST = ZoneInfo("Asia/Tokyo")


class FinalReviewerTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 8, 15, 18, tzinfo=JST)
        self.settings = DeadlineSettings()
        self.plan = build_deadline_plan(
            {"race_date": "2026-08-08", "post_time": "15:25"},
            now=self.now,
            settings=self.settings,
        )
        self.collected = {
            "snapshot_id": "S1",
            "snapshot_complete": True,
            "combo_odds": [
                {
                    "snapshot_id": "S1", "snapshot_complete": True,
                    "bet_type": "win", "combination": "1",
                }
            ],
            "official_odds_as_of": "2026-08-08T15:17:00+09:00",
            "conditions": {
                "weather": "雨",
                "track_condition": "稍重",
                "captured_at": "2026-08-08T15:17:30+09:00",
            },
            "entries": [
                {
                    "horse_number": "1",
                    "body_weight_status": "published",
                    "current_body_weight": "472",
                    "body_weight_change": "-2",
                }
            ],
            "quality_report": {
                "bet_types_missing": [],
                "missing_current_odds_entries": 0,
                "official_odds_timestamps_complete": True,
                "combination_coverage": {"complete": True},
            },
            "lineup": {"matches": True},
            "baseline_quality": {
                "history_complete": True,
                "review_ok": True,
                "parser_quality_ok": True,
                "manifest_ok": True,
            },
        }
        self.ticket_plan = {
            "tickets": [
                {"bet_type": "win", "horse_number": 1, "odds_source": "jra_live"}
            ]
        }

    def test_go_requires_every_final_gate(self):
        review = FinalReviewerAgent(settings=self.settings, now=lambda: self.now).run(
            plan=self.plan,
            collected=self.collected,
            ticket_plan=self.ticket_plan,
            quantitative_review={"status": "OK"},
        )

        self.assertEqual("OK", review["status"])
        self.assertEqual("GO", review["decision"])

    def test_unpublished_body_weight_forces_no_go(self):
        self.collected["entries"][0]["body_weight_status"] = "unpublished"
        review = FinalReviewerAgent(settings=self.settings, now=lambda: self.now).run(
            plan=self.plan,
            collected=self.collected,
            ticket_plan=self.ticket_plan,
            quantitative_review={"status": "OK"},
        )

        self.assertEqual("NG", review["status"])
        self.assertEqual("NO_GO", review["decision"])
        self.assertFalse(review["checks"]["body_weights_released"])

    def test_estimated_ticket_odds_force_no_go(self):
        self.ticket_plan["tickets"][0]["odds_source"] = "estimated"
        review = FinalReviewerAgent(settings=self.settings, now=lambda: self.now).run(
            plan=self.plan,
            collected=self.collected,
            ticket_plan=self.ticket_plan,
            quantitative_review={"status": "OK"},
        )

        self.assertEqual("NO_GO", review["decision"])
        self.assertFalse(review["checks"]["tickets_use_jra_live_odds"])

    def test_formation_requires_every_point_in_the_live_snapshot(self):
        self.collected["combo_odds"] = [{
            "snapshot_id": "S1", "snapshot_complete": True,
            "bet_type": "sanrentan", "combination": "1>2>3",
        }]
        self.ticket_plan["tickets"] = [{
            "bet_type": "sanrentan", "odds_source": "jra_live",
            "points": [
                {"horse_numbers": ["1", "2", "3"], "odds_source": "jra_live"},
                {"horse_numbers": ["1", "3", "2"], "odds_source": "estimated"},
            ],
        }]

        review = FinalReviewerAgent(settings=self.settings, now=lambda: self.now).run(
            plan=self.plan,
            collected=self.collected,
            ticket_plan=self.ticket_plan,
            quantitative_review={"status": "OK"},
        )

        self.assertEqual("NO_GO", review["decision"])
        self.assertFalse(review["checks"]["tickets_use_jra_live_odds"])

    def test_incomplete_baseline_history_forces_no_go(self):
        self.collected["baseline_quality"]["history_complete"] = False
        review = FinalReviewerAgent(settings=self.settings, now=lambda: self.now).run(
            plan=self.plan,
            collected=self.collected,
            ticket_plan=self.ticket_plan,
            quantitative_review={"status": "OK"},
        )

        self.assertEqual("NO_GO", review["decision"])
        self.assertFalse(review["checks"]["baseline_history_complete"])

    def test_wakuren_expands_frames_for_body_weight_safety(self):
        self.collected["entries"] = [
            {
                "horse_number": "1", "frame_number": "1",
                "body_weight_status": "published", "current_body_weight": "472",
                "body_weight_change": "-2",
            },
            {
                "horse_number": "2", "frame_number": "2",
                "body_weight_status": "published", "current_body_weight": "500",
                "body_weight_change": "+24",
            },
        ]
        self.collected["combo_odds"] = [{
            "snapshot_id": "S1", "snapshot_complete": True,
            "bet_type": "wakuren", "combination": "1-2",
        }]
        self.ticket_plan["tickets"] = [{
            "bet_type": "wakuren", "odds_source": "jra_live", "frame_numbers": ["1", "2"],
        }]

        review = FinalReviewerAgent(settings=self.settings, now=lambda: self.now).run(
            plan=self.plan,
            collected=self.collected,
            ticket_plan=self.ticket_plan,
            quantitative_review={"status": "OK"},
        )

        self.assertEqual("NO_GO", review["decision"])
        self.assertEqual(["2"], review["extreme_selected_horse_numbers"])


class MergeLiveEntriesTest(unittest.TestCase):
    def test_updates_every_history_row_and_detects_matching_lineup(self):
        baseline = [
            {"race_id": "R1", "horse_id": "h1", "horse_number": "1", "run_index": str(index)}
            for index in range(1, 6)
        ]
        live = [
            {
                "race_id": "R1",
                "horse_id": "h1",
                "horse_number": "1",
                "current_odds": "3.2",
                "current_body_weight": "472",
                "body_weight_change": "-2",
                "body_weight_status": "published",
                "target_weather": "雨",
                "target_track_condition": "稍重",
            }
        ]

        rows, lineup = merge_live_entries(baseline, live)

        self.assertEqual(5, len(rows))
        self.assertTrue(lineup["matches"])
        self.assertEqual({"3.2"}, {row["current_odds"] for row in rows})
        self.assertEqual({"472"}, {row["current_body_weight"] for row in rows})


class FinalPredictionWorkflowTest(unittest.TestCase):
    def test_runs_six_roles_and_persists_go_decision(self):
        now = datetime(2026, 8, 8, 15, 18, tzinfo=JST)
        events = []

        class LiveCollector:
            def collect(self, race_config, **kwargs):
                events.append("data_collector")
                return {
                    "snapshot_id": "S1",
                    "snapshot_complete": True,
                    "official_odds_as_of": "2026-08-08T15:17:00+09:00",
                    "conditions": {
                        "weather": "雨",
                        "track_condition": "稍重",
                        "captured_at": "2026-08-08T15:17:30+09:00",
                    },
                    "entries": [{
                        "race_id": "R1", "horse_id": "h1", "horse_number": "1",
                        "current_odds": "3.2", "body_weight_status": "published",
                        "current_body_weight": "472",
                        "body_weight_change": "-2", "target_weather": "雨",
                        "target_track_condition": "稍重",
                    }],
                    "combo_odds": [{
                        "race_id": "R1", "bet_type": "win", "combination": "1",
                        "odds": "3.2", "snapshot_id": "S1", "snapshot_complete": True,
                    }],
                    "quality_report": {
                        "issues_by_severity": {}, "missing_current_odds_entries": 0,
                        "bet_types_missing": [], "official_odds_timestamps_complete": True,
                        "combination_coverage": {"complete": True},
                    },
                }

            def close(self):
                return None

        class Agent:
            def __init__(self, name, result):
                self.name = name
                self.result = result

            def run(self, *args, **kwargs):
                events.append(self.name)
                return dict(self.result)

        race = {
            "race_id": "R1", "race_name": "Test", "race_date": "2026-08-08",
            "post_time": "15:25", "track": "札幌", "race_number": 11,
            "source_url": "https://example.test/race",
        }
        baseline = [
            {"race_id": "R1", "horse_id": "h1", "horse_number": "1", "run_index": str(index)}
            for index in range(1, 6)
        ]
        with TemporaryDirectory() as directory:
            output_dir = Path(directory) / "final"
            workflow = FinalPredictionWorkflow(
                ScrapeConfig(raw_dir=Path(directory) / "raw"),
                output_dir=output_dir,
                now=lambda: now,
                live_collector=LiveCollector(),
            )
            workflow.analyzer = Agent("analyzer", {"feature_rows": [{}]})
            workflow.simulator = Agent("simulator", {"scenario_rows": [{}]})
            workflow.ev_calculator = Agent("ev_calculator", {"ev_rows": [{}]})
            workflow.bet_builder = Agent("bet_builder", {"tickets": [{
                "bet_type": "win", "horse_number": 1, "odds_source": "jra_live",
            }]})
            workflow.quantitative_reviewer = Agent("reviewer", {"status": "OK"})

            payload = workflow.run(race, baseline=baseline)

            self.assertEqual("GO", payload["final_decision"]["decision"])
            self.assertTrue((output_dir / "run_manifest.json").exists())

        self.assertEqual(
            ["data_collector", "analyzer", "simulator", "ev_calculator", "bet_builder", "reviewer"],
            events,
        )


if __name__ == "__main__":
    unittest.main()
