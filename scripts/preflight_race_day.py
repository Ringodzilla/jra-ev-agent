#!/usr/bin/env python3
"""Race-day preflight audit (Issue #39 workstream D).

Purpose: surface input blockers the day before (or on the morning of) a
race, instead of discovering them minutes before post time. On 2026-11-08
the Argentine Republic Cup went NO_GO because collection gaps in two
horses' overseas histories were only found at 13:53 on race day.

For each target race (the date's graded races plus any config-registered
races, discovered exactly as in scripts/capture_stage0_day.py) this tool:

1. collects entries and histories with the existing DataCollectorAgent
   (isolated work files under report/preflight/work/, shared raw HTML
   evidence under data/raw), and
2. runs the real input audit (src.data_integrity.audit_collected_data,
   purpose 'prediction', NO approvals) and reports the gate as computed.

Read-only diagnosis: it never places bets, produces tickets, runs the
analyzer, or marks anything approved. Findings are never softened; the
raw gate is reported verbatim.

Scoping note (odds): the audit's target-entry checks for current_odds /
current_popularity can only be satisfied by race-day market data, which
does not exist before the race. Those findings are partitioned into the
'race_day_market' scope in the report — they still count in the raw
gate, so a pre-race audit cannot PASS on market grounds, and readiness
below is defined strictly on the raw gate. The partition exists so the
plain-language actions can separate "nothing to do until race-day odds"
from genuine history/input blockers that can be fixed in advance.

Readiness: a race is READY only if the audit gate is PASS, otherwise
NOT_READY with reasons. Exit code: 0 = every target READY, 1 = any
NOT_READY (or no targets), 2 = internal error before any report.

Reports: latest at report/preflight/<date>.json (with a
diff_vs_previous section when an earlier report for the date exists)
and an archive copy at report/preflight/runs/<date>_<timestamp>.json.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jra_scraper.config import ScrapeConfig
from jra_scraper.scraper import JRAScraper, safe_filename
from scripts.capture_stage0_day import (
    JST,
    build_scrape_config,
    load_config_races,
    merge_targets,
    parse_program_page,
    program_url,
    resolve_source_urls,
)
from src.artifacts import atomic_write_json
from src.data_integrity import audit_collected_data
from src.skill_workflow import attach_history_sources

MARKET_FIELDS = {"current_odds", "current_popularity"}
COVERAGE_OK_STATES = {"OBSERVED", "NOT_MEASURED", "NOT_APPLICABLE", "SUBSTITUTED"}
FIELD_LABELS = {
    "date": "日付", "course": "コース", "race_name": "レース名",
    "distance": "距離", "history_surface": "馬場種別", "track_condition": "馬場状態",
    "position": "着順", "time": "走破時計", "last_3f": "上がり3F",
    "passing_order": "通過順", "popularity": "人気", "weight": "馬体重",
    "jockey": "騎手", "pace": "ペース", "weather": "天候", "odds": "オッズ",
    "history": "近走履歴", "assigned_weight": "負担重量",
    "current_odds": "単勝オッズ", "current_popularity": "人気",
}


def discover_targets(root: Path, race_date: str, scraper) -> tuple[list[dict], bool, str]:
    """Graded + config targets for the date (same flow as Stage 0)."""
    client = scraper or JRAScraper(build_scrape_config(root))
    url = program_url(race_date)
    html = client.fetch(url, raw_name=f"preflight_program_{race_date.replace('-', '')}.html",
                        use_cache=False)
    schedule = parse_program_page(html, race_date) if html else []
    configs = load_config_races(root / "config", race_date)
    targets = merge_targets(schedule, configs)
    links = []
    race_list_html = client.fetch_relative(
        build_scrape_config(root).race_list_path, raw_name="race_list.html",
        use_cache=False)
    if race_list_html:
        from jra_scraper.parser import JRAParser
        links = JRAParser(build_scrape_config(root).base_url).parse_race_list(
            race_list_html)
    resolve_source_urls(targets, links)
    return targets, bool(html), url


def collector_config_for(target: dict) -> dict:
    """Race config dict shared by the collector and the audit."""
    return {
        "race_id": target["race_id"],
        "race_name": target.get("race_name", ""),
        "race_date": target["race_date"],
        "track": target.get("track", ""),
        "race_number": target.get("race_number", 0),
        "source_url": target.get("source_url", ""),
        "surface": target.get("surface", ""),
        "distance": target.get("distance", ""),
        "post_time": target.get("post_time", ""),
    }


def build_preflight_config(root: Path, race_date: str, race_id: str) -> ScrapeConfig:
    """Collector config isolated from production processed files.

    Raw HTML evidence and the manual history overrides stay shared with
    production (they are inputs/evidence, not outputs); every writable
    pipeline artifact lives under report/preflight/work/<date>/<race>/.
    """
    work = root / "report" / "preflight" / "work" / race_date / safe_filename(race_id)
    return ScrapeConfig(
        output_csv=work / "race_last5.csv",
        entries_csv=work / "race_entries.csv",
        odds_snapshots_csv=work / "live_odds_snapshots.csv",
        combo_odds_csv=work / "live_combo_odds.csv",
        raw_dir=root / "data/raw",
        state_path=work / "pipeline_state.json",
        quality_report_path=work / "data_quality.json",
        missing_history_requests_path=work / "missing_history_requests.json",
        manual_history_template_csv=work / "manual_history_template.csv",
        manual_history_csv=root / "data/manual/horse_history_overrides.csv",
        stages_dir=work / "stages",
    )


def run_collection(config: dict, root: Path, race_date: str) -> dict:
    """Collect one race with the existing DataCollectorAgent machinery."""
    from src.agents.data_collector import DataCollectorAgent

    agent = DataCollectorAgent(
        build_preflight_config(root, race_date, str(config["race_id"])))
    return agent.run([config])


def classify_finding(finding: dict) -> str:
    """Partition a finding: market data is a race-day-only scope."""
    field = str(finding.get("field", ""))
    if field in MARKET_FIELDS:
        return "race_day_market"
    if field == "assigned_weight":
        return "entry_inputs"
    return "history_inputs"


def _horse_names(collected: dict, race_id: str) -> dict[str, str]:
    names: dict[str, str] = {}
    for source in (collected.get("rows", []), collected.get("entries", [])):
        for row in source:
            if str(row.get("race_id", "")) != race_id:
                continue
            number = str(row.get("horse_number", ""))
            if number and row.get("horse_name"):
                names.setdefault(number, str(row["horse_name"]))
    return names


def _finding_view(finding: dict, names: dict[str, str]) -> dict:
    number = str(finding.get("horse_number", ""))
    return {
        "horse_number": number,
        "horse_name": names.get(number, ""),
        "horse_id": str(finding.get("horse_id", "")),
        "run_index": str(finding.get("run_index", "")),
        "field": str(finding.get("field", "")),
        "method": str(finding.get("method", "")),
        "required_missing": bool(finding.get("required_missing")),
    }


def _group_by_horse(views: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}
    for view in views:
        group = groups.setdefault(view["horse_number"], {
            "horse_number": view["horse_number"],
            "horse_name": view["horse_name"],
            "items": [],
        })
        group["items"].append({key: view[key]
                               for key in ("run_index", "field", "method")})
    return [groups[key] for key in sorted(groups, key=lambda n: (len(n), n))]


def coverage_blockers(audit: dict, race_id: str, names: dict[str, str]) -> list[dict]:
    """Coverage records in blocking states for one race, grouped by horse/field."""
    coverage = audit.get("acquisition_coverage") or {}
    groups: dict[tuple[str, str], dict] = {}
    for record in coverage.get("records", []):
        if str(record.get("race_id", "")) != race_id:
            continue
        state = str(record.get("state", ""))
        if state in COVERAGE_OK_STATES:
            continue
        number = str(record.get("horse_number", ""))
        field = str(record.get("field", ""))
        group = groups.setdefault((number, field), {
            "horse_number": number,
            "horse_name": names.get(number, ""),
            "field": field,
            "states": [],
            "reasons": [],
        })
        if state and state not in group["states"]:
            group["states"].append(state)
        reason = str(record.get("reason", "")).strip()
        if reason and reason not in group["reasons"]:
            group["reasons"].append(reason)
    return [groups[key] for key in sorted(groups)]


def _field_label(field: str) -> str:
    return FIELD_LABELS.get(field, field)


def build_actions(target: dict, collected: dict | None, audit: dict | None,
                  section: dict) -> list[str]:
    """Plain-language list of what would make this race predictable."""
    actions: list[str] = []
    if section["audit_status"] == "NOT_RUN":
        actions.append(
            "JRADBのレースURLが未解決で収集を開始できない。出走表はレース週の"
            "木曜に公開され、その時点でURLが解決する。公開後にプリフライトを"
            "再実行すること。")
        return actions
    if section["audit_status"] == "ERROR":
        actions.append(
            f"収集または監査で内部エラーが発生した: {section.get('error', '')}。"
            "エラーを解消して再実行すること。")
        return actions
    assert collected is not None and audit is not None
    names = section["_names"]
    if not section["entries"]:
        actions.append(
            "出走表（entries）が1件も収集できていない。出走表の公開前"
            "（木曜公開）か、収集経路の失敗が考えられる。公開後に再実行し、"
            "失敗なら収集ログを確認すること。")
    counts = {str(c.get("horse_number", "")): c
              for c in audit.get("history_counts", [])}
    coverage_by_key = {(g["horse_number"], g["field"]): g
                       for g in section["coverage_blockers"]}
    for group in section["required_missing"]:
        number = group["horse_number"]
        name = group["horse_name"] or number
        for item in group["items"]:
            field = item["field"]
            label = _field_label(field)
            run = f"{item['run_index']}走目" if item["run_index"] else "対象走"
            blocker = coverage_by_key.get((number, field))
            states = blocker["states"] if blocker else []
            reasons = blocker["reasons"] if blocker else []
            if "SOURCE_ROUTE_UNAVAILABLE" in states:
                reason_text = f"（記録された理由: {reasons[0]}）" if reasons else ""
                actions.append(
                    f"{name}の{run} {label}: 公式ソース経路が存在しない"
                    f"{reason_text}。経路の新設か、ユーザー承認つき例外の"
                    "設計が必要。")
            elif item["method"] == "unsupported_source_observation":
                reason_text = f"（カバレッジ記録: {reasons[0]}）" if reasons else ""
                actions.append(
                    f"{name}の{run} {label}: 原典で裏付けられた観測が"
                    f"モデルで使える形で収集できていない{reason_text}。"
                    "対応する公式ソースを収集して出典に束縛するか、"
                    "ユーザー承認つきの出典束縛例外を検討すること。")
            else:
                actions.append(
                    f"{name}の{run} {label}が欠落している。公式原典から"
                    "再収集すること（推測や既定値で埋めない）。")
            if field == "time":
                actions.append(
                    f"{name}の{run} 走破時計は例外メソッドの対象外。"
                    "収集段階で公式観測を回復する必要がある。")
    for number, count in sorted(counts.items()):
        name = names.get(number, "") or number
        if count.get("status") == "MISSING":
            actions.append(
                f"{name}: 近走履歴が {count.get('collected')}走 / 必要 "
                f"{count.get('required')}走 しか収集できていない。"
                "公式原典から履歴を再収集すること。")
        elif count.get("status") == "UNVERIFIED":
            actions.append(
                f"{name}: 必要履歴数が未確定（通算出走数の裏付けなし）。"
                "公式原典でキャリアを確認すること。")
    for view in section["unapproved_findings"]:
        name = view["horse_name"] or view["horse_number"]
        run = f"{view['run_index']}走目" if view["run_index"] else "対象走"
        actions.append(
            f"{name}の{run} {_field_label(view['field'])}: "
            f"補完（{view['method']}）が未承認。原典の再取得で観測を"
            "回復するか、ユーザーに承認を求めること。")
    covered = {(g["horse_number"], item["field"])
               for g in section["required_missing"] for item in g["items"]}
    for group in section["coverage_blockers"]:
        if (group["horse_number"], group["field"]) in covered:
            continue
        name = group["horse_name"] or group["horse_number"]
        reason_text = f": {group['reasons'][0]}" if group["reasons"] else ""
        actions.append(
            f"{name} {_field_label(group['field'])}: 取得カバレッジが "
            f"{'/'.join(group['states'])} のため未確定{reason_text}。"
            "原典の収集状態を解消すること。")
    if audit.get("source_audit_status") != "VERIFIED_REPARSE":
        actions.append(
            "原典の再解析照合が完了していない（source UNVERIFIED）。"
            "原典HTMLが保存され、再解析で一致する状態にすること。")
    if section["findings_by_scope"].get("race_day_market"):
        actions.append(
            "単勝オッズ・人気はレース当日にしか存在しないため、監査の"
            "最終PASSは当日オッズの取得後にのみ判定できる"
            "（プリフライトでは対象外として分離済み）。")
    if section["readiness"] == "READY":
        actions.append(
            "入力監査はPASS。残る工程は当日のオッズ取得と最終予測ランのみ。")
    return actions


def build_race_section(target: dict, collected: dict, audit: dict) -> dict:
    """Per-race readiness section from a completed audit (gate verbatim)."""
    race_id = target["race_id"]
    names = _horse_names(collected, race_id)
    findings = [f for f in audit.get("findings", [])
                if str(f.get("race_id", "")) == race_id]
    views = [_finding_view(f, names) for f in findings]
    required = [v for v in views if v["required_missing"]]
    unapproved = [v for v in views
                  if not v["required_missing"]
                  and classify_finding(v) != "race_day_market"]
    gate = str(audit.get("gate", "UNVERIFIED"))
    section: dict = {
        "race_id": race_id,
        "race_name": target.get("race_name", ""),
        "track": target.get("track", ""),
        "race_number": target.get("race_number"),
        "grade": target.get("grade", ""),
        "surface": target.get("surface", ""),
        "distance": target.get("distance", ""),
        "post_time": target.get("post_time", ""),
        "origins": target.get("origins", [target.get("origin", "")]),
        "source_url_resolved": bool(target.get("source_url")),
        "audit_status": "COMPLETED",
        "gate": gate,
        "readiness": "READY" if gate == "PASS" else "NOT_READY",
        "input_sha256": audit.get("input_sha256"),
        "entries": sum(1 for e in collected.get("entries", [])
                       if str(e.get("race_id", "")) == race_id),
        "history_counts": [
            {"horse_number": str(c.get("horse_number", "")),
             "horse_name": names.get(str(c.get("horse_number", "")), ""),
             "collected": c.get("collected"), "required": c.get("required"),
             "status": c.get("status")}
            for c in audit.get("history_counts", [])
            if str(c.get("race_id", "")) == race_id
        ],
        "required_missing": _group_by_horse(required),
        "required_missing_count": len(required),
        "unapproved_findings": unapproved,
        "findings_by_scope": dict(Counter(classify_finding(f) for f in findings)),
        "coverage_blockers": coverage_blockers(audit, race_id, names),
        "coverage_status": str((audit.get("acquisition_coverage") or {}).get("status", "")),
        "source_audit_status": str(audit.get("source_audit_status", "")),
        "_names": names,
    }
    section["actions"] = build_actions(target, collected, audit, section)
    del section["_names"]
    return section


def unresolved_section(target: dict) -> dict:
    """Section for a race whose JRADB URL cannot be resolved yet."""
    section: dict = {
        "race_id": target["race_id"],
        "race_name": target.get("race_name", ""),
        "track": target.get("track", ""),
        "race_number": target.get("race_number"),
        "grade": target.get("grade", ""),
        "surface": target.get("surface", ""),
        "distance": target.get("distance", ""),
        "post_time": target.get("post_time", ""),
        "origins": target.get("origins", [target.get("origin", "")]),
        "source_url_resolved": False,
        "audit_status": "NOT_RUN",
        "gate": None,
        "readiness": "NOT_READY",
        "input_sha256": None,
        "entries": 0,
        "history_counts": [],
        "required_missing": [],
        "required_missing_count": 0,
        "unapproved_findings": [],
        "findings_by_scope": {},
        "coverage_blockers": [],
        "coverage_status": "",
        "source_audit_status": "",
    }
    section["actions"] = build_actions(target, None, None, section)
    return section


def error_section(target: dict, exc: Exception) -> dict:
    section = unresolved_section(target)
    section["audit_status"] = "ERROR"
    section["source_url_resolved"] = bool(target.get("source_url"))
    section["error"] = f"{type(exc).__name__}: {exc}"
    section["actions"] = build_actions(target, None, None, section)
    return section


def _required_keys(section: dict) -> set[tuple]:
    return {(section["race_id"], group["horse_number"], item["run_index"],
             item["field"], item["method"])
            for group in section.get("required_missing", [])
            for item in group["items"]}


def compute_diff(previous: dict | None, current: dict) -> dict | None:
    """Blocker-level diff against the previous report for the same date."""
    if not previous:
        return None
    prev_races = {r["race_id"]: r for r in previous.get("races", [])}
    diff: dict = {"previous_generated_at": previous.get("generated_at"),
                  "races": {}}
    for section in current.get("races", []):
        race_id = section["race_id"]
        old = prev_races.get(race_id)
        new_keys = _required_keys(section)
        if old is None:
            diff["races"][race_id] = {
                "readiness_before": None,
                "readiness_after": section["readiness"],
                "new_blockers": sorted(new_keys),
                "resolved_blockers": [],
            }
            continue
        old_keys = _required_keys(old)
        diff["races"][race_id] = {
            "readiness_before": old.get("readiness"),
            "readiness_after": section["readiness"],
            "new_blockers": sorted(new_keys - old_keys),
            "resolved_blockers": sorted(old_keys - new_keys),
        }
    return diff


def _target_ref(target: dict) -> dict:
    return {"race_id": target["race_id"], "race_name": target.get("race_name", ""),
            "track": target.get("track", ""), "race_number": target.get("race_number"),
            "post_time": target.get("post_time", ""),
            "source_url_resolved": bool(target.get("source_url"))}


def main(argv: list[str] | None = None, *, now: datetime | None = None,
         scraper=None, collector_runner=None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(
        description="Race-day preflight audit: surface input blockers before race day")
    parser.add_argument("--date", default=None,
                        help="Target date YYYY-MM-DD (default: today, JST)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Discovery only; no collection, no audit, no report file")
    args = parser.parse_args(argv)

    now_jst = (now or datetime.now(JST)).astimezone(JST)
    race_date = args.date or now_jst.date().isoformat()
    try:
        datetime.fromisoformat(race_date)
    except ValueError:
        parser.error(f"invalid --date: {race_date!r} (expected YYYY-MM-DD)")

    targets, program_found, url = discover_targets(root, race_date, scraper)

    if args.dry_run:
        print(json.dumps({
            "date": race_date, "dry_run": True, "program_url": url,
            "program_found": program_found,
            "targets": [_target_ref(t) for t in targets],
        }, ensure_ascii=False, indent=2))
        return 0

    runner = collector_runner or run_collection
    sections: list[dict] = []
    for target in targets:
        if not target.get("source_url"):
            sections.append(unresolved_section(target))
            continue
        config = collector_config_for(target)
        try:
            collected = runner(config, root, race_date)
            attach_history_sources(collected, root / "data/raw")
            audit = audit_collected_data(collected, [config], [], "prediction")
            sections.append(build_race_section(target, collected, audit))
        except Exception as exc:  # one race must not kill the whole preflight
            sections.append(error_section(target, exc))

    ready = sum(1 for s in sections if s["readiness"] == "READY")
    report: dict = {
        "schema_version": 1,
        "kind": "preflight",
        "date": race_date,
        "generated_at": now_jst.isoformat(),
        "program_url": url,
        "program_found": program_found,
        "purpose": "prediction",
        "approvals_applied": [],
        "summary": {"targets": len(sections), "ready": ready,
                    "not_ready": len(sections) - ready},
        "races": sections,
    }
    preflight_dir = root / "report" / "preflight"
    latest_path = preflight_dir / f"{race_date}.json"
    previous = None
    if latest_path.is_file():
        try:
            previous = json.loads(latest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = None
    report["diff_vs_previous"] = compute_diff(previous, report)
    stamp = now_jst.strftime("%Y%m%dT%H%M%S")
    archive_path = preflight_dir / "runs" / f"{race_date}_{stamp}.json"
    atomic_write_json(archive_path, report)
    atomic_write_json(latest_path, report)
    report["report_path"] = str(latest_path)
    report["archive_path"] = str(archive_path)

    print(f"preflight {race_date}: targets={len(sections)} "
          f"ready={ready} not_ready={len(sections) - ready}")
    for section in sections:
        gate_text = section["gate"] if section["gate"] else section["audit_status"]
        print(f"- {section['track']}{section['race_number']}R "
              f"{section['race_name']}: {section['readiness']} "
              f"gate={gate_text} "
              f"required_missing={section['required_missing_count']} "
              f"entries={section['entries']}")
        for action in section["actions"]:
            print(f"    action: {action}")
    print(f"report: {latest_path}")
    return 0 if sections and ready == len(sections) else 1


if __name__ == "__main__":
    raise SystemExit(main())
