from __future__ import annotations

import pytest
from itertools import permutations

from analysis.candidate_ev import build_candidate_evaluations


@pytest.mark.parametrize(
    ("last_probability", "expected_status"),
    [("0.400002", "OK"), ("0.399998", "OK"), ("0.400003", "NG"), ("0.399997", "NG")],
)
def test_saved_probability_sum_tolerance_boundary(last_probability: str, expected_status: str) -> None:
    rows = [
        {"race_id": "R1", "horse_number": number, "frame_number": number, "win_prob": probability}
        for number, probability in enumerate(["0.1", "0.2", "0.3", last_probability], start=1)
    ]
    odds = [{"race_id": "R1", "bet_type": "win", "combination": "1", "odds": "10"}]

    for ordered_rows in permutations(rows):
        result = build_candidate_evaluations(list(ordered_rows), odds)
        assert result["validation"]["status"] == expected_status
