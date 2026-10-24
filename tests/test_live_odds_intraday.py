import csv
import tempfile
import unittest
from pathlib import Path

from analysis.ev import build_feature_rows, compute_ev, simulate_race_scenarios
from jra_scraper.pipeline import append_live_odds_snapshots
from src.feature_engineering import summarize_live_odds_rows
from src.react_workflow import ReviewerAgent, WorkflowSettings


class TestLiveOddsIntraday(unittest.TestCase):
    def test_snapshot_accumulation(self):
        entry_rows = [
            {
                "race_id": "r1",
                "horse_id": "h1",
                "horse_name": "A",
                "horse_number": "1",
                "current_odds": "4.8",
                "current_popularity": "3",
            },
            {
                "race_id": "r1",
                "horse_id": "h2",
                "horse_name": "B",
                "horse_number": "2",
                "current_odds": "8.2",
                "current_popularity": "7",
            },
        ]

        with tempfile.TemporaryDirectory() as td:
            snapshot_path = Path(td) / "live_odds_snapshots.csv"
            first = append_live_odds_snapshots(
                snapshot_path,
                entry_rows,
                captured_at="2026-03-29T01:00:00+00:00",
            )
            second = append_live_odds_snapshots(
                snapshot_path,
                entry_rows,
                captured_at="2026-03-29T01:10:00+00:00",
            )
            duplicate = append_live_odds_snapshots(
                snapshot_path,
                entry_rows,
                captured_at="2026-03-29T01:10:00+00:00",
            )

            with snapshot_path.open("r", encoding="utf-8", newline="") as file_obj:
                saved = list(csv.DictReader(file_obj))

        self.assertEqual(2, len(first))
        self.assertEqual(2, len(second))
        self.assertEqual([], duplicate)
        self.assertEqual(4, len(saved))
        self.assertEqual("2026-03-29T01:00:00+00:00", saved[0]["captured_at"])
        self.assertEqual("2026-03-29T01:10:00+00:00", saved[-1]["captured_at"])

    def test_intraday_odds_feature_generation(self):
        summaries = summarize_live_odds_rows(
            [
                {
                    "race_id": "r1",
                    "horse_number": "1",
                    "current_odds": "6.0",
                    "current_popularity": "5",
                    "captured_at": "2026-03-29T01:00:00+00:00",
                },
                {
                    "race_id": "r1",
                    "horse_number": "1",
                    "current_odds": "5.4",
                    "current_popularity": "4",
                    "captured_at": "2026-03-29T01:10:00+00:00",
                },
                {
                    "race_id": "r1",
                    "horse_number": "1",
                    "current_odds": "5.0",
                    "current_popularity": "3",
                    "captured_at": "2026-03-29T01:20:00+00:00",
                },
            ]
        )

        summary = summaries[("r1", "1")]
        self.assertEqual(6.0, summary["odds_first"])
        self.assertEqual(5.0, summary["odds_latest"])
        self.assertEqual(5.0, summary["odds_min"])
        self.assertEqual(6.0, summary["odds_max"])
        self.assertAlmostEqual(0.2, float(summary["odds_range_ratio"]), places=6)
        self.assertAlmostEqual(-0.5, float(summary["odds_slope_full"]), places=6)
        self.assertAlmostEqual(-0.5, float(summary["odds_slope_recent"]), places=6)
        self.assertEqual(3.0, summary["popularity_latest"])
        self.assertEqual(-2.0, summary["popularity_change"])

    def test_live_first_prediction_selection(self):
        rows = [
            {
                "race_id": "r1",
                "horse_id": "h1",
                "horse_name": "A",
                "horse_number": "1",
                "current_odds": "5.0",
                "current_popularity": "4",
                "current_jockey": "戸崎",
                "assigned_weight": "57",
                "target_track": "中山",
                "target_race_date": "2026-03-29",
                "target_race_number": "11",
                "target_surface": "芝",
                "target_distance": "2000",
                "run_index": "1",
                "date": "2026-02-28",
                "course": "中山",
                "distance": "2000",
                "position": "2",
                "time": "120.1",
                "weight": "57",
                "jockey": "戸崎",
                "last_3f": "34.5",
                "passing_order": "7-7-6-4",
                "odds": "6.5",
                "popularity": "5",
            },
            {
                "race_id": "r1",
                "horse_id": "h2",
                "horse_name": "B",
                "horse_number": "2",
                "current_odds": "7.0",
                "current_popularity": "8",
                "current_jockey": "ルメール",
                "assigned_weight": "56",
                "target_track": "中山",
                "target_race_date": "2026-03-29",
                "target_race_number": "11",
                "target_surface": "芝",
                "target_distance": "2000",
                "run_index": "1",
                "date": "2026-02-28",
                "course": "東京",
                "distance": "1800",
                "position": "4",
                "time": "110.2",
                "weight": "56",
                "jockey": "ルメール",
                "last_3f": "35.2",
                "passing_order": "10-10-8-7",
                "odds": "5.8",
                "popularity": "4",
            },
        ]
        odds_snapshots = [
            {
                "race_id": "r1",
                "horse_number": "1",
                "current_odds": "7.0",
                "current_popularity": "6",
                "captured_at": "2026-03-29T01:00:00+00:00",
            },
            {
                "race_id": "r1",
                "horse_number": "1",
                "current_odds": "6.0",
                "current_popularity": "5",
                "captured_at": "2026-03-29T01:10:00+00:00",
            },
            {
                "race_id": "r1",
                "horse_number": "1",
                "current_odds": "5.0",
                "current_popularity": "4",
                "captured_at": "2026-03-29T01:20:00+00:00",
            },
            {
                "race_id": "r1",
                "horse_number": "2",
                "current_odds": "7.0",
                "current_popularity": "8",
                "captured_at": "2026-03-29T01:20:00+00:00",
            },
        ]

        feature_rows = build_feature_rows(rows, odds_snapshots=odds_snapshots)
        scenario_rows = simulate_race_scenarios(feature_rows)
        scored = compute_ev(scenario_rows)
        by_horse = {row["horse_id"]: row for row in scored}

        self.assertEqual("live", by_horse["h1"]["predicted_odds_source"])
        self.assertEqual("structural", by_horse["h2"]["predicted_odds_source"])
        self.assertEqual(by_horse["h1"]["predicted_odds"], by_horse["h1"]["predicted_odds_live"])
        self.assertEqual(by_horse["h2"]["predicted_odds"], by_horse["h2"]["predicted_odds_structural"])

    def test_popularity_band_reviewer_threshold(self):
        reviewer = ReviewerAgent(WorkflowSettings())

        favorite_review = reviewer.run(
            {"quality_report": {"issues_by_severity": {}}, "entries": [{"race_id": "r1", "horse_id": "h1"}]},
            scenario_rows=[{"race_id": "r1", "horse_id": "h1"}],
            ev_rows=[
                {
                    "race_id": "r1",
                    "horse_id": "h1",
                    "horse_name": "Favorite",
                    "current_popularity": "2",
                    "popularity_latest": "2",
                    "current_odds": "3.0",
                    "predicted_odds": "3.3",
                    "ev_current": "1.00",
                    "ev_predicted": "1.14",
                    "win_prob": "1.0",
                }
            ],
            ticket_plan={"tickets": []},
            attempt=0,
        )
        longshot_review = reviewer.run(
            {"quality_report": {"issues_by_severity": {}}, "entries": [{"race_id": "r2", "horse_id": "h9"}]},
            scenario_rows=[{"race_id": "r2", "horse_id": "h9"}],
            ev_rows=[
                {
                    "race_id": "r2",
                    "horse_id": "h9",
                    "horse_name": "Longshot",
                    "current_popularity": "12",
                    "popularity_latest": "12",
                    "current_odds": "30.0",
                    "predicted_odds": "33.0",
                    "ev_current": "1.00",
                    "ev_predicted": "1.14",
                    "win_prob": "1.0",
                }
            ],
            ticket_plan={"tickets": []},
            attempt=0,
        )

        self.assertEqual("OK", favorite_review["status"])
        self.assertNotIn("PREDICTED_CURRENT_EV_DIVERGENCE", favorite_review["reason_codes"])
        self.assertEqual("OK", longshot_review["status"])
        self.assertEqual("favorite", favorite_review["divergent_rows"][0]["popularity_band"])
        self.assertEqual([], favorite_review["selected_divergent_rows"])


if __name__ == "__main__":
    unittest.main()
