from __future__ import annotations

import csv
import hashlib
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .config import ScrapeConfig
from .models import ParserIssue, RaceLink
from .parser import JRAParser
from .scraper import JRAScraper, safe_filename
from .validation import ENTRY_COLUMNS, OUTPUT_COLUMNS, build_entry_rows, validate_rows

LIVE_ODDS_SNAPSHOT_COLUMNS = [
    "race_id",
    "horse_id",
    "horse_name",
    "horse_number",
    "current_odds",
    "current_popularity",
    "captured_at",
]

LIVE_COMBO_ODDS_COLUMNS = [
    "race_id",
    "bet_type",
    "combination",
    "odds",
    "odds_min",
    "odds_max",
    "captured_at",
    "source_cname",
]


logger = logging.getLogger(__name__)


class JRAPipeline:
    """Persistent pipeline with caching, repair logs, and race-level entry outputs."""

    def __init__(self, config: ScrapeConfig | None = None) -> None:
        self.config = config or ScrapeConfig()
        self.config.ensure_dirs()
        self.scraper = JRAScraper(self.config)
        self.parser = JRAParser(self.config.base_url)

    def run(
        self,
        race_limit: int | None = None,
        horse_limit: int | None = None,
        *,
        race_urls: list[str] | None = None,
        race_specs: list[dict[str, object]] | None = None,
        reprocess_raw: bool = False,
        force_rebuild: bool = False,
        aggressive_repair: bool = False,
    ) -> list[dict[str, str]]:
        logger.info(
            "Pipeline start race_limit=%s horse_limit=%s race_specs=%s race_urls=%s reprocess_raw=%s force_rebuild=%s aggressive_repair=%s",
            race_limit,
            horse_limit,
            len(race_specs) if race_specs else 0,
            len(race_urls) if race_urls else 0,
            reprocess_raw,
            force_rebuild,
            aggressive_repair,
        )

        state = self._load_state()
        existing_rows = [] if force_rebuild else self._read_existing_rows(self.config.output_csv)
        processed_races = set(state.get("processed_race_ids", []))
        failures = state.get("failures", {})
        all_new_rows: list[dict[str, str]] = []
        all_combo_odds_rows: list[dict[str, str]] = []
        processed_this_run: list[str] = []
        issues: list[ParserIssue] = []
        missing_history_requests: list[dict[str, object]] = []
        manual_history_rows = self._read_existing_rows(self.config.manual_history_csv)

        races = self._resolve_races(
            race_specs=race_specs,
            race_urls=race_urls,
            race_limit=race_limit,
            reprocess_raw=reprocess_raw,
            issues=issues,
        )
        if not races:
            self._write_csv(existing_rows, self.config.output_csv, OUTPUT_COLUMNS)
            self._write_csv(build_entry_rows(existing_rows), self.config.entries_csv, ENTRY_COLUMNS)
            self._save_state(processed_races, failures, 0, 0)
            self._write_quality_report(issues, build_entry_rows(existing_rows), existing_rows)
            return existing_rows

        for race in races:
            if not force_rebuild and race.race_id in processed_races:
                logger.info("Skip already processed race race_id=%s", race.race_id)
                continue

            race_raw_name = f"race_{safe_filename(race.race_id)}.html"
            race_html = self.scraper.fetch(
                race.race_url,
                raw_name=race_raw_name,
                use_cache=reprocess_raw or not force_rebuild,
                cache_only=reprocess_raw,
            )
            if not race_html:
                failures[race.race_url] = failures.get(race.race_url, 0) + 1
                logger.warning("Skip race due to missing html: %s", race.race_url)
                continue

            try:
                target_conditions_captured_at = _raw_file_captured_at(
                    self.config.raw_dir / race_raw_name
                )
                horses = self.parser.parse_race_detail(
                    race_html,
                    race.race_id,
                    race.race_name,
                    target_race_date=race.race_date,
                    target_track=race.track,
                    target_race_number=race.race_number,
                    target_surface=race.target_surface,
                    target_distance=race.target_distance,
                    target_weather=race.target_weather,
                    target_track_condition=race.target_track_condition,
                    target_conditions_captured_at=target_conditions_captured_at,
                    issue_sink=issues,
                    aggressive_repair=aggressive_repair,
                )
            except ValueError as exc:
                failures[race.race_url] = failures.get(race.race_url, 0) + 1
                logger.warning("Skip race due to parse failure race_id=%s err=%s", race.race_id, exc)
                continue

            combo_odds_rows = self._fetch_combo_odds_rows(
                race,
                race_html,
                reprocess_raw=reprocess_raw,
                force_refresh=force_rebuild,
                issues=issues,
            )
            all_combo_odds_rows.extend(combo_odds_rows)

            if horse_limit is not None:
                horses = horses[:horse_limit]
            logger.info("Race=%s horses=%d", race.race_id, len(horses))

            race_rows: list[dict[str, str]] = []
            race_failed = False
            for horse in horses:
                embedded_rows = _rows_from_embedded_history(horse)
                if len(embedded_rows) >= 5:
                    race_rows.extend(embedded_rows)
                    continue

                horse_html = self.scraper.fetch(
                    horse.horse_url,
                    raw_name=f"horse_{safe_filename(horse.horse_id)}.html",
                    use_cache=True,
                    cache_only=reprocess_raw,
                )
                if not horse_html:
                    failures[horse.horse_url] = failures.get(horse.horse_url, 0) + 1
                    logger.warning("Skip horse due to missing html: %s", horse.horse_url)
                    manual_rows = _manual_rows_for_horse(horse, manual_history_rows)
                    rows = _merge_history_rows(
                        embedded_rows,
                        manual_rows,
                        target_race_date=horse.target_race_date,
                    )
                    if rows:
                        race_rows.extend(rows)
                        if len(rows) < 5:
                            missing_history_requests.append(
                                _missing_history_request(
                                    horse,
                                    history_count=len(rows),
                                    manual_history_csv=self.config.manual_history_csv,
                                )
                            )
                            issues.append(
                                ParserIssue(
                                    stage="pipeline.history",
                                    severity="medium",
                                    code="history_incomplete",
                                    message="Embedded history had fewer than five runs and horse detail was unavailable.",
                                    context={
                                        "race_id": horse.race_id,
                                        "horse_name": horse.horse_name,
                                        "history_count": str(len(rows)),
                                    },
                                )
                            )
                    else:
                        race_failed = True
                    continue

                detail_issues: list[ParserIssue] = []
                detail_rows = self.parser.parse_horse_last5(
                    horse_html,
                    race_id=horse.race_id,
                    horse_id=horse.horse_id,
                    horse_name=horse.horse_name,
                    horse_url=horse.horse_url,
                    current_entry=horse,
                    issue_sink=detail_issues,
                    aggressive_repair=aggressive_repair,
                )
                manual_rows = _manual_rows_for_horse(horse, manual_history_rows)
                rows = _merge_history_rows(
                    embedded_rows,
                    detail_rows + manual_rows,
                    target_race_date=horse.target_race_date,
                )
                issues.extend(_issues_for_selected_history(detail_issues, rows))
                if not rows:
                    race_failed = True
                elif len(rows) < 5:
                    missing_history_requests.append(
                        _missing_history_request(
                            horse,
                            history_count=len(rows),
                            manual_history_csv=self.config.manual_history_csv,
                        )
                    )
                    issues.append(
                        ParserIssue(
                            stage="pipeline.history",
                            severity="medium",
                            code="history_incomplete",
                            message="Fewer than five history rows were available after merging sources.",
                            context={
                                "race_id": horse.race_id,
                                "horse_name": horse.horse_name,
                                "history_count": str(len(rows)),
                            },
                        )
                    )
                race_rows.extend(rows)

            all_new_rows.extend(race_rows)
            if not race_failed:
                processed_races.add(race.race_id)
                processed_this_run.append(race.race_id)

        final_rows = validate_rows(existing_rows + all_new_rows)
        entry_rows = build_entry_rows(final_rows)
        self._write_csv(final_rows, self.config.output_csv, OUTPUT_COLUMNS)
        self._write_csv(entry_rows, self.config.entries_csv, ENTRY_COLUMNS)
        append_live_odds_snapshots(self.config.odds_snapshots_csv, entry_rows)
        append_live_combo_odds(self.config.combo_odds_csv, all_combo_odds_rows)
        self._save_state(processed_races, failures, len(processed_this_run), len(all_new_rows))
        self._write_quality_report(issues, entry_rows, final_rows)
        self._write_missing_history_requests(missing_history_requests)

        logger.info(
            "Pipeline complete total_rows=%d entry_rows=%d new_rows=%d processed_races=%d",
            len(final_rows),
            len(entry_rows),
            len(all_new_rows),
            len(processed_this_run),
        )
        return final_rows

    def close(self) -> None:
        self.scraper.close()

    def _resolve_races(
        self,
        *,
        race_specs: list[dict[str, object]] | None,
        race_urls: list[str] | None,
        race_limit: int | None,
        reprocess_raw: bool,
        issues: list[ParserIssue],
    ) -> list[RaceLink]:
        if race_specs:
            races = [self._race_from_spec(spec) for spec in race_specs]
            if race_limit is not None:
                races = races[:race_limit]
            return races

        if race_urls:
            races = [self._race_from_spec({"source_url": url}) for url in race_urls]
            if race_limit is not None:
                races = races[:race_limit]
            return races

        race_list_html = self.scraper.fetch_relative(
            self.config.race_list_path,
            raw_name="race_list.html",
            use_cache=True,
            cache_only=reprocess_raw,
        )
        if not race_list_html:
            logger.error("Race list unavailable.")
            issues.append(
                ParserIssue(
                    stage="pipeline",
                    severity="high",
                    code="race_list_unavailable",
                    message="Race list HTML was unavailable.",
                    context={"race_list_path": self.config.race_list_path},
                )
            )
            return []

        races = self.parser.parse_race_list(race_list_html)
        if race_limit is not None:
            races = races[:race_limit]
        return races

    @staticmethod
    def _write_csv(rows: list[dict[str, str]], output_csv: Path, fieldnames: list[str]) -> None:
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        with output_csv.open("w", encoding="utf-8", newline="") as file_obj:
            writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key, "") for key in fieldnames})

    @staticmethod
    def _read_existing_rows(path: Path) -> list[dict[str, str]]:
        if not path.exists() or path.stat().st_size == 0:
            return []
        with path.open("r", encoding="utf-8", newline="") as file_obj:
            return list(csv.DictReader(file_obj))

    def _load_state(self) -> dict:
        if not self.config.state_path.exists():
            return {"processed_race_ids": [], "failures": {}}
        with self.config.state_path.open("r", encoding="utf-8") as file_obj:
            return json.load(file_obj)

    def _save_state(
        self,
        processed_races: set[str],
        failures: dict[str, int],
        processed_count: int,
        new_rows: int,
    ) -> None:
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "processed_race_ids": sorted(processed_races),
            "failures": failures,
            "last_run": {
                "processed_races": processed_count,
                "new_rows": new_rows,
            },
        }
        self.config.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self.config.state_path.open("w", encoding="utf-8") as file_obj:
            json.dump(payload, file_obj, ensure_ascii=False, indent=2)

    def _write_quality_report(
        self,
        issues: list[ParserIssue],
        entry_rows: list[dict[str, str]],
        history_rows: list[dict[str, str]] | None = None,
    ) -> None:
        severity_counts = Counter(issue.severity for issue in issues)
        code_counts = Counter(issue.code for issue in issues)
        selected_history_rows = list(history_rows or [])
        fallback_count = int(code_counts.get("last3f_fallback", 0))
        missing_last3f_count = sum(1 for row in selected_history_rows if not str(row.get("last_3f", "")).strip())
        observed_last3f_count = max(0, len(selected_history_rows) - fallback_count - missing_last3f_count)
        snapshot_rows = self._read_existing_rows(self.config.odds_snapshots_csv)
        combo_odds_rows = self._read_existing_rows(self.config.combo_odds_csv)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "issue_count": len(issues),
            "issues_by_severity": dict(severity_counts),
            "issues_by_code": dict(code_counts),
            "repaired_row_count": sum(1 for issue in issues if issue.code in {"row_padding", "row_merge"}),
            "missing_current_odds_entries": sum(1 for row in entry_rows if not row.get("current_odds")),
            "incomplete_history_entries": sum(
                1 for row in entry_rows if int(str(row.get("history_count") or "0")) < 5
            ),
            "history_row_count": len(selected_history_rows),
            "last3f_observed_rows": observed_last3f_count,
            "last3f_fallback_rows": fallback_count,
            "last3f_missing_rows": missing_last3f_count,
            "last3f_observed_rate": (
                round(observed_last3f_count / len(selected_history_rows), 6)
                if selected_history_rows
                else 0.0
            ),
            "entry_count": len(entry_rows),
            "live_snapshot_count": len(snapshot_rows),
            "live_combo_odds_count": len(combo_odds_rows),
            "issues": [issue.to_dict() for issue in issues],
        }
        with self.config.quality_report_path.open("w", encoding="utf-8") as file_obj:
            json.dump(payload, file_obj, ensure_ascii=False, indent=2)

    def _write_missing_history_requests(self, requests: list[dict[str, object]]) -> None:
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "action_required" if requests else "complete",
            "fallback_policy": "continue_with_neutral_score_0.5",
            "manual_history_csv": str(self.config.manual_history_csv),
            "requests": requests,
        }
        with self.config.missing_history_requests_path.open("w", encoding="utf-8") as file_obj:
            json.dump(payload, file_obj, ensure_ascii=False, indent=2)

    def _race_from_spec(self, spec: dict[str, object]) -> RaceLink:
        race_name = str(spec.get("race_name", "")).strip()
        race_url = str(spec.get("source_url") or spec.get("race_url") or "").strip()
        race_date = str(spec.get("race_date", "")).strip()
        track = str(spec.get("track", "")).strip()
        race_number = str(spec.get("race_number", "")).strip()
        target_surface = str(spec.get("target_surface") or spec.get("surface") or spec.get("surface_label") or "").strip()
        target_distance = str(spec.get("target_distance") or spec.get("distance") or spec.get("distance_label") or "").strip()
        target_weather = str(spec.get("target_weather") or spec.get("weather") or "").strip()
        target_track_condition = str(
            spec.get("target_track_condition") or spec.get("track_condition") or ""
        ).strip()

        race_id = str(spec.get("race_id", "")).strip()
        if not race_id and race_date and track and race_number:
            compact_date = race_date.replace("-", "")
            race_id = f"{compact_date}_{track}_{int(race_number):02d}"
        if not race_id and race_url:
            race_id = self._build_direct_race_id(race_url)
        if not race_name:
            race_name = f"{track}{race_number}R" if track or race_number else f"direct_race_{race_id[:8]}"

        return RaceLink(
            race_id=race_id,
            race_name=race_name,
            race_url=race_url,
            race_date=race_date,
            track=track,
            race_number=race_number,
            target_surface=target_surface,
            target_distance=target_distance,
            target_weather=target_weather,
            target_track_condition=target_track_condition,
        )

    @staticmethod
    def _build_direct_race_id(race_url: str) -> str:
        return f"direct_{hashlib.md5(race_url.encode('utf-8')).hexdigest()[:12]}"

    def _fetch_combo_odds_rows(
        self,
        race: RaceLink,
        race_html: str,
        *,
        reprocess_raw: bool,
        force_refresh: bool,
        issues: list[ParserIssue],
    ) -> list[dict[str, str]]:
        captured_at = datetime.now(timezone.utc).isoformat()
        initial_cname = self.parser.extract_initial_odds_cname(race_html)
        if not initial_cname:
            return []

        odds_url = f"{self.config.base_url}/JRADB/accessO.html"
        first_html = self.scraper.fetch_post(
            odds_url,
            {"CNAME": initial_cname},
            raw_name=f"odds_{safe_filename(race.race_id)}_win_place.html",
            use_cache=reprocess_raw or not force_refresh,
            cache_only=reprocess_raw,
        )
        if not first_html:
            issues.append(
                ParserIssue(
                    stage="pipeline.odds",
                    severity="low",
                    code="odds_page_unavailable",
                    message="Could not fetch the JRA odds page.",
                    context={"race_id": race.race_id, "cname": initial_cname},
                )
            )
            return []

        rows = self.parser.parse_odds_page(
            first_html,
            race_id=race.race_id,
            source_cname=initial_cname,
            captured_at=captured_at,
        )
        cnames = self.parser.extract_odds_cnames(first_html)
        cnames.setdefault("win_place", initial_cname)
        for bet_type, cname in sorted(cnames.items()):
            if cname == initial_cname:
                continue
            html = self.scraper.fetch_post(
                odds_url,
                {"CNAME": cname},
                raw_name=f"odds_{safe_filename(race.race_id)}_{bet_type}.html",
                use_cache=reprocess_raw or not force_refresh,
                cache_only=reprocess_raw,
            )
            if not html:
                continue
            rows.extend(
                self.parser.parse_odds_page(
                    html,
                    race_id=race.race_id,
                    source_cname=cname,
                    captured_at=captured_at,
                )
            )
        return _dedupe_combo_odds_rows(rows)


def append_live_odds_snapshots(
    snapshot_path: Path,
    entry_rows: list[dict[str, str]],
    *,
    captured_at: str | None = None,
) -> list[dict[str, str]]:
    if not entry_rows:
        return []

    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    captured_at = captured_at or datetime.now(timezone.utc).isoformat()
    snapshots = [
        {
            "race_id": str(row.get("race_id", "")).strip(),
            "horse_id": str(row.get("horse_id", "")).strip(),
            "horse_name": str(row.get("horse_name", "")).strip(),
            "horse_number": str(row.get("horse_number", "")).strip(),
            "current_odds": str(row.get("current_odds", "")).strip(),
            "current_popularity": str(row.get("current_popularity", "")).strip(),
            "captured_at": captured_at,
        }
        for row in entry_rows
    ]

    existing_keys: set[tuple[str, str, str]] = set()
    if snapshot_path.exists() and snapshot_path.stat().st_size > 0:
        with snapshot_path.open("r", encoding="utf-8", newline="") as file_obj:
            for row in csv.DictReader(file_obj):
                existing_keys.add(
                    (
                        str(row.get("race_id", "")).strip(),
                        str(row.get("horse_number", "")).strip(),
                        str(row.get("captured_at", "")).strip(),
                    )
                )

    pending_rows = [
        row
        for row in snapshots
        if (
            row["race_id"],
            row["horse_number"],
            row["captured_at"],
        ) not in existing_keys
    ]
    if not pending_rows:
        return []

    write_header = not snapshot_path.exists() or snapshot_path.stat().st_size == 0
    with snapshot_path.open("a", encoding="utf-8", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=LIVE_ODDS_SNAPSHOT_COLUMNS)
        if write_header:
            writer.writeheader()
        for row in pending_rows:
            writer.writerow(row)
    return pending_rows


def append_live_combo_odds(
    combo_odds_path: Path,
    combo_odds_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    if not combo_odds_rows:
        return []

    combo_odds_path.parent.mkdir(parents=True, exist_ok=True)
    existing_keys: set[tuple[str, str, str, str]] = set()
    if combo_odds_path.exists() and combo_odds_path.stat().st_size > 0:
        with combo_odds_path.open("r", encoding="utf-8", newline="") as file_obj:
            for row in csv.DictReader(file_obj):
                existing_keys.add(
                    (
                        str(row.get("race_id", "")).strip(),
                        str(row.get("bet_type", "")).strip(),
                        str(row.get("combination", "")).strip(),
                        str(row.get("captured_at", "")).strip(),
                    )
                )

    pending_rows = [
        row
        for row in _dedupe_combo_odds_rows(combo_odds_rows)
        if (
            str(row.get("race_id", "")).strip(),
            str(row.get("bet_type", "")).strip(),
            str(row.get("combination", "")).strip(),
            str(row.get("captured_at", "")).strip(),
        )
        not in existing_keys
    ]
    if not pending_rows:
        return []

    write_header = not combo_odds_path.exists() or combo_odds_path.stat().st_size == 0
    with combo_odds_path.open("a", encoding="utf-8", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=LIVE_COMBO_ODDS_COLUMNS)
        if write_header:
            writer.writeheader()
        for row in pending_rows:
            writer.writerow({key: row.get(key, "") for key in LIVE_COMBO_ODDS_COLUMNS})
    return pending_rows


def _dedupe_combo_odds_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str, str]] = set()
    out: list[dict[str, str]] = []
    for row in rows:
        key = (
            str(row.get("race_id", "")).strip(),
            str(row.get("bet_type", "")).strip(),
            str(row.get("combination", "")).strip(),
            str(row.get("captured_at", "")).strip(),
        )
        if not all(key) or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _raw_file_captured_at(path: Path) -> str:
    if not path.exists():
        return ""
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _rows_from_embedded_history(horse) -> list[dict[str, str]]:
    embedded_history = list(getattr(horse, "embedded_history", []) or [])
    if not embedded_history:
        return []

    rows: list[dict[str, str]] = []
    for run_idx, history in enumerate(embedded_history[:5], start=1):
        row = {
            "race_id": horse.race_id,
            "horse_id": horse.horse_id,
            "horse_name": horse.horse_name,
            "horse_url": horse.horse_url,
            "run_index": str(history.get("run_index") or run_idx),
            "frame_number": horse.frame_number,
            "horse_number": horse.horse_number,
            "current_jockey": horse.current_jockey,
            "assigned_weight": horse.assigned_weight,
            "current_odds": horse.current_odds,
            "current_popularity": horse.current_popularity,
            "target_track": horse.target_track,
            "target_race_date": horse.target_race_date,
            "target_race_number": horse.target_race_number,
            "target_surface": horse.target_surface,
            "target_distance": horse.target_distance,
            "target_weather": horse.target_weather,
            "target_track_condition": horse.target_track_condition,
            "target_conditions_captured_at": horse.target_conditions_captured_at,
            "horse_country": horse.horse_country,
            "date": str(history.get("date", "")),
            "course": str(history.get("course", "")),
            "race_name": str(history.get("race_name", "")),
            "distance": str(history.get("distance", "")),
            "position": str(history.get("position", "")),
            "time": str(history.get("time", "")),
            "weight": str(history.get("weight", "")),
            "jockey": str(history.get("jockey", "")),
            "pace": str(history.get("pace", "")),
            "last_3f": str(history.get("last_3f", "")),
            "last_3f_source": str(history.get("last_3f_source", "embedded")),
            "track_condition": str(history.get("track_condition", "")),
            "weather": str(history.get("weather", "")),
            "passing_order": str(history.get("passing_order", "")),
            "odds": str(history.get("odds", "")),
            "popularity": str(history.get("popularity", "")),
        }
        if not row["last_3f"]:
            row["last_3f"] = JRAParser.LAST_3F_NEUTRAL_BASELINE
            row["last_3f_source"] = "fallback"
        rows.append(row)
    return rows


def _merge_history_rows(
    embedded_rows: list[dict[str, str]],
    detail_rows: list[dict[str, str]],
    *,
    target_race_date: str = "",
) -> list[dict[str, str]]:
    """Merge newest-first history sources and return exactly the best available last five."""
    merged: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    target_date_key = _compact_date_key(target_race_date)
    for row in list(embedded_rows) + list(detail_rows):
        history_date_key = _compact_date_key(str(row.get("date", "")))
        if target_date_key and history_date_key and history_date_key >= target_date_key:
            continue
        key = (
            str(row.get("date", "")).strip(),
            str(row.get("race_name", "")).strip(),
            str(row.get("course", "")).strip(),
        )
        if not any(key) or key in seen:
            continue
        seen.add(key)
        normalized = dict(row)
        normalized["run_index"] = str(len(merged) + 1)
        merged.append(normalized)
        if len(merged) == 5:
            break
    return merged


def _issues_for_selected_history(
    detail_issues: list[ParserIssue],
    selected_rows: list[dict[str, str]],
) -> list[ParserIssue]:
    """Keep parser diagnostics only when they affect the five rows used downstream."""
    retained = [
        issue
        for issue in detail_issues
        if issue.code != "last3f_fallback"
        and not (issue.code == "history_header_missing" and "last_3f" in issue.message)
    ]
    fallback_rows = [
        row
        for row in selected_rows
        if str(row.get("last_3f_source", "")).strip() == "fallback"
        or not str(row.get("last_3f", "")).strip()
    ]
    if not fallback_rows:
        return retained

    missing_header = next(
        (
            issue
            for issue in detail_issues
            if issue.code == "history_header_missing" and "last_3f" in issue.message
        ),
        None,
    )
    if missing_header is not None:
        retained.append(missing_header)

    for row in fallback_rows:
        retained.append(
            ParserIssue(
                stage="pipeline.history",
                severity="medium",
                code="last3f_fallback",
                message="Selected history row uses the neutral last_3f fallback.",
                context={
                    "race_id": str(row.get("race_id", "")),
                    "horse_name": str(row.get("horse_name", "")),
                    "run_index": str(row.get("run_index", "")),
                },
            )
        )
    return retained


def _manual_rows_for_horse(horse, manual_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    matched: list[dict[str, str]] = []
    for row in manual_rows:
        row_horse_id = str(row.get("horse_id", "")).strip()
        row_horse_name = str(row.get("horse_name", "")).strip()
        if row_horse_id and row_horse_id != horse.horse_id:
            continue
        if not row_horse_id and row_horse_name != horse.horse_name:
            continue
        enriched = dict(row)
        enriched["last_3f_source"] = "manual" if str(row.get("last_3f", "")).strip() else "fallback"
        enriched.update(
            {
                "race_id": horse.race_id,
                "horse_id": horse.horse_id,
                "horse_name": horse.horse_name,
                "horse_url": horse.horse_url,
                "frame_number": horse.frame_number,
                "horse_number": horse.horse_number,
                "current_jockey": horse.current_jockey,
                "assigned_weight": horse.assigned_weight,
                "current_odds": horse.current_odds,
                "current_popularity": horse.current_popularity,
                "target_track": horse.target_track,
                "target_race_date": horse.target_race_date,
                "target_race_number": horse.target_race_number,
                "target_surface": horse.target_surface,
                "target_distance": horse.target_distance,
                "target_weather": horse.target_weather,
                "target_track_condition": horse.target_track_condition,
                "target_conditions_captured_at": horse.target_conditions_captured_at,
                "horse_country": horse.horse_country,
            }
        )
        matched.append(enriched)
    return matched


def _missing_history_request(horse, *, history_count: int, manual_history_csv: Path) -> dict[str, object]:
    return {
        "race_id": horse.race_id,
        "horse_id": horse.horse_id,
        "horse_name": horse.horse_name,
        "horse_url": horse.horse_url,
        "history_count": history_count,
        "missing_count": max(0, 5 - history_count),
        "fallback_score": 0.5,
        "fallback_reason": "user_unavailable_or_manual_data_not_stored",
        "action": "Research missing prior runs and append them to the manual history CSV.",
        "manual_history_csv": str(manual_history_csv),
    }


def _compact_date_key(value: str) -> str:
    normalized = (
        value.strip()
        .replace("年", "-")
        .replace("月", "-")
        .replace("日", "")
        .replace("/", "-")
        .replace(".", "-")
    )
    parts = [part for part in normalized.split("-") if part]
    if len(parts) >= 3 and all(part.isdigit() for part in parts[:3]):
        year, month, day = parts[:3]
        return f"{int(year):04d}{int(month):02d}{int(day):02d}"
    digits = "".join(character for character in value if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""
