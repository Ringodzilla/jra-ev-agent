import json
import os
import tempfile
import unittest
from pathlib import Path

try:
    from jra_scraper.config import ScrapeConfig
    from scripts.run_pipeline import _artifact_dir_for_run, _race_artifact_id, load_race_configs
    from jra_scraper.pipeline import JRAPipeline, _raw_file_captured_at
    HAS_RUN_PIPELINE = True
except ModuleNotFoundError:
    HAS_RUN_PIPELINE = False


@unittest.skipUnless(HAS_RUN_PIPELINE, "run_pipeline dependencies are not installed")
class TestPipelineEntrypoints(unittest.TestCase):
    def test_load_race_configs_requires_keys(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "races.json"
            p.write_text(json.dumps([{"race_name": "x"}], ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_race_configs(p)

    def test_race_spec_accepts_documented_surface_and_distance_keys(self):
        pipeline = JRAPipeline()
        race = pipeline._race_from_spec(
            {
                "race_name": "サンプルレース",
                "race_date": "2026-04-12",
                "track": "阪神",
                "race_number": 11,
                "source_url": "https://example.test/race",
                "surface": "芝",
                "distance": "1600",
            }
        )

        self.assertEqual("芝", race.target_surface)
        self.assertEqual("1600", race.target_distance)

    def test_force_rebuild_refreshes_race_page(self):
        class FakeScraper:
            def __init__(self):
                self.calls = []

            def fetch(self, url, raw_name=None, *, use_cache=True, cache_only=False):
                self.calls.append({"url": url, "use_cache": use_cache, "cache_only": cache_only})
                return "<html></html>"

            def close(self):
                return None

        class FakeParser:
            def __init__(self):
                self.condition_timestamp = ""

            def parse_race_detail(self, html, race_id, race_name, **kwargs):
                self.condition_timestamp = kwargs.get("target_conditions_captured_at", "")
                return []

            def extract_initial_odds_cname(self, html):
                return ""

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = ScrapeConfig(
                output_csv=root / "race_last5.csv",
                entries_csv=root / "entries.csv",
                odds_snapshots_csv=root / "odds.csv",
                combo_odds_csv=root / "combo.csv",
                raw_dir=root / "raw",
                state_path=root / "state.json",
                quality_report_path=root / "quality.json",
                stages_dir=root / "stages",
            )
            pipeline = JRAPipeline(config)
            scraper = FakeScraper()
            parser = FakeParser()
            pipeline.scraper = scraper
            pipeline.parser = parser

            pipeline.run(
                race_specs=[
                    {
                        "race_name": "サンプルレース",
                        "race_date": "2026-06-21",
                        "track": "東京",
                        "race_number": 11,
                        "source_url": "https://example.test/race",
                    }
                ],
                force_rebuild=True,
            )

            self.assertFalse(scraper.calls[0]["use_cache"])
            self.assertEqual("", parser.condition_timestamp)

    def test_force_rebuild_reprocess_raw_still_reads_cache(self):
        class FakeScraper:
            def __init__(self):
                self.calls = []

            def fetch(self, url, raw_name=None, *, use_cache=True, cache_only=False):
                self.calls.append({"use_cache": use_cache, "cache_only": cache_only})
                return "<html></html>"

            def close(self):
                return None

        class FakeParser:
            def parse_race_detail(self, html, race_id, race_name, **kwargs):
                return []

            def extract_initial_odds_cname(self, html):
                return ""

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = ScrapeConfig(
                output_csv=root / "race_last5.csv",
                entries_csv=root / "entries.csv",
                odds_snapshots_csv=root / "odds.csv",
                combo_odds_csv=root / "combo.csv",
                raw_dir=root / "raw",
                state_path=root / "state.json",
                quality_report_path=root / "quality.json",
                stages_dir=root / "stages",
            )
            pipeline = JRAPipeline(config)
            scraper = FakeScraper()
            pipeline.scraper = scraper
            pipeline.parser = FakeParser()

            pipeline.run(
                race_specs=[{"source_url": "https://example.test/race"}],
                force_rebuild=True,
                reprocess_raw=True,
            )

            self.assertTrue(scraper.calls[0]["use_cache"])
            self.assertTrue(scraper.calls[0]["cache_only"])

    def test_raw_file_captured_at_uses_file_mtime(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "race.html"
            path.write_text("cached", encoding="utf-8")
            timestamp = 1_750_000_000
            os.utime(path, (timestamp, timestamp))

            self.assertEqual(
                "2025-06-15T15:06:40+00:00",
                _raw_file_captured_at(path),
            )

    def test_race_artifact_id_prefers_stable_race_metadata(self):
        artifact_id = _race_artifact_id(
            {
                "race_date": "2026-05-30",
                "track": "京都",
                "race_number": 11,
                "output_slug": "sample",
            },
            [],
        )

        self.assertEqual("20260530_京都_11", artifact_id)

    def test_artifact_dir_uses_mode_partition_for_win5(self):
        artifact_dir = _artifact_dir_for_run(
            Path("/repo"),
            [
                {
                    "race_date": "2026-06-07",
                    "track": "東京",
                    "race_number": 9,
                    "output_slug": "win5-leg-1",
                }
            ],
            [],
            mode="win5_under_10",
        )

        self.assertEqual(Path("/repo/report/win5/20260607/win5_under_10"), artifact_dir)

    def test_artifact_dir_keeps_regular_race_layout(self):
        artifact_dir = _artifact_dir_for_run(
            Path("/repo"),
            [
                {
                    "race_date": "2026-06-07",
                    "track": "東京",
                    "race_number": 11,
                    "output_slug": "yasuda",
                }
            ],
            [],
            mode="balanced",
        )

        self.assertEqual(Path("/repo/report/races/20260607_東京_11"), artifact_dir)


if __name__ == "__main__":
    unittest.main()
