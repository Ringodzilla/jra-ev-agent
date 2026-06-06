import unittest

from src.react_workflow import BetBuilderAgent, WorkflowSettings
from strategy.win5 import generate_win5_plan


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


def _ev_rows(race_count: int = 5) -> list[dict[str, object]]:
    rows = []
    for race_index in range(1, race_count + 1):
        race_id = f"r{race_index}"
        for horse_index, probability in enumerate([0.32, 0.22, 0.16, 0.10, 0.08, 0.06], start=1):
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
