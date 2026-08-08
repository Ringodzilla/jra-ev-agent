from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zoneinfo import ZoneInfo

from jra_scraper.config import ScrapeConfig
from jra_scraper.live_snapshot import LiveSnapshotCollector, _combination_coverage
from jra_scraper.models import HorseEntry


JST = ZoneInfo("Asia/Tokyo")


class FakeScraper:
    def __init__(self, config):
        self.config = config
        self.get_calls = []
        self.post_calls = []

    def fetch(self, url, raw_name=None, *, use_cache=True):
        self.get_calls.append(url)
        return "<html><body>race</body></html>"

    def fetch_post(self, url, data, raw_name=None, *, use_cache=True):
        self.post_calls.append(dict(data))
        return "<html><body>15時17分現在オッズ</body></html>"

    def close(self):
        return None


class FakeParser:
    CNAMES = {
        "win_place": "c_win",
        "wakuren": "c_wakuren",
        "umaren": "c_umaren",
        "wide": "c_wide",
        "umatan": "c_umatan",
        "sanrenpuku": "c_sanrenpuku",
        "sanrentan": "c_sanrentan",
    }

    def parse_race_detail(self, html, race_id, race_name, **kwargs):
        return [
            HorseEntry(
                race_id=race_id,
                race_name=race_name,
                horse_id="h1",
                horse_name="Horse A",
                horse_url="https://example.test/horse/h1",
                frame_number="1",
                horse_number="1",
                current_body_weight="472",
                body_weight_change="-2",
                body_weight_status="published",
                target_track="札幌",
                target_race_date="2026-08-08",
                target_race_number="11",
                target_surface="ダート",
                target_distance="1700",
                target_weather="雨",
                target_track_condition="稍重",
                target_conditions_captured_at=kwargs["target_conditions_captured_at"],
            )
        ]

    def extract_initial_odds_cname(self, html):
        return "c_win"

    def extract_odds_cnames(self, html):
        return dict(self.CNAMES)

    def parse_odds_page(self, html, *, race_id, source_cname, captured_at):
        page = next(key for key, cname in self.CNAMES.items() if cname == source_cname)
        types = ("win", "place") if page == "win_place" else (page,)
        return [
            {
                "race_id": race_id,
                "bet_type": bet_type,
                "combination": "1",
                "odds": "3.2",
                "odds_min": "3.2",
                "odds_max": "3.2",
                "captured_at": captured_at,
                "source_cname": source_cname,
            }
            for bet_type in types
        ]


class LiveSnapshotCollectorTest(unittest.TestCase):
    def test_collects_body_conditions_and_all_odds_without_horse_history_fetch(self):
        now = datetime(2026, 8, 8, 15, 18, tzinfo=JST)
        with TemporaryDirectory() as directory:
            config = ScrapeConfig(raw_dir=Path(directory) / "raw", delay_seconds=0, max_retries=1)
            collector = LiveSnapshotCollector(config, now=lambda: now)
            fake_scraper = FakeScraper(config)
            collector.scraper = fake_scraper
            collector.parser = FakeParser()

            result = collector.collect(
                {
                    "race_id": "20260808_札幌_11",
                    "race_name": "エルムS",
                    "race_date": "2026-08-08",
                    "track": "札幌",
                    "race_number": 11,
                    "surface": "ダート",
                    "distance": "1700",
                    "source_url": "https://example.test/race",
                },
                deadline_at=datetime(2026, 8, 8, 15, 20, tzinfo=JST),
            )

        self.assertEqual(["https://example.test/race"], fake_scraper.get_calls)
        self.assertEqual(7, len(fake_scraper.post_calls))
        self.assertTrue(result["snapshot_complete"])
        self.assertEqual("472", result["entries"][0]["current_body_weight"])
        self.assertEqual("雨", result["conditions"]["weather"])
        self.assertEqual([], result["quality_report"]["bet_types_missing"])
        self.assertTrue(result["quality_report"]["official_odds_timestamps_complete"])

    def test_each_bet_type_one_row_is_not_a_complete_snapshot(self):
        horses = [
            HorseEntry(
                race_id="R1", race_name="R", horse_id=f"h{number}", horse_name=f"H{number}",
                horse_url="", horse_number=str(number), frame_number=str(number),
            )
            for number in range(1, 4)
        ]
        rows = [
            {"bet_type": bet_type, "combination": "1"}
            for bet_type in ("place", "wide", "wakuren", "umaren", "umatan", "sanrenpuku", "sanrentan")
        ] + [
            {"bet_type": "win", "combination": str(number)} for number in range(1, 4)
        ]

        coverage = _combination_coverage(horses, rows)

        self.assertFalse(coverage["complete"])
        self.assertGreater(coverage["missing_counts"]["sanrentan"], 0)


if __name__ == "__main__":
    unittest.main()
