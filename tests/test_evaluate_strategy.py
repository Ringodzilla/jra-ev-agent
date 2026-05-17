import unittest
from unittest.mock import patch

from scripts import evaluate_strategy as evaluator


class TestEvaluateStrategy(unittest.TestCase):
    def test_payout_lookup_accepts_jra_style_labels(self):
        results = [
            {"race_id": "r1", "式別": "単勝", "馬番": "1", "払戻金": "250円"},
            {"race_id": "r1", "式別": "複勝", "馬番": "1", "払戻金": "140"},
            {"race_id": "r1", "式別": "ワイド", "組番": "2-1", "払戻金": "580"},
            {"race_id": "r1", "式別": "枠連", "組番": "2-1", "払戻金": "820"},
            {"race_id": "r1", "式別": "馬連", "組番": "2-1", "払戻金": "940"},
            {"race_id": "r1", "式別": "馬単", "組番": "1→2", "払戻金": "2,400円"},
            {"race_id": "r1", "式別": "三連複", "組番": "3-2-1", "払戻金": "2,250"},
            {"race_id": "r1", "式別": "三連単", "組番": "1→2→3", "払戻金": "8,200"},
        ]

        lookup = evaluator._build_payout_lookup(results)

        self.assertEqual(250, lookup[("r1", "win", "1")])
        self.assertEqual(140, lookup[("r1", "place", "1")])
        self.assertEqual(580, lookup[("r1", "wide", "1-2")])
        self.assertEqual(820, lookup[("r1", "wakuren", "1-2")])
        self.assertEqual(940, lookup[("r1", "umaren", "1-2")])
        self.assertEqual(2400, lookup[("r1", "umatan", "1>2")])
        self.assertEqual(2250, lookup[("r1", "sanrenpuku", "1-2-3")])
        self.assertEqual(8200, lookup[("r1", "sanrentan", "1>2>3")])

    def test_evaluate_strategy_settles_all_supported_bet_types(self):
        tickets = [
            {"race_id": "r1", "bet_type": "win", "horse_number": "1", "stake": 100},
            {"race_id": "r1", "bet_type": "place", "horse_number": "1", "stake": 100},
            {"race_id": "r1", "bet_type": "wide", "horse_numbers": ["1", "2"], "horse_number": "1-2", "stake": 100},
            {"race_id": "r1", "bet_type": "wakuren", "frame_numbers": ["1", "2"], "horse_number": "1-2", "stake": 100},
            {"race_id": "r1", "bet_type": "umaren", "horse_numbers": ["1", "2"], "horse_number": "1-2", "stake": 100},
            {"race_id": "r1", "bet_type": "umatan", "horse_numbers": ["1", "2"], "horse_number": "1→2", "stake": 100},
            {"race_id": "r1", "bet_type": "sanrenpuku", "horse_numbers": ["1", "2", "3"], "horse_number": "1-2-3", "stake": 100},
            {"race_id": "r1", "bet_type": "sanrentan", "horse_numbers": ["1", "2", "3"], "horse_number": "1→2→3", "stake": 100},
        ]
        results = [
            {"race_id": "r1", "式別": "単勝", "馬番": "1", "払戻金": "250"},
            {"race_id": "r1", "式別": "複勝", "馬番": "1", "払戻金": "140"},
            {"race_id": "r1", "式別": "ワイド", "組番": "1-2", "払戻金": "580"},
            {"race_id": "r1", "式別": "枠連", "組番": "1-2", "払戻金": "820"},
            {"race_id": "r1", "式別": "馬連", "組番": "1-2", "払戻金": "940"},
            {"race_id": "r1", "式別": "馬単", "組番": "1-2", "払戻金": "2400"},
            {"race_id": "r1", "式別": "三連複", "組番": "1-2-3", "払戻金": "2250"},
            {"race_id": "r1", "式別": "三連単", "組番": "1-2-3", "払戻金": "8200"},
        ]

        with patch.object(evaluator, "compute_ev", return_value=[{"race_id": "r1"}]), patch.object(
            evaluator,
            "generate_tickets",
            return_value={"tickets": tickets},
        ):
            metrics = evaluator.evaluate_strategy(rows=[{"race_id": "r1"}], results=results)

        self.assertEqual(800, metrics["invested"])
        self.assertEqual(15580, metrics["returned"])
        self.assertEqual(8, metrics["hit_ticket_count"])
        self.assertEqual(1.0, metrics["ticket_hit_rate"])
        self.assertEqual("available", metrics["label_status"])
        self.assertEqual(8200, metrics["bet_type_breakdown"]["sanrentan"]["returned"])
        self.assertEqual(24.0, metrics["bet_type_breakdown"]["umatan"]["roi"])

    def test_legacy_win_and_place_columns_are_supported(self):
        results = [
            {
                "race_id": "r1",
                "horse_number": "4",
                "win_payout": "510",
                "place_payout": "180",
            }
        ]

        lookup = evaluator._build_payout_lookup(results)

        self.assertEqual(510, lookup[("r1", "win", "4")])
        self.assertEqual(180, lookup[("r1", "place", "4")])


if __name__ == "__main__":
    unittest.main()
