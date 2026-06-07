from __future__ import annotations

import unittest

from src.react_workflow import BetBuilderAgent, WorkflowSettings
from strategy.win5 import evaluate_win5_coverage, generate_win5_plan


class TestWin5Strategy(unittest.TestCase):
    def test_under_10_mode_respects_point_limit(self):
        plan = generate_win5_plan(_ev_rows(), mode="win5_under_10", max_points=10)

        self.assertEqual("OK", plan["status"])
        self.assertEqual("win5", plan["bet_type"])
        self.assertLessEqual(plan["points"], 10)
        self.assertEqual(5, len(plan["legs"]))
        self.assertEqual(plan["points"], len(plan["tickets"]))
        self.assertTrue(plan["fixed_legs"])

    def test_requires_exactly_five_races(self):
        plan = generate_win5_plan(_ev_rows(race_count=4), mode="win5_compact")

        self.assertEqual("NG", plan["status"])
        self.assertEqual([], plan["tickets"])
        self.assertIn("exactly 5 races", plan["reason"])

    def test_bet_builder_routes_win5_modes(self):
        builder = BetBuilderAgent(WorkflowSettings(mode="win5_under_10", win5_max_points=8))

        plan = builder.run(_ev_rows())

        self.assertEqual("win5", plan["bet_type"])
        self.assertLessEqual(plan["points"], 8)
        self.assertEqual(["win5"], plan["bet_types_considered"])

    def test_bet_builder_passes_config_order_to_win5_plan(self):
        builder = BetBuilderAgent(WorkflowSettings(mode="win5_under_10", win5_max_points=8))

        plan = builder.run(
            _ev_rows(),
            race_configs=[
                {"race_id": "r2"},
                {"race_id": "r1"},
                {"race_id": "r3"},
                {"race_id": "r5"},
                {"race_id": "r4"},
            ],
        )

        self.assertEqual(["r2", "r1", "r3", "r5", "r4"], plan["race_order"])

    def test_uses_config_race_order_for_legs(self):
        rows = _ev_rows()
        plan = generate_win5_plan(
            rows,
            mode="win5_under_10",
            max_points=8,
            race_order=["r1", "r4", "r2", "r5", "r3"],
        )

        self.assertEqual(["r1", "r4", "r2", "r5", "r3"], plan["race_order"])
        self.assertEqual(["r1", "r4", "r2", "r5", "r3"], [leg["race_id"] for leg in plan["legs"]])
        self.assertEqual("config", plan["race_order_source"])

    def test_under_10_warns_on_fragile_fixed_favorites(self):
        rows = _ev_rows(probabilities=[0.34, 0.20, 0.15, 0.11, 0.09, 0.06])
        plan = generate_win5_plan(rows, mode="win5_under_10", max_points=1)

        self.assertEqual(1, plan["points"])
        self.assertIn("under_10_contains_fragile_fixed_leg", plan["warnings"])
        self.assertTrue(any("fixed_favorite_below_35pct" in warning for warning in plan["warnings"]))

    def test_evaluate_win5_coverage_reports_selected_and_top5_hits(self):
        plan = generate_win5_plan(_ev_rows(), mode="win5_under_10", max_points=8)
        result = evaluate_win5_coverage(plan, ["1", "2", "3", "4", "5"])

        self.assertEqual("win5", result["bet_type"])
        self.assertFalse(result["hit"])
        self.assertGreaterEqual(result["top5_hit_count"], result["selected_hit_count"])
        self.assertEqual(5, len(result["leg_results"]))


def _ev_rows(
    race_count: int = 5,
    *,
    probabilities: list[float] | None = None,
) -> list[dict[str, object]]:
    rows = []
    probabilities = probabilities or [0.32, 0.22, 0.16, 0.10, 0.08, 0.06]
    for race_index in range(1, race_count + 1):
        race_id = f"r{race_index}"
        for horse_index, probability in enumerate(probabilities, start=1):
            odds = 1.0 / max(probability * 0.82, 0.01)
            rows.append(
                {
                    "race_id": race_id,
                    "race_name": f"Race {race_index}",
                    "horse_id": f"{race_id}_h{horse_index}",
                    "horse_name": f"H{race_index}_{horse_index}",
                    "horse_number": str(horse_index),
                    "current_popularity": str(horse_index),
                    "target_track": "東京" if race_index % 2 else "阪神",
                    "target_race_number": str(8 + race_index),
                    "win_prob": str(probability),
                    "current_odds": str(odds),
                    "ev": str(probability * odds),
                    "odds_gap_ratio": "0.02",
                }
            )
    return rows


if __name__ == "__main__":
    unittest.main()
