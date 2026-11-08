"""Race-day preflight audit: discovery reuse, report shape, readiness, diff.

No live network: the program page is a fixture (same markup quirk as the
Stage 0 tests), the collector is a test double, and the audit itself is
either the real audit over an empty collection or a stubbed audit dict —
the preflight's own logic (partitioning, grouping, actions, diff) is
what is under test here; the audit has its own test suite.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from scripts import preflight_race_day as preflight
from scripts.preflight_race_day import (
    build_race_section,
    classify_finding,
    compute_diff,
    main,
)

JST = timezone(timedelta(hours=9))
DATE = "2026-11-15"
NOW = datetime(2026, 11, 14, 10, 0, tzinfo=JST)

PROGRAM_HTML = """
<html><body>
<div class="cell">
<table class="basic narrow-xy">
<tr><th>レース 番号</th><th>レース名・条件</th><th>発走時刻</th></tr>
<tr><td>11 レース</td><td>第30回 武蔵野ステークス（GⅢ） 3歳以上オープン 1,600 （ダ）定量</td><td>15時30分</td></tr>
</table>
</div>
<div class="inner"><div class="main">5回東京4日</div></div>
<div class="cell">
<table class="basic narrow-xy">
<tr><th>レース 番号</th><th>レース名・条件</th><th>発走時刻</th></tr>
<tr><td>11 レース</td><td>第51回 エリザベス女王杯（GⅠ） 3歳以上オープン 2,200 （芝・外）（牝）定量</td><td>15時40分</td></tr>
</table>
</div>
<div class="inner"><div class="main">5回京都4日</div></div>
</body></html>
"""

KYOTO_ID = "20261115_京都_11"
TOKYO_ID = "20261115_東京_11"


class FakeScraper:
    def fetch(self, url, raw_name=None, use_cache=True):
        return PROGRAM_HTML

    def fetch_relative(self, path, raw_name=None, use_cache=True):
        return ""


def _write_kyoto_config(root: Path) -> None:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    payload = [{
        "race_name": "エリザベス女王杯", "race_date": DATE, "track": "京都",
        "race_number": 11, "source_url": "https://example.invalid/kyoto11",
        "post_time": "15:40", "surface": "芝", "distance": "2200m",
    }]
    (config_dir / "20261115_kyoto_11.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _empty_collected(config, root, race_date):
    return {"rows": [], "entries": [], "quality_report": {}, "source_audit": {}}


def _audit_dict(gate, findings, *, counts=None, coverage_records=None,
                coverage_status="PASS", source_status="VERIFIED_REPARSE"):
    return {
        "gate": gate, "status": gate, "purpose": "prediction",
        "input_sha256": "0" * 64,
        "findings": findings,
        "acquisition_coverage": {"status": coverage_status,
                                 "records": coverage_records or [],
                                 "counts": {}},
        "history_counts": counts or [],
        "source_audit_status": source_status,
        "missing_field_counts": {},
    }


def _finding(horse_number, field, method, *, required=False, run_index="",
             horse_id="h1", race_id=KYOTO_ID):
    return {"race_id": race_id, "horse_id": horse_id,
            "horse_number": horse_number, "run_index": run_index,
            "field": field, "method": method,
            "required_missing": required, "approved": False,
            "finding_id": f"{horse_number}-{field}-{run_index}"}


def _target(race_id=KYOTO_ID):
    return {"race_id": race_id, "race_name": "エリザベス女王杯",
            "race_date": DATE, "track": "京都", "race_number": 11,
            "grade": "G1", "surface": "芝", "distance": "2200m",
            "post_time": "15:40", "source_url": "https://example.invalid/x",
            "origins": ["schedule", "config"]}


def _collected_with_horse():
    row = {"race_id": KYOTO_ID, "horse_id": "h1", "horse_number": "3",
           "horse_name": "テストホース", "run_index": "2"}
    entry = {"race_id": KYOTO_ID, "horse_id": "h1", "horse_number": "3",
             "horse_name": "テストホース"}
    return {"rows": [row], "entries": [entry], "quality_report": {},
            "source_audit": {}}


def test_dry_run_reuses_stage0_discovery_and_config_merge(tmp_path, capsys):
    _write_kyoto_config(tmp_path)
    code = main(["--date", DATE, "--dry-run"], now=NOW,
                scraper=FakeScraper(), root=tmp_path)
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    by_id = {t["race_id"]: t for t in out["targets"]}
    assert set(by_id) == {KYOTO_ID, TOKYO_ID}
    assert by_id[KYOTO_ID]["source_url_resolved"] is True
    assert by_id[TOKYO_ID]["source_url_resolved"] is False
    assert not (tmp_path / "report" / "preflight").exists()


def test_real_audit_on_empty_collection_is_not_ready_with_reasons(
        tmp_path, capsys):
    _write_kyoto_config(tmp_path)
    code = main(["--date", DATE], now=NOW, scraper=FakeScraper(),
                collector_runner=_empty_collected, root=tmp_path)
    assert code == 1
    report = json.loads(
        (tmp_path / "report" / "preflight" / f"{DATE}.json").read_text(
            encoding="utf-8"))
    assert report["summary"] == {"targets": 2, "ready": 0, "not_ready": 2}
    by_id = {r["race_id"]: r for r in report["races"]}
    kyoto = by_id[KYOTO_ID]
    assert kyoto["audit_status"] == "COMPLETED"
    assert kyoto["gate"] == "BLOCKED_MISSING_DATA"
    assert kyoto["readiness"] == "NOT_READY"
    assert kyoto["entries"] == 0
    assert any("出走表" in action for action in kyoto["actions"])
    tokyo = by_id[TOKYO_ID]
    assert tokyo["audit_status"] == "NOT_RUN"
    assert tokyo["gate"] is None
    assert any("URLが未解決" in action for action in tokyo["actions"])
    assert report["diff_vs_previous"] is None
    runs = list((tmp_path / "report" / "preflight" / "runs").glob("*.json"))
    assert len(runs) == 1


def test_ready_only_when_gate_pass(tmp_path, monkeypatch):
    audit = _audit_dict("PASS", [], counts=[{
        "race_id": KYOTO_ID, "horse_number": "3", "collected": 5,
        "required": 5, "status": "COMPLETE"}])
    monkeypatch.setattr(preflight, "audit_collected_data",
                        lambda collected, configs, approvals, purpose: audit)
    section = build_race_section(_target(), _collected_with_horse(), audit)
    assert section["readiness"] == "READY"
    assert section["required_missing_count"] == 0
    assert any("PASS" in action for action in section["actions"])


def test_findings_partition_market_entry_and_history_scopes(
        tmp_path, monkeypatch):
    findings = [
        _finding("3", "last_3f", "unsupported_source_observation",
                 required=True, run_index="2"),
        _finding("3", "current_odds", "default_0"),
        _finding("3", "current_popularity", "history_average_or_10"),
        _finding("3", "assigned_weight", "default_55"),
    ]
    audit = _audit_dict("BLOCKED_MISSING_DATA", findings, counts=[{
        "race_id": KYOTO_ID, "horse_number": "3", "collected": 5,
        "required": 5, "status": "COMPLETE"}])
    monkeypatch.setattr(preflight, "audit_collected_data",
                        lambda collected, configs, approvals, purpose: audit)
    section = build_race_section(_target(), _collected_with_horse(), audit)
    assert section["findings_by_scope"] == {
        "history_inputs": 1, "race_day_market": 2, "entry_inputs": 1}
    assert section["required_missing"][0]["horse_name"] == "テストホース"
    assert section["required_missing"][0]["items"] == [
        {"run_index": "2", "field": "last_3f",
         "method": "unsupported_source_observation"}]
    unapproved_fields = {v["field"] for v in section["unapproved_findings"]}
    assert unapproved_fields == {"assigned_weight"}
    assert any("単勝オッズ" in action for action in section["actions"])
    assert classify_finding({"field": "current_odds"}) == "race_day_market"


def test_route_unavailable_action_uses_coverage_reason(monkeypatch):
    findings = [_finding("3", "passing_order", "unsupported_source_observation",
                         required=True, run_index="4")]
    records = [{"race_id": KYOTO_ID, "horse_id": "h1", "horse_number": "3",
                "run_index": "4", "date": "2025-02-16",
                "field": "passing_order", "state": "SOURCE_ROUTE_UNAVAILABLE",
                "reason": "no official source route for this jurisdiction"}]
    audit = _audit_dict("BLOCKED_MISSING_DATA", findings,
                        coverage_records=records, coverage_status="BLOCKED")
    section = build_race_section(_target(), _collected_with_horse(), audit)
    assert section["coverage_blockers"][0]["states"] == [
        "SOURCE_ROUTE_UNAVAILABLE"]
    assert any("公式ソース経路が存在しない" in action
               for action in section["actions"])


def test_diff_reports_resolved_and_new_blockers(tmp_path, monkeypatch):
    _write_kyoto_config(tmp_path)
    blocker = _finding("3", "last_3f", "unsupported_source_observation",
                       required=True, run_index="2")
    collected = _collected_with_horse()

    def runner(config, root, race_date):
        return collected

    audits = iter([
        _audit_dict("BLOCKED_MISSING_DATA", [blocker]),
        _audit_dict("PASS", []),
    ])
    monkeypatch.setattr(
        preflight, "audit_collected_data",
        lambda collected_, configs, approvals, purpose: next(audits))
    first = main(["--date", DATE], now=NOW, scraper=FakeScraper(),
                 collector_runner=runner, root=tmp_path)
    assert first == 1
    second = main(["--date", DATE], now=NOW, scraper=FakeScraper(),
                  collector_runner=runner, root=tmp_path)
    assert second == 1  # Tokyo target is still NOT_RUN in both runs
    report = json.loads(
        (tmp_path / "report" / "preflight" / f"{DATE}.json").read_text(
            encoding="utf-8"))
    diff = report["diff_vs_previous"]["races"][KYOTO_ID]
    assert diff["readiness_before"] == "NOT_READY"
    assert diff["readiness_after"] == "READY"
    assert diff["resolved_blockers"] == [
        [KYOTO_ID, "3", "2", "last_3f", "unsupported_source_observation"]]
    assert diff["new_blockers"] == []


def test_compute_diff_without_previous_is_none():
    assert compute_diff(None, {"races": []}) is None


def test_collector_error_is_recorded_not_raised(tmp_path, capsys):
    _write_kyoto_config(tmp_path)

    def broken(config, root, race_date):
        raise RuntimeError("boom")

    code = main(["--date", DATE], now=NOW, scraper=FakeScraper(),
                collector_runner=broken, root=tmp_path)
    assert code == 1
    report = json.loads(
        (tmp_path / "report" / "preflight" / f"{DATE}.json").read_text(
            encoding="utf-8"))
    kyoto = {r["race_id"]: r for r in report["races"]}[KYOTO_ID]
    assert kyoto["audit_status"] == "ERROR"
    assert "boom" in kyoto["error"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
