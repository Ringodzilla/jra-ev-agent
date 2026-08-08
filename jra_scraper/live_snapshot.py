from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations, permutations
from typing import Callable

from bs4 import BeautifulSoup

from .config import ScrapeConfig
from .models import ParserIssue, RaceLink
from .parser import JRAParser
from .scraper import JRAScraper, safe_filename


REQUIRED_BET_TYPES = {
    "win",
    "place",
    "wide",
    "wakuren",
    "umaren",
    "umatan",
    "sanrenpuku",
    "sanrentan",
}

ODDS_PAGE_ORDER = (
    "win_place",
    "wakuren",
    "umaren",
    "wide",
    "umatan",
    "sanrenpuku",
    "sanrentan",
)


class LiveSnapshotDeadlineExceeded(RuntimeError):
    pass


class LiveSnapshotCollector:
    """Collect only race-day inputs required for a final betting decision.

    This collector never fetches horse-detail/history pages and never mutates the
    baseline pipeline state. Raw race/odds HTML is written under its own config
    directory so a failed final refresh cannot overwrite a valid baseline.
    """

    def __init__(
        self,
        config: ScrapeConfig,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self.config.ensure_dirs()
        self.scraper = JRAScraper(config)
        self.parser = JRAParser(config.base_url)
        self._now = now or (lambda: datetime.now(timezone.utc))

    def collect(
        self,
        race_config: dict[str, object],
        *,
        deadline_at: datetime,
        emit_reserve_seconds: int = 10,
    ) -> dict[str, object]:
        started_at = _as_utc(self._now())
        race = _race_from_config(race_config)
        snapshot_id = _snapshot_id(race.race_id, started_at)
        issues: list[ParserIssue] = []

        self._ensure_budget(deadline_at, emit_reserve_seconds, requests_remaining=8)
        race_html = self.scraper.fetch(
            race.race_url,
            raw_name=f"live_{safe_filename(snapshot_id)}_race.html",
            use_cache=False,
        )
        if not race_html:
            raise RuntimeError("JRA race detail refresh failed")

        race_captured_at = _as_utc(self._now())
        horses = self.parser.parse_race_detail(
            race_html,
            race.race_id,
            race.race_name,
            target_race_date=race.race_date,
            target_track=race.track,
            target_race_number=race.race_number,
            target_surface=race.target_surface,
            target_distance=race.target_distance,
            # Final mode must prove these came from the fresh JRA page. Do not
            # allow values copied into config earlier in the day to mask a
            # missing live condition field.
            target_weather="",
            target_track_condition="",
            target_conditions_captured_at=race_captured_at.isoformat(),
            issue_sink=issues,
        )

        combo_odds, official_times = self._collect_odds(
            race,
            race_html,
            snapshot_id=snapshot_id,
            deadline_at=deadline_at,
            emit_reserve_seconds=emit_reserve_seconds,
            issues=issues,
        )
        completed_at = _as_utc(self._now())
        bet_types_present = sorted({str(row.get("bet_type", "")) for row in combo_odds})
        combination_coverage = _combination_coverage(horses, combo_odds)
        snapshot_complete = REQUIRED_BET_TYPES.issubset(set(bet_types_present)) and bool(
            combination_coverage.get("complete")
        )
        for row in combo_odds:
            row["snapshot_id"] = snapshot_id
            row["snapshot_complete"] = snapshot_complete
            row["snapshot_completed_at"] = completed_at.isoformat()

        win_odds = {
            str(row.get("combination", "")): str(row.get("odds", ""))
            for row in combo_odds
            if row.get("bet_type") == "win"
        }
        entries = [_entry_row(horse, win_odds=win_odds, snapshot_id=snapshot_id) for horse in horses]
        severity_counts = Counter(issue.severity for issue in issues)
        conditions = {
            "weather": entries[0].get("target_weather", "") if entries else "",
            "track_condition": entries[0].get("target_track_condition", "") if entries else "",
            "captured_at": race_captured_at.isoformat(),
        }
        quality_report = {
            "issues_by_severity": dict(severity_counts),
            "issues": [issue.to_dict() for issue in issues],
            "entry_count": len(entries),
            "missing_current_odds_entries": sum(1 for row in entries if not row.get("current_odds")),
            "missing_body_weight_entries": sum(
                1 for row in entries if row.get("body_weight_status") == "unpublished"
            ),
            "not_measured_body_weight_entries": sum(
                1 for row in entries if row.get("body_weight_status") == "not_measured"
            ),
            "missing_conditions": [key for key in ("weather", "track_condition") if not conditions[key]],
            "bet_types_present": bet_types_present,
            "bet_types_missing": sorted(REQUIRED_BET_TYPES - set(bet_types_present)),
            "combo_odds_count": len(combo_odds),
            "snapshot_complete": snapshot_complete,
            "combination_coverage": combination_coverage,
            "official_odds_page_count": len(official_times),
            "official_odds_timestamps_complete": len(official_times) == len(ODDS_PAGE_ORDER),
        }
        # The oldest page time is the conservative timestamp for the complete
        # multi-page snapshot. A fresh last page must not mask a stale first one.
        official_odds_as_of = min(official_times) if official_times else ""
        return {
            "collector_key": "live_final",
            "race_configs": [dict(race_config)],
            "snapshot_id": snapshot_id,
            "snapshot_complete": snapshot_complete,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "official_odds_as_of": official_odds_as_of,
            "official_odds_latest_as_of": max(official_times) if official_times else "",
            "conditions": conditions,
            "entries": entries,
            "combo_odds": combo_odds,
            "quality_report": quality_report,
        }

    def close(self) -> None:
        self.scraper.close()

    def _collect_odds(
        self,
        race: RaceLink,
        race_html: str,
        *,
        snapshot_id: str,
        deadline_at: datetime,
        emit_reserve_seconds: int,
        issues: list[ParserIssue],
    ) -> tuple[list[dict[str, object]], list[str]]:
        initial_cname = self.parser.extract_initial_odds_cname(race_html)
        if not initial_cname:
            issues.append(
                ParserIssue(
                    stage="live_snapshot.odds",
                    severity="high",
                    code="odds_link_missing",
                    message="Race detail did not expose the win/place odds link.",
                    context={"race_id": race.race_id},
                )
            )
            return [], []

        odds_url = f"{self.config.base_url}/JRADB/accessO.html"
        cnames = {"win_place": initial_cname}
        rows: list[dict[str, object]] = []
        official_times: list[str] = []
        fetched_cnames: set[str] = set()

        for page_index, page_key in enumerate(ODDS_PAGE_ORDER):
            cname = cnames.get(page_key, "")
            if not cname or cname in fetched_cnames:
                continue
            self._ensure_budget(
                deadline_at,
                emit_reserve_seconds,
                requests_remaining=max(1, len(ODDS_PAGE_ORDER) - page_index),
            )
            html = self.scraper.fetch_post(
                odds_url,
                {"CNAME": cname},
                raw_name=f"live_{safe_filename(snapshot_id)}_{page_key}.html",
                use_cache=False,
            )
            fetched_cnames.add(cname)
            if not html:
                issues.append(
                    ParserIssue(
                        stage="live_snapshot.odds",
                        severity="high",
                        code="odds_page_unavailable",
                        message="A required JRA odds page could not be refreshed.",
                        context={"race_id": race.race_id, "page": page_key},
                    )
                )
                continue
            page_captured_at = _as_utc(self._now()).isoformat()
            parsed = self.parser.parse_odds_page(
                html,
                race_id=race.race_id,
                source_cname=cname,
                captured_at=page_captured_at,
            )
            rows.extend(dict(row) for row in parsed)
            official_time = _odds_displayed_at(html, race.race_date)
            if official_time:
                official_times.append(official_time)
            cnames.update(self.parser.extract_odds_cnames(html))

        return _dedupe_odds(rows), official_times

    def _ensure_budget(
        self,
        deadline_at: datetime,
        emit_reserve_seconds: int,
        *,
        requests_remaining: int = 1,
    ) -> None:
        now = _as_utc(self._now())
        remaining = (_as_utc(deadline_at) - now).total_seconds() - emit_reserve_seconds
        if remaining <= 0:
            raise LiveSnapshotDeadlineExceeded("live refresh budget exhausted before output deadline")
        # JRAScraper also has an adapter retry, so reserve two timeout windows
        # for each remaining request instead of allowing one request to consume
        # the entire race-level deadline.
        per_request_budget = remaining / max(1, requests_remaining * 2)
        if per_request_budget < 1:
            raise LiveSnapshotDeadlineExceeded(
                "insufficient time for all remaining JRA snapshot requests"
            )
        self.scraper.config.timeout = max(
            1,
            min(int(self.config.timeout), max(1, int(per_request_budget))),
        )


def _race_from_config(config: dict[str, object]) -> RaceLink:
    race_date = str(config.get("race_date", "")).strip()
    track = str(config.get("track", "")).strip()
    race_number = str(config.get("race_number", "")).strip()
    race_id = str(config.get("race_id", "")).strip()
    if not race_id and race_date and track and race_number:
        race_id = f"{race_date.replace('-', '')}_{track}_{int(float(race_number)):02d}"
    source_url = str(config.get("source_url", "")).strip()
    if not source_url:
        raise ValueError("final prediction requires source_url")
    return RaceLink(
        race_id=race_id or "live_race",
        race_name=str(config.get("race_name", "JRAレース")),
        race_url=source_url,
        race_date=race_date,
        track=track,
        race_number=race_number,
        target_surface=str(config.get("surface", "")),
        target_distance=str(config.get("distance", "")),
        target_weather=str(config.get("weather", "")),
        target_track_condition=str(config.get("track_condition", "")),
    )


def _entry_row(horse, *, win_odds: dict[str, str], snapshot_id: str) -> dict[str, str]:
    horse_number = str(horse.horse_number).strip()
    return {
        "race_id": str(horse.race_id),
        "horse_id": str(horse.horse_id),
        "horse_name": str(horse.horse_name),
        "frame_number": str(horse.frame_number),
        "horse_number": horse_number,
        "current_jockey": str(horse.current_jockey),
        "assigned_weight": str(horse.assigned_weight),
        "current_body_weight": str(getattr(horse, "current_body_weight", "")),
        "body_weight_change": str(getattr(horse, "body_weight_change", "")),
        "body_weight_status": str(getattr(horse, "body_weight_status", "unpublished")),
        "body_weight_captured_at": str(horse.target_conditions_captured_at),
        # Final mode never falls back to the race-detail value. The win page is
        # part of the coherent snapshot and missing keys must remain missing so
        # the reviewer can stop the purchase.
        "current_odds": win_odds.get(horse_number, ""),
        "current_popularity": str(horse.current_popularity),
        "target_track": str(horse.target_track),
        "target_race_date": str(horse.target_race_date),
        "target_race_number": str(horse.target_race_number),
        "target_surface": str(horse.target_surface),
        "target_distance": str(horse.target_distance),
        "target_weather": str(horse.target_weather),
        "target_track_condition": str(horse.target_track_condition),
        "target_conditions_captured_at": str(horse.target_conditions_captured_at),
        "horse_country": str(horse.horse_country),
        "snapshot_id": snapshot_id,
    }


def _odds_displayed_at(html: str, race_date: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    match = re.search(r"(\d{1,2})時(\d{2})分現在オッズ", text)
    if not match or not race_date:
        return ""
    try:
        value = datetime.fromisoformat(
            f"{race_date}T{int(match.group(1)):02d}:{int(match.group(2)):02d}:00+09:00"
        )
    except ValueError:
        return ""
    return value.isoformat()


def _dedupe_odds(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, object]] = []
    for row in rows:
        key = (
            str(row.get("race_id", "")),
            str(row.get("bet_type", "")),
            str(row.get("combination", "")),
        )
        if not all(key) or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _combination_coverage(horses, rows: list[dict[str, object]]) -> dict[str, object]:
    active_numbers = {
        str(row.get("combination", ""))
        for row in rows
        if row.get("bet_type") == "win" and str(row.get("combination", "")).strip()
    }
    frame_by_number = {
        str(horse.horse_number): str(horse.frame_number)
        for horse in horses
        if str(horse.horse_number) in active_numbers and str(horse.frame_number).strip()
    }
    frame_counts = Counter(frame_by_number.values())
    horse_count = len(active_numbers)
    numbers = sorted(active_numbers, key=lambda value: int(value))
    frames = sorted(frame_counts, key=lambda value: int(value))
    expected_keys = {
        "win": set(numbers),
        "place": set(numbers),
        "wide": {"-".join(values) for values in combinations(numbers, 2)},
        "wakuren": {
            "-".join(values) for values in combinations(frames, 2)
        } | {f"{frame}-{frame}" for frame, count in frame_counts.items() if count >= 2},
        "umaren": {"-".join(values) for values in combinations(numbers, 2)},
        "umatan": {">".join(values) for values in permutations(numbers, 2)},
        "sanrenpuku": {"-".join(values) for values in combinations(numbers, 3)},
        "sanrentan": {">".join(values) for values in permutations(numbers, 3)},
    }
    actual_keys: dict[str, set[str]] = {bet_type: set() for bet_type in expected_keys}
    for row in rows:
        bet_type = str(row.get("bet_type", ""))
        if bet_type in actual_keys:
            actual_keys[bet_type].add(str(row.get("combination", "")))
    missing_keys = {
        bet_type: sorted(keys - actual_keys[bet_type])
        for bet_type, keys in expected_keys.items()
    }
    return {
        "active_horse_count": horse_count,
        "frame_mapping_complete": len(frame_by_number) == horse_count,
        "expected_counts": {key: len(values) for key, values in expected_keys.items()},
        "actual_counts": {key: len(actual_keys[key]) for key in expected_keys},
        "missing_counts": {key: len(values) for key, values in missing_keys.items()},
        "missing_examples": {key: values[:5] for key, values in missing_keys.items() if values},
        "complete": horse_count > 0
        and len(frame_by_number) == horse_count
        and not any(missing_keys.values()),
    }


def _snapshot_id(race_id: str, captured_at: datetime) -> str:
    raw = f"{race_id}|{captured_at.isoformat()}"
    suffix = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return f"{safe_filename(race_id)}_{captured_at.strftime('%Y%m%dT%H%M%SZ')}_{suffix}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
