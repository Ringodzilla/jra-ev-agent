import unittest

from analysis.ev import EVWeights, build_feature_rows, compute_ev, simulate_race_scenarios
from src.feature_engineering import build_feature_row
from src.react_workflow import ReviewerAgent, WorkflowSettings, apply_ticket_repair_actions
from strategy.betting import (
    _calibrate_ticket_probabilities,
    _frame_quality_adjustment,
    _limit_eligible_selection_pool,
    _wide_pace_adjustment,
    generate_tickets,
)


class TestEVPipeline(unittest.TestCase):
    def test_compute_ev_uses_reproducible_monte_carlo_mean_probability(self):
        rows = [
            {
                "race_id": "r_mc",
                "horse_id": "h1",
                "horse_name": "A",
                "horse_number": "1",
                "current_odds": "3.0",
                "current_popularity": "1",
                "ability_score": 0.80,
                "course_score": 0.70,
                "pace_score": 0.60,
                "weight_score": 0.50,
                "jockey_score": 0.60,
                "market_support": 0.33,
                "consistency": 0.80,
                "pace_mix_high": 0.3,
            },
            {
                "race_id": "r_mc",
                "horse_id": "h2",
                "horse_name": "B",
                "horse_number": "2",
                "current_odds": "5.0",
                "current_popularity": "2",
                "ability_score": 0.65,
                "course_score": 0.55,
                "pace_score": 0.70,
                "weight_score": 0.50,
                "jockey_score": 0.50,
                "market_support": 0.20,
                "consistency": 0.35,
                "pace_mix_high": 0.3,
            },
        ]
        weights = EVWeights(monte_carlo_iterations=500, monte_carlo_seed=42, luck_score_std=0.20)

        first = compute_ev(rows, weights=weights)
        second = compute_ev(rows, weights=weights)

        self.assertEqual(first, second)
        self.assertAlmostEqual(1.0, sum(float(row["win_prob"]) for row in first), places=5)
        self.assertTrue(all(row["win_prob"] == row["win_prob_mean"] for row in first))
        self.assertTrue(all(float(row["win_prob_std"]) > 0 for row in first))
        self.assertTrue(all(row["monte_carlo_iterations"] == "500" for row in first))
        for row in first:
            self.assertAlmostEqual(
                float(row["ev_current"]),
                float(row["win_prob_mean"]) * float(row["current_odds"]),
                places=5,
            )

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

    def test_generate_tickets_classifies_every_ticket_horse_exactly_once(self):
        ev_rows = []
        for number, win_prob, odds, place_prob in [
            (1, 0.28, 4.0, 0.55),
            (2, 0.22, 5.0, 0.45),
            (3, 0.18, 7.0, 0.35),
            (4, 0.12, 10.0, 0.25),
            (5, 0.08, 18.0, 0.15),
        ]:
            ev_rows.append(
                {
                    "race_id": "r_classification",
                    "horse_id": f"h{number}",
                    "horse_name": f"Horse {number}",
                    "horse_number": str(number),
                    "win_prob": str(win_prob),
                    "place_prob": str(place_prob),
                    "market_place_prob": "0.10",
                    "current_odds": str(odds),
                    "predicted_odds": str(odds),
                    "market_prob": str(1 / odds),
                    "ev": str(win_prob * odds),
                    "ev_current": str(win_prob * odds),
                    "ev_predicted": str(win_prob * odds),
                    "model_score": str(1 - number * 0.05),
                    "consistency": "0.7",
                    "history_count": "5",
                }
            )

        plan = generate_tickets(ev_rows, max_tickets_per_race=5, min_portfolio_ev=0.0)
        classified = plan["core"] + plan["partner"] + plan["long"]
        classified_numbers = [str(item["horse_number"]) for item in classified]
        ticket_numbers = {
            str(number)
            for ticket in plan["tickets"]
            for number in (ticket.get("horse_numbers") or [ticket.get("horse_number")])
        }

        self.assertTrue(ticket_numbers)
        self.assertTrue(ticket_numbers.issubset(set(classified_numbers)))
        self.assertEqual(len(classified_numbers), len(set(classified_numbers)))

    def test_generate_tickets_adds_multi_bet_candidates_across_bet_types(self):
        ev_rows = []
        for horse_name, horse_number, frame_number, win_prob, odds, market_prob in [
            ("A", "1", "1", 0.32, 6.0, 0.01),
            ("B", "2", "1", 0.24, 8.0, 0.04),
            ("C", "3", "2", 0.18, 12.0, 0.08),
            ("D", "4", "2", 0.11, 18.0, 0.10),
            ("E", "5", "3", 0.08, 30.0, 0.14),
            ("F", "6", "3", 0.07, 40.0, 0.16),
        ]:
            ev_rows.append(
                {
                    "race_id": "r_exotic",
                    "horse_id": f"h{horse_number}",
                    "horse_name": horse_name,
                    "frame_number": frame_number,
                    "horse_number": horse_number,
                    "win_prob": str(win_prob),
                    "place_prob": str(min(0.75, win_prob * 2.1)),
                    "current_odds": str(odds),
                    "predicted_odds": str(odds),
                    "ev": str(win_prob * odds),
                    "ev_current": str(win_prob * odds),
                    "ev_predicted": str(win_prob * odds),
                    "market_prob": str(market_prob),
                    "consistency": "0.70",
                    "history_count": "5",
                }
            )

        plan = generate_tickets(
            ev_rows,
            prefer_wide=True,
            max_tickets_per_race=8,
            max_exotic_tickets_per_race=6,
        )
        bet_types = {ticket["bet_type"] for ticket in plan["tickets"]}
        candidate_types = set(plan["candidate_counts"].keys())
        available_types = bet_types | candidate_types

        self.assertIn("place", available_types)
        self.assertIn("wide", available_types)
        self.assertIn("wakuren", available_types)
        self.assertIn("umaren", available_types)
        self.assertIn("umatan", available_types)
        self.assertIn("sanrenpuku", available_types)
        self.assertIn("sanrentan", available_types)
        self.assertEqual(
            ["win", "place", "wide", "wakuren", "umaren", "umatan", "sanrenpuku", "sanrentan"],
            plan["bet_types_considered"],
        )
        self.assertTrue(plan["fukusho"])
        self.assertTrue(plan["wakuren"])
        self.assertTrue(plan["umaren"])
        self.assertTrue(plan["umatan"])
        self.assertTrue(plan["sanrenpuku"])
        self.assertTrue(plan["sanrentan"])

        candidates = list(plan["tickets"]) + list(plan["races"][0]["candidates"])
        place = next(ticket for ticket in candidates if ticket["bet_type"] == "place")
        wakuren = next(ticket for ticket in candidates if ticket["bet_type"] == "wakuren")
        umaren = next(ticket for ticket in candidates if ticket["bet_type"] == "umaren")
        umatan = next(ticket for ticket in candidates if ticket["bet_type"] == "umatan")
        sanrenpuku = next(ticket for ticket in candidates if ticket["bet_type"] == "sanrenpuku")
        sanrentan = next(ticket for ticket in candidates if ticket["bet_type"] == "sanrentan")

        self.assertIn("place_odds_est", place)
        self.assertIn("wakuren_odds_est", wakuren)
        self.assertIn("umaren_odds_est", umaren)
        self.assertIn("umatan_odds_est", umatan)
        self.assertIn("trio_odds_est", sanrenpuku)
        self.assertIn("trifecta_odds_est", sanrentan)
        self.assertGreater(float(place["hit_prob"]), 0.0)
        self.assertGreater(float(wakuren["ev_current"]), 1.0)
        self.assertGreater(float(umaren["hit_prob"]), 0.0)
        self.assertEqual(2, len(umatan["horse_numbers"]))
        self.assertGreater(float(sanrenpuku["ev_current"]), 1.0)
        self.assertEqual(3, len(sanrentan["horse_numbers"]))

    def test_generate_tickets_translates_high_win_ev_outsider_to_coverage_surface(self):
        ev_rows = [
            {
                "race_id": "r_translate",
                "horse_id": "h1",
                "horse_name": "Favorite",
                "frame_number": "1",
                "horse_number": "1",
                "win_prob": "0.22",
                "current_odds": "3.2",
                "predicted_odds": "3.3",
                "ev": "0.704",
                "ev_current": "0.704",
                "ev_predicted": "0.726",
                "market_prob": "0.25",
                "consistency": "0.75",
                "history_count": "4",
            },
            {
                "race_id": "r_translate",
                "horse_id": "h2",
                "horse_name": "Anchor",
                "frame_number": "2",
                "horse_number": "2",
                "win_prob": "0.16",
                "current_odds": "5.0",
                "predicted_odds": "5.2",
                "ev": "0.80",
                "ev_current": "0.80",
                "ev_predicted": "0.832",
                "market_prob": "0.16",
                "consistency": "0.70",
                "history_count": "4",
            },
            {
                "race_id": "r_translate",
                "horse_id": "h3",
                "horse_name": "ValueOutsider",
                "frame_number": "3",
                "horse_number": "3",
                "win_prob": "0.055",
                "current_odds": "19.0",
                "predicted_odds": "20.0",
                "ev": "1.045",
                "ev_current": "1.045",
                "ev_predicted": "1.10",
                "market_prob": "0.045",
                "consistency": "0.55",
                "history_count": "3",
            },
        ]
        for idx in range(4, 10):
            ev_rows.append(
                {
                    "race_id": "r_translate",
                    "horse_id": f"h{idx}",
                    "horse_name": f"Other{idx}",
                    "frame_number": str(min(idx, 8)),
                    "horse_number": str(idx),
                    "win_prob": "0.04",
                    "current_odds": "20.0",
                    "predicted_odds": "20.0",
                    "ev": "0.80",
                    "ev_current": "0.80",
                    "ev_predicted": "0.80",
                    "market_prob": "0.04",
                    "consistency": "0.40",
                    "history_count": "3",
                }
            )
        odds_rows = [
            {"race_id": "r_translate", "bet_type": "place", "combination": "3", "odds_min": "4.3", "odds_max": "5.2", "captured_at": "15:00"},
            {"race_id": "r_translate", "bet_type": "wide", "combination": "2-3", "odds_min": "9.6", "odds_max": "11.2", "captured_at": "15:00"},
        ]

        plan = generate_tickets(
            ev_rows,
            odds_rows=odds_rows,
            min_coverage_ev=0.74,
            max_tickets_per_race=5,
        )
        coverage_reasons = {
            str(ticket.get("coverage_reason", ""))
            for ticket in plan["races"][0]["candidates"]
            if str(ticket.get("horse_number", "")) in {"3", "3-2"}
            or "ValueOutsider" in str(ticket.get("horse_name", ""))
        }

        self.assertIn("win_ev_longshot_place_translation", coverage_reasons)
        self.assertIn("win_ev_longshot_wide_translation", coverage_reasons)

    def test_high_pace_penalizes_front_pair_wide(self):
        adjustment = _wide_pace_adjustment(
            {"pace_mix_high": "0.55", "front_rate": "0.76", "closing_strength": "0.30"},
            {"pace_mix_high": "0.55", "front_rate": "0.70", "closing_strength": "0.34"},
        )

        self.assertLess(adjustment, 1.0)

    def test_track_condition_match_improves_course_score(self):
        base = {
            "race_id": "r_condition",
            "horse_name": "A",
            "horse_number": "1",
            "current_odds": "5.0",
            "target_track": "東京",
            "target_surface": "芝",
            "target_distance": "1800",
            "target_track_condition": "稍重",
            "run_index": "1",
            "date": "2026-06-01",
            "course": "東京",
            "distance": "芝1800",
            "position": "3",
            "time": "106.0",
            "weight": "55",
            "last_3f": "34.5",
            "passing_order": "3-3-3",
            "popularity": "4",
        }
        matching = {**base, "horse_id": "matching", "track_condition": "稍重"}
        mismatching = {
            **base,
            "horse_id": "mismatching",
            "horse_name": "B",
            "horse_number": "2",
            "track_condition": "良",
        }

        features = {row["horse_id"]: row for row in build_feature_rows([matching, mismatching])}

        self.assertGreater(features["matching"]["track_condition_score"], features["mismatching"]["track_condition_score"])
        self.assertGreater(features["matching"]["course_score"], features["mismatching"]["course_score"])
        self.assertEqual(0.7, features["mismatching"]["track_condition_score"])

    def test_missing_track_condition_history_is_neutral(self):
        row = {
            "race_id": "r_condition_missing",
            "horse_id": "h1",
            "horse_name": "A",
            "horse_number": "1",
            "current_odds": "5.0",
            "target_track": "東京",
            "target_surface": "芝",
            "target_distance": "1800",
            "target_track_condition": "稍重",
            "run_index": "1",
            "date": "2026-06-01",
            "course": "東京",
            "distance": "芝1800",
            "position": "3",
            "time": "106.0",
            "weight": "55",
            "last_3f": "34.5",
            "passing_order": "3-3-3",
            "popularity": "4",
            "track_condition": "",
        }

        feature = build_feature_rows([row])[0]

        self.assertEqual(0.5, feature["track_condition_score"])
        self.assertEqual(0.0, feature["track_condition_confidence"])

    def test_unseen_bad_track_uses_low_proxy_confidence(self):
        base = {
            "race_id": "r_condition_confidence",
            "horse_name": "A",
            "horse_number": "1",
            "current_odds": "5.0",
            "target_track": "新潟",
            "target_surface": "芝",
            "target_distance": "1600",
            "target_track_condition": "不良",
            "date": "2026-06-01",
            "course": "東京",
            "distance": "芝1600",
            "position": "3",
            "time": "96.0",
            "weight": "56",
            "last_3f": "34.5",
            "passing_order": "3-3",
            "popularity": "4",
        }
        exact = build_feature_rows([{**base, "horse_id": "exact", "track_condition": "不良"}])[0]
        proxy = build_feature_rows([{**base, "horse_id": "proxy", "track_condition": "重"}])[0]

        self.assertGreater(exact["track_condition_confidence"], proxy["track_condition_confidence"])
        self.assertEqual(0.0, proxy["track_condition_evidence"])

    def test_ticket_type_calibration_shrinks_exotics_more_than_win(self):
        win, trifecta = _calibrate_ticket_probabilities(
            [
                {"bet_type": "win", "horse_number": "1", "hit_prob": "0.20", "predicted_odds": "10"},
                {
                    "bet_type": "sanrentan",
                    "horse_number": "1>2>3",
                    "hit_prob": "0.20",
                    "predicted_odds": "10",
                },
            ]
        )

        self.assertGreater(float(win["hit_prob"]), float(trifecta["hit_prob"]))
        self.assertEqual("0.82", trifecta["bet_type_market_shrink"])

    def test_unprofiled_track_bias_is_neutral(self):
        row = {
            "race_id": "r_bias_neutral",
            "horse_id": "h1",
            "horse_name": "A",
            "frame_number": "4",
            "horse_number": "4",
            "current_odds": "5.0",
            "target_track": "東京",
            "target_surface": "ダート",
            "target_distance": "1600",
            "target_track_condition": "良",
            "run_index": "1",
            "date": "2026-06-01",
            "course": "東京",
            "distance": "ダ1600",
            "position": "3",
            "time": "98.0",
            "weight": "55",
            "last_3f": "37.0",
            "passing_order": "3-3",
            "popularity": "4",
            "track_condition": "良",
        }

        feature = build_feature_rows([row])[0]

        self.assertEqual(0.0, feature["track_bias_score"])
        self.assertEqual("neutral", feature["track_bias_style"])
        self.assertEqual(0.0, feature["track_bias_strength"])

    def test_local_dirt_1700_bias_rewards_forward_position(self):
        base = {
            "race_id": "r_kokura_bias",
            "current_odds": "5.0",
            "target_track": "小倉",
            "target_surface": "ダート",
            "target_distance": "1700",
            "target_track_condition": "良",
            "run_index": "1",
            "date": "2026-06-01",
            "course": "小倉",
            "distance": "ダ1700",
            "position": "4",
            "time": "106.0",
            "weight": "55",
            "last_3f": "38.8",
            "popularity": "4",
            "track_condition": "良",
        }
        leader = {
            **base,
            "horse_id": "leader",
            "horse_name": "Leader",
            "frame_number": "6",
            "horse_number": "6",
            "passing_order": "1-1-1-1",
        }
        closer = {
            **base,
            "horse_id": "closer",
            "horse_name": "Closer",
            "frame_number": "8",
            "horse_number": "8",
            "passing_order": "14-14-14-14",
        }

        features = {row["horse_id"]: row for row in build_feature_rows([leader, closer])}

        self.assertEqual("front", features["leader"]["track_bias_style"])
        self.assertEqual("closer", features["closer"]["track_bias_style"])
        self.assertGreater(features["leader"]["track_bias_score"], features["closer"]["track_bias_score"])
        self.assertGreater(features["leader"]["pace_score"], features["closer"]["pace_score"])

    def test_simulator_exposes_relative_front_structure(self):
        rows = [
            {"race_id": "r_front", "horse_id": "leader", "front_rate": 0.9, "closing_strength": 0.2, "ability_score": 0.5, "course_score": 0.5, "consistency": 0.5},
            {"race_id": "r_front", "horse_id": "stalker", "front_rate": 0.7, "closing_strength": 0.3, "ability_score": 0.5, "course_score": 0.5, "consistency": 0.5},
            {"race_id": "r_front", "horse_id": "closer", "front_rate": 0.2, "closing_strength": 0.8, "ability_score": 0.5, "course_score": 0.5, "consistency": 0.5},
        ]

        simulated = {row["horse_id"]: row for row in simulate_race_scenarios(rows)}

        self.assertGreater(float(simulated["leader"]["relative_front_rank"]), float(simulated["stalker"]["relative_front_rank"]))
        self.assertGreater(float(simulated["leader"]["solo_lead_score"]), float(simulated["stalker"]["solo_lead_score"]))
        self.assertEqual("2", simulated["leader"]["front_competitor_count"])

    def test_fukushima_turf1200_reduces_thin_front_bias(self):
        rows = [
            {
                "race_id": "r_fukushima",
                "horse_id": "leader",
                "target_track": "福島",
                "target_surface": "芝",
                "target_distance": "1200",
                "target_race_number": "11",
                "front_rate": 0.82,
                "closing_strength": 0.30,
                "ability_score": 0.5,
                "course_score": 0.5,
                "consistency": 0.5,
            },
            {
                "race_id": "r_fukushima",
                "horse_id": "stalker",
                "target_track": "福島",
                "target_surface": "芝",
                "target_distance": "1200",
                "target_race_number": "11",
                "front_rate": 0.70,
                "closing_strength": 0.36,
                "ability_score": 0.5,
                "course_score": 0.5,
                "consistency": 0.5,
            },
            {
                "race_id": "r_fukushima",
                "horse_id": "closer",
                "target_track": "福島",
                "target_surface": "芝",
                "target_distance": "1200",
                "target_race_number": "11",
                "front_rate": 0.28,
                "closing_strength": 0.74,
                "ability_score": 0.5,
                "course_score": 0.5,
                "consistency": 0.5,
            },
        ]

        simulated = {row["horse_id"]: row for row in simulate_race_scenarios(rows)}

        self.assertLess(float(simulated["leader"]["pace_front_overuse_penalty"]), 1.0)
        self.assertLess(float(simulated["stalker"]["pace_front_overuse_penalty"]), 1.0)
        self.assertEqual("1", simulated["closer"]["pace_front_overuse_penalty"])

    def test_short_sprint_front_density_penalizes_crowded_speed(self):
        rows = [
            {
                "race_id": "r_kokura_short",
                "horse_id": "leader",
                "target_track": "小倉",
                "target_surface": "芝",
                "target_distance": "1200",
                "front_rate": 0.84,
                "closing_strength": 0.22,
                "ability_score": 0.5,
                "course_score": 0.5,
                "consistency": 0.5,
            },
            {
                "race_id": "r_kokura_short",
                "horse_id": "speed2",
                "target_track": "小倉",
                "target_surface": "芝",
                "target_distance": "1200",
                "front_rate": 0.80,
                "closing_strength": 0.28,
                "ability_score": 0.5,
                "course_score": 0.5,
                "consistency": 0.5,
            },
            {
                "race_id": "r_kokura_short",
                "horse_id": "speed3",
                "target_track": "小倉",
                "target_surface": "芝",
                "target_distance": "1200",
                "front_rate": 0.74,
                "closing_strength": 0.30,
                "ability_score": 0.5,
                "course_score": 0.5,
                "consistency": 0.5,
            },
            {
                "race_id": "r_kokura_short",
                "horse_id": "speed4",
                "target_track": "小倉",
                "target_surface": "芝",
                "target_distance": "1200",
                "front_rate": 0.70,
                "closing_strength": 0.34,
                "ability_score": 0.5,
                "course_score": 0.5,
                "consistency": 0.5,
            },
            {
                "race_id": "r_kokura_short",
                "horse_id": "stalker",
                "target_track": "小倉",
                "target_surface": "芝",
                "target_distance": "1200",
                "front_rate": 0.48,
                "closing_strength": 0.52,
                "ability_score": 0.5,
                "course_score": 0.5,
                "consistency": 0.5,
            },
        ]

        simulated = {row["horse_id"]: row for row in simulate_race_scenarios(rows)}

        self.assertLess(float(simulated["leader"]["short_sprint_front_density_adjustment"]), 0.0)
        self.assertLess(float(simulated["leader"]["pace_front_overuse_penalty"]), 1.0)
        self.assertGreater(float(simulated["stalker"]["short_sprint_front_density_adjustment"]), 0.0)
        self.assertGreater(float(simulated["stalker"]["pace_front_overuse_penalty"]), 1.0)

    def test_frame_quality_penalizes_one_strong_one_weak_frame(self):
        adjustment = _frame_quality_adjustment(
            [{"win_prob": "0.20"}, {"win_prob": "0.02"}],
            [{"win_prob": "0.18"}, {"win_prob": "0.03"}],
            same_frame=False,
        )

        self.assertLess(adjustment, 1.0)

    def test_generate_tickets_allocates_stakes_by_portfolio_ev(self):
        ev_rows = []
        for horse_name, horse_number, frame_number, win_prob, odds, market_prob in [
            ("A", "1", "1", 0.30, 6.0, 0.04),
            ("B", "2", "2", 0.22, 9.0, 0.06),
            ("C", "3", "3", 0.17, 12.0, 0.08),
            ("D", "4", "4", 0.12, 18.0, 0.12),
            ("E", "5", "5", 0.09, 24.0, 0.15),
            ("F", "6", "6", 0.06, 34.0, 0.18),
        ]:
            ev_rows.append(
                {
                    "race_id": "r_portfolio",
                    "horse_id": f"h{horse_number}",
                    "horse_name": horse_name,
                    "frame_number": frame_number,
                    "horse_number": horse_number,
                    "win_prob": str(win_prob),
                    "current_odds": str(odds),
                    "predicted_odds": str(odds),
                    "ev": str(win_prob * odds),
                    "ev_current": str(win_prob * odds),
                    "ev_predicted": str(win_prob * odds),
                    "market_prob": str(market_prob),
                    "consistency": "0.72",
                    "history_count": "5",
                }
            )

        plan = generate_tickets(
            ev_rows,
            prefer_wide=False,
            bankroll_per_race=1000,
            max_tickets_per_race=4,
            max_exotic_tickets_per_race=4,
        )

        stakes = [int(ticket["stake"]) for ticket in plan["tickets"]]
        self.assertTrue(plan["tickets"])
        self.assertLessEqual(plan["portfolio_summary"]["total_stake"], 1000)
        self.assertGreater(float(plan["portfolio_summary"]["portfolio_ev"]), 1.0)
        self.assertTrue(any(stake > 100 for stake in stakes))
        self.assertGreaterEqual(max(stakes), min(stakes))
        for ticket in plan["tickets"]:
            self.assertIn("portfolio_expected_profit", ticket)
            self.assertIn("portfolio_total_points", ticket)

    def test_generate_tickets_builds_sanrentan_formation_by_total_points(self):
        ev_rows = []
        for horse_name, horse_number, win_prob, odds, market_prob in [
            ("A", "1", 0.30, 5.0, 0.09),
            ("B", "2", 0.22, 7.0, 0.08),
            ("C", "3", 0.18, 9.0, 0.07),
            ("D", "4", 0.12, 14.0, 0.06),
            ("E", "5", 0.08, 22.0, 0.05),
            ("F", "6", 0.05, 34.0, 0.04),
        ]:
            ev_rows.append(
                {
                    "race_id": "r_formation",
                    "horse_id": f"h{horse_number}",
                    "horse_name": horse_name,
                    "frame_number": horse_number,
                    "horse_number": horse_number,
                    "win_prob": str(win_prob),
                    "current_odds": str(odds),
                    "predicted_odds": str(odds),
                    "ev": str(win_prob * odds),
                    "ev_current": str(win_prob * odds),
                    "ev_predicted": str(win_prob * odds),
                    "market_prob": str(market_prob),
                    "consistency": "0.70",
                    "history_count": "5",
                }
            )

        odds_rows = [
            {"race_id": "r_formation", "bet_type": "sanrentan", "combination": combination, "odds": "55.0", "captured_at": "2026-05-24T01:00:00+00:00"}
            for combination in ["1>2>3", "1>2>4", "1>2>5", "1>3>2", "1>3>4", "1>3>5"]
        ]

        plan = generate_tickets(
            ev_rows,
            odds_rows=odds_rows,
            prefer_wide=False,
            bankroll_per_race=1000,
            max_tickets_per_race=6,
            max_exotic_tickets_per_race=10,
            min_sanrentan_ev=1.01,
        )
        candidates = list(plan["tickets"]) + list(plan["races"][0]["candidates"])
        formation = next(
            ticket
            for ticket in candidates
            if ticket["bet_type"] == "sanrentan" and ticket.get("ticket_shape") == "formation"
        )

        self.assertEqual("total_points", formation["formation_ev_basis"])
        self.assertGreater(int(formation["point_count"]), 1)
        self.assertEqual(int(formation["point_count"]) * 100, int(formation["stake"]))
        self.assertGreaterEqual(float(formation["ev_current"]), 1.01)
        self.assertIn("formation", formation)
        self.assertIn("points", formation)

    def test_generate_tickets_prefers_jra_live_combo_odds(self):
        ev_rows = []
        for horse_name, horse_number, frame_number, win_prob, odds, market_prob in [
            ("A", "1", "1", 0.32, 6.0, 0.01),
            ("B", "2", "1", 0.24, 8.0, 0.04),
            ("C", "3", "2", 0.18, 12.0, 0.08),
            ("D", "4", "2", 0.11, 18.0, 0.10),
            ("E", "5", "3", 0.08, 30.0, 0.14),
            ("F", "6", "3", 0.07, 40.0, 0.16),
        ]:
            ev_rows.append(
                {
                    "race_id": "r_live_odds",
                    "horse_id": f"h{horse_number}",
                    "horse_name": horse_name,
                    "frame_number": frame_number,
                    "horse_number": horse_number,
                    "win_prob": str(win_prob),
                    "current_odds": str(odds),
                    "predicted_odds": str(odds),
                    "ev": str(win_prob * odds),
                    "ev_current": str(win_prob * odds),
                    "ev_predicted": str(win_prob * odds),
                    "market_prob": str(market_prob),
                    "consistency": "0.70",
                    "history_count": "5",
                }
            )
        odds_rows = [
            {"race_id": "r_live_odds", "bet_type": "win", "combination": "1", "odds": "4.2", "captured_at": "2026-05-17T01:00:00+00:00"},
            {"race_id": "r_live_odds", "bet_type": "place", "combination": "1", "odds": "2.4", "odds_min": "2.4", "odds_max": "3.0", "captured_at": "2026-05-17T01:00:00+00:00"},
            {"race_id": "r_live_odds", "bet_type": "wide", "combination": "1-2", "odds": "5.8", "odds_min": "5.8", "odds_max": "6.4", "captured_at": "2026-05-17T01:00:00+00:00"},
            {"race_id": "r_live_odds", "bet_type": "wakuren", "combination": "1-2", "odds": "8.2", "captured_at": "2026-05-17T01:00:00+00:00"},
            {"race_id": "r_live_odds", "bet_type": "umaren", "combination": "1-2", "odds": "9.4", "captured_at": "2026-05-17T01:00:00+00:00"},
            {"race_id": "r_live_odds", "bet_type": "umatan", "combination": "1>2", "odds": "24.0", "captured_at": "2026-05-17T01:00:00+00:00"},
            {"race_id": "r_live_odds", "bet_type": "sanrenpuku", "combination": "1-2-3", "odds": "22.5", "captured_at": "2026-05-17T01:00:00+00:00"},
            {"race_id": "r_live_odds", "bet_type": "sanrentan", "combination": "1>2>3", "odds": "82.0", "captured_at": "2026-05-17T01:00:00+00:00"},
        ]

        plan = generate_tickets(
            ev_rows,
            odds_rows=odds_rows,
            prefer_wide=True,
            max_tickets_per_race=8,
            max_exotic_tickets_per_race=6,
        )
        candidates = plan["races"][0]["candidates"]
        by_key = {(ticket["bet_type"], str(ticket["horse_number"])): ticket for ticket in candidates}

        self.assertEqual("jra_live", by_key[("win", "1")]["odds_source"])
        self.assertEqual("4.2", by_key[("win", "1")]["win_odds"])
        self.assertEqual("jra_live", by_key[("place", "1")]["odds_source"])
        self.assertEqual("2.4", by_key[("place", "1")]["place_odds_est"])
        self.assertEqual("5.8", by_key[("wide", "1-2")]["wide_odds_est"])
        self.assertEqual("8.2", by_key[("wakuren", "1-2")]["wakuren_odds_est"])
        self.assertEqual("9.4", by_key[("umaren", "1 - 2")]["umaren_odds_est"])
        self.assertEqual("24", by_key[("umatan", "1 → 2")]["umatan_odds_est"])
        self.assertEqual("22.5", by_key[("sanrenpuku", "1 - 2 - 3")]["trio_odds_est"])
        self.assertEqual("82", by_key[("sanrentan", "1 → 2 → 3")]["trifecta_odds_est"])

    def test_generate_tickets_keeps_top_model_wide_coverage_with_real_odds(self):
        ev_rows = []
        for horse_name, horse_number, win_prob, odds, market_prob in [
            ("A", "1", 0.24, 3.0, 0.30),
            ("B", "2", 0.18, 4.2, 0.22),
            ("C", "3", 0.14, 8.0, 0.12),
            ("D", "4", 0.10, 12.0, 0.10),
            ("E", "5", 0.08, 18.0, 0.09),
            ("F", "6", 0.06, 24.0, 0.07),
        ]:
            ev_rows.append(
                {
                    "race_id": "r_model_pair",
                    "horse_id": f"h{horse_number}",
                    "horse_name": horse_name,
                    "frame_number": horse_number,
                    "horse_number": horse_number,
                    "win_prob": str(win_prob),
                    "current_odds": str(odds),
                    "predicted_odds": str(odds),
                    "ev": str(win_prob * odds),
                    "ev_current": str(win_prob * odds),
                    "ev_predicted": str(win_prob * odds),
                    "market_prob": str(market_prob),
                    "consistency": "0.70",
                    "history_count": "5",
                }
            )

        odds_rows = [
            {"race_id": "r_model_pair", "bet_type": "wide", "combination": "1-2", "odds": "3.2", "captured_at": "2026-05-17T01:00:00+00:00"},
            {"race_id": "r_model_pair", "bet_type": "wide", "combination": "1-3", "odds": "5.5", "captured_at": "2026-05-17T01:00:00+00:00"},
            {"race_id": "r_model_pair", "bet_type": "wide", "combination": "2-3", "odds": "7.2", "captured_at": "2026-05-17T01:00:00+00:00"},
        ]

        plan = generate_tickets(
            ev_rows,
            odds_rows=odds_rows,
            prefer_wide=False,
            max_tickets_per_race=5,
        )
        all_tickets = list(plan["tickets"]) + list(plan["races"][0]["candidates"])
        coverage = [
            ticket
            for ticket in all_tickets
            if ticket["bet_type"] == "wide"
            and ticket["horse_number"] == "1-2"
            and ticket.get("coverage_reason") == "top_model_pair_real_odds"
        ]

        self.assertTrue(coverage)
        self.assertEqual("coverage", coverage[0]["ticket_role"])
        self.assertEqual("jra_live", coverage[0]["odds_source"])

    def test_generate_tickets_keeps_marked_top5_trio_coverage_with_real_odds(self):
        ev_rows = []
        for horse_name, horse_number, win_prob, odds, market_prob in [
            ("A", "1", 0.24, 3.0, 0.25),
            ("B", "2", 0.20, 4.0, 0.20),
            ("C", "3", 0.16, 7.0, 0.16),
            ("D", "4", 0.12, 12.0, 0.12),
            ("E", "5", 0.08, 18.0, 0.08),
            ("F", "6", 0.06, 24.0, 0.06),
        ]:
            ev_rows.append(
                {
                    "race_id": "r_marked_top5",
                    "horse_id": f"h{horse_number}",
                    "horse_name": horse_name,
                    "frame_number": horse_number,
                    "horse_number": horse_number,
                    "win_prob": str(win_prob),
                    "current_odds": str(odds),
                    "predicted_odds": str(odds),
                    "ev": str(win_prob * odds),
                    "ev_current": str(win_prob * odds),
                    "ev_predicted": str(win_prob * odds),
                    "market_prob": str(market_prob),
                    "consistency": "0.70",
                    "history_count": "5",
                }
            )

        odds_rows = [
            {"race_id": "r_marked_top5", "bet_type": "sanrenpuku", "combination": "1-3-5", "odds": "46.0", "captured_at": "2026-05-17T01:00:00+00:00"},
            {"race_id": "r_marked_top5", "bet_type": "wide", "combination": "1-5", "odds": "8.5", "captured_at": "2026-05-17T01:00:00+00:00"},
        ]

        plan = generate_tickets(
            ev_rows,
            odds_rows=odds_rows,
            prefer_wide=False,
            max_tickets_per_race=5,
        )
        all_tickets = list(plan["tickets"]) + list(plan["races"][0]["candidates"])
        trio_coverage = [
            ticket
            for ticket in all_tickets
            if ticket["bet_type"] == "sanrenpuku"
            and ticket["horse_number"] == "1 - 3 - 5"
            and ticket.get("coverage_reason") == "marked_top5_trio_real_odds"
        ]

        self.assertTrue(trio_coverage)
        self.assertEqual("coverage", trio_coverage[0]["ticket_role"])
        self.assertEqual("jra_live", trio_coverage[0]["odds_source"])

    def test_generate_tickets_promotes_marked_core_coverage_with_real_odds(self):
        ev_rows = []
        for horse_name, horse_number, win_prob, odds, market_prob in [
            ("A", "1", 0.22, 3.4, 0.24),
            ("B", "2", 0.18, 4.8, 0.19),
            ("C", "3", 0.15, 7.5, 0.15),
            ("D", "4", 0.11, 11.0, 0.11),
            ("E", "5", 0.08, 18.0, 0.08),
            ("F", "6", 0.06, 24.0, 0.06),
        ]:
            ev_rows.append(
                {
                    "race_id": "r_marked_core",
                    "horse_id": f"h{horse_number}",
                    "horse_name": horse_name,
                    "frame_number": horse_number,
                    "horse_number": horse_number,
                    "win_prob": str(win_prob),
                    "current_odds": str(odds),
                    "predicted_odds": str(odds),
                    "ev": str(win_prob * odds),
                    "ev_current": str(win_prob * odds),
                    "ev_predicted": str(win_prob * odds),
                    "market_prob": str(market_prob),
                    "consistency": "0.70",
                    "history_count": "5",
                }
            )

        odds_rows = [
            {"race_id": "r_marked_core", "bet_type": "wide", "combination": "1-4", "odds": "7.0", "captured_at": "2026-05-17T01:00:00+00:00"},
            {"race_id": "r_marked_core", "bet_type": "sanrenpuku", "combination": "1-2-4", "odds": "34.0", "captured_at": "2026-05-17T01:00:00+00:00"},
        ]

        plan = generate_tickets(
            ev_rows,
            odds_rows=odds_rows,
            prefer_wide=False,
            max_tickets_per_race=5,
        )
        all_tickets = list(plan["tickets"]) + list(plan["races"][0]["candidates"])
        reasons = {str(ticket.get("coverage_reason", "")) for ticket in all_tickets}

        self.assertIn("marked_core_pair_real_odds", reasons)
        self.assertIn("marked_core_trio_real_odds", reasons)

    def test_generate_tickets_calibrates_extreme_low_evidence_win_ticket_out_of_selection(self):
        ev_rows = [
            {
                "race_id": "r_low_win",
                "horse_id": "h1",
                "horse_name": "A",
                "frame_number": "1",
                "horse_number": "1",
                "win_prob": "0.28",
                "current_odds": "4.0",
                "predicted_odds": "4.0",
                "ev": "1.12",
                "ev_current": "1.12",
                "ev_predicted": "1.12",
                "market_prob": "0.22",
                "consistency": "0.70",
                "history_count": "5",
            },
            {
                "race_id": "r_low_win",
                "horse_id": "h9",
                "horse_name": "Long",
                "frame_number": "8",
                "horse_number": "9",
                "win_prob": "0.005",
                "current_odds": "400.0",
                "predicted_odds": "400.0",
                "ev": "2.0",
                "ev_current": "2.0",
                "ev_predicted": "2.0",
                "market_prob": "0.0025",
                "consistency": "0.10",
                "history_count": "1",
            },
        ]
        for idx in range(2, 7):
            ev_rows.append(
                {
                    "race_id": "r_low_win",
                    "horse_id": f"h{idx}",
                    "horse_name": f"H{idx}",
                    "frame_number": str(idx),
                    "horse_number": str(idx),
                    "win_prob": "0.10",
                    "current_odds": "9.0",
                    "predicted_odds": "9.0",
                    "ev": "0.9",
                    "ev_current": "0.9",
                    "ev_predicted": "0.9",
                    "market_prob": "0.12",
                    "consistency": "0.60",
                    "history_count": "5",
                }
            )

        plan = generate_tickets(ev_rows, prefer_wide=False)

        self.assertFalse(
            [
                ticket
                for ticket in plan["tickets"]
                if ticket["bet_type"] == "win" and str(ticket["horse_number"]) == "9"
            ]
        )

    def test_generate_tickets_keeps_cbc_boundary_win_row_eligible(self):
        win_prob = 0.04798577599968
        stale_odds = 20.0
        official_odds = 27.3
        ev_rows = [
            {
                "race_id": "r_cbc_boundary",
                "horse_id": "h2",
                "horse_name": "フロムダスク",
                "frame_number": "1",
                "horse_number": "2",
                "win_prob": str(win_prob),
                "current_odds": str(stale_odds),
                "predicted_odds": str(stale_odds),
                "ev": str(win_prob * stale_odds),
                "ev_current": str(win_prob * stale_odds),
                "ev_predicted": str(win_prob * stale_odds),
                "market_prob": str(1.0 / stale_odds),
                "consistency": "0.1523",
                "history_count": "5",
            }
        ]
        odds_rows = [
            {
                "race_id": "r_cbc_boundary",
                "bet_type": "win",
                "combination": "2",
                "odds": str(official_odds),
                "captured_at": "2026-08-09T15:25:00+09:00",
            }
        ]

        plan = generate_tickets(
            ev_rows,
            odds_rows=odds_rows,
            prefer_wide=False,
            max_tickets_per_race=1,
            min_place_ev=9.0,
            min_wide_ev=9.0,
            min_wakuren_ev=9.0,
            min_umaren_ev=9.0,
            min_umatan_ev=9.0,
            min_sanrenpuku_ev=9.0,
            min_sanrentan_ev=9.0,
        )
        boundary = next(
            ticket
            for ticket in plan["tickets"]
            if ticket["bet_type"] == "win" and str(ticket["horse_number"]) == "2"
        )

        self.assertGreater(float(boundary["ev_current"]), 1.05)
        self.assertEqual(official_odds, float(boundary["win_odds"]))
        self.assertAlmostEqual(win_prob, float(boundary["raw_hit_prob"]), places=6)

    def test_generate_tickets_refills_win_selection_from_complete_eligible_universe(self):
        ev_rows = []
        for horse_name, horse_number, win_prob, odds, consistency, history_count in [
            ("RejectedA", "1", 0.0100, 200.0, 0.10, 1),
            ("RejectedB", "2", 0.0095, 200.0, 0.10, 1),
            ("RefillA", "3", 0.04798577599968, 27.3, 0.65, 5),
            ("RefillB", "4", 0.0800, 16.0, 0.65, 5),
        ]:
            ev = win_prob * odds
            ev_rows.append(
                {
                    "race_id": "r_win_refill",
                    "horse_id": f"h{horse_number}",
                    "horse_name": horse_name,
                    "frame_number": horse_number,
                    "horse_number": horse_number,
                    "win_prob": str(win_prob),
                    "current_odds": str(odds),
                    "predicted_odds": str(odds),
                    "ev": str(ev),
                    "ev_current": str(ev),
                    "ev_predicted": str(ev),
                    "market_prob": str(1.0 / odds),
                    "consistency": str(consistency),
                    "history_count": str(history_count),
                }
            )

        plan = generate_tickets(
            ev_rows,
            prefer_wide=False,
            max_tickets_per_race=2,
            min_place_ev=9.0,
            min_wide_ev=9.0,
            min_wakuren_ev=9.0,
            min_umaren_ev=9.0,
            min_umatan_ev=9.0,
            min_sanrenpuku_ev=9.0,
            min_sanrentan_ev=9.0,
        )
        selected_win_numbers = {
            str(ticket["horse_number"])
            for ticket in plan["tickets"]
            if ticket["bet_type"] == "win"
        }

        self.assertEqual({"3", "4"}, selected_win_numbers)

    def test_selection_caps_rank_the_post_calibration_eligible_pool(self):
        low_ev_first = {
            "race_id": "r_cap_order",
            "bet_type": "wide",
            "horse_number": "1-2",
            "ev_current": "1.10",
            "hit_prob": "0.25",
        }
        high_ev_second = {
            "race_id": "r_cap_order",
            "bet_type": "wide",
            "horse_number": "3-4",
            "ev_current": "1.40",
            "hit_prob": "0.20",
        }

        limited = _limit_eligible_selection_pool(
            [low_ev_first, high_ev_second],
            max_wide_tickets_per_race=1,
            max_exotic_tickets_per_race=0,
        )

        self.assertEqual(["3-4"], [ticket["horse_number"] for ticket in limited])

    def test_generate_tickets_requires_one_point_zero_five_ev_for_win_candidates(self):
        ev_rows = []
        for horse_name, horse_number, win_prob, odds, model_score in [
            ("ThinWin", "1", 0.20, 5.20, 0.40),
            ("A", "2", 0.18, 4.00, 0.38),
            ("B", "3", 0.16, 4.50, 0.36),
            ("C", "4", 0.14, 6.00, 0.34),
            ("D", "5", 0.12, 7.00, 0.32),
            ("E", "6", 0.10, 8.00, 0.30),
        ]:
            ev = win_prob * odds
            ev_rows.append(
                {
                    "race_id": "r_win_floor",
                    "horse_id": f"h{horse_number}",
                    "horse_name": horse_name,
                    "frame_number": horse_number,
                    "horse_number": horse_number,
                    "win_prob": str(win_prob),
                    "current_odds": str(odds),
                    "predicted_odds": str(odds),
                    "ev": str(ev),
                    "ev_current": str(ev),
                    "ev_predicted": str(ev),
                    "market_prob": str(max(0.01, 1.0 / odds)),
                    "model_score": str(model_score),
                    "consistency": "0.65",
                    "history_count": "5",
                }
            )

        plan = generate_tickets(ev_rows, min_ev=1.03, prefer_wide=False)
        all_tickets = list(plan["tickets"]) + list(plan["races"][0]["candidates"])

        self.assertFalse(
            [
                ticket
                for ticket in all_tickets
                if ticket["bet_type"] == "win" and str(ticket["horse_number"]) == "1"
            ]
        )

    def test_generate_tickets_keeps_top_model_score_longshot_as_long_candidate(self):
        ev_rows = [
            {
                "race_id": "r_model_long",
                "horse_id": "h1",
                "horse_name": "ModelLong",
                "frame_number": "1",
                "horse_number": "1",
                "win_prob": "0.025",
                "current_odds": "34.0",
                "predicted_odds": "34.0",
                "ev": "0.85",
                "ev_current": "0.85",
                "ev_predicted": "0.85",
                "market_prob": "0.029",
                "model_score": "0.45",
                "pace_score": "0.48",
                "weight_score": "1.5",
                "course_score": "0.37",
                "probability_band": "longshot",
                "consistency": "0.55",
                "history_count": "5",
            }
        ]
        for horse_name, horse_number, win_prob, odds, model_score in [
            ("Favorite", "2", 0.22, 3.0, 0.36),
            ("Contender", "3", 0.16, 5.0, 0.34),
            ("Partner", "4", 0.13, 7.0, 0.32),
            ("Mid", "5", 0.10, 12.0, 0.30),
            ("Other", "6", 0.08, 18.0, 0.28),
        ]:
            ev = win_prob * odds
            ev_rows.append(
                {
                    "race_id": "r_model_long",
                    "horse_id": f"h{horse_number}",
                    "horse_name": horse_name,
                    "frame_number": horse_number,
                    "horse_number": horse_number,
                    "win_prob": str(win_prob),
                    "current_odds": str(odds),
                    "predicted_odds": str(odds),
                    "ev": str(ev),
                    "ev_current": str(ev),
                    "ev_predicted": str(ev),
                    "market_prob": str(max(0.01, 1.0 / odds)),
                    "model_score": str(model_score),
                    "pace_score": "0.30",
                    "weight_score": "0.0",
                    "course_score": "0.30",
                    "consistency": "0.60",
                    "history_count": "5",
                }
            )

        plan = generate_tickets(ev_rows)
        long_by_number = {str(row["horse_number"]): row for row in plan["long"]}

        self.assertIn("1", long_by_number)
        self.assertEqual("top_model_score_longshot", long_by_number["1"]["long_reason"])

    def test_generate_tickets_can_add_no_gami_coverage_when_portfolio_ev_survives(self):
        ev_rows = []
        for horse_name, horse_number, win_prob, odds, market_prob in [
            ("A", "1", 0.38, 4.0, 0.22),
            ("B", "2", 0.24, 2.0, 0.16),
            ("C", "3", 0.16, 3.0, 0.12),
            ("D", "4", 0.10, 4.0, 0.20),
            ("E", "5", 0.07, 4.0, 0.15),
            ("F", "6", 0.05, 4.0, 0.15),
        ]:
            ev_rows.append(
                {
                    "race_id": "r_coverage",
                    "horse_id": f"h{horse_number}",
                    "horse_name": horse_name,
                    "frame_number": horse_number,
                    "horse_number": horse_number,
                    "win_prob": str(win_prob),
                    "current_odds": str(odds),
                    "predicted_odds": str(odds),
                    "ev": str(win_prob * odds),
                    "ev_current": str(win_prob * odds),
                    "ev_predicted": str(win_prob * odds),
                    "market_prob": str(market_prob),
                    "consistency": "0.70",
                    "history_count": "5",
                }
            )

        odds_rows = [
            {"race_id": "r_coverage", "bet_type": "win", "combination": "1", "odds": "4.0", "captured_at": "2026-05-17T01:00:00+00:00"},
            {"race_id": "r_coverage", "bet_type": "umaren", "combination": "1-2", "odds": "4.0", "captured_at": "2026-05-17T01:00:00+00:00"},
            {"race_id": "r_coverage", "bet_type": "umatan", "combination": "1>2", "odds": "7.0", "captured_at": "2026-05-17T01:00:00+00:00"},
            {"race_id": "r_coverage", "bet_type": "sanrenpuku", "combination": "1-2-3", "odds": "8.0", "captured_at": "2026-05-17T01:00:00+00:00"},
        ]

        plan = generate_tickets(
            ev_rows,
            odds_rows=odds_rows,
            prefer_wide=False,
            max_tickets_per_race=5,
            min_place_ev=9.0,
            min_wide_ev=9.0,
            min_wakuren_ev=9.0,
            min_umaren_ev=9.0,
            min_umatan_ev=9.0,
            min_sanrenpuku_ev=9.0,
            min_sanrentan_ev=9.0,
        )

        coverage_tickets = [ticket for ticket in plan["tickets"] if ticket.get("ticket_role") == "coverage"]
        self.assertTrue(coverage_tickets)
        self.assertGreaterEqual(float(plan["tickets"][0]["portfolio_ev"]), 1.0)
        for ticket in plan["tickets"]:
            self.assertTrue(ticket["portfolio_no_gami"])
            self.assertGreaterEqual(int(ticket["return_if_hit"]), int(ticket["portfolio_total_stake"]))

    def test_generate_tickets_prunes_gami_portfolios(self):
        ev_rows = []
        for horse_name, horse_number in [("A", "1"), ("B", "2"), ("C", "3")]:
            ev_rows.append(
                {
                    "race_id": "r_gami",
                    "horse_id": f"h{horse_number}",
                    "horse_name": horse_name,
                    "frame_number": horse_number,
                    "horse_number": horse_number,
                    "win_prob": "0.60",
                    "current_odds": "2.0",
                    "predicted_odds": "2.0",
                    "ev": "1.2",
                    "ev_current": "1.2",
                    "ev_predicted": "1.2",
                    "market_prob": "0.20",
                    "consistency": "0.70",
                    "history_count": "5",
                }
            )

        plan = generate_tickets(
            ev_rows,
            prefer_wide=False,
            max_tickets_per_race=3,
            min_place_ev=9.0,
            min_wide_ev=9.0,
            min_wakuren_ev=9.0,
            min_umaren_ev=9.0,
            min_umatan_ev=9.0,
            min_sanrenpuku_ev=9.0,
            min_sanrentan_ev=9.0,
        )

        self.assertLessEqual(len(plan["tickets"]), 2)
        for ticket in plan["tickets"]:
            self.assertTrue(ticket["portfolio_no_gami"])
            self.assertGreaterEqual(int(ticket["return_if_hit"]), int(ticket["portfolio_total_stake"]))

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

    def test_reviewer_rejects_missing_top_horses_and_dependency(self):
        reviewer = ReviewerAgent(WorkflowSettings())
        ev_rows = [
            {
                "race_id": "r_cover",
                "horse_number": str(number),
                "horse_name": name,
                "win_prob": str(probability),
                "current_odds": "5.0",
                "predicted_odds": "5.0",
                "ev_current": "1.1",
                "ev_predicted": "1.1",
            }
            for number, name, probability in [(1, "A", 0.30), (2, "B", 0.20), (3, "C", 0.15)]
        ]
        tickets = [
            {
                "race_id": "r_cover",
                "bet_type": "wide",
                "horse_number": selection,
                "hit_prob": "0.2",
                "predicted_odds": "6.0",
                "ev_current": "1.2",
                "stake": 100,
            }
            for selection in ("2-4", "2-5", "2-6")
        ]

        review = reviewer.run(
            {"quality_report": {"issues_by_severity": {}}, "entries": [{"race_id": "r_cover"}]},
            scenario_rows=ev_rows,
            ev_rows=ev_rows,
            ticket_plan={"tickets": tickets},
            attempt=0,
        )

        self.assertEqual("NG", review["status"])
        self.assertIn("top win-probability horse", review["reason"])
        self.assertIn("dependency ratio", review["reason"])

    def test_reviewer_rejects_eligible_official_win_missing_from_candidate_universe(self):
        reviewer = ReviewerAgent(WorkflowSettings(min_ev=1.05))
        ev_rows = [
            {
                "race_id": "r_candidate_audit",
                "horse_id": "h1",
                "horse_number": "1",
                "horse_name": "Omitted",
                "win_prob": "0.06",
                "current_odds": "20.0",
                "ev_current": "1.2",
            },
            {
                "race_id": "r_candidate_audit",
                "horse_id": "h2",
                "horse_number": "2",
                "horse_name": "Considered",
                "win_prob": "0.94",
                "current_odds": "2.0",
                "ev_current": "1.88",
            },
        ]
        collected = {
            "quality_report": {"issues_by_severity": {}},
            "entries": [
                {"race_id": "r_candidate_audit", "horse_id": "h1"},
                {"race_id": "r_candidate_audit", "horse_id": "h2"},
            ],
            "combo_odds": [
                {
                    "race_id": "r_candidate_audit",
                    "bet_type": "win",
                    "combination": "1",
                    "odds": "20.0",
                    "captured_at": "2026-08-09T15:25:00+09:00",
                },
                {
                    "race_id": "r_candidate_audit",
                    "bet_type": "win",
                    "combination": "2",
                    "odds": "2.0",
                    "captured_at": "2026-08-09T15:25:00+09:00",
                },
            ],
        }
        ticket_plan = {
            "tickets": [],
            "races": [
                {
                    "race_id": "r_candidate_audit",
                    "candidates": [],
                }
            ],
            "candidate_evaluations": [
                {
                    "race_id": "r_candidate_audit",
                    "bet_type": "win",
                    "combination": "2",
                    "official_odds": "2.0",
                }
            ],
        }

        review = reviewer.run(
            collected,
            scenario_rows=ev_rows,
            ev_rows=ev_rows,
            ticket_plan=ticket_plan,
            attempt=0,
        )

        self.assertEqual("NG", review["status"])
        self.assertIn("missing from candidate universe", review["reason"])
        self.assertEqual(
            ["1"],
            [row["horse_number"] for row in review["missing_eligible_win_candidates"]],
        )
        self.assertEqual("1.2", review["missing_eligible_win_candidates"][0]["win_ev"])

    def test_reviewer_repairs_only_unsafe_tickets_and_rechecks_residual_portfolio(self):
        reviewer = ReviewerAgent(WorkflowSettings(max_repair_attempts=0))
        ev_rows = [
            {"race_id": "r_repair", "horse_number": "2", "horse_name": "H2", "win_prob": "0.207243"},
            {"race_id": "r_repair", "horse_number": "7", "horse_name": "H7", "win_prob": "0.192909"},
            {"race_id": "r_repair", "horse_number": "1", "horse_name": "H1", "win_prob": "0.178825"},
            {"race_id": "r_repair", "horse_number": "10", "horse_name": "H10", "win_prob": "0.167023"},
            {"race_id": "r_repair", "horse_number": "3", "horse_name": "H3", "win_prob": "0.119301"},
            {
                "race_id": "r_repair", "horse_id": "h5", "horse_number": "5",
                "horse_name": "H5", "win_prob": "0.134699", "current_odds": "78",
                "predicted_odds": "66.393789", "ev_current": "1.395951",
                "ev_predicted": "1.188237", "current_popularity": "5",
            },
        ]

        def wide(numbers, stake, hit_prob, odds, ev):
            return {
                "race_id": "r_repair", "bet_type": "wide",
                "horse_number": "-".join(numbers), "horse_numbers": numbers,
                "horse_name": " - ".join(numbers), "stake": stake,
                "hit_prob": str(hit_prob), "wide_odds_est": str(odds),
                "win_odds": str(odds), "ev_current": str(ev), "ev": str(ev),
                "odds_source": "jra_live",
            }

        tickets = [
            {
                "race_id": "r_repair", "bet_type": "win", "horse_id": "h5",
                "horse_number": 5, "horse_name": "H5", "stake": 100,
                "hit_prob": "0.014162", "win_odds": "78",
                "ev_current": "1.104638", "ev": "1.104638", "odds_source": "jra_live",
            },
            wide(["7", "3"], 400, 0.208809, 5.2, 1.172653),
            wide(["10", "3"], 300, 0.193254, 5.5, 1.155014),
            wide(["2", "3"], 100, 0.227879, 4.6, 1.137447),
            wide(["1", "10"], 100, 0.248214, 4.0, 1.076377),
        ]
        ticket_plan = {
            "tickets": tickets,
            "races": [{"race_id": "r_repair", "tickets": tickets}],
            "reviewer_ticket_repair_enabled": True,
        }
        collected = {
            "entries": [{"horse_number": row["horse_number"]} for row in ev_rows],
            "quality_report": {"issues_by_severity": {}},
        }

        initial = reviewer.run(collected, ev_rows, ev_rows, ticket_plan, attempt=0)
        repaired = apply_ticket_repair_actions(ticket_plan, initial["repair_actions"])
        final = reviewer.run(collected, ev_rows, ev_rows, repaired, attempt=0)

        self.assertEqual("NG", initial["status"])
        self.assertEqual("OK", final["status"])
        self.assertEqual(
            [("7-3", 400), ("1-10", 400)],
            [(ticket["horse_number"], ticket["stake"]) for ticket in repaired["tickets"]],
        )
        self.assertEqual(800, repaired["portfolio_summary"]["total_stake"])
        self.assertEqual("1.124515", repaired["portfolio_summary"]["portfolio_ev"])
        self.assertTrue(repaired["portfolio_summary"]["no_gami"])
        self.assertEqual(200, repaired["unused_bankroll"])
        self.assertEqual(
            repaired["tickets"],
            repaired["races"][0]["tickets"],
        )

    def test_reviewer_win_candidate_audit_uses_actionable_ticket_floor(self):
        reviewer = ReviewerAgent(WorkflowSettings(min_ev=1.05))
        ev_rows = [{
            "race_id": "r_floor", "horse_id": "h1", "horse_number": "1",
            "horse_name": "Below ticket floor", "win_prob": "0.0535",
        }]
        review = reviewer.run(
            {
                "quality_report": {"issues_by_severity": {}},
                "entries": [{"race_id": "r_floor", "horse_id": "h1"}],
                "combo_odds": [{
                    "race_id": "r_floor", "bet_type": "win", "combination": "1",
                    "odds": "20.0",
                }],
            },
            scenario_rows=ev_rows,
            ev_rows=ev_rows,
            ticket_plan={"tickets": [], "races": [{"race_id": "r_floor", "candidates": []}]},
            attempt=0,
        )

        self.assertEqual([], review["missing_eligible_win_candidates"])

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


    def test_weight_increase_is_penalty_and_decrease_is_bonus(self):
        current = {
            "race_id": "r1",
            "horse_id": "h1",
            "assigned_weight": "57",
            "target_distance": "1400",
        }
        summary = {"avg_weight": 55.0}

        increased = build_feature_row(current, summary)
        decreased = build_feature_row({**current, "assigned_weight": "53"}, summary)

        self.assertLess(float(increased["weight_score"]), 0.0)
        self.assertGreater(float(decreased["weight_score"]), 0.0)
        self.assertAlmostEqual(
            float(increased["weight_score"]),
            -float(decreased["weight_score"]),
        )

    def test_feature_row_preserves_body_weight_as_gate_only_metadata(self):
        current = {
            "race_id": "r_body_weight",
            "horse_id": "h1",
            "assigned_weight": "55",
            "current_body_weight": "472",
            "body_weight_change": "-2",
            "body_weight_status": "published",
            "target_distance": "1200",
        }

        feature = build_feature_row(current, {"avg_weight": 55.0})

        self.assertEqual("472", feature["current_body_weight"])
        self.assertEqual("-2", feature["body_weight_change"])
        self.assertEqual("published", feature["body_weight_status"])
        self.assertEqual("gate_only", feature["body_weight_model_usage"])
        self.assertFalse(feature["body_weight_adjustment_applied"])


if __name__ == "__main__":
    unittest.main()
