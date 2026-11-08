"""Pipeline/agent wiring: attempts recorded, France pass merged, audit exposed."""
from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import jra_scraper
from jra_scraper.config import ScrapeConfig
from jra_scraper.models import HorseEntry
from jra_scraper.pipeline import JRAPipeline


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


def _horse(horse_id: str, number: str = "1") -> HorseEntry:
    return HorseEntry(
        race_id="R",
        race_name="Race",
        horse_id=horse_id,
        horse_name=f"Horse {horse_id}",
        horse_url=f"https://horse/{horse_id}",
        frame_number=number,
        horse_number=number,
        current_jockey="J",
        assigned_weight="55",
        current_odds="2.0",
        current_popularity=number,
        target_track="東京",
        target_race_date="2026-08-29",
        target_race_number="11",
        target_surface="芝",
        target_distance="2400",
        target_weather="晴",
        target_track_condition="良",
        target_conditions_captured_at="2026-08-29T00:00:00+00:00",
    )


def _france_row(horse_id: str, *, time: str = "") -> dict[str, str]:
    return {
        "race_id": "R", "horse_id": horse_id, "horse_name": f"Horse {horse_id}",
        "horse_number": "1", "run_index": "1", "date": "2025年10月5日",
        "course": "ロンシャン", "race_name": "凱旋門賞", "distance": "2400",
        "history_surface": "芝", "track_condition": "重", "position": "3",
        "time": time, "last_3f": "", "last_3f_source": "", "last_3f_status": "",
        "passing_order": "", "popularity": "5", "weight": "58", "jockey": "J",
        "pace": "", "weather": "曇", "odds": "10.0",
    }


def _entry_rows(rows):
    return [
        {"race_id": row.get("race_id", ""), "horse_id": row.get("horse_id", ""),
         "horse_name": row.get("horse_name", ""), "horse_number": row.get("horse_number", ""),
         "current_odds": row.get("current_odds", ""),
         "current_popularity": row.get("current_popularity", ""), "history_count": "1"}
        for row in rows
    ]


def _run(pipeline: JRAPipeline):
    with patch.object(pipeline, "_fetch_combo_odds_rows", return_value=[]), patch(
        "jra_scraper.pipeline.validate_rows", side_effect=lambda rows: rows
    ), patch("jra_scraper.pipeline.build_entry_rows", side_effect=_entry_rows):
        return pipeline.run(
            race_specs=[{"race_id": "R", "race_name": "Race", "source_url": "https://race/R"}],
            force_rebuild=True,
        )


def _pipeline(tmp_path: Path, horses, detail_rows, horse_html) -> JRAPipeline:
    pipeline = JRAPipeline(_config(tmp_path))
    pipeline.scraper = Mock()
    pipeline.parser = Mock()
    pipeline.parser.parse_race_detail.return_value = horses

    def fetch(url, **kwargs):
        if url == "https://race/R":
            return "race html"
        return horse_html.get(url)

    pipeline.scraper.fetch.side_effect = fetch
    pipeline.parser.parse_horse_last5.side_effect = (
        lambda html, **kwargs: detail_rows.get(kwargs["horse_id"], []))
    return pipeline


@pytest.fixture
def france_stub(monkeypatch):
    """Stub jra_scraper.france_galop per the coordinator's API contract."""
    module = types.ModuleType("jra_scraper.france_galop")
    module.is_france_history_row = lambda row: "ロンシャン" in str(row.get("course", ""))
    calls: list[dict] = []

    def collect(row, *, official_horse_name, fetch_bytes, fetch_identity, store_dir):
        calls.append({"horse_id": row.get("horse_id"), "name": official_horse_name})
        horse_id = str(row.get("horse_id", ""))
        return {
            "artifact": {"kind": "france_tracking_pdf", "sha256": "f" * 64,
                         "html": "extracted tracking text", "date": "2025-10-05",
                         "race_id": "R", "horse_id": horse_id,
                         "horse_name": row.get("horse_name", ""),
                         "url": "https://www7.france-galop.com/tracking.pdf"},
            "observation": {
                "race_id": "R", "horse_id": horse_id,
                "horse_name": row.get("horse_name", ""), "horse_number": "1",
                "date": "2025年10月5日", "course": "ロンシャン", "race_name": "凱旋門賞",
                "time": "2:25.4", "last_3f": "34.5", "last_3f_status": "OBSERVED",
                "last_3f_source": "official_france_galop_tracking_last600m",
            },
            "attempt": {"route": "france_galop_tracking", "race_id": "R",
                        "horse_id": horse_id, "horse_number": "1", "run_index": "1",
                        "date": "2025-10-05", "fields": ["time", "last_3f"],
                        "outcome": "observed", "reason": "",
                        "artifact_sha256": "f" * 64},
        }

    module.collect_france_history = collect
    module.load_identity_sources = lambda path: {}
    module.identity_fetcher_from_map = (
        lambda mapping, fetch_text: (lambda date_iso, jp_race_name, horse_name: None))
    module.calls = calls
    monkeypatch.setitem(sys.modules, "jra_scraper.france_galop", module)
    monkeypatch.setattr(jra_scraper, "france_galop", module, raising=False)
    return module


def test_horse_fetch_failure_is_recorded(tmp_path):
    pipeline = _pipeline(tmp_path, [_horse("H1")], {}, {})
    rows = _run(pipeline)
    assert rows == []
    attempts = pipeline.acquisition_recorder.attempts()
    horse_attempts = [a for a in attempts if a["route"] == "jra_horse_history"
                      and a["horse_id"] == "H1" and a["outcome"] == "unavailable"]
    assert horse_attempts and horse_attempts[0]["fields"]
    assert any(a["route"] == "jra_race_detail" and a["outcome"] == "fetched"
               for a in attempts)
    assert any(a["route"] == "manual_history_overrides" and a["outcome"] == "consulted"
               for a in attempts)
    assert any(a["outcome"] == "horse_detail_html_unavailable" for a in attempts)


def test_france_pass_merges_observation_and_writes_descriptor(tmp_path, france_stub):
    (tmp_path / "foreign_horse_names.json").write_text(
        json.dumps({"HF": "BYZANTINE DREAM", "HK": "KEPT NAME"}), encoding="utf-8")
    horses = [_horse("HF", "1"), _horse("HK", "2")]
    detail = {"HF": [_france_row("HF")], "HK": [_france_row("HK", time="2:20.0")]}
    html = {horse.horse_url: "detail html" for horse in horses}
    pipeline = _pipeline(tmp_path, horses, detail, html)
    rows = _run(pipeline)

    by_horse = {row["horse_id"]: row for row in rows}
    # Empty time is filled from the France total time; an existing time is not.
    assert by_horse["HF"]["time"] == "2:25.4"
    assert by_horse["HF"]["last_3f"] == "34.5"
    assert by_horse["HK"]["time"] == "2:20.0"

    assert {call["horse_id"] for call in france_stub.calls} == {"HF", "HK"}
    descriptors = sorted((tmp_path / "raw" / "foreign_artifacts").glob("*.json"))
    assert [path.name for path in descriptors] == [
        "france_tracking_2025-10-05_HF.json", "france_tracking_2025-10-05_HK.json"]
    descriptor = json.loads(descriptors[0].read_text(encoding="utf-8"))
    assert descriptor["kind"] == "france_tracking_pdf"

    attempts = pipeline.acquisition_recorder.attempts()
    assert any(a["route"] == "france_galop_tracking" and a["outcome"] == "observed"
               for a in attempts)

    # The run wrote a SHA-bound acquisition bundle and the quality report refs it.
    assert len(pipeline.last_acquisition_reports) == 1
    ref = pipeline.last_acquisition_reports[0]
    assert ref["race_id"] == "R" and Path(ref["path"]).is_file()
    report = json.loads(Path(ref["path"]).read_text(encoding="utf-8"))
    assert report["role"] == "data_collector"
    assert report["evidence_files"] == ["artifact_index.json"]
    quality = json.loads((tmp_path / "quality.json").read_text(encoding="utf-8"))
    assert quality["acquisition_reports"] == [ref]


def test_france_identity_unresolved_never_calls_collect(tmp_path, france_stub):
    def forbidden(*args, **kwargs):
        raise AssertionError("collect must not run without an operator-supplied name")

    france_stub.collect_france_history = forbidden
    pipeline = _pipeline(tmp_path, [_horse("HX")], {"HX": [_france_row("HX")]},
                         {"https://horse/HX": "detail html"})
    rows = _run(pipeline)
    assert rows and rows[0]["time"] == ""
    attempts = pipeline.acquisition_recorder.attempts()
    assert any(a["route"] == "france_galop_tracking"
               and a["outcome"] == "identity_unresolved"
               and a["horse_id"] == "HX" for a in attempts)


def test_france_route_error_is_recorded_not_fatal(tmp_path, france_stub):
    def boom(*args, **kwargs):
        raise RuntimeError("tracking endpoint exploded")

    (tmp_path / "foreign_horse_names.json").write_text(
        json.dumps({"HE": "ERROR HORSE"}), encoding="utf-8")
    france_stub.collect_france_history = boom
    pipeline = _pipeline(tmp_path, [_horse("HE")], {"HE": [_france_row("HE")]},
                         {"https://horse/HE": "detail html"})
    rows = _run(pipeline)
    assert rows and rows[0]["time"] == ""
    attempts = pipeline.acquisition_recorder.attempts()
    assert any(a["route"] == "france_galop_tracking" and a["outcome"] == "error"
               and "tracking endpoint exploded" in a["reason"] for a in attempts)


def test_data_collector_exposes_source_audit(tmp_path):
    from src.agents.data_collector import DataCollectorAgent

    config = _config(tmp_path)
    domestic = {"HD": [{
        "race_id": "R", "horse_id": "HD", "horse_name": "Horse HD", "horse_number": "1",
        "run_index": "1", "date": "2026-08-01", "course": "東京", "race_name": "Past",
        "distance": "1600", "position": "1", "last_3f": "34.0",
        "last_3f_source": "observed",
    }]}
    pipeline = _pipeline(tmp_path, [_horse("HD")], domestic,
                         {"https://horse/HD": "detail html"})
    pipeline.parser.extract_initial_odds_cname.return_value = ""
    agent = DataCollectorAgent(config)
    with patch("src.agents.data_collector.JRAPipeline", lambda cfg: pipeline), patch(
        "src.agents.data_collector.capture_environment", lambda *a, **k: {}
    ):
        result = _run_agent(agent)
    attempts = result["source_audit"]["attempts"]
    assert any(a["route"] == "jra_horse_history" and a["horse_id"] == "HD"
               for a in attempts)
    reports = result["source_audit"]["acquisition_reports"]
    assert len(reports) == 1 and reports[0]["race_id"] == "R"
    assert Path(reports[0]["path"]).is_file()


def _run_agent(agent):
    with _agent_patches():
        return agent.run(
            [{"race_id": "R", "race_name": "Race", "source_url": "https://race/R"}],
            force_rebuild=True,
        )


class _agent_patches:
    """Pipeline-internal patches the collector run needs (mirrors _run)."""

    def __enter__(self):
        self._stack = []
        for target, kwargs in (
            ("jra_scraper.pipeline.validate_rows", {"side_effect": lambda rows: rows}),
            ("jra_scraper.pipeline.build_entry_rows", {"side_effect": _entry_rows}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self._stack.append(patcher)
        return self

    def __exit__(self, *exc):
        for patcher in reversed(self._stack):
            patcher.stop()
        return False


def test_attach_history_sources_picks_up_foreign_descriptors(tmp_path):
    from src.skill_workflow import attach_history_sources

    raw_dir = tmp_path / "raw"
    foreign_dir = raw_dir / "foreign_artifacts"
    foreign_dir.mkdir(parents=True)
    artifact = {"kind": "france_tracking_pdf", "sha256": "a" * 64,
                "html": "extracted tracking text", "race_id": "R", "horse_id": "H1",
                "horse_name": "Horse H1", "date": "2025-10-05",
                "url": "https://www7.france-galop.com/tracking.pdf"}
    (foreign_dir / "france_tracking_2025-10-05_H1.json").write_text(
        json.dumps(artifact), encoding="utf-8")
    (foreign_dir / "broken.json").write_text("not json", encoding="utf-8")
    collected = {"rows": [{"race_id": "R", "horse_id": "H1"}],
                 "source_audit": {"attempts": [{"route": "jra_horse_history"}]}}
    attach_history_sources(collected, raw_dir)
    artifacts = collected["source_audit"]["artifacts"]
    assert artifacts == [artifact]
    assert collected["source_audit"]["attempts"] == [{"route": "jra_horse_history"}]
    attach_history_sources(collected, raw_dir)
    assert collected["source_audit"]["artifacts"] == [artifact]  # dedupe by (kind, sha256)


def test_attach_history_sources_skips_non_object_foreign_files(tmp_path):
    from src.skill_workflow import attach_history_sources

    raw_dir = tmp_path / "raw"
    foreign_dir = raw_dir / "foreign_artifacts"
    foreign_dir.mkdir(parents=True)
    (foreign_dir / "list.json").write_text("[1, 2, 3]", encoding="utf-8")
    (foreign_dir / "scalar.json").write_text('"just text"', encoding="utf-8")
    collected = {"rows": [], "source_audit": {"attempts": []}}
    attach_history_sources(collected, raw_dir)
    assert collected["source_audit"]["artifacts"] == []


def test_record_fetch_attempt_binds_the_existing_raw_file(tmp_path):
    pipeline = JRAPipeline(_config(tmp_path))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(exist_ok=True)
    payload = b"<html>official race detail</html>"
    (raw_dir / "race_R.html").write_bytes(payload)

    pipeline._record_fetch_attempt(
        "jra_race_detail", url="https://race/R", raw_name="race_R.html",
        fetched=True, existed_before=False, race_id="R", fields=("time",))
    pipeline._record_fetch_attempt(
        "jra_race_detail", url="https://race/R", raw_name="race_R.html",
        fetched=True, existed_before=True, race_id="R")
    attempts = pipeline.acquisition_recorder.attempts()
    expected_sha = hashlib.sha256(payload).hexdigest()
    assert [a["outcome"] for a in attempts] == ["fetched", "cache_hit"]
    for attempt in attempts:
        assert attempt["artifact_sha256"] == expected_sha
        assert attempt["artifact_path"] == str((raw_dir / "race_R.html").resolve())


def test_foreign_horse_names_map_tolerates_a_broken_json_file(tmp_path):
    (tmp_path / "foreign_horse_names.json").write_text("{broken json", encoding="utf-8")
    pipeline = JRAPipeline(_config(tmp_path))
    assert pipeline._foreign_horse_names_map() == {}


def test_france_fetch_helpers_return_content_or_none(tmp_path):
    pipeline = JRAPipeline(_config(tmp_path))
    text = "ロンシャン"
    payload = text.encode("cp932")

    class _Response:
        content = payload

        def raise_for_status(self):
            return None

    pipeline.scraper = types.SimpleNamespace(
        session=types.SimpleNamespace(get=lambda url, timeout=None: _Response()))
    assert pipeline._france_fetch_bytes("https://example.invalid/pdf") == payload
    assert pipeline._fetch_text_cp932("https://example.invalid/page") == text

    def down(url, timeout=None):
        raise OSError("network down")

    pipeline.scraper = types.SimpleNamespace(
        session=types.SimpleNamespace(get=down))
    assert pipeline._france_fetch_bytes("https://example.invalid/pdf") is None
    assert pipeline._fetch_text_cp932("https://example.invalid/page") is None


def test_apply_france_history_without_the_module_returns_rows(tmp_path, monkeypatch):
    pipeline = JRAPipeline(_config(tmp_path))
    monkeypatch.setitem(sys.modules, "jra_scraper.france_galop", None)
    monkeypatch.delattr(jra_scraper, "france_galop", raising=False)
    rows = [_france_row("HF")]
    assert pipeline._apply_france_history(_horse("HF"), rows) == rows
    assert pipeline.acquisition_recorder.attempts() == []


def test_store_foreign_artifact_registers_the_bound_pdf(tmp_path):
    pipeline = JRAPipeline(_config(tmp_path))
    artifact = {"kind": "france_tracking_pdf", "date": "2025-10-05",
                "url": "https://www7.france-galop.com/tracking.pdf",
                "html": "extracted tracking text", "sha256": "e" * 64,
                "pdf_path": str(tmp_path / "france_tracking.pdf"),
                "pdf_sha256": "d" * 64}
    pipeline._store_foreign_artifact(
        artifact, _france_row("HF"), {"race_id": "R", "horse_id": "HF"})
    descriptor = tmp_path / "raw" / "foreign_artifacts" / "france_tracking_2025-10-05_HF.json"
    assert json.loads(descriptor.read_text(encoding="utf-8"))["kind"] == "france_tracking_pdf"
    assert pipeline.acquisition_recorder.artifacts() == [{
        "path": str(tmp_path / "france_tracking.pdf"), "sha256": "d" * 64,
        "kind": "france_tracking_pdf",
        "url": "https://www7.france-galop.com/tracking.pdf", "race_id": "R"}]


def test_write_acquisition_bundles_skips_a_race_with_no_recorded_routes(tmp_path):
    pipeline = JRAPipeline(_config(tmp_path))
    refs = pipeline._write_acquisition_bundles([_france_row("HF")], ["R"])
    assert refs == []
    assert not (tmp_path / "raw" / "acquisition").exists()
