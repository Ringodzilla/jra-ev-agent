from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from src.deadline import DeadlineSettings, build_deadline_plan


JST = ZoneInfo("Asia/Tokyo")


class DeadlinePlanTest(unittest.TestCase):
    def setUp(self):
        self.race = {"race_date": "2026-08-08", "post_time": "15:25"}
        self.settings = DeadlineSettings()

    def test_execution_mode_transitions_around_t5(self):
        normal = build_deadline_plan(
            self.race,
            now=datetime(2026, 8, 8, 15, 4, tzinfo=JST),
            settings=self.settings,
        )
        fast = build_deadline_plan(
            self.race,
            now=datetime(2026, 8, 8, 15, 12, tzinfo=JST),
            settings=self.settings,
        )
        emergency = build_deadline_plan(
            self.race,
            now=datetime(2026, 8, 8, 15, 19, tzinfo=JST),
            settings=self.settings,
        )
        too_late = build_deadline_plan(
            self.race,
            now=datetime(2026, 8, 8, 15, 20, tzinfo=JST),
            settings=self.settings,
        )
        insufficient = build_deadline_plan(
            self.race,
            now=datetime(2026, 8, 8, 15, 19, 40, tzinfo=JST),
            settings=self.settings,
        )

        self.assertEqual("normal", normal.execution_mode)
        self.assertEqual("fast", fast.execution_mode)
        self.assertEqual("emergency", emergency.execution_mode)
        self.assertTrue(emergency.may_start_network_refresh)
        self.assertEqual("emergency", insufficient.execution_mode)
        self.assertFalse(insufficient.may_start_network_refresh)
        self.assertEqual("too_late", too_late.execution_mode)
        self.assertFalse(too_late.may_start_network_refresh)

    def test_post_time_is_required(self):
        with self.assertRaises(ValueError):
            build_deadline_plan({"race_date": "2026-08-08"}, settings=self.settings)


if __name__ == "__main__":
    unittest.main()
