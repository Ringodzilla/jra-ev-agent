import unittest

from jra_scraper.validation import ENTRY_COLUMNS, OUTPUT_COLUMNS, build_entry_rows, build_row_id, validate_rows


class TestValidation(unittest.TestCase):
    def test_validate_rows_normalizes_and_caps_five(self):
        rows = []
        for i in range(1, 8):
            rows.append(
                {
                    "race_id": "20260329_中山_11",
                    "horse_id": "h1",
                    "horse_name": "A",
                    "run_index": str(i),
                    "date": f"2026/03/0{i}",
                    "race_name": "弥生賞",
                    "course": "芝",
                    "distance": "芝2000",
                    "position": f"{i}着",
                    "time": "1:59.9",
                    "weight": "57.0kg",
                    "jockey": "戸崎",
                    "pace": "36.0",
                    "last_3f": "34.2",
                    "track_condition": "良",
                    "weather": "晴",
                    "target_weather": "雨",
                    "target_track_condition": "重",
                    "target_conditions_captured_at": "2026-03-29T06:30:00+00:00",
                    "assigned_weight": "57.0kg",
                    "current_body_weight": "472kg",
                    "body_weight_change": "-2kg",
                    "body_weight_status": "published",
                    "passing_order": "5-5-4-2",
                    "odds": "3.2倍",
                    "popularity": "1人気",
                }
            )
        rows.append(rows[0].copy())

        validated = validate_rows(rows)
        self.assertEqual(5, len(validated))
        self.assertEqual([str(i) for i in range(1, 6)], [r["run_index"] for r in validated])
        self.assertEqual("2026-03-01", validated[0]["date"])
        self.assertEqual("2000", validated[0]["distance"])
        self.assertEqual("1", validated[0]["position"])
        self.assertEqual("57", validated[0]["weight"])
        self.assertEqual("119.9", validated[0]["time"])
        self.assertEqual("36", validated[0]["pace"])
        self.assertEqual("34.2", validated[0]["last_3f"])
        self.assertEqual("2", validated[0]["passing_order"])
        self.assertEqual("3.2", validated[0]["odds"])
        self.assertEqual("1", validated[0]["popularity"])
        self.assertEqual("雨", validated[0]["target_weather"])
        self.assertEqual("重", validated[0]["target_track_condition"])
        self.assertEqual("2026-03-29T06:30:00+00:00", validated[0]["target_conditions_captured_at"])
        self.assertEqual("57", validated[0]["assigned_weight"])
        self.assertEqual("472", validated[0]["current_body_weight"])
        self.assertEqual("-2", validated[0]["body_weight_change"])
        self.assertEqual("published", validated[0]["body_weight_status"])
        self.assertEqual(set(OUTPUT_COLUMNS), set(validated[0].keys()))

        entries = build_entry_rows(validated)
        self.assertEqual(set(ENTRY_COLUMNS), set(entries[0].keys()))
        self.assertEqual("57", entries[0]["assigned_weight"])
        self.assertEqual("472", entries[0]["current_body_weight"])
        self.assertEqual("-2", entries[0]["body_weight_change"])
        self.assertEqual("published", entries[0]["body_weight_status"])

    def test_validate_rows_defaults_missing_body_weight_to_unpublished(self):
        validated = validate_rows([
            {
                "race_id": "r1",
                "horse_id": "h1",
                "horse_name": "A",
                "run_index": "1",
            }
        ])
        self.assertEqual("", validated[0]["current_body_weight"])
        self.assertEqual("", validated[0]["body_weight_change"])
        self.assertEqual("unpublished", validated[0]["body_weight_status"])

    def test_build_row_id_stable(self):
        row = {
            "race_id": "r1",
            "horse_id": "h1",
            "run_index": "1",
            "date": "2026-01-01",
            "race_name": "X",
            "position": "1",
            "odds": "3.2",
        }
        self.assertEqual(build_row_id(row), build_row_id(row.copy()))


if __name__ == "__main__":
    unittest.main()
