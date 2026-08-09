import csv
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    from jra_scraper.config import ScrapeConfig
    from scripts.run_pipeline import _artifact_dir_for_run, _race_artifact_id, load_race_configs
    from jra_scraper.pipeline import (
        JRAPipeline,
        _issues_for_selected_history,
        _manual_rows_for_horse,
        _merge_history_rows,
        _missing_history_request,
        _raw_file_captured_at,
    )
    from jra_scraper.data_repair import MissingHistoryRepairAction
    from jra_scraper.models import ParserIssue
    from jra_scraper.validation import OUTPUT_COLUMNS
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
                missing_history_requests_path=root / "missing_history.json",
                manual_history_csv=root / "manual_history.csv",
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
                missing_history_requests_path=root / "missing_history.json",
                manual_history_csv=root / "manual_history.csv",
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

    def test_processed_race_skip_does_not_append_fresh_odds_snapshot(self):
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
                missing_history_requests_path=root / "missing.json",
                manual_history_csv=root / "manual.csv",
                stages_dir=root / "stages",
            )
            pipeline = JRAPipeline(config)
            pipeline._write_csv(
                [
                    {
                        "race_id": "R1",
                        "horse_id": "H1",
                        "horse_name": "A",
                        "horse_number": "1",
                        "current_odds": "5.0",
                        "current_popularity": "1",
                        "run_index": "1",
                        "date": "2026-01-01",
                        "race_name": "前走",
                    }
                ],
                config.output_csv,
                OUTPUT_COLUMNS,
            )
            config.state_path.write_text(
                json.dumps({"processed_race_ids": ["R1"], "failures": {}}),
                encoding="utf-8",
            )

            pipeline.run(race_specs=[{"race_id": "R1", "source_url": "https://example.test/race"}])
            pipeline.close()

            self.assertFalse(config.odds_snapshots_csv.exists())

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

    def test_merge_history_rows_fills_missing_fifth_run(self):
        embedded = [
            {"run_index": str(index), "date": f"2026-0{6-index}-01", "race_name": f"R{index}", "course": "東京"}
            for index in range(1, 5)
        ]
        detail = embedded + [
            {"run_index": "5", "date": "2025-12-01", "race_name": "R5", "course": "東京"}
        ]

        merged = _merge_history_rows(embedded, detail)

        self.assertEqual(5, len(merged))
        self.assertEqual("R5", merged[-1]["race_name"])
        self.assertEqual(["1", "2", "3", "4", "5"], [row["run_index"] for row in merged])

    def test_merge_history_rows_dedupes_same_run_with_different_race_labels(self):
        embedded = [
            {
                "run_index": "1",
                "date": "2026年4月18日",
                "race_name": "1勝クラス",
                "course": "中山",
                "distance": "1800芝",
                "last_3f": "33.1",
                "last_3f_source": "embedded",
            }
        ]
        detail = [
            {
                "run_index": "1",
                "date": "2026-04-18",
                "race_name": "3歳1勝クラス",
                "course": "中山",
                "distance": "芝1800",
                "last_3f": "36.0",
                "last_3f_source": "fallback",
            },
            {
                "run_index": "2",
                "date": "2026-02-01",
                "race_name": "未勝利",
                "course": "東京",
                "last_3f": "36.0",
                "last_3f_source": "fallback",
            },
        ]

        merged = _merge_history_rows(embedded, detail)

        self.assertEqual(2, len(merged))
        self.assertEqual("33.1", merged[0]["last_3f"])
        self.assertEqual("embedded", merged[0]["last_3f_source"])
        self.assertEqual("2026-02-01", merged[1]["date"])

    def test_merge_history_rows_replaces_fallback_with_manual_observation(self):
        rows = [
            {
                "date": f"2026-0{5-index}-01",
                "race_name": f"R{index}",
                "last_3f": "36.0",
                "last_3f_source": "fallback",
            }
            for index in range(5)
        ] + [
            {
                "date": "2026年1月1日",
                "race_name": "別表記R4",
                "last_3f": "34.2",
                "last_3f_source": "manual",
            },
        ]

        merged = _merge_history_rows([], rows)

        self.assertEqual(5, len(merged))
        self.assertEqual("34.2", merged[4]["last_3f"])
        self.assertEqual("manual", merged[4]["last_3f_source"])

    def test_merge_history_rows_ignores_empty_records(self):
        self.assertEqual([], _merge_history_rows([], [{"last_3f": "36.0"}]))

    def test_merge_history_rows_excludes_target_race_result(self):
        detail = [
            {"date": "2026年6月21日", "race_name": "対象レース", "course": "東京"},
            {"date": "2026年5月1日", "race_name": "前走", "course": "東京"},
        ]

        merged = _merge_history_rows([], detail, target_race_date="2026-06-21")

        self.assertEqual(["前走"], [row["race_name"] for row in merged])

    def test_selected_history_issues_ignore_discarded_detail_fallbacks(self):
        detail_issues = [
            ParserIssue(
                stage="parser.horse_history",
                severity="medium",
                code="history_header_missing",
                message="Missing expected history header: last_3f",
            ),
            *[
                ParserIssue(
                    stage="parser.horse_history",
                    severity="medium",
                    code="last3f_fallback",
                    message="fallback",
                    context={"run_index": str(index)},
                )
                for index in range(1, 6)
            ],
        ]
        selected_rows = [
            {
                "race_id": "r1",
                "horse_name": "A",
                "run_index": str(index),
                "last_3f": "34.5",
                "last_3f_source": "embedded",
            }
            for index in range(1, 5)
        ] + [
            {
                "race_id": "r1",
                "horse_name": "A",
                "run_index": "5",
                "last_3f": "36.0",
                "last_3f_source": "fallback",
            }
        ]

        issues = _issues_for_selected_history(detail_issues, selected_rows)

        self.assertEqual(1, sum(issue.code == "last3f_fallback" for issue in issues))
        self.assertEqual(1, sum(issue.code == "history_header_missing" for issue in issues))

    def test_quality_report_distinguishes_observed_fallback_and_missing_last3f(self):
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
                missing_history_requests_path=root / "missing.json",
                manual_history_csv=root / "manual.csv",
                stages_dir=root / "stages",
            )
            pipeline = JRAPipeline(config)
            issues = [
                ParserIssue(
                    stage="pipeline.history",
                    severity="medium",
                    code="last3f_fallback",
                    message="fallback",
                )
            ]
            history_rows = [{"last_3f": "34.5"}, {"last_3f": "36.0"}, {"last_3f": ""}]

            pipeline._write_quality_report(
                issues,
                [],
                history_rows,
                [
                    {
                        "type": "history_gap_repair",
                        "reason": "history_sources_exhausted",
                        "attempted_urls": ["https://example.test/h1"],
                    }
                ],
            )
            report = json.loads(config.quality_report_path.read_text(encoding="utf-8"))
            pipeline.close()

        self.assertEqual(3, report["history_row_count"])
        self.assertEqual(1, report["last3f_observed_rows"])
        self.assertEqual(1, report["last3f_fallback_rows"])
        self.assertEqual(1, report["last3f_missing_rows"])
        self.assertEqual(0.333333, report["last3f_observed_rate"])
        self.assertEqual(
            ["https://example.test/h1"],
            report["missing_data_repair_actions"][0]["attempted_urls"],
        )

    def test_manual_history_rows_are_enriched_for_current_entry(self):
        horse = SimpleNamespace(
            race_id="r1", horse_id="h1", horse_name="A", horse_url="https://example.test/h1",
            frame_number="1", horse_number="2", current_jockey="騎手", assigned_weight="55",
            current_odds="8.0", current_popularity="4", target_track="東京",
            target_race_date="2026-06-21", target_race_number="11", target_surface="芝",
            target_distance="1800", target_weather="曇", target_track_condition="稍重",
            target_conditions_captured_at="2026-06-21T06:00:00+00:00", horse_country="",
        )

        rows = _manual_rows_for_horse(
            horse,
            [{"horse_id": "h1", "horse_name": "A", "date": "2025-06-22", "race_name": "府中牝馬S"}],
        )

        self.assertEqual(1, len(rows))
        self.assertEqual("r1", rows[0]["race_id"])
        self.assertEqual("稍重", rows[0]["target_track_condition"])

    def test_missing_history_request_uses_neutral_fallback(self):
        horse = SimpleNamespace(
            race_id="r1", horse_id="h1", horse_name="A", horse_url="https://example.test/h1"
        )

        request = _missing_history_request(horse, history_count=4, manual_history_csv=Path("manual.csv"))

        self.assertEqual(1, request["missing_count"])
        self.assertEqual(0.5, request["fallback_score"])
        self.assertEqual("user_unavailable_or_manual_data_not_stored", request["fallback_reason"])
        self.assertEqual(["https://example.test/h1"], request["attempted_urls"])

    def test_missing_history_repair_action_prepares_manual_template(self):
        horse = SimpleNamespace(
            race_id="r1", horse_id="h1", horse_name="A", horse_url="https://example.test/h1"
        )
        action = MissingHistoryRepairAction(
            manual_history_csv=Path("manual.csv"),
            manual_template_csv=Path("manual_template.csv"),
        )

        result = action.build_result(
            horse,
            rows=[{"date": "2026-01-01"}, {"date": "2025-12-01"}, {"date": "2025-11-01"}],
            reason="history_sources_exhausted",
            source_counts={"embedded": 1, "detail": 2, "manual": 0},
        )

        self.assertTrue(result.requires_manual_input)
        self.assertEqual("manual_required", result.repair_actions[0]["status"])
        self.assertEqual(2, result.manual_requests[0]["missing_count"])
        self.assertEqual("history_sources_exhausted", result.manual_requests[0]["fallback_reason"])
        self.assertEqual(["https://example.test/h1"], result.manual_requests[0]["attempted_urls"])
        self.assertEqual(["https://example.test/h1"], result.repair_actions[0]["attempted_urls"])
        self.assertEqual(2, len(result.manual_template_rows))
        self.assertEqual("h1", result.manual_template_rows[0]["horse_id"])

    def test_write_manual_history_template_outputs_fillable_csv(self):
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
                missing_history_requests_path=root / "missing.json",
                manual_history_template_csv=root / "manual_template.csv",
                manual_history_csv=root / "manual.csv",
                stages_dir=root / "stages",
            )
            pipeline = JRAPipeline(config)

            pipeline._write_manual_history_template(
                [{"horse_id": "h1", "horse_name": "A", "date": ""}]
            )
            with config.manual_history_template_csv.open(encoding="utf-8") as file_obj:
                rows = list(csv.DictReader(file_obj))
            pipeline.close()

        self.assertEqual(1, len(rows))
        self.assertEqual("h1", rows[0]["horse_id"])
        self.assertIn("last_3f", rows[0])
        self.assertNotIn("_missing_slot", rows[0])

    def test_write_manual_history_template_dedupes_same_missing_slot(self):
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
                missing_history_requests_path=root / "missing.json",
                manual_history_template_csv=root / "manual_template.csv",
                manual_history_csv=root / "manual.csv",
                stages_dir=root / "stages",
            )
            pipeline = JRAPipeline(config)

            pipeline._write_manual_history_template(
                [
                    {"horse_id": "h1", "horse_name": "A", "_missing_slot": "1"},
                    {"horse_id": "h1", "horse_name": "A", "_missing_slot": "2"},
                    {"horse_id": "h1", "horse_name": "A", "_missing_slot": "1"},
                ]
            )
            with config.manual_history_template_csv.open(encoding="utf-8") as file_obj:
                rows = list(csv.DictReader(file_obj))
            pipeline.close()

        self.assertEqual(2, len(rows))
        self.assertEqual(["h1", "h1"], [row["horse_id"] for row in rows])

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
