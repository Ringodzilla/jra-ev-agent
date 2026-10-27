"""Observational win-odds changes around a timestamped body-weight release.

This module estimates no causal effect and fits no coefficient. Publication
must be explicitly observed; a scraper capture time is not a publication time.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math


def _time(value: object) -> datetime | None:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _number(value: object) -> float | None:
    try:
        number = float(value)
        return number if not isinstance(value, bool) and math.isfinite(number) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _reference(value: object) -> bool:
    return isinstance(value, dict) and bool(value) and any(v not in (None, "") for v in value.values())


def observe_body_weight_market(
    event: dict,
    quotes: list[dict],
    *,
    as_of: str | datetime,
    post_time: str | datetime,
) -> dict:
    """Select last pre-release and first post-release quotes for one horse.

    Event fields: race_id, horse_id, body_weight_status='published', published_at,
    current_body_weight (kg), body_weight_change (signed kg), source_ref.
    Quote fields: race_id, horse_id, bet_type='win', captured_at, odds, source_ref.
    Optional event captured_at must also be available by as_of and before post.
    Timestamps must carry UTC offsets. Quotes exactly at publication are post.
    Conflicting prices at a selected timestamp are ambiguous, never averaged.
    Missing or invalid observations return UNMEASURED with null measurements.
    Source references are retained but their external contents are not verified.
    """
    result = dict(schema_version=1, race_id=event.get("race_id"), horse_id=event.get("horse_id"),
                  status="UNMEASURED", reason=None, association_kind="observational_not_causal",
                  production_coefficient_applied=False, published_at=None,
                  current_body_weight=None, body_weight_change=None,
                  change_ratio_current_weight=None, change_ratio_previous_weight=None,
                  log_odds_change=None, odds_before=None, odds_after=None,
                  seconds_before_release=None, seconds_after_release=None, quote_gap_seconds=None,
                  before_quote=None, after_quote=None, source_refs={}, excluded_quotes=[])
    def missing(reason):
        return dict(result, reason=reason)
    cutoff, start = _time(as_of), _time(post_time)
    if cutoff is None or start is None:
        return missing("INVALID_AS_OF_OR_POST_TIME")
    if not event.get("race_id") or not event.get("horse_id"):
        return missing("MISSING_RACE_OR_HORSE_ID")
    if event.get("body_weight_status") != "published":
        return missing("BODY_WEIGHT_NOT_PUBLISHED")
    released = _time(event.get("published_at"))
    if released is None:
        return missing("MISSING_OR_INVALID_PUBLICATION_TIME")
    if released > cutoff or released >= start:
        return missing("PUBLICATION_NOT_AVAILABLE_BEFORE_CUTOFF")
    if event.get("captured_at") is not None:
        captured = _time(event["captured_at"])
        if captured is None or captured < released or captured > cutoff or captured >= start:
            return missing("EVENT_CAPTURE_NOT_AVAILABLE_BEFORE_CUTOFF")
    weight, change = _number(event.get("current_body_weight")), _number(event.get("body_weight_change"))
    if weight is None or change is None or weight <= 0 or weight - change <= 0:
        return missing("INVALID_OR_MISSING_BODY_WEIGHT_OBSERVATION")
    if not all(math.isfinite(value) for value in (weight - change, change / weight, change / (weight - change))):
        return missing("NONFINITE_BODY_WEIGHT_DERIVATION")
    if not _reference(event.get("source_ref")):
        return missing("MISSING_EVENT_SOURCE_REFERENCE")
    result.update(published_at=released.isoformat(), current_body_weight=weight, body_weight_change=change,
                  change_ratio_current_weight=change / weight,
                  change_ratio_previous_weight=change / (weight - change),
                  source_refs={"event": deepcopy(event["source_ref"])})
    eligible = []
    for index, quote in enumerate(quotes):
        reason = None
        if quote.get("race_id") != event["race_id"] or quote.get("horse_id") != event["horse_id"]:
            reason = "OTHER_RACE_OR_HORSE"
        elif quote.get("bet_type") != "win":
            reason = "NOT_WIN_ODDS"
        else:
            captured, odds = _time(quote.get("captured_at")), _number(quote.get("odds"))
            if captured is None:
                reason = "INVALID_QUOTE_TIME"
            elif captured > cutoff or captured >= start:
                reason = "QUOTE_AFTER_CUTOFF_OR_START"
            elif odds is None or odds <= 0:
                reason = "INVALID_QUOTE_ODDS"
            elif not _reference(quote.get("source_ref")):
                reason = "MISSING_QUOTE_SOURCE_REFERENCE"
            else:
                eligible.append((captured, odds, quote["source_ref"]))
        if reason:
            result["excluded_quotes"].append({"index": index, "reason": reason})
    before = [quote for quote in eligible if quote[0] < released]
    after = [quote for quote in eligible if quote[0] >= released]
    if not before or not after:
        return missing("MISSING_BOTH_QUOTES" if not before and not after else
                       "MISSING_PRE_RELEASE_QUOTE" if not before else "MISSING_POST_RELEASE_QUOTE")
    before_time, after_time = max(q[0] for q in before), min(q[0] for q in after)
    selected = [[q for q in side if q[0] == timestamp] for side, timestamp in
                ((before, before_time), (after, after_time))]
    if any(len({q[1] for q in side}) != 1 for side in selected):
        return missing("AMBIGUOUS_SELECTED_QUOTE_TIMESTAMP")
    summaries = [dict(captured_at=side[0][0].isoformat(), odds=side[0][1],
                      source_refs=deepcopy([q[2] for q in side])) for side in selected]
    pre, post = summaries
    result.update(status="MEASURED", reason=None, before_quote=pre, after_quote=post,
                  odds_before=pre["odds"], odds_after=post["odds"],
                  log_odds_change=math.log(post["odds"]) - math.log(pre["odds"]),
                  seconds_before_release=(released - before_time).total_seconds(),
                  seconds_after_release=(after_time - released).total_seconds(),
                  quote_gap_seconds=(after_time - before_time).total_seconds())
    result["source_refs"].update(before=deepcopy(pre["source_refs"]), after=deepcopy(post["source_refs"]))
    return result
