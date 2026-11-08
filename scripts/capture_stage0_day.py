#!/usr/bin/env python3
"""Stage 0 day-level environment capture driver (Issue #39 workstream B).

Stage 0 accumulates FORWARD, pre-race environment observations: the only
kind eligible for environment-model learning (post-hoc reconstructions are
research-only). Discovery combines two official/configured sources:

- the JRA daily program page (競馬番組) for the date, filtered to graded
  races (G1/G2/G3, including jump grades), and
- any per-race configs in config/ whose race_date matches (user-predicted
  races), which also supply the JRADB source_url used for capture.

Capture reuses the existing machinery: DataCollectorAgent runs the normal
pipeline collection and jra_scraper.environment.capture_environment, which
writes timestamped snapshots under report/environment_observations/<race_id>/.

Discipline: a real capture happens only on the race's own day and only
before its post time. Anything else is skipped with a recorded reason in
the run log under report/environment_observations/stage0_runs/. Nothing is
backdated and this driver never marks a snapshot eligible itself;
eligibility is decided solely by capture_environment.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bs4 import BeautifulSoup

from jra_scraper.config import ScrapeConfig
from jra_scraper.parser import TRACKS, JRAParser
from jra_scraper.scraper import JRAScraper, safe_filename

JST = timezone(timedelta(hours=9))
MINIMUM_RACES = {"train": 100, "validation": 20, "test": 20}
REFRESH_DEDUPE_MINUTES = 20

_GRADE_RE = re.compile(r"[（\(](J・)?G([ⅠⅡⅢ123])[）\)]")
_GRADE_DIGITS = {"Ⅰ": "1", "Ⅱ": "2", "Ⅲ": "3", "1": "1", "2": "2", "3": "3"}
_MEETING_RE = re.compile(r"^\d+回\s*(.+?)\s*\d+日$")
_POST_TIME_RE = re.compile(r"(\d{1,2})時(\d{2})分")
_CONDITION_RE = re.compile(r"(\d[\d,]*)\s*[（\(](芝|ダ|障)")
_SURFACES = {"芝": "芝", "ダ": "ダート", "障": "障害"}


def program_url(race_date: str) -> str:
    """Official JRA daily program page (競馬番組) for one date."""
    day = datetime.fromisoformat(race_date).date()
    return (f"https://www.jra.go.jp/keiba/calendar{day.year}/{day.year}/"
            f"{day.month:02d}/{day.month:02d}{day.day:02d}.html")


def canonical_race_id(race_date: str, track: str, race_number: int) -> str:
    return f"{race_date.replace('-', '')}_{track}_{race_number:02d}"


def _meeting_track(table) -> str:
    """Track for one program table.

    In the program page markup each meeting title (e.g. 5回東京4日) sits in
    a div.main that FOLLOWS its table, so the title is the next matching
    div.main before the following table.
    """
    for node in table.find_all_next(["div", "table"]):
        if node.name == "table":
            return ""
        if "main" not in (node.get("class") or []):
            continue
        match = _MEETING_RE.match(node.get_text(" ", strip=True))
        if match:
            return next((t for t in TRACKS if t in match.group(1)), "")
    return ""


def parse_program_page(html: str, race_date: str) -> list[dict]:
    """Graded-race targets from the official daily program page."""
    soup = BeautifulSoup(html, "html.parser")
    targets = []
    for table in soup.select("table.basic"):
        track = _meeting_track(table)
        if not track:
            continue
        for row in table.select("tr"):
            cells = [c.get_text(" ", strip=True) for c in row.select("th, td")]
            if len(cells) < 3:
                continue
            grade_match = _GRADE_RE.search(cells[1])
            time_match = _POST_TIME_RE.search(cells[2])
            number_match = re.search(r"(\d+)", cells[0])
            if not grade_match or not time_match or not number_match:
                continue
            number = int(number_match.group(1))
            condition = _CONDITION_RE.search(cells[1])
            targets.append({
                "race_id": canonical_race_id(race_date, track, number),
                "race_name": cells[1][:grade_match.end()].replace(" ", ""),
                "race_date": race_date,
                "track": track,
                "race_number": number,
                "grade": "G" + _GRADE_DIGITS[grade_match.group(2)],
                "surface": _SURFACES[condition.group(2)] if condition else "",
                "distance": f"{condition.group(1).replace(',', '')}m" if condition else "",
                "post_time": f"{int(time_match.group(1)):02d}:{time_match.group(2)}",
                "source_url": "",
                "origin": "schedule",
            })
    return targets


def _as_int(value: object) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def load_config_races(config_dir: Path, race_date: str) -> list[dict]:
    """Targets from per-race configs in config/ for the given date."""
    targets = []
    if not config_dir.is_dir():
        return targets
    for path in sorted(config_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entries = payload if isinstance(payload, list) else [payload]
        for entry in entries:
            if not isinstance(entry, dict) or str(entry.get("race_date", "")) != race_date:
                continue
            track = str(entry.get("track", ""))
            number = _as_int(entry.get("race_number", 0))
            if not track or not number:
                continue
            targets.append({
                "race_id": str(entry.get("race_id")
                               or canonical_race_id(race_date, track, number)),
                "race_name": str(entry.get("race_name", "")),
                "race_date": race_date,
                "track": track,
                "race_number": number,
                "grade": "",
                "surface": str(entry.get("surface", "")),
                "distance": str(entry.get("distance", "")),
                "post_time": str(entry.get("post_time", "")),
                "source_url": str(entry.get("source_url", "")),
                "origin": "config",
                "config_path": str(path),
            })
    return targets


def merge_targets(schedule: list[dict], configs: list[dict]) -> list[dict]:
    """Merge schedule and config targets by race_id; config fields win."""
    merged: dict[str, dict] = {}
    for target in schedule:
        merged[target["race_id"]] = dict(target, origins=["schedule"])
    for target in configs:
        base = merged.get(target["race_id"])
        if base is None:
            merged[target["race_id"]] = dict(target, origins=["config"])
            continue
        combined = dict(base)
        for key in ("race_name", "surface", "distance", "post_time", "source_url"):
            if target.get(key):
                combined[key] = target[key]
        combined["origin"] = "config"
        combined["origins"] = ["schedule", "config"]
        if target.get("config_path"):
            combined["config_path"] = target["config_path"]
        merged[target["race_id"]] = combined
    return sorted(merged.values(), key=lambda t: (t["track"], t["race_number"]))


def resolve_source_urls(targets: list[dict], links: list) -> list[dict]:
    """Fill missing source_url values from JRADB race-list links."""
    by_id = {link.race_id: link.race_url for link in links}
    by_key = {(link.race_date, link.track, str(link.race_number)): link.race_url
              for link in links}
    for target in targets:
        if target.get("source_url"):
            continue
        url = (by_id.get(target["race_id"])
               or by_key.get((target["race_date"], target["track"],
                              str(target["race_number"]))))
        if url:
            target["source_url"] = url
    return targets


def load_snapshots(observations_dir: Path, race_id: str) -> list[dict]:
    folder = observations_dir / safe_filename(race_id)
    snapshots = []
    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            try:
                snapshots.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
    return snapshots


def decide_action(target: dict, now: datetime, mode: str,
                  snapshots: list[dict]) -> tuple[str, str]:
    """Return ('capture', '') or ('skip', reason) for one target.

    Pure decision logic: pre-race discipline and per-mode idempotency.
    """
    if not target.get("post_time"):
        return "skip", "post_time_unknown"
    day = datetime.fromisoformat(target["race_date"]).date()
    hour, minute = (int(part) for part in str(target["post_time"]).split(":"))
    post = datetime(day.year, day.month, day.day, hour, minute, tzinfo=JST)
    if now >= post:
        return "skip", "past_post_time"
    captured = []
    for snap in snapshots:
        try:
            captured.append(datetime.fromisoformat(snap["captured_at"]).astimezone(JST))
        except (KeyError, TypeError, ValueError):
            continue
    if mode == "morning":
        if any(c.date().isoformat() == target["race_date"] and c.hour < 12
               for c in captured):
            return "skip", "already_captured_morning"
    else:  # refresh
        window_start = now - timedelta(minutes=REFRESH_DEDUPE_MINUTES)
        if any(window_start <= c <= now for c in captured):
            return "skip", "recent_snapshot_exists"
    return "capture", ""


def build_scrape_config(root: Path = ROOT) -> ScrapeConfig:
    return ScrapeConfig(
        output_csv=root / "data/processed/race_last5.csv",
        entries_csv=root / "data/processed/race_entries.csv",
        odds_snapshots_csv=root / "data/processed/live_odds_snapshots.csv",
        combo_odds_csv=root / "data/processed/live_combo_odds.csv",
        raw_dir=root / "data/raw",
        state_path=root / "data/processed/pipeline_state.json",
        quality_report_path=root / "report/data_quality.json",
        missing_history_requests_path=root / "report/missing_history_requests.json",
        manual_history_template_csv=root / "report/manual_history_template.csv",
        manual_history_csv=root / "data/manual/horse_history_overrides.csv",
        stages_dir=root / "report/stages",
    )


def run_capture(targets: list[dict], root: Path, now: datetime) -> dict:
    """Capture via the existing DataCollectorAgent machinery.

    Returns {race_id: environment snapshot dict}. The `now` argument is
    accepted for symmetry with test doubles; the underlying machinery
    stamps its own capture time.
    """
    from src.agents.data_collector import DataCollectorAgent

    agent = DataCollectorAgent(build_scrape_config(root))
    result = agent.run([dict(target) for target in targets])
    return {str(snap.get("race_id")): snap
            for snap in result.get("environment_snapshots", [])}


def collect_status(root: Path = ROOT) -> dict:
    """Accumulated Stage 0 counts and progress toward promotion minimums."""
    from scripts.update_environment_learning import usable_snapshot

    observations_dir = root / "report/environment_observations"
    races: dict[str, int] = {}
    eligible_races: set[str] = set()
    snapshot_total = 0
    if observations_dir.is_dir():
        for folder in sorted(observations_dir.iterdir()):
            if not folder.is_dir() or folder.name == "stage0_runs":
                continue
            for path in sorted(folder.glob("*.json")):
                try:
                    snap = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                race_id = str(snap.get("race_id") or folder.name)
                snapshot_total += 1
                races[race_id] = races.get(race_id, 0) + 1
                if usable_snapshot(snap):
                    eligible_races.add(race_id)
    status: dict = {
        "observations_dir": str(observations_dir),
        "races_with_observations": len(races),
        "snapshots": snapshot_total,
        "learning_eligible_races": len(eligible_races),
        "learning_eligible_race_ids": sorted(eligible_races),
        "minimum_races": MINIMUM_RACES,
        "minimum_total": sum(MINIMUM_RACES.values()),
        "learning_status": None,
        "split_progress": {key: {"current": 0, "minimum": minimum}
                           for key, minimum in MINIMUM_RACES.items()},
    }
    learning_path = root / "report/environment_model/learning_status.json"
    if learning_path.exists():
        try:
            report = json.loads(learning_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report = {}
        blocks = report.get("blocks") or {}
        status["learning_status"] = {
            "status": report.get("status"),
            "generated_at": report.get("generated_at"),
            "dataset_races": {key: len(blocks.get(key) or [])
                              for key in MINIMUM_RACES},
            "pending_environment": len(report.get("pending_environment") or []),
            "excluded_races": len(report.get("excluded_races") or []),
        }
        status["split_progress"] = {
            key: {"current": len(blocks.get(key) or []), "minimum": minimum}
            for key, minimum in MINIMUM_RACES.items()
        }
    pool = status["learning_eligible_races"]
    status["eligible_pool_progress"] = {
        "current": pool,
        "minimum_total": status["minimum_total"],
        "remaining": max(0, status["minimum_total"] - pool),
    }
    return status


def _target_ref(target: dict) -> dict:
    return {"race_id": target["race_id"], "race_name": target.get("race_name", ""),
            "track": target.get("track", ""), "race_number": target.get("race_number"),
            "post_time": target.get("post_time", "")}


def _write_run_log(root: Path, record: dict) -> Path:
    runs_dir = root / "report/environment_observations/stage0_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(JST).strftime("%Y%m%dT%H%M%S")
    path = runs_dir / f"{record['date']}_{record['mode']}_{stamp}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def main(argv: list[str] | None = None, *, now: datetime | None = None,
         scraper=None, capture_runner=None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(
        description="Stage 0 day-level environment capture driver")
    parser.add_argument("--date", default=None,
                        help="Target date YYYY-MM-DD (default: today, JST)")
    parser.add_argument("--mode", choices=("morning", "refresh"), default="morning",
                        help="morning: first capture of the day; refresh: pre-post update")
    parser.add_argument("--dry-run", action="store_true",
                        help="Discovery only; no capture and no run log")
    parser.add_argument("--status", action="store_true",
                        help="Print accumulated counts and minimum progress")
    args = parser.parse_args(argv)

    if args.status:
        print(json.dumps(collect_status(root), ensure_ascii=False, indent=2))
        return 0

    now_jst = (now or datetime.now(timezone.utc)).astimezone(JST)
    race_date = args.date or now_jst.date().isoformat()
    try:
        datetime.fromisoformat(race_date)
    except ValueError:
        parser.error(f"invalid --date: {race_date!r} (expected YYYY-MM-DD)")

    client = scraper or JRAScraper(build_scrape_config(root))
    url = program_url(race_date)
    html = client.fetch(url, raw_name=f"stage0_program_{race_date.replace('-', '')}.html",
                        use_cache=False)
    schedule = parse_program_page(html, race_date) if html else []
    configs = load_config_races(root / "config", race_date)
    targets = merge_targets(schedule, configs)

    links = []
    race_list_html = client.fetch_relative(
        build_scrape_config(root).race_list_path, raw_name="race_list.html",
        use_cache=False)
    if race_list_html:
        links = JRAParser(build_scrape_config(root).base_url).parse_race_list(
            race_list_html)
    resolve_source_urls(targets, links)

    if args.dry_run:
        print(json.dumps({
            "date": race_date, "mode": args.mode, "dry_run": True,
            "program_url": url, "program_found": bool(html),
            "targets": [dict(t, source_url_resolved=bool(t.get("source_url")))
                        for t in targets],
        }, ensure_ascii=False, indent=2))
        return 0

    record: dict = {"date": race_date, "mode": args.mode,
                    "started_at": now_jst.isoformat(), "program_url": url,
                    "program_found": bool(html),
                    "targets": [_target_ref(t) for t in targets],
                    "captured": [], "skipped": []}
    observations_dir = root / "report/environment_observations"
    if race_date != now_jst.date().isoformat():
        for target in targets:
            record["skipped"].append(dict(_target_ref(target), reason="not_race_day"))
    else:
        to_capture = []
        for target in targets:
            if not target.get("source_url"):
                record["skipped"].append(
                    dict(_target_ref(target), reason="source_url_unavailable"))
                continue
            snapshots = load_snapshots(observations_dir, target["race_id"])
            action, reason = decide_action(target, now_jst, args.mode, snapshots)
            if action == "skip":
                record["skipped"].append(dict(_target_ref(target), reason=reason))
            else:
                to_capture.append(target)
        if to_capture:
            runner = capture_runner or run_capture
            results = runner(to_capture, root, now_jst)
            for target in to_capture:
                snap = results.get(target["race_id"])
                if snap is None:
                    record["skipped"].append(
                        dict(_target_ref(target), reason="capture_failed"))
                    continue
                record["captured"].append({
                    "race_id": target["race_id"],
                    "status": snap.get("status"),
                    "collection_complete": bool(snap.get("collection_complete")),
                    "eligible_for_learning": bool(snap.get("eligible_for_learning")),
                    "captured_at": snap.get("captured_at"),
                    "observation_path": snap.get("observation_path"),
                })
    log_path = _write_run_log(root, record)
    record["run_log"] = str(log_path)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
