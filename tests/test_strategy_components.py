import unittest

from strategy.live_odds import build_live_odds_lookup, live_combo_key, live_odds_value, lookup_live_odds
from strategy.portfolio import portfolio_ev, portfolio_no_gami, portfolio_summary, with_adjusted_stake


class LiveOddsTest(unittest.TestCase):
    def test_latest_snapshot_wins_and_unordered_combinations_are_normalized(self):
        rows = [
            {"race_id": "R1", "bet_type": "wide", "combination": "2-8", "odds": "5.0", "captured_at": "10:00"},
            {"race_id": "R1", "bet_type": "wide", "combination": "2-8", "odds": "6.2", "captured_at": "10:05"},
        ]
        lookup = build_live_odds_lookup(rows)

        self.assertEqual(live_combo_key("wide", ["8", "2"]), "2-8")
        self.assertEqual(live_odds_value(lookup_live_odds(lookup["R1"], "wide", ["8", "2"])), 6.2)

    def test_latest_complete_snapshot_does_not_backfill_missing_combinations(self):
        rows = [
            {
                "race_id": "R1", "bet_type": "win", "combination": "1", "odds": "4.0",
                "captured_at": "10:00", "snapshot_id": "S1", "snapshot_complete": "true",
            },
            {
                "race_id": "R1", "bet_type": "win", "combination": "2", "odds": "8.0",
                "captured_at": "10:00", "snapshot_id": "S1", "snapshot_complete": "true",
            },
            {
                "race_id": "R1", "bet_type": "win", "combination": "1", "odds": "5.0",
                "captured_at": "10:05", "snapshot_id": "S2", "snapshot_complete": "true",
            },
            {
                "race_id": "R1", "bet_type": "win", "combination": "2", "odds": "9.0",
                "captured_at": "10:10", "snapshot_id": "S3", "snapshot_complete": "false",
            },
        ]

        lookup = build_live_odds_lookup(rows)

        self.assertEqual(live_odds_value(lookup_live_odds(lookup["R1"], "win", ["1"])), 5.0)
        self.assertEqual(lookup_live_odds(lookup["R1"], "win", ["2"]), {})


class PortfolioTest(unittest.TestCase):
    def test_summary_uses_stake_weighted_ev(self):
        tickets = [
            {"stake": 200, "ev": "1.2", "win_odds": "3.0"},
            {"stake": 100, "ev": "0.9", "win_odds": "4.0"},
        ]

        self.assertAlmostEqual(portfolio_ev(tickets), 1.1)
        self.assertTrue(portfolio_no_gami(tickets))
        self.assertEqual(portfolio_summary(tickets)["expected_profit"], 30)

    def test_formation_stakes_are_rounded_by_point_count(self):
        ticket = {"ticket_shape": "formation", "point_count": 3, "trifecta_odds_min": "8", "trifecta_odds_max": "12"}

        adjusted = with_adjusted_stake(ticket, 550)

        self.assertEqual(adjusted["stake"], 300)
        self.assertEqual(adjusted["stake_per_point"], 100)
        self.assertEqual(adjusted["min_return_if_hit"], 800)


if __name__ == "__main__":
    unittest.main()
