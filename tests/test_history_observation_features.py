import pytest

from src.feature_engineering import (
    UnsupportedHistoryObservation, _comparable_last3f, _front_running_score,
    summarize_history_rows,
)


def history():
    return {"race_id": "r", "horse_id": "h", "run_index": "1", "date": "2026-09-19",
            "position": "2", "last_3f": "34.5", "popularity": "3", "weight": "55",
            "distance": "1600", "time": "95", "passing_order": "3-4",
            "odds": "7.5", "course": "東京", "history_surface": "芝",
            "target_surface": "芝", "track_condition": "良", "jockey": "A"}


def test_observed_flat_history_is_numerically_unchanged_by_explicit_status():
    row = history()
    original = summarize_history_rows([row], group_keys=("race_id", "horse_id"))
    marked = dict(row, passing_order_status="OBSERVED", last_3f_status="OBSERVED")
    assert summarize_history_rows([marked], group_keys=("race_id", "horse_id")) == original


@pytest.mark.parametrize("state", ["NOT_APPLICABLE", "NOT_OBSERVED", "UNVERIFIED", "UNKNOWN_STATE"])
def test_explicit_unavailable_corner_is_never_neutralized(state):
    row = dict(history(), passing_order="", passing_order_status=state,
               passing_order_missing_reason="STRAIGHT_COURSE_NO_CORNERS")
    with pytest.raises(UnsupportedHistoryObservation, match="passing_order"):
        summarize_history_rows([row], group_keys=("race_id", "horse_id"))


def test_mixed_history_is_not_dropped_or_reweighted():
    straight = dict(history(), run_index="2", passing_order="", passing_order_status="NOT_APPLICABLE")
    with pytest.raises(UnsupportedHistoryObservation):
        summarize_history_rows([history(), straight], group_keys=("race_id", "horse_id"))


def test_average1f_even_with_stale_numeric_last3f_is_not_36_or_a_closing_feature():
    row = dict(history(), last_3f="14.0", average_1f="14.0",
               history_surface="障害", last_3f_status="NOT_APPLICABLE",
               last_3f_missing_reason="RESULT_COLUMN_IS_AVERAGE_1F")
    with pytest.raises(UnsupportedHistoryObservation, match="RESULT_COLUMN_IS_AVERAGE_1F"):
        _comparable_last3f(row)


@pytest.mark.parametrize("value", ["", "nan", "0", "invalid"])
def test_marked_observed_last3f_must_be_positive_finite(value):
    with pytest.raises(UnsupportedHistoryObservation):
        _comparable_last3f(dict(history(), last_3f=value, last_3f_status="OBSERVED"))


@pytest.mark.parametrize("value", ["", "bad-4", "0-4", None])
def test_marked_observed_corner_must_be_observed_positions(value):
    with pytest.raises(UnsupportedHistoryObservation):
        _front_running_score(value, status="OBSERVED")


def test_status_whitespace_does_not_bypass_observed_value_validation():
    with pytest.raises(UnsupportedHistoryObservation):
        _front_running_score("", status=" OBSERVED ")


def test_legacy_unmarked_path_remains_compatible_and_marked_jump_is_unsupported():
    assert _front_running_score("") == 0.5
    assert _comparable_last3f({"history_surface": "障害", "last_3f": "14"}) == 36
    with pytest.raises(UnsupportedHistoryObservation, match="jump histories"):
        _comparable_last3f(dict(history(), history_surface="障害", last_3f_status="OBSERVED"))
