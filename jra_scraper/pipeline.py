from __future__ import annotations

import csv
import hashlib
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .acquisition_report import (
    AUDITED_FIELDS,
    ROUTE_REGISTRY,
    AcquisitionRecorder,
    build_acquisition_report,
    jurisdiction_for_row,
    write_acquisition_bundle,
)
from .config import ScrapeConfig
from .data_repair import MANUAL_HISTORY_COLUMNS, MissingHistoryRepairAction, missing_history_request
from .history_observations import SEMANTIC_FIELDS
from .models import HorseEntry, ParserIssue, RaceLink
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
        self.acquisition_recorder = AcquisitionRecorder()
        self.last_acquisition_reports: list[dict[str, str]] = []
        self._foreign_horse_names: dict[str, str] | None = None
        self._france_identity_fetcher = None

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
        missing_history_template_rows: list[dict[str, str]] = []
        repair_actions: list[dict[str, object]] = []
        manual_history_rows = self._read_existing_rows(self.config.manual_history_csv)
        history_repair = MissingHistoryRepairAction(
            self.config.manual_history_csv,
            self.config.manual_history_template_csv,
        )

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
            self._write_quality_report(issues, build_entry_rows(existing_rows), existing_rows, [])
            self._write_manual_history_template([])
            return existing_rows

        for race in races:
            if not force_rebuild and race.race_id in processed_races:
                logger.info("Skip already processed race race_id=%s", race.race_id)
                continue
            result = self._process_race(
                race,
                horse_limit=horse_limit,
                reprocess_raw=reprocess_raw,
                force_rebuild=force_rebuild,
                aggressive_repair=aggressive_repair,
                manual_history_rows=manual_history_rows,
                history_repair=history_repair,
                failures=failures,
                issues=issues,
                repair_actions=repair_actions,
                missing_history_requests=missing_history_requests,
                missing_history_template_rows=missing_history_template_rows,
            )
            if result is None:
                continue
            race_rows, combo_odds_rows, race_failed = result
            all_combo_odds_rows.extend(combo_odds_rows)
            all_new_rows.extend(race_rows)
            if not race_failed:
                processed_races.add(race.race_id)
                processed_this_run.append(race.race_id)

        return self._write_run_outputs(
            existing_rows=existing_rows,
            all_new_rows=all_new_rows,
            all_combo_odds_rows=all_combo_odds_rows,
            processed_this_run=processed_this_run,
            processed_races=processed_races,
            failures=failures,
            issues=issues,
            repair_actions=repair_actions,
            missing_history_requests=missing_history_requests,
            missing_history_template_rows=missing_history_template_rows,
        )

    def _record_fetch_attempt(
        self,
        route: str,
        *,
        url: str,
        raw_name: str,
        fetched: bool,
        existed_before: bool,
        race_id: str = "",
        horse_id: str = "",
        horse_number: str = "",
        fields: tuple[str, ...] = (),
    ) -> None:
        """Record one fetch exactly as it happened, bound to its raw file."""
        raw_path = self.config.raw_dir / raw_name
        artifact_sha256 = ""
        artifact_path = ""
        if raw_path.exists():
            artifact_sha256 = hashlib.sha256(raw_path.read_bytes()).hexdigest()
            artifact_path = str(raw_path.resolve())
        outcome = "unavailable" if not fetched else ("cache_hit" if existed_before else "fetched")
        self.acquisition_recorder.record(
            route,
            url=url,
            outcome=outcome,
            race_id=race_id,
            horse_id=horse_id,
            horse_number=horse_number,
            fields=fields,
            artifact_sha256=artifact_sha256,
            artifact_path=artifact_path,
        )

    def _record_manual_consultation(self, horse, manual_rows: list[dict[str, str]]) -> None:
        self.acquisition_recorder.record(
            "manual_history_overrides",
            outcome="consulted",
            race_id=horse.race_id,
            horse_id=horse.horse_id,
            horse_number=horse.horse_number,
            fields=AUDITED_FIELDS,
            reason=f"{len(manual_rows)} manual override rows consulted for this horse",
        )

    def _record_repair_outcome(self, horse, reason: str, repair_result) -> None:
        status = ""
        if repair_result.repair_actions:
            status = str(repair_result.repair_actions[0].get("status", ""))
        self.acquisition_recorder.record(
            "jra_horse_history",
            url=horse.horse_url,
            outcome=reason,
            race_id=horse.race_id,
            horse_id=horse.horse_id,
            horse_number=horse.horse_number,
            fields=AUDITED_FIELDS,
            reason=f"history repair outcome: {status}" if status else "history repair outcome",
        )

    def _foreign_horse_names_map(self) -> dict[str, str]:
        """Operator-supplied English names; never guess an unmapped name."""
        if self._foreign_horse_names is None:
            path = self.config.manual_history_csv.parent / "foreign_horse_names.json"
            mapping: dict[str, str] = {}
            try:
                if path.exists():
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(payload, dict):
                        mapping = {str(key): str(value) for key, value in payload.items()}
            except (OSError, ValueError):
                mapping = {}
            self._foreign_horse_names = mapping
        return self._foreign_horse_names

    def _france_fetch_bytes(self, url: str) -> bytes | None:
        try:
            response = self.scraper.session.get(url, timeout=self.config.timeout)
            response.raise_for_status()
            return response.content
        except Exception:  # noqa: BLE001 - optional route; failure is recorded by caller
            return None

    def _fetch_text_cp932(self, url: str) -> str | None:
        try:
            response = self.scraper.session.get(url, timeout=self.config.timeout)
            response.raise_for_status()
            return response.content.decode("cp932")
        except Exception:  # noqa: BLE001 - optional route; absence is recorded by caller
            return None

    def _france_fetch_identity(self):
        if self._france_identity_fetcher is None:
            from jra_scraper import france_galop

            path = self.config.manual_history_csv.parent / "foreign_identity_sources.json"
            mapping = france_galop.load_identity_sources(path)
            self._france_identity_fetcher = france_galop.identity_fetcher_from_map(
                mapping, self._fetch_text_cp932
            )
        return self._france_identity_fetcher

    def _apply_france_history(
        self, horse, rows: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        """Optional France Galop route; failures are recorded, never fatal."""
        if not rows:
            return rows
        try:
            from jra_scraper import france_galop
        except Exception as exc:  # noqa: BLE001 - module ships separately
            logger.warning("France Galop module unavailable: %s", exc)
            return rows
        targets = [row for row in rows if france_galop.is_france_history_row(row)]
        if not targets:
            return rows
        names = self._foreign_horse_names_map()
        store_dir = self.config.raw_dir / "france_tracking"
        for row in targets:
            scope = {
                "race_id": str(row.get("race_id", "") or horse.race_id),
                "horse_id": str(row.get("horse_id", "") or horse.horse_id),
                "horse_number": str(row.get("horse_number", "") or horse.horse_number),
                "run_index": str(row.get("run_index", "")),
                "date": str(row.get("date", "")),
            }
            try:
                official_name = names.get(scope["horse_id"], "")
                if not official_name:
                    self.acquisition_recorder.record(
                        "france_galop_tracking",
                        outcome="identity_unresolved",
                        fields=("time", "last_3f"),
                        reason="No operator-supplied English name for this horse",
                        **scope,
                    )
                    continue
                result = france_galop.collect_france_history(
                    row,
                    official_horse_name=official_name,
                    fetch_bytes=self._france_fetch_bytes,
                    fetch_identity=self._france_fetch_identity(),
                    store_dir=store_dir,
                )
                attempt = dict(result.get("attempt") or {})
                self.acquisition_recorder.record(
                    str(attempt.get("route", "france_galop_tracking")),
                    url=str(attempt.get("url", "")),
                    outcome=str(attempt.get("outcome", "")),
                    race_id=str(attempt.get("race_id", "") or scope["race_id"]),
                    horse_id=str(attempt.get("horse_id", "") or scope["horse_id"]),
                    horse_number=str(attempt.get("horse_number", "") or scope["horse_number"]),
                    run_index=str(attempt.get("run_index", "") or scope["run_index"]),
                    date=str(row.get("date", "")),
                    fields=tuple(attempt.get("fields") or ()),
                    artifact_sha256=str(attempt.get("artifact_sha256", "")),
                    artifact_path=str(attempt.get("artifact_path", "")),
                    reason=str(attempt.get("reason", "")),
                )
                artifact = result.get("artifact")
                if artifact is not None:
                    self._store_foreign_artifact(artifact, row, scope)
                observation = result.get("observation")
                if observation is not None:
                    rows = _merge_history_rows(
                        rows, [observation], target_race_date=horse.target_race_date
                    )
            except Exception as exc:  # noqa: BLE001 - audit remains the gate
                logger.warning("France Galop route failed for horse_id=%s: %s",
                               scope["horse_id"], exc)
                self.acquisition_recorder.record(
                    "france_galop_tracking",
                    outcome="error",
                    fields=("time", "last_3f"),
                    reason=str(exc),
                    **scope,
                )
        return rows

    def _store_foreign_artifact(self, artifact: dict, row, scope: dict[str, str]) -> None:
        descriptor_dir = self.config.raw_dir / "foreign_artifacts"
        descriptor_dir.mkdir(parents=True, exist_ok=True)
        from .acquisition_report import iso_date

        date_iso = iso_date(artifact.get("date") or row.get("date", ""))
        filename = f"france_tracking_{date_iso}_{safe_filename(scope['horse_id'])}.json"
        (descriptor_dir / filename).write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        pdf_path = str(artifact.get("pdf_path", ""))
        pdf_sha256 = str(artifact.get("pdf_sha256", ""))
        if pdf_path and pdf_sha256:
            self.acquisition_recorder.register_artifact(
                pdf_path,
                pdf_sha256,
                kind="france_tracking_pdf",
                url=str(artifact.get("url", "")),
                race_id=scope["race_id"],
            )

    def _process_race(
        self,
        race: RaceLink,
        *,
        horse_limit: int | None,
        reprocess_raw: bool,
        force_rebuild: bool,
        aggressive_repair: bool,
        manual_history_rows: list[dict[str, str]],
        history_repair: MissingHistoryRepairAction,
        failures: dict[str, int],
        issues: list[ParserIssue],
        repair_actions: list[dict[str, object]],
        missing_history_requests: list[dict[str, object]],
        missing_history_template_rows: list[dict[str, str]],
    ) -> tuple[list[dict[str, str]], list[dict[str, str]], bool] | None:
        race_raw_name = f"race_{safe_filename(race.race_id)}.html"
        race_raw_existed = (self.config.raw_dir / race_raw_name).exists()
        race_html = self.scraper.fetch(
            race.race_url,
            raw_name=race_raw_name,
            use_cache=reprocess_raw or not force_rebuild,
            cache_only=reprocess_raw,
        )
        self._record_fetch_attempt(
            "jra_race_detail",
            url=race.race_url,
            raw_name=race_raw_name,
            fetched=bool(race_html),
            existed_before=race_raw_existed,
            race_id=race.race_id,
        )
        if not race_html:
            failures[race.race_url] = failures.get(race.race_url, 0) + 1
            logger.warning("Skip race due to missing html: %s", race.race_url)
            return None

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
            return None

        combo_odds_rows = self._fetch_combo_odds_rows(
            race,
            race_html,
            reprocess_raw=reprocess_raw,
            force_refresh=force_rebuild,
            issues=issues,
        )

        if horse_limit is not None:
            horses = horses[:horse_limit]
        logger.info("Race=%s horses=%d", race.race_id, len(horses))

        race_rows: list[dict[str, str]] = []
        race_failed = False
        for horse in horses:
            rows, horse_failed = self._process_horse(
                horse,
                reprocess_raw=reprocess_raw,
                force_rebuild=force_rebuild,
                aggressive_repair=aggressive_repair,
                manual_history_rows=manual_history_rows,
                history_repair=history_repair,
                failures=failures,
                issues=issues,
                repair_actions=repair_actions,
                missing_history_requests=missing_history_requests,
                missing_history_template_rows=missing_history_template_rows,
            )
            race_rows.extend(rows)
            self._record_route_determinations(rows)
            if horse_failed:
                race_failed = True
        return race_rows, combo_odds_rows, race_failed

    def _record_route_determinations(self, rows: list[dict[str, str]]) -> None:
        """Record, per history scope, that no official route exists for it.

        The collector genuinely evaluates the route registry for every
        overseas history row when deciding which foreign routes apply. For
        jurisdictions without a registered official route, that evaluation
        is recorded as an attempt carrying route_status so the audit's
        coverage layer can classify the absence as SOURCE_ROUTE_UNAVAILABLE
        (still blocked) instead of a generic missing field. Domestic-course
        rows are never classified overseas: single-kanji jurisdiction
        markers can collide with domestic race names (e.g. 愛 in 愛知杯).
        """
        domestic_courses = {'札幌', '函館', '福島', '新潟', '東京', '中山',
                            '中京', '京都', '阪神', '小倉'}
        for row in rows:
            if str(row.get('course', '')).strip() in domestic_courses:
                continue
            jurisdiction = jurisdiction_for_row(row)
            if jurisdiction == 'japan':
                continue
            registry = ROUTE_REGISTRY.get(jurisdiction, {})
            if registry.get('official_route_registered', False):
                continue
            reason = str(registry.get('reason', '')).strip() or (
                f'No official source route is registered in this system for '
                f'{jurisdiction}; the JRA official history is the only route.')
            self.acquisition_recorder.record(
                'route_registry',
                outcome='no_official_source_route',
                route_status='no_official_source_route',
                race_id=str(row.get('race_id', '')),
                horse_id=str(row.get('horse_id', '')),
                horse_number=str(row.get('horse_number', '')),
                run_index=str(row.get('run_index', '')),
                date=str(row.get('date', '')),
                fields=AUDITED_FIELDS,
                reason=reason,
            )

    def _process_horse(
        self,
        horse: HorseEntry,
        *,
        reprocess_raw: bool,
        force_rebuild: bool,
        aggressive_repair: bool,
        manual_history_rows: list[dict[str, str]],
        history_repair: MissingHistoryRepairAction,
        failures: dict[str, int],
        issues: list[ParserIssue],
        repair_actions: list[dict[str, object]],
        missing_history_requests: list[dict[str, object]],
        missing_history_template_rows: list[dict[str, str]],
    ) -> tuple[list[dict[str, str]], bool]:
        embedded_rows = _rows_from_embedded_history(horse)
        if len(embedded_rows) >= 5:
            return embedded_rows, False

        horse_raw_name = f"horse_{safe_filename(horse.horse_id)}.html"
        horse_raw_existed = (self.config.raw_dir / horse_raw_name).exists()
        horse_html = self.scraper.fetch(
            horse.horse_url,
            raw_name=horse_raw_name,
            use_cache=reprocess_raw or not force_rebuild,
            cache_only=reprocess_raw,
        )
        self._record_fetch_attempt(
            "jra_horse_history",
            url=horse.horse_url,
            raw_name=horse_raw_name,
            fetched=bool(horse_html),
            existed_before=horse_raw_existed,
            race_id=horse.race_id,
            horse_id=horse.horse_id,
            horse_number=horse.horse_number,
            fields=AUDITED_FIELDS,
        )
        if not horse_html:
            failures[horse.horse_url] = failures.get(horse.horse_url, 0) + 1
            logger.warning("Skip horse due to missing html: %s", horse.horse_url)
            manual_rows = _manual_rows_for_horse(horse, manual_history_rows)
            self._record_manual_consultation(horse, manual_rows)
            rows = _merge_history_rows(
                embedded_rows,
                manual_rows,
                target_race_date=horse.target_race_date,
            )
            rows = self._apply_france_history(horse, rows)
            repair_result = history_repair.build_result(
                horse,
                rows=rows,
                reason="horse_detail_html_unavailable",
                source_counts={
                    "embedded": len(embedded_rows),
                    "manual": len(manual_rows),
                    "detail": 0,
                },
                attempted_urls=[horse.horse_url],
            )
            self._record_repair_outcome(horse, "horse_detail_html_unavailable", repair_result)
            rows = repair_result.rows
            repair_actions.extend(repair_result.repair_actions)
            missing_history_requests.extend(repair_result.manual_requests)
            missing_history_template_rows.extend(repair_result.manual_template_rows)
            if rows:
                issues.extend(repair_result.issues)
            return rows, not rows

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
        self._record_manual_consultation(horse, manual_rows)
        rows = _merge_history_rows(
            embedded_rows,
            detail_rows + manual_rows,
            target_race_date=horse.target_race_date,
        )
        rows = self._apply_france_history(horse, rows)
        issues.extend(_issues_for_selected_history(detail_issues, rows))
        history_table_missing = any(
            issue.code == "history_table_missing" for issue in detail_issues
        )
        required_history_count = 5
        repair_reason = "history_sources_exhausted"
        if force_rebuild and not history_table_missing and len(detail_rows) < 5:
            # A successfully parsed, freshly fetched JRA horse profile lists
            # every completed start. Young horses with fewer than five starts
            # therefore have complete career history rather than missing rows.
            required_history_count = min(
                5,
                max(len(embedded_rows), len(detail_rows)),
            )
            repair_reason = "official_career_history_complete"
        repair_result = history_repair.build_result(
            horse,
            rows=rows,
            reason=repair_reason,
            source_counts={
                "embedded": len(embedded_rows),
                "manual": len(manual_rows),
                "detail": len(detail_rows),
            },
            attempted_urls=[horse.horse_url],
            required_history_count=required_history_count,
        )
        self._record_repair_outcome(horse, repair_reason, repair_result)
        rows = repair_result.rows
        repair_actions.extend(repair_result.repair_actions)
        missing_history_requests.extend(repair_result.manual_requests)
        missing_history_template_rows.extend(repair_result.manual_template_rows)
        if not rows:
            return rows, True
        if len(rows) < 5:
            issues.extend(repair_result.issues)
        return rows, False

    def _write_run_outputs(
        self,
        *,
        existing_rows: list[dict[str, str]],
        all_new_rows: list[dict[str, str]],
        all_combo_odds_rows: list[dict[str, str]],
        processed_this_run: list[str],
        processed_races: set[str],
        failures: dict[str, int],
        issues: list[ParserIssue],
        repair_actions: list[dict[str, object]],
        missing_history_requests: list[dict[str, object]],
        missing_history_template_rows: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        final_rows = validate_rows(existing_rows + all_new_rows)
        entry_rows = build_entry_rows(final_rows)
        self._write_csv(final_rows, self.config.output_csv, OUTPUT_COLUMNS)
        self._write_csv(entry_rows, self.config.entries_csv, ENTRY_COLUMNS)
        refreshed_race_ids = set(processed_this_run)
        append_live_odds_snapshots(
            self.config.odds_snapshots_csv,
            [row for row in entry_rows if row.get("race_id") in refreshed_race_ids],
        )
        append_live_combo_odds(self.config.combo_odds_csv, all_combo_odds_rows)
        self._save_state(processed_races, failures, len(processed_this_run), len(all_new_rows))
        acquisition_reports = self._write_acquisition_bundles(final_rows, processed_this_run)
        self.last_acquisition_reports = acquisition_reports
        self._write_quality_report(
            issues, entry_rows, final_rows, repair_actions,
            acquisition_reports=acquisition_reports,
        )
        self._write_missing_history_requests(missing_history_requests)
        self._write_manual_history_template(missing_history_template_rows)

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

    def _write_acquisition_bundles(
        self,
        final_rows: list[dict[str, str]],
        processed_this_run: list[str],
    ) -> list[dict[str, str]]:
        """One SHA-bound acquisition report per race processed in this run."""
        refs: list[dict[str, str]] = []
        generated_at = datetime.now(timezone.utc).isoformat()
        for race_id in processed_this_run:
            race_rows = [row for row in final_rows if str(row.get("race_id", "")) == race_id]
            try:
                report = build_acquisition_report(
                    race_id=race_id,
                    rows=race_rows,
                    recorder=self.acquisition_recorder,
                    generated_at=generated_at,
                )
            except ValueError:
                # No route was recorded at all; an honest bundle cannot be
                # built, and fabricating routes is not an option.
                logger.warning("No acquisition routes recorded for race_id=%s", race_id)
                continue
            evidence = {
                "artifact_index.json": {
                    "artifacts": self.acquisition_recorder.artifacts_for_race(race_id),
                }
            }
            ref = write_acquisition_bundle(
                self.config.raw_dir / "acquisition" / safe_filename(race_id),
                report,
                evidence,
            )
            refs.append({"race_id": race_id, **ref})
        return refs

    def _write_quality_report(
        self,
        issues: list[ParserIssue],
        entry_rows: list[dict[str, str]],
        history_rows: list[dict[str, str]] | None = None,
        repair_actions: list[dict[str, object]] | None = None,
        acquisition_reports: list[dict[str, str]] | None = None,
    ) -> None:
        severity_counts = Counter(issue.severity for issue in issues)
        code_counts = Counter(issue.code for issue in issues)
        selected_history_rows = list(history_rows or [])
        fallback_count = int(code_counts.get("last3f_fallback", 0))
        missing_last3f_count = sum(1 for row in selected_history_rows if not str(row.get("last_3f", "")).strip())
        observed_last3f_count = max(0, len(selected_history_rows) - fallback_count - missing_last3f_count)
        snapshot_rows = self._read_existing_rows(self.config.odds_snapshots_csv)
        combo_odds_rows = self._read_existing_rows(self.config.combo_odds_csv)
        required_history_counts = {
            (str(action.get("race_id", "")), str(action.get("horse_id", ""))): int(
                action.get("required_history_count", 5) or 0
            )
            for action in (repair_actions or [])
            if action.get("type") == "history_gap_repair"
        }
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "issue_count": len(issues),
            "issues_by_severity": dict(severity_counts),
            "issues_by_code": dict(code_counts),
            "repaired_row_count": sum(1 for issue in issues if issue.code in {"row_padding", "row_merge"}),
            "missing_current_odds_entries": sum(1 for row in entry_rows if not row.get("current_odds")),
            "incomplete_history_entries": sum(
                1
                for row in entry_rows
                if int(str(row.get("history_count") or "0"))
                < required_history_counts.get(
                    (str(row.get("race_id", "")), str(row.get("horse_id", ""))),
                    5,
                )
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
            "missing_data_repair_action_count": len(repair_actions or []),
            "missing_data_repair_actions": repair_actions or [],
            "acquisition_reports": acquisition_reports or [],
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

    def _write_manual_history_template(self, rows: list[dict[str, str]]) -> None:
        if not rows and not self.config.manual_history_template_csv.exists():
            return
        deduped: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for index, row in enumerate(rows, start=1):
            key = (
                str(row.get("horse_id", "")).strip(),
                str(row.get("horse_name", "")).strip(),
                str(row.get("_missing_slot") or index).strip(),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(row)
        self._write_csv(deduped, self.config.manual_history_template_csv, MANUAL_HISTORY_COLUMNS)

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
        odds_url = f"{self.config.base_url}/JRADB/accessO.html"
        initial_cname = self.parser.extract_initial_odds_cname(race_html)
        if not initial_cname:
            self.acquisition_recorder.record(
                "jra_combo_odds",
                url=odds_url,
                outcome="unavailable",
                race_id=race.race_id,
                reason="Race page exposes no odds cname",
            )
            return []

        odds_raw_name = f"odds_{safe_filename(race.race_id)}_win_place.html"
        odds_raw_existed = (self.config.raw_dir / odds_raw_name).exists()
        first_html = self.scraper.fetch_post(
            odds_url,
            {"CNAME": initial_cname},
            raw_name=odds_raw_name,
            use_cache=reprocess_raw or not force_refresh,
            cache_only=reprocess_raw,
        )
        self._record_fetch_attempt(
            "jra_combo_odds",
            url=odds_url,
            raw_name=odds_raw_name,
            fetched=bool(first_html),
            existed_before=odds_raw_existed,
            race_id=race.race_id,
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
            "current_body_weight": getattr(horse, "current_body_weight", ""),
            "body_weight_change": getattr(horse, "body_weight_change", ""),
            "body_weight_status": getattr(horse, "body_weight_status", "unpublished"),
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
            "history_surface": str(history.get("history_surface", "")),
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
        row.update({key: str(history.get(key, "")) for key in SEMANTIC_FIELDS})
        row["history_semantic_conflicts"] = str(history.get("history_semantic_conflicts", ""))
        if not row["last_3f"] and not row["last_3f_status"]:
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
    row_indexes: dict[tuple[str, ...], int] = {}
    target_date_key = _compact_date_key(target_race_date)
    for row in list(embedded_rows) + list(detail_rows):
        history_date_key = _compact_date_key(str(row.get("date", "")))
        if target_date_key and history_date_key and history_date_key >= target_date_key:
            continue

        key = _history_identity(row)
        if not any(key):
            continue

        existing_index = row_indexes.get(key)
        if existing_index is not None:
            merged[existing_index] = _coalesce_history_rows(merged[existing_index], row)
            continue

        if len(merged) >= 5:
            continue

        normalized = dict(row)
        normalized["run_index"] = str(len(merged) + 1)
        row_indexes[key] = len(merged)
        merged.append(normalized)
    return merged


def _coalesce_semantic_observations(
    merged: dict[str, str], primary: dict[str, str], secondary: dict[str, str],
) -> dict[str, str]:
    """Keep source-proven nonapplicability from being filled by another column.

    Unknown legacy numbers are retained as conflict evidence, never silently
    promoted to comparable sectionals or corner positions.
    """
    groups = {
        "last_3f": ("last_3f", "last_3f_source", "last_3f_status", "last_3f_missing_reason",
                    "average_1f", "average_1f_status", "average_1f_header", "average_1f_raw",
                    "sectional_header", "sectional_raw"),
        "passing_order": ("passing_order", "passing_order_status", "passing_order_missing_reason",
                          "passing_order_raw", "official_course_text"),
    }
    conflicts = []
    for field, keys in groups.items():
        status = field + "_status"
        nonapplicable = [source for source in (primary, secondary)
                         if source.get(status) == "NOT_APPLICABLE"]
        if nonapplicable:
            chosen = nonapplicable[0]
            for key in keys:
                merged[key] = chosen.get(key, "")
            merged[field] = ""
            if field == "last_3f":
                merged["last_3f_source"] = ""
            incompatible = [source for source in (primary, secondary)
                            if str(source.get(field, "")).strip()
                            and source.get("last_3f_source" if field == "last_3f" else status) != "fallback"]
            if incompatible:
                conflicts.append({"field": field, "reason": "NONAPPLICABLE_OBSERVATION_CONFLICT",
                                  "sources": [{key: source.get(key, "") for key in keys}
                                              for source in (primary, secondary)]})
                merged[status] = "UNVERIFIED"
                merged[field + "_missing_reason"] = "NONAPPLICABLE_OBSERVATION_CONFLICT"
        else:
            primary_value = str(primary.get(field, "")).strip()
            primary_fallback = field == "last_3f" and primary.get("last_3f_source") == "fallback"
            chosen = None
            if primary.get(status) == "OBSERVED" and primary_value:
                chosen = primary
            elif secondary.get(status) == "OBSERVED" and str(secondary.get(field, "")).strip():
                if primary.get(status) or not primary_value or primary_fallback:
                    chosen = secondary
            elif primary.get(status):
                chosen = primary
            elif (not primary_value or primary_fallback) and secondary.get(status):
                chosen = secondary
            if chosen is not None:
                for key in keys:
                    merged[key] = chosen.get(key, "")
                if chosen.get(status) != "OBSERVED" or not str(chosen.get(field, "")).strip():
                    merged[field] = ""
                    if field == "last_3f":
                        merged["last_3f_source"] = ""
            elif primary_value and not primary_fallback and secondary.get(status):
                # An empty secondary source cannot attach its missing-status to
                # a primary legacy observation. Its raw artifact stays retained.
                for key in keys:
                    merged[key] = primary.get(key, "")
    if conflicts:
        previous = []
        for source in (primary, secondary):
            value = source.get("history_semantic_conflicts", "")
            if value:
                try:
                    parsed = json.loads(value)
                    previous.extend(parsed if isinstance(parsed, list) else [{"previous": parsed}])
                except (ValueError, TypeError):
                    previous.append({"unparsed_previous": value})
        merged["history_semantic_conflicts"] = json.dumps(previous + conflicts, ensure_ascii=False)
    return merged


def _history_identity(row: dict[str, str]) -> tuple[str, ...]:
    """Identify one run consistently across JRA tables with different race labels."""
    date_key = _compact_date_key(str(row.get("date", "")))
    if date_key:
        # A horse cannot have two JRA starts on the same date. This also handles
        # labels such as "1勝クラス" versus "3歳1勝クラス" for the same run.
        return ("date", date_key)

    fields = (
        _compact_history_text(row.get("race_name", "")),
        _compact_history_text(row.get("course", "")),
        _compact_history_text(row.get("distance", "")),
        _compact_history_text(row.get("position", "")),
    )
    return ("fields", *fields) if any(fields) else ()


def _coalesce_history_rows(primary: dict[str, str], secondary: dict[str, str]) -> dict[str, str]:
    """Fill gaps in a duplicate run without replacing observed values with fallbacks."""
    merged = dict(primary)
    for key, value in secondary.items():
        if key == "run_index":
            continue
        if not str(merged.get(key, "")).strip() and str(value).strip():
            merged[key] = value

    primary_last3f = str(primary.get("last_3f", "")).strip()
    secondary_last3f = str(secondary.get("last_3f", "")).strip()
    primary_source = str(primary.get("last_3f_source", "")).strip()
    secondary_source = str(secondary.get("last_3f_source", "")).strip()
    if secondary_last3f and _is_last3f_fallback(primary_last3f, primary_source) and not _is_last3f_fallback(
        secondary_last3f,
        secondary_source,
    ):
        merged["last_3f"] = secondary_last3f
        merged["last_3f_source"] = secondary_source or "observed"

    return _coalesce_semantic_observations(merged, primary, secondary)


def _is_last3f_fallback(value: str, source: str) -> bool:
    return not value or source == "fallback"


def _compact_history_text(value: object) -> str:
    return "".join(str(value or "").split()).lower()


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
                "current_body_weight": getattr(horse, "current_body_weight", ""),
                "body_weight_change": getattr(horse, "body_weight_change", ""),
                "body_weight_status": getattr(horse, "body_weight_status", "unpublished"),
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
    return missing_history_request(
        horse,
        history_count=history_count,
        manual_history_csv=manual_history_csv,
        manual_template_csv=manual_history_csv.with_name("manual_history_template.csv"),
        reason="user_unavailable_or_manual_data_not_stored",
        attempted_urls=[horse.horse_url],
    )


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
