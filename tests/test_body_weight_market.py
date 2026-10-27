from copy import deepcopy
from datetime import datetime
import math

import pytest

from analysis.body_weight_market import observe_body_weight_market


def event(**updates):
    return dict(dict(race_id="R", horse_id="H", body_weight_status="published",
                     published_at="2026-10-25T14:30:00+09:00", current_body_weight="480",
                     body_weight_change="+8", source_ref={"sha256": "event"}), **updates)


def quote(time="14:30:00", odds="4", **updates):
    return dict(dict(race_id="R", horse_id="H", bet_type="win", captured_at=f"2026-10-25T{time}+09:00",
                     odds=odds, source_ref={"sha256": f"quote-{time}"}), **updates)


def observe(e=None, quotes=None, **kwargs):
    return observe_body_weight_market(event() if e is None else e,
        [quote("14:29:00", "5"), quote()] if quotes is None else quotes,
        **dict(dict(as_of="2026-10-25T15:35:00+09:00", post_time="2026-10-25T15:40:00+09:00"), **kwargs))


def test_selects_last_before_first_after_and_does_not_mutate_sources():
    e = event()
    quotes = [quote("14:31:00", "2"), quote("14:29:00", "5"), quote("14:28:00", "7"), quote()]
    before = deepcopy((e, quotes))
    result = observe(e, quotes)
    assert result["status"] == "MEASURED"
    assert result["log_odds_change"] == pytest.approx(math.log(4 / 5))
    assert result["change_ratio_current_weight"] == pytest.approx(8 / 480)
    assert result["change_ratio_previous_weight"] == pytest.approx(8 / 472)
    assert result["seconds_before_release"] == result["quote_gap_seconds"] == 60
    assert result["seconds_after_release"] == 0
    assert result["association_kind"] == "observational_not_causal"
    result["source_refs"]["event"]["sha256"] = "changed"
    assert (e, quotes) == before


@pytest.mark.parametrize("change,odds", [("-8", "8"), ("+8", "8"), ("0", "5")])
def test_signed_weight_does_not_prescribe_market_direction(change, odds):
    result = observe(event(body_weight_change=change), [quote("14:29:00", "5"), quote(odds=odds)])
    assert result["status"] == "MEASURED"
    assert result["log_odds_change"] == pytest.approx(math.log(float(odds) / 5))


@pytest.mark.parametrize("updates,kwargs,reason", [
    ({}, {"as_of": "invalid"}, "INVALID_AS_OF_OR_POST_TIME"),
    ({}, {"post_time": "2026-10-25T15:40:00"}, "INVALID_AS_OF_OR_POST_TIME"),
    ({"horse_id": ""}, {}, "MISSING_RACE_OR_HORSE_ID"),
    ({"body_weight_status": "unpublished"}, {}, "BODY_WEIGHT_NOT_PUBLISHED"),
    ({"published_at": None}, {}, "MISSING_OR_INVALID_PUBLICATION_TIME"),
    ({"published_at": "2026-10-25T15:40:00+09:00"}, {}, "PUBLICATION_NOT_AVAILABLE_BEFORE_CUTOFF"),
    ({"published_at": "2026-10-25T15:36:00+09:00"}, {}, "PUBLICATION_NOT_AVAILABLE_BEFORE_CUTOFF"),
    ({"captured_at": "bad"}, {}, "EVENT_CAPTURE_NOT_AVAILABLE_BEFORE_CUTOFF"),
    ({"captured_at": "2026-10-25T14:29:00+09:00"}, {}, "EVENT_CAPTURE_NOT_AVAILABLE_BEFORE_CUTOFF"),
    ({"captured_at": "2026-10-25T15:36:00+09:00"}, {}, "EVENT_CAPTURE_NOT_AVAILABLE_BEFORE_CUTOFF"),
    ({"current_body_weight": ""}, {}, "INVALID_OR_MISSING_BODY_WEIGHT_OBSERVATION"),
    ({"current_body_weight": 0}, {}, "INVALID_OR_MISSING_BODY_WEIGHT_OBSERVATION"),
    ({"body_weight_change": 480}, {}, "INVALID_OR_MISSING_BODY_WEIGHT_OBSERVATION"),
    ({"body_weight_change": float("nan")}, {}, "INVALID_OR_MISSING_BODY_WEIGHT_OBSERVATION"),
    ({"body_weight_change": True}, {}, "INVALID_OR_MISSING_BODY_WEIGHT_OBSERVATION"),
    ({"source_ref": {}}, {}, "MISSING_EVENT_SOURCE_REFERENCE"),
])
def test_missing_invalid_or_not_yet_known_events_are_unmeasured(updates, kwargs, reason):
    result = observe(event(**updates), **kwargs)
    assert result["status"] == "UNMEASURED"
    assert result["reason"] == reason
    assert result["log_odds_change"] is None


@pytest.mark.parametrize("quotes,reason", [([], "MISSING_BOTH_QUOTES"),
    ([quote()], "MISSING_PRE_RELEASE_QUOTE"),
    ([quote("14:29:00")], "MISSING_POST_RELEASE_QUOTE")])
def test_missing_quotes_are_not_zero_reactions(quotes, reason):
    result = observe(quotes=quotes)
    assert result["reason"] == reason
    assert result["log_odds_change"] is None


def test_cross_identity_other_bets_and_future_quotes_are_excluded():
    invalid = [quote(horse_id="OTHER"), quote(race_id="OTHER"), quote(bet_type="place"),
        quote(captured_at="bad"), quote("15:36:00"), quote("15:40:00"),
        quote(odds="0"), quote(odds="bad"), quote(source_ref={})]
    result = observe(quotes=[quote("14:29:00", "5"), quote()] + invalid)
    assert result["status"] == "MEASURED"
    assert len(result["excluded_quotes"]) == len(invalid)
    assert {q["reason"] for q in result["excluded_quotes"]} == {
        "OTHER_RACE_OR_HORSE", "NOT_WIN_ODDS", "INVALID_QUOTE_TIME", "QUOTE_AFTER_CUTOFF_OR_START",
        "INVALID_QUOTE_ODDS", "MISSING_QUOTE_SOURCE_REFERENCE"}


def test_post_start_quote_excluded_even_if_as_of_is_later_and_as_of_is_inclusive():
    result = observe(quotes=[quote("14:29:00"), quote("15:40:00")], as_of="2026-10-25T16:00:00+09:00")
    assert result["reason"] == "MISSING_POST_RELEASE_QUOTE"
    assert observe(as_of="2026-10-25T14:30:00+09:00")["status"] == "MEASURED"


@pytest.mark.parametrize("side", ["before", "after"])
def test_conflicting_selected_timestamp_is_ambiguous_without_averaging(side):
    quotes = [quote("14:29:00", "5"), quote()]
    quotes.append(quote("14:29:00" if side == "before" else "14:30:00", "9"))
    result = observe(quotes=quotes)
    assert result["reason"] == "AMBIGUOUS_SELECTED_QUOTE_TIMESTAMP"
    assert result["log_odds_change"] is None


def test_duplicate_identical_quotes_retain_all_refs_and_utc_datetimes_are_supported():
    result = observe(event(captured_at="2026-10-25T14:31:00+09:00"),
        [quote("14:29:00", "5"), quote(), quote(source_ref={"sha256": "second"})],
        as_of=datetime.fromisoformat("2026-10-25T05:31:00+00:00"))
    assert result["status"] == "MEASURED"
    assert len(result["after_quote"]["source_refs"]) == 2


@pytest.mark.parametrize("weight,change", [(1e308, -1e308), (1e-308, -1e308)])
def test_nonfinite_derived_ratios_are_not_measured(weight, change):
    result = observe(event(current_body_weight=weight, body_weight_change=change))
    assert result["reason"] == "NONFINITE_BODY_WEIGHT_DERIVATION"
    assert result["log_odds_change"] is None
