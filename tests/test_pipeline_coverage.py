from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jra_scraper.config import ScrapeConfig
from jra_scraper.models import HorseEntry, ParserIssue
from jra_scraper.pipeline import (
    JRAPipeline,
    _dedupe_combo_odds_rows,
    _issues_for_selected_history,
    _manual_rows_for_horse,
    _merge_history_rows,
    _rows_from_embedded_history,
    append_live_combo_odds,
    append_live_odds_snapshots,
)


def _config(tmp_path: Path) -> ScrapeConfig:
    return ScrapeConfig(
        output_csv=tmp_path / "race_last5.csv",
        entries_csv=tmp_path / "entries.csv",
        odds_snapshots_csv=tmp_path / "odds.csv",
        combo_odds_csv=tmp_path / "combo.csv",
        raw_dir=tmp_path / "raw",
        state_path=tmp_path / "state.json",
        quality_report_path=tmp_path / "quality.json",
        missing_history_requests_path=tmp_path / "missing.json",
        manual_history_template_csv=tmp_path / "manual-template.csv",
        manual_history_csv=tmp_path / "manual.csv",
        stages_dir=tmp_path / "stages",
        delay_seconds=0,
        max_retries=1,
    )


def _horse(number: int, *, embedded: list[dict[str, str]] | None = None) -> HorseEntry:
    return HorseEntry(
        race_id="R",
        race_name="Race",
        horse_id=f"H{number}",
        horse_name=f"Horse {number}",
        horse_url=f"https://horse/{number}",
        frame_number=str(number),
        horse_number=str(number),
        current_jockey="J",
        assigned_weight="55",
        current_odds="2.0",
        current_popularity=str(number),
        target_track="札幌",
        target_race_date="2026-08-29",
        target_race_number="7",
        target_surface="芝",
        target_distance="1200",
        target_weather="晴",
        target_track_condition="良",
        target_conditions_captured_at="2026-08-29T00:00:00+00:00",
        embedded_history=list(embedded or []),
    )


def _history(horse_id: str, index: int, *, last_3f: str = "35.0") -> dict[str, str]:
    return {
        "race_id": "R",
        "horse_id": horse_id,
        "horse_name": horse_id,
        "run_index": str(index),
        "date": f"2026-0{min(index, 7)}-01",
        "race_name": f"Past {index}",
        "course": "札幌",
        "distance": "1200",
        "position": str(index),
        "last_3f": last_3f,
        "last_3f_source": "observed" if last_3f else "fallback",
    }


def _write_manual(path: Path) -> None:
    rows = [{"horse_id": "H3", "horse_name": "Horse 3", "date": "2026-01-01", "race_name": "Manual", "last_3f": "35.1"}]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_pipeline_no_race_missing_html_and_parse_failure(tmp_path: Path) -> None:
    config = _config(tmp_path)
    pipeline = JRAPipeline(config)
    pipeline.scraper = Mock()
    pipeline.parser = Mock()
    pipeline.scraper.fetch_relative.return_value = None
    assert pipeline.run() == []
    assert pipeline.parser.parse_race_list.call_count == 0

    pipeline.scraper.fetch.return_value = None
    assert pipeline.run(race_specs=[{"race_id": "R1", "source_url": "https://race/1"}], force_rebuild=True) == []

    pipeline.scraper.fetch.return_value = "race html"
    pipeline.parser.parse_race_detail.side_effect = ValueError("bad race")
    assert pipeline.run(race_specs=[{"race_id": "R2", "source_url": "https://race/2"}], force_rebuild=True) == []
    pipeline.close()


def test_pipeline_full_horse_repair_paths(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _write_manual(config.manual_history_csv)
    pipeline = JRAPipeline(config)
    pipeline.scraper = Mock()
    pipeline.parser = Mock()
    embedded = [{"date": f"2026-0{i}-01", "race_name": f"E{i}", "last_3f": ""} for i in range(1, 6)]
    horses = [_horse(1, embedded=embedded), _horse(2), _horse(3), _horse(4), _horse(5)]
    pipeline.parser.parse_race_detail.return_value = horses
    pipeline.scraper.fetch.side_effect = lambda url, **kwargs: "race" if "race" in url else (
        "detail" if url.endswith("/4") or url.endswith("/5") else None
    )
    pipeline.parser.parse_horse_last5.side_effect = lambda html, **kwargs: (
        [] if kwargs["horse_id"] == "H4" else [_history("H5", 1), _history("H5", 2)]
    )

    with patch.object(pipeline, "_fetch_combo_odds_rows", return_value=[]), patch(
        "jra_scraper.pipeline.validate_rows", side_effect=lambda rows: rows
    ), patch(
        "jra_scraper.pipeline.build_entry_rows",
        side_effect=lambda rows: [
            {"race_id": row.get("race_id", ""), "horse_id": row.get("horse_id", ""), "horse_name": row.get("horse_name", ""),
             "horse_number": row.get("horse_number", ""), "current_odds": row.get("current_odds", ""),
             "current_popularity": row.get("current_popularity", ""), "history_count": "1"}
            for row in rows
        ],
    ):
        rows = pipeline.run(
            race_specs=[{"race_id": "R", "race_name": "Race", "source_url": "https://race"}],
            force_rebuild=True,
            horse_limit=5,
        )
    assert any(row.get("horse_id") == "H1" for row in rows)
    assert any(row.get("horse_id") == "H3" for row in rows)
    assert config.missing_history_requests_path.exists()


def test_pipeline_race_resolution_and_combo_odds_paths(tmp_path: Path) -> None:
    pipeline = JRAPipeline(_config(tmp_path))
    pipeline.scraper = Mock()
    pipeline.parser = Mock()
    issues: list[ParserIssue] = []
    assert len(
        pipeline._resolve_races(
            race_specs=[{"race_id": "R1", "source_url": "u1"}, {"race_id": "R2", "source_url": "u2"}],
            race_urls=None, race_limit=1, reprocess_raw=False, issues=issues,
        )
    ) == 1
    assert len(
        pipeline._resolve_races(
            race_specs=None, race_urls=["u1", "u2"], race_limit=1, reprocess_raw=False, issues=issues,
        )
    ) == 1
    pipeline.scraper.fetch_relative.return_value = "race list"
    pipeline.parser.parse_race_list.return_value = [pipeline._race_from_spec({"race_id": "R", "source_url": "u"})]
    assert len(
        pipeline._resolve_races(
            race_specs=None, race_urls=None, race_limit=1, reprocess_raw=True, issues=issues,
        )
    ) == 1

    race = pipeline._race_from_spec({"race_id": "R", "source_url": "u"})
    pipeline.parser.extract_initial_odds_cname.return_value = ""
    assert pipeline._fetch_combo_odds_rows(race, "race", reprocess_raw=False, force_refresh=False, issues=issues) == []
    pipeline.parser.extract_initial_odds_cname.return_value = "first"
    pipeline.scraper.fetch_post.return_value = None
    assert pipeline._fetch_combo_odds_rows(race, "race", reprocess_raw=True, force_refresh=True, issues=issues) == []
    assert issues[-1].code == "odds_page_unavailable"

    pipeline.scraper.fetch_post.side_effect = ["first html", None, "second html"]
    pipeline.parser.parse_odds_page.side_effect = lambda html, **kwargs: [
        {"race_id": "R", "bet_type": "win", "combination": "1", "captured_at": kwargs["captured_at"]}
    ]
    pipeline.parser.extract_odds_cnames.return_value = {"win_place": "first", "wide": "missing", "umaren": "second"}
    rows = pipeline._fetch_combo_odds_rows(race, "race", reprocess_raw=False, force_refresh=True, issues=issues)
    assert len(rows) == 1


def test_append_odds_helpers_cover_empty_existing_and_dedupe(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshots.csv"
    entry = {"race_id": "R", "horse_id": "H", "horse_name": "Horse", "horse_number": "1", "current_odds": "2", "current_popularity": "1"}
    assert append_live_odds_snapshots(snapshot, []) == []
    assert len(append_live_odds_snapshots(snapshot, [entry], captured_at="T")) == 1
    assert append_live_odds_snapshots(snapshot, [entry], captured_at="T") == []

    combo = tmp_path / "combo.csv"
    row = {"race_id": "R", "bet_type": "wide", "combination": "1-2", "odds": "3", "odds_min": "3", "odds_max": "3", "captured_at": "T", "source_cname": "c"}
    assert append_live_combo_odds(combo, []) == []
    assert append_live_combo_odds(combo, [row, dict(row)]) == [row]
    assert append_live_combo_odds(combo, [row]) == []
    assert _dedupe_combo_odds_rows([{}, row, dict(row)]) == [row]


def test_history_helper_tail_edges() -> None:
    horse = _horse(1, embedded=[{"date": "2026-01-01", "race_name": "Past", "last_3f": ""}])
    rows = _rows_from_embedded_history(horse)
    assert rows[0]["last_3f_source"] == "fallback"

    six = [_history("H", index) for index in range(1, 7)]
    assert len(_merge_history_rows([], six, target_race_date="2026-08-29")) == 5
    retained = _issues_for_selected_history([], [{"race_id": "R", "horse_name": "H", "run_index": "1", "last_3f": "", "last_3f_source": "fallback"}])
    assert retained[0].code == "last3f_fallback"

    manual_rows = [
        {"horse_id": "OTHER", "horse_name": "Horse 1"},
        {"horse_id": "", "horse_name": "Other"},
    ]
    assert _manual_rows_for_horse(horse, manual_rows) == []
