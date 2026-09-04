import hashlib
import json
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zoneinfo import ZoneInfo

from jra_scraper.config import ScrapeConfig
from scripts.run_final_prediction import load_combo_odds_history, load_fixed_analysis
from src.final_workflow import FinalPredictionWorkflow, build_fixed_analysis_ev_rows


JST = ZoneInfo("Asia/Tokyo")


def fixed_analysis():
    return {
        "analyzer": {
            "race_id": "R1",
            "role": "analyzer",
            "scores": [
                {
                    "horse_number": 1,
                    "horse": "Alpha",
                    "ability": 50,
                    "course": 0,
                    "pace": 0,
                    "weight": 0,
                    "jockey": 0,
                    "S": 50,
                },
                {
                    "horse_number": 2,
                    "horse": "Beta",
                    "ability": 40,
                    "course": 0,
                    "pace": 0,
                    "weight": 0,
                    "jockey": 0,
                    "S": 40,
                },
            ],
        },
        "simulator": {
            "race_id": "R1",
            "role": "simulator",
            "method": {"scenario_weights": {"high": 0.3, "mid": 0.5, "slow": 0.2}},
            "probabilities": [
                {
                    "horse_number": 1,
                    "horse": "Alpha",
                    "high": 0.45,
                    "mid": 0.40,
                    "slow": 0.35,
                    "final": 0.40,
                },
                {
                    "horse_number": 2,
                    "horse": "Beta",
                    "high": 0.55,
                    "mid": 0.60,
                    "slow": 0.65,
                    "final": 0.60,
                },
            ],
        },
        "provenance": {
            "analyzer": {"path": "/fixed/02_analyzer.json", "sha256": "a" * 64},
            "simulator": {"path": "/fixed/03_simulator.json", "sha256": "b" * 64},
        },
    }


def live_entries():
    shared = {
        "race_id": "R1",
        "current_jockey": "Jockey",
        "assigned_weight": "57",
        "target_track": "札幌",
        "target_surface": "芝",
        "target_distance": "1200",
        "target_track_condition": "良",
        "target_weather": "晴",
        "body_weight_status": "published",
        "body_weight_change": "0",
    }
    return [
        {
            **shared,
            "horse_id": "h1",
            "horse_name": "Alpha",
            "horse_number": "1",
            "frame_number": "1",
            "current_body_weight": "480",
            "current_odds": "2.0",
        },
        {
            **shared,
            "horse_id": "h2",
            "horse_name": "Beta",
            "horse_number": "2",
            "frame_number": "2",
            "current_body_weight": "470",
            "current_odds": "4.0",
        },
    ]


def baseline_rows():
    return [
        {
            **entry,
            "run_index": str(index),
        }
        for entry in live_entries()
        for index in range(1, 6)
    ]


def combo_odds():
    return [
        {
            "race_id": "R1",
            "bet_type": "win",
            "combination": "1",
            "odds": "2.0",
            "snapshot_id": "S1",
            "snapshot_complete": True,
        },
        {
            "race_id": "R1",
            "bet_type": "win",
            "combination": "2",
            "odds": "4.0",
            "snapshot_id": "S1",
            "snapshot_complete": True,
        },
    ]


class FixedAnalysisRepriceTest(unittest.TestCase):
    def test_reprices_without_changing_fixed_probabilities(self):
        rows = build_fixed_analysis_ev_rows(
            race_config={"race_id": "R1"},
            fixed_analysis=fixed_analysis(),
            live_entries=live_entries(),
            combo_odds=combo_odds(),
            baseline_rows=baseline_rows(),
        )

        by_number = {row["horse_number"]: row for row in rows}
        self.assertEqual("0.4", by_number["1"]["win_prob"])
        self.assertEqual("0.8", by_number["1"]["ev"])
        self.assertEqual("0.6", by_number["2"]["win_prob"])
        self.assertEqual("2.4", by_number["2"]["ev"])
        self.assertTrue(all(row["fixed_probability"] for row in rows))

    def test_rejects_condition_change_instead_of_reusing_stale_analysis(self):
        entries = live_entries()
        entries[0]["target_track_condition"] = "稍重"
        with self.assertRaisesRegex(ValueError, "target_track_condition changed"):
            build_fixed_analysis_ev_rows(
                race_config={"race_id": "R1"},
                fixed_analysis=fixed_analysis(),
                live_entries=entries,
                combo_odds=combo_odds(),
                baseline_rows=baseline_rows(),
            )

    def test_load_fixed_analysis_requires_matching_pinned_hashes(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            analyzer = json.dumps(fixed_analysis()["analyzer"])
            simulator = json.dumps(fixed_analysis()["simulator"])
            (path / "02_analyzer.json").write_text(analyzer, encoding="utf-8")
            (path / "03_simulator.json").write_text(simulator, encoding="utf-8")
            analyzer_hash = hashlib.sha256(analyzer.encode()).hexdigest()
            simulator_hash = hashlib.sha256(simulator.encode()).hexdigest()

            loaded = load_fixed_analysis(
                path,
                expected_analyzer_sha256=analyzer_hash,
                expected_simulator_sha256=simulator_hash,
            )
            self.assertEqual(analyzer_hash, loaded["provenance"]["analyzer"]["sha256"])
            with self.assertRaisesRegex(ValueError, "sha256"):
                load_fixed_analysis(
                    path,
                    expected_analyzer_sha256="0" * 64,
                    expected_simulator_sha256=simulator_hash,
                )


class FixedAnalysisWorkflowTest(unittest.TestCase):
    def test_skips_analyzer_and_simulator_and_reprices_only(self):
        now = datetime(2026, 8, 8, 15, 18, tzinfo=JST)

        class LiveCollector:
            def collect(self, race_config, **kwargs):
                return {
                    "snapshot_id": "S1",
                    "snapshot_complete": True,
                    "official_odds_as_of": "2026-08-08T15:17:00+09:00",
                    "conditions": {
                        "weather": "晴",
                        "track_condition": "良",
                        "captured_at": "2026-08-08T15:17:30+09:00",
                    },
                    "entries": live_entries(),
                    "combo_odds": combo_odds(),
                    "quality_report": {
                        "issues_by_severity": {},
                        "missing_current_odds_entries": 0,
                        "bet_types_missing": [],
                        "official_odds_timestamps_complete": True,
                        "combination_coverage": {"complete": True},
                    },
                }

            def close(self):
                return None

        class FailAgent:
            def run(self, *args, **kwargs):
                raise AssertionError("fixed-analysis mode must not call this stage")

        class Agent:
            def __init__(self, result):
                self.result = result

            def run(self, *args, **kwargs):
                return dict(self.result)

        race = {
            "race_id": "R1",
            "race_name": "Test",
            "race_date": "2026-08-08",
            "post_time": "15:25",
            "track": "札幌",
            "race_number": 11,
            "source_url": "https://example.test/race",
        }
        with TemporaryDirectory() as directory:
            output_dir = Path(directory) / "final"
            workflow = FinalPredictionWorkflow(
                ScrapeConfig(raw_dir=Path(directory) / "raw"),
                output_dir=output_dir,
                now=lambda: now,
                live_collector=LiveCollector(),
            )
            workflow.analyzer = FailAgent()
            workflow.simulator = FailAgent()
            workflow.ev_calculator = FailAgent()
            workflow.bet_builder = Agent(
                {
                    "tickets": [
                        {
                            "bet_type": "win",
                            "horse_number": 1,
                            "odds_source": "jra_live",
                        }
                    ]
                }
            )
            workflow.quantitative_reviewer = Agent({"status": "OK"})

            payload = workflow.run(
                race,
                baseline=baseline_rows(),
                fixed_analysis=fixed_analysis(),
            )

            self.assertEqual("GO", payload["final_decision"]["decision"])
            self.assertEqual("fixed_reprice", payload["final_decision"]["analysis_mode"])
            self.assertTrue(payload["analyzer"]["reused"])
            self.assertTrue(payload["simulator"]["reused"])
            ev_by_number = {
                row["horse_number"]: row for row in payload["ev_calculator"]["ev_rows"]
            }
            self.assertEqual("0.8", ev_by_number["1"]["ev"])
            manifest = json.loads((output_dir / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(
                "a" * 64,
                manifest["reuse_provenance"]["analyzer"]["sha256"],
            )


def test_load_combo_odds_history_filters_to_active_race() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "odds.csv"
        path.write_text(
            "race_id,bet_type,combination,odds,captured_at\n"
            "R1,wide,1-2,10.0,2026-08-30T05:00:00+00:00\n"
            "R2,wide,1-2,20.0,2026-08-30T05:00:00+00:00\n",
            encoding="utf-8",
        )
        rows = load_combo_odds_history(path, race_id="R1")

    assert len(rows) == 1
    assert rows[0]["race_id"] == "R1"
    assert load_combo_odds_history(path, race_id="R1") == []


if __name__ == "__main__":
    unittest.main()
