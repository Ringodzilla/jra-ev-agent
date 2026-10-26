import hashlib

import pytest

from jra_scraper.history_pace import parse_official_opening_600m
from src.data_integrity import _history_result_observations, verify_source_artifacts


def page(distance, laps):
    return f'''<div class="cell course">コース：{distance:,}<span>メートル</span></div>
    <div class="cell date">2026年9月19日</div><table>
    <tr><th>ハロンタイム</th><td>{laps}</td></tr>
    <tr><td class="horse"><a>A</a></td><td class="f_time">35.1</td>
    <td class="place">2</td></tr>
    <tr><td class="horse"><a>B</a></td><td class="f_time">34.2</td>
    <td class="place">1</td></tr></table>'''


@pytest.mark.parametrize("distance,count,expected", [(2000, 10, "36"), (2400, 12, "36"),
                                                        (2500, 13, ""), (1700, 9, "")])
def test_exact_opening_distance_boundaries(distance, count, expected):
    html = page(distance, " - ".join(["12.0"] * count))
    result = parse_official_opening_600m(html, expected_distance=distance)
    assert result["pace"] == expected
    assert result["pace_scope"] == "race_leader_at_each_boundary"
    if not expected:
        assert result["interval_lengths_m"][:3] == [100, 200, 200]
        assert result["pace_missing_reason"] == "NO_600M_BOUNDARY_PARTIAL_INTERVAL_NOT_ESTIMATED"


@pytest.mark.parametrize("html,kwargs,reason", [
    ("", {}, "MISSING_OR_AMBIGUOUS_DISTANCE"),
    (page(2000, "12 - 12").replace("ハロンタイム", "上り"), {}, "MISSING_OR_AMBIGUOUS_INTERVALS"),
    (page(2000, "12 - 12") + '<table><tr><th>ハロンタイム</th><td>12</td></tr></table>', {}, "MISSING_OR_AMBIGUOUS_INTERVALS"),
    (page(2000, ""), {}, "INVALID_INTERVALS"),
    (page(2000, "12 - nan - 12"), {}, "INVALID_INTERVALS"),
    (page(2000, "12 - 12 - 12"), {}, "INVALID_INTERVAL_COUNT_OR_VALUE"),
    (page(2000, " - ".join(["0"] * 10)), {}, "INVALID_INTERVAL_COUNT_OR_VALUE"),
    (page(2000, " - ".join(["12"] * 10)), {"expected_distance": 2400}, "DISTANCE_MISMATCH"),
    (page(2050, "12"), {}, "UNSUPPORTED_DISTANCE"),
])
def test_missing_and_invalid_laps_are_never_imputed(html, kwargs, reason):
    result = parse_official_opening_600m(html, **kwargs)
    assert result["pace"] == ""
    assert result["pace_missing_reason"] == reason


def test_race_pace_is_shared_by_runners_and_original_reparse_rejects_tampering():
    html = page(2000, " - ".join(["12.0"] * 10))
    artifacts = []
    rows = []
    for name in ("A", "B"):
        artifact = dict(kind="history_result", race_id="r1", horse_id=name, horse_name=name,
                        html=html, sha256=hashlib.sha256(html.encode()).hexdigest())
        row = _history_result_observations(html, artifact)[0]
        assert row["pace"] == "36"
        assert row["pace_scope"] == "race_leader_at_each_boundary"
        row["target_race_date"] = "2026-10-25"
        artifacts.append(artifact)
        rows.append(row)
    assert rows[0]["last_3f"] != rows[1]["last_3f"]
    collected = {"rows": rows, "source_audit": {"artifacts": artifacts}}
    assert verify_source_artifacts(collected)["status"] == "PASS"
    rows[0]["pace"] = "35"
    assert verify_source_artifacts(collected)["status"] == "MISMATCH"
