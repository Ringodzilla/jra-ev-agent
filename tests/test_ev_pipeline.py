import unittest

from analysis.ev import build_feature_rows, compute_ev, simulate_race_scenarios
from src.react_workflow import ReviewerAgent, WorkflowSettings
from strategy.betting import generate_tickets


class TestEVPipeline(unittest.TestCase):
    def test_compute_ev_is_race_normalized(self):
        rows = [
            {
                "race_id": "20260329_中山_11",
                "horse_id": "h1",
                "horse_name": "A",
                "horse_number": "1",
                "current_odds": "3.0",
                "current_popularity": "1",
                "current_jockey": "戸崎",
                "assigned_weight": "57",
                "target_track": "中山",
                "target_race_date": "2026-03-29",
                "target_race_number": "11",
                "target_surface": "芝",
                "target_distance": "2000",
                "run_index": "1",
                "date": "2026-03-01",
                "course": "中山",
                "distance": "2000",
                "position": "1",
                "time": "119.9",
                "weight": "57",
                "jockey": "戸崎",
                "last_3f": "34.2",
                "passing_order": "5-5-4-2",
                "odds": "4.8",
                "popularity": "1",
            },
            {
                "race_id": "20260329_中山_11",
                "horse_id": "h1",
                "horse_name": "A",
                "horse_number": "1",
                "current_odds": "3.0",
                "current_popularity": "1",
                "current_jockey": "戸崎",
                "assigned_weight": "57",
                "target_track": "中山",
                "target_race_date": "2026-03-29",
                "target_race_number": "11",
                "target_surface": "芝",
                "target_distance": "2000",
                "run_index": "2",
                "date": "2026-02-01",
                "course": "中山",
                "distance": "2000",
                "position": "2",
                "time": "120.3",
                "weight": "57",
                "jockey": "戸崎",
                "last_3f": "34.6",
                "passing_order": "6-6-5-3",
                "odds": "7.2",
                "popularity": "2",
            },
            {
                "race_id": "20260329_中山_11",
                "horse_id": "h2",
                "horse_name": "B",
                "horse_number": "2",
                "current_odds": "6.0",
                "current_popularity": "4",
                "current_jockey": "ルメール",
                "assigned_weight": "56",
                "target_track": "中山",
                "target_race_date": "2026-03-29",
                "target_race_number": "11",
                "target_surface": "芝",
                "target_distance": "2000",
                "run_index": "1",
                "date": "2026-03-01",
                "course": "東京",
                "distance": "1800",
                "position": "3",
                "time": "109.9",
                "weight": "56",
                "jockey": "ルメール",
                "last_3f": "35.0",
                "passing_order": "9-9-8-6",
                "odds": "5.4",
                "popularity": "4",
            },
            {
                "race_id": "20260329_中山_11",
                "horse_id": "h2",
                "horse_name": "B",
                "horse_number": "2",
                "current_odds": "6.0",
                "current_popularity": "4",
                "current_jockey": "ルメール",
                "assigned_weight": "56",
                "target_track": "中山",
                "target_race_date": "2026-03-29",
                "target_race_number": "11",
                "target_surface": "芝",
                "target_distance": "2000",
                "run_index": "2",
                "date": "2025-12-28",
                "course": "東京",
                "distance": "1800",
                "position": "5",
                "time": "110.8",
                "weight": "56",
                "jockey": "ルメール",
                "last_3f": "35.4",
                "passing_order": "10-10-9-8",
                "odds": "4.1",
                "popularity": "2",
            },
        ]

        feature_rows = build_feature_rows(rows)
        scenario_rows = simulate_race_scenarios(feature_rows)
        scored = compute_ev(scenario_rows)

        self.assertEqual(2, len(scored))
        prob_sum = sum(float(row["win_prob"]) for row in scored)
        self.assertAlmostEqual(1.0, prob_sum, places=4)
        self.assertIn("current_odds", scored[0])
        self.assertIn("predicted_odds", scored[0])
        self.assertIn("fair_odds", scored[0])
        self.assertIn("ev_current", scored[0])
        self.assertIn("ev_predicted", scored[0])
        self.assertTrue(any(row["predicted_odds"] != row["current_odds"] for row in scored))

    def test_generate_tickets_returns_structured_plan(self):
        ev_rows = [
            {
                "race_id": "r1",
                "horse_id": "h1",
                "horse_name": "A",
                "horse_number": "1",
                "win_prob": "0.24",
                "current_odds": "7.0",
                "predicted_odds": "6.5",
                "ev": "1.68",
                "ev_current": "1.68",
                "ev_predicted": "1.56",
                "fair_odds": "4.10",
                "market_prob": "0.14",
                "consistency": "0.71",
                "history_count": "5",
            },
            {
                "race_id": "r1",
                "horse_id": "h2",
                "horse_name": "B",
                "horse_number": "2",
                "win_prob": "0.20",
                "current_odds": "11.0",
                "predicted_odds": "10.2",
                "ev": "1.87",
                "ev_current": "1.87",
                "ev_predicted": "1.73",
                "fair_odds": "5.0",
                "market_prob": "0.08",
                "consistency": "0.66",
                "history_count": "5",
            },
            {
                "race_id": "r1",
                "horse_id": "h3",
                "horse_name": "C",
                "horse_number": "3",
                "win_prob": "0.14",
                "current_odds": "13.0",
                "predicted_odds": "12.5",
                "ev": "1.82",
                "ev_current": "1.82",
                "ev_predicted": "1.75",
                "fair_odds": "7.10",
                "market_prob": "0.06",
                "consistency": "0.63",
                "history_count": "4",
            },
        ]
        for idx in range(4, 10):
            ev_rows.append(
                {
                    "race_id": "r1",
                    "horse_id": f"h{idx}",
                    "horse_name": f"X{idx}",
                    "horse_number": str(idx),
                    "win_prob": "0.05",
                    "current_odds": "18.0",
                    "predicted_odds": "18.5",
                    "ev": "0.9",
                    "ev_current": "0.9",
                    "ev_predicted": "0.925",
                    "fair_odds": "20.0",
                    "market_prob": "0.05",
                    "consistency": "0.45",
                    "history_count": "3",
                }
            )
        plan = generate_tickets(ev_rows, prefer_wide=True)
        self.assertIn("tickets", plan)
        self.assertIn("races", plan)
        self.assertTrue(plan["tickets"])
        self.assertEqual("wide", plan["primary_bet_type"])
        self.assertEqual("wide", plan["tickets"][0]["bet_type"])
        self.assertIn("wide_odds_est", plan["tickets"][0])
        self.assertIn("ev_predicted", plan["tickets"][0])

    def test_reviewer_rejects_meaningful_ev_divergence(self):
        reviewer = ReviewerAgent(WorkflowSettings())
        ev_rows = [
            {
                "race_id": "r1",
                "horse_id": "h1",
                "horse_name": "A",
                "current_odds": "3.0",
                "predicted_odds": "4.2",
                "ev_current": "1.08",
                "ev_predicted": "1.512",
                "win_prob": "0.36",
            }
        ]

        review = reviewer.run(
            {"quality_report": {"issues_by_severity": {}}, "entries": [{"race_id": "r1", "horse_id": "h1"}]},
            scenario_rows=[{"race_id": "r1", "horse_id": "h1"}],
            ev_rows=ev_rows,
            ticket_plan={"tickets": []},
            attempt=0,
        )

        self.assertEqual("NG", review["status"])
        self.assertIn("predicted/current EV divergence", review["reason"])
        self.assertTrue(review["divergent_rows"])

    def test_compute_ev_calibrates_extreme_longshots_toward_market(self):
        feature_rows = []
        odds_ladder = [3.2, 4.1, 6.8, 10.5, 13.2, 18.4, 24.7, 33.0, 55.0, 80.0, 120.0, 260.0]
        score_ladder = [0.86, 0.82, 0.78, 0.74, 0.70, 0.66, 0.62, 0.58, 0.54, 0.60, 0.64, 0.68]
        popularity_ladder = [1, 2, 3, 4, 5, 6, 8, 9, 11, 13, 15, 18]
        for idx, (odds, base_score, popularity) in enumerate(zip(odds_ladder, score_ladder, popularity_ladder), start=1):
            feature_rows.append(
                {
                    "race_id": "r_long",
                    "horse_id": f"h{idx}",
                    "horse_name": f"Horse{idx}",
                    "horse_number": str(idx),
                    "current_odds": str(odds),
                    "current_popularity": str(popularity),
                    "ability_score": base_score,
                    "course_score": 0.58 if idx >= 10 else 0.52,
                    "pace_score": 0.57 if idx >= 10 else 0.50,
                    "weight_score": 0.0,
                    "jockey_score": 0.48 if idx >= 10 else 0.55,
                    "market_support": round(1.0 / odds, 4),
                    "history_count": 4,
                    "odds_snapshot_count": "2",
                    "odds_span_minutes": "10",
                }
            )

        scored = compute_ev(feature_rows)
        by_horse = {row["horse_id"]: row for row in scored}
        favorite = by_horse["h1"]
        longshot = by_horse["h12"]

        self.assertEqual("longshot", longshot["probability_band"])
        self.assertGreater(float(longshot["market_shrink_used"]), float(favorite["market_shrink_used"]))
        self.assertLess(float(longshot["win_prob"]) / float(longshot["market_prob"]), 1.2)
        self.assertLess(float(longshot["predicted_odds"]), float(longshot["current_odds"]) * 1.06)
        self.assertLess(float(longshot["win_prob"]), float(favorite["win_prob"]))

    def test_overseas_country_bias_adjusts_market_support(self):
        rows = [
            {
                "race_id": "20260426_シャティン_07",
                "horse_id": "h_jpn",
                "horse_name": "JPN馬",
                "horse_country": "JPN",
                "horse_number": "1",
                "current_odds": "5.0",
                "current_popularity": "1",
                "current_jockey": "川田",
                "assigned_weight": "57",
                "target_track": "シャティン",
                "target_race_date": "2026-04-26",
                "target_race_number": "07",
                "target_surface": "芝",
                "target_distance": "1600",
                "run_index": "1",
                "date": "2026-04-06",
                "course": "HK",
                "distance": "1600",
                "position": "3",
                "time": "93.8",
                "weight": "57",
                "jockey": "川田",
                "last_3f": "35.8",
                "passing_order": "6-4",
                "odds": "5.8",
                "popularity": "2",
            },
            {
                "race_id": "20260426_シャティン_07",
                "horse_id": "h_hk",
                "horse_name": "HK馬",
                "horse_country": "HK",
                "horse_number": "2",
                "current_odds": "5.0",
                "current_popularity": "2",
                "current_jockey": "J.モレイラ",
                "assigned_weight": "57",
                "target_track": "シャティン",
                "target_race_date": "2026-04-26",
                "target_race_number": "07",
                "target_surface": "芝",
                "target_distance": "1600",
                "run_index": "1",
                "date": "2026-04-06",
                "course": "HK",
                "distance": "1600",
                "position": "3",
                "time": "93.8",
                "weight": "57",
                "jockey": "J.モレイラ",
                "last_3f": "35.8",
                "passing_order": "6-4",
                "odds": "5.8",
                "popularity": "2",
            },
        ]

        feature_rows = build_feature_rows(rows)
        by_horse = {row["horse_id"]: row for row in feature_rows}

        jpn = by_horse["h_jpn"]
        hk = by_horse["h_hk"]

        self.assertEqual(1, int(jpn["is_overseas_race"]))
        self.assertLess(float(jpn["country_value_score"]), 0.0)
        self.assertGreater(float(hk["country_value_score"]), 0.0)
        self.assertLess(float(jpn["market_support"]), float(jpn["market_support_base"]))
        self.assertGreater(float(hk["market_support"]), float(hk["market_support_base"]))


if __name__ == "__main__":
    unittest.main()
