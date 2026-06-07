import csv
import tempfile
import unittest
from pathlib import Path

from scripts.append_result_labels import append_rows, rows_from_review


class TestAppendResultLabels(unittest.TestCase):
    def test_rows_from_review_builds_jra_style_labels(self):
        rows = rows_from_review(
            {
                "race": {"date": "2026-05-30", "track": "京都", "race_number": 11},
                "result": {
                    "payouts": [
                        {"bet_type": "単勝", "combination": "2", "payout_yen_per_100": 870},
                        {"bet_type": "ワイド", "combination": "2-12", "payout_yen_per_100": 1480},
                    ]
                },
            }
        )

        self.assertEqual(
            {"race_id": "20260530_京都_11", "式別": "単勝", "組番": "", "馬番": "2", "払戻金": "870"},
            rows[0],
        )
        self.assertEqual(
            {"race_id": "20260530_京都_11", "式別": "ワイド", "組番": "2-12", "馬番": "", "払戻金": "1480"},
            rows[1],
        )

    def test_append_rows_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "result_labels.csv"
            rows = [
                {"race_id": "r1", "式別": "単勝", "組番": "", "馬番": "2", "払戻金": "870"},
            ]

            self.assertEqual(1, len(append_rows(path, rows)))
            self.assertEqual(0, len(append_rows(path, rows)))

            with path.open(encoding="utf-8", newline="") as file_obj:
                stored = list(csv.DictReader(file_obj))
            self.assertEqual(1, len(stored))

    def test_rows_from_review_builds_win5_label_without_race_metadata(self):
        rows = rows_from_review(
            {
                "result": {
                    "win5": {
                        "numbers": ["8", "4", "6", "2", "9"],
                        "payout_yen_per_100": "3159870",
                    }
                }
            }
        )

        self.assertEqual(
            {"race_id": "WIN5", "式別": "WIN5", "組番": "8-4-6-2-9", "馬番": "", "払戻金": "3159870"},
            rows[0],
        )


if __name__ == "__main__":
    unittest.main()
