import json
import tempfile
import unittest
from pathlib import Path

try:
    from scripts.run_pipeline import load_race_configs
    from jra_scraper.pipeline import JRAPipeline
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


if __name__ == "__main__":
    unittest.main()
