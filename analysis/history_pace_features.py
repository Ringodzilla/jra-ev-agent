"""Source-bound pace/position observations for research, never production scores.

The opening split belongs to the race leader at each timing boundary. It is
not the runner's own opening split. All input histories are retained; missing
observations produce null context values rather than reweighted aggregates.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import hashlib
from pathlib import Path
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from jra_scraper.history_observations import parse_result_history_semantics
from jra_scraper.history_pace import parse_official_opening_600m


class PaceObservationError(ValueError):
    """A source, identity, date or recorded observation did not verify."""


def _day(value: str) -> str:
    match = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", value.strip())
    result = date(*map(int, match.groups())).isoformat() if match else date.fromisoformat(value).isoformat()
    return result


def _registration(value: str) -> str:
    match = re.search(r"pw01dud\d{2}(\d{10})", value)
    return match[1] if match else ""


def _source_day(value: str) -> str:
    matches = re.findall(r"(\d{4})年(\d{1,2})月(\d{1,2})日", value)
    if len(matches) != 1 or not re.match(r"\d{4}年\d{1,2}月\d{1,2}日", value):
        raise PaceObservationError("Official source date is ambiguous")
    return date(*map(int, matches[0])).isoformat()


def observed_history_pace_context(
    rows: list[dict], *, target_race_date: str,
) -> dict:
    """Connect exact historical leader600m and the runner's final-corner rank.

    ``seconds_x_final_corner_position`` is an uncalibrated s*ordinal product,
    not an ability/pace score or a dimensionless comparison across courses.
    No coefficients, probabilities, labels, aggregate defaults or production
    routing are created here. Each row must carry official_result_source with
    raw bytes path/SHA/encoding/URL and a horse name/registration identity.
    """
    cutoff = _day(target_race_date)
    observations = []
    identities = set()
    cache: dict[tuple[str, str, str], tuple[BeautifulSoup, dict]] = {}
    for row in rows:
        day = _day(str(row.get("date", "")))
        if day >= cutoff:
            raise PaceObservationError("Historical observation is not before target race")
        identity = (str(row.get("race_id", "")), str(row.get("horse_id", "")), day)
        if identity in identities:
            raise PaceObservationError("Duplicate target/horse/history date")
        identities.add(identity)
        source = row.get("official_result_source") or {}
        if not all(source.get(key) for key in ("path", "sha256", "encoding", "url")):
            raise PaceObservationError("Source provenance is required for every history")
        if urlparse(source["url"]).hostname not in {"jra.jp", "www.jra.jp", "jra.go.jp", "www.jra.go.jp"}:
            raise PaceObservationError("Exact JRA leader-600m source contract required")
        key = (source["path"], source["sha256"], source["encoding"])
        # Hash bytes on every input row, including a cache hit: a changed source
        # is never accepted solely because its URL/path was previously parsed.
        raw = Path(source["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != source["sha256"]:
            raise PaceObservationError("Original source bytes changed")
        if key not in cache:
            html = raw.decode(source["encoding"])
            soup = BeautifulSoup(html, "html.parser")
            cache[key] = soup, parse_official_opening_600m(html)
        soup, pace = cache[key]
        dates = soup.select(".cell.date")
        if len(dates) != 1 or _source_day(dates[0].get_text(" ", strip=True)) != day:
            raise PaceObservationError("Historical row/source date mismatch")
        matches = [tr for tr in soup.select("tr") if tr.select_one("td.horse a")
                   and tr.select_one("td.horse a").get_text(strip=True) == row.get("horse_name")]
        if len(matches) != 1:
            raise PaceObservationError("Horse name must match one official result row")
        own = matches[0]
        target_id = _registration(str(row.get("horse_id", "")))
        source_id = _registration(own.select_one("td.horse a").get("href", ""))
        if not target_id or target_id != source_id:
            raise PaceObservationError("Horse registration identity mismatch")
        if str(row.get("pace", "")).strip() != pace["pace"]:
            raise PaceObservationError("Stored pace differs from exact official intervals")
        declared = row.get("official_pace_observation", {})
        for field in ("pace", "pace_source", "pace_scope", "interval_seconds", "interval_lengths_m"):
            if field in declared and declared[field] != pace.get(field):
                raise PaceObservationError("Stored pace metadata differs: " + field)
            if field != "pace" and field in row and row[field] != pace.get(field):
                raise PaceObservationError("Stored top-level pace metadata differs: " + field)
        semantics = parse_result_history_semantics(soup, own)
        if str(row.get("passing_order", "")).strip() != semantics["passing_order"]:
            raise PaceObservationError("Stored passing order differs from official result")
        for field in ("passing_order_status", "passing_order_missing_reason"):
            if field in row and row[field] != semantics[field]:
                raise PaceObservationError("Stored corner semantics differ: " + field)
        pace_value = float(pace["pace"]) if pace["pace"] else None
        recorded_corner = (int(semantics["passing_order"].split("-")[-1])
                           if semantics["passing_order_status"] == "OBSERVED" else None)
        place_cell = own.select_one("td.place")
        place = place_cell.get_text(strip=True) if place_cell else ""
        if "position" in row and str(row["position"]).strip() != place:
            raise PaceObservationError("Stored completion status differs from official result")
        finished = place.isdigit() and int(place) > 0
        corner_value = recorded_corner if finished else None
        final_reason = (semantics["passing_order_missing_reason"] if recorded_corner is None else (
            "" if finished else "RESULT_COMPLETION_STATUS_NOT_VERIFIED"))
        if recorded_corner is not None and any(status in place for status in ("中止", "失格")):
            final_reason = "NONFINISHER_FINAL_CORNER_NOT_VERIFIED"
        product = (float(Decimal(pace["pace"]) * corner_value)
                   if pace_value is not None and corner_value is not None else None)
        observations.append({
            "race_id": row.get("race_id", ""), "run_index": row.get("run_index", ""),
            "date": day, "horse_id": row["horse_id"], "horse_name": row["horse_name"],
            "source": dict(source), "course": row.get("course", ""),
            "history_surface": row.get("history_surface", ""),
            "distance_m": pace.get("distance_m"), "leader_opening_600m_seconds": pace_value,
            "pace_scope": pace["pace_scope"],
            "pace_status": "OBSERVED" if pace_value is not None else "NOT_MEASURED",
            "pace_missing_reason": pace.get("pace_missing_reason", ""),
            "interval_seconds": pace.get("interval_seconds", []),
            "interval_lengths_m": pace.get("interval_lengths_m", []),
            "final_corner_position": corner_value,
            "last_recorded_corner_position": recorded_corner,
            "source_result_position": place,
            "final_corner_status": "OBSERVED_FINISHER_LAST_RECORDED" if corner_value is not None else "UNAVAILABLE",
            "final_corner_missing_reason": final_reason,
            "corner_scope": "official_recorded_corners_not_position_at600m",
            "recorded_corner_positions": ([int(v) for v in semantics["passing_order"].split("-")]
                                          if recorded_corner is not None else []),
            "passing_order_status": semantics["passing_order_status"],
            "passing_order_missing_reason": semantics["passing_order_missing_reason"],
            "seconds_x_final_corner_position": product,
            "interaction_units": "seconds*ordinal_position",
            "interaction_status": "OBSERVED_INPUT_PRODUCT" if product is not None else "UNAVAILABLE",
        })
    return {"contract": "source_bound_leader600m_finalcorner_research_v1",
            "target_race_date": cutoff, "observations": observations,
            "input_history_count": len(rows), "retained_history_count": len(observations),
            "complete_context_count": sum(o["interaction_status"] == "OBSERVED_INPUT_PRODUCT" for o in observations),
            "aggregate": None, "aggregate_status": "NOT_DEFINED_NO_PARTIAL_HISTORY_REWEIGHTING",
            "production_score_applied": False, "model_status": "OBSERVATION_FEATURES_NOT_PROMOTED"}
