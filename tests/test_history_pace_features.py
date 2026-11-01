import hashlib

import pytest

from analysis.history_pace_features import PaceObservationError, observed_history_pace_context


def source_row(tmp_path, *, distance=1200, straight=False, day="2026年9月19日", place="2"):
    count = distance // 200 + int(distance % 200 != 0)
    laps = " - ".join(["12.0"] * count)
    corner = "" if straight else "<li>3</li><li>5</li>"
    html = f'''<div class="cell date">{day}</div><div class="cell course">
    コース：{distance:,}メートル（芝{'・直' if straight else '・左'}）</div>
    <table><thead><tr><th class="f_time">推定上り</th></tr></thead><tbody>
    <tr><td class="horse"><a href="/JRADB/accessU.html?CNAME=pw01dud102024102341/F9">A</a></td>
    <td class="f_time">34.5</td><td class="place">{place}</td><td class="corner">{corner}</td></tr>
    </tbody></table><table><tr><th>ハロンタイム</th><td>{laps}</td></tr></table>'''
    raw = html.encode();path = tmp_path / (hashlib.sha256(raw).hexdigest() + ".html");path.write_bytes(raw)
    return {"race_id": "target", "horse_name": "A", "horse_id": "pw01dud002024102341/12",
            "date": day, "run_index": "1", "course": "東京", "history_surface": "芝",
            "pace": "" if distance % 200 else "36", "passing_order": "" if straight else "3-5",
            "official_result_source": {"path":str(path), "sha256":hashlib.sha256(raw).hexdigest(),
                                       "encoding":"utf-8", "url":"https://jra.jp/JRADB/accessS.html?CNAME=fixture"}}


def test_exact_leader_pace_position_connection_is_not_a_score(tmp_path):
    row = source_row(tmp_path)
    result = observed_history_pace_context([row], target_race_date="2026-10-25")
    obs = result["observations"][0]
    assert obs["leader_opening_600m_seconds"] == 36
    assert obs["final_corner_position"] == 5
    assert obs["seconds_x_final_corner_position"] == 180
    assert obs["interaction_units"] == "seconds*ordinal_position"
    assert obs["pace_scope"] == "race_leader_at_each_boundary"
    assert result["production_score_applied"] is False
    assert result["aggregate"] is None


def test_no600m_boundary_and_straight_nonapplicability_preserve_all_rows(tmp_path):
    no_boundary = source_row(tmp_path, distance=1500)
    straight = source_row(tmp_path, distance=1000, straight=True, day="2026年9月20日")
    result = observed_history_pace_context([no_boundary, straight], target_race_date="2026-10-25")
    assert result["retained_history_count"] == result["input_history_count"] == 2
    assert result["complete_context_count"] == 0
    first, second = result["observations"]
    assert first["leader_opening_600m_seconds"] is None
    assert first["pace_missing_reason"] == "NO_600M_BOUNDARY_PARTIAL_INTERVAL_NOT_ESTIMATED"
    assert second["passing_order_status"] == "NOT_APPLICABLE"
    assert second["final_corner_position"] is None
    assert all(row["seconds_x_final_corner_position"] is None for row in result["observations"])
    assert result["aggregate"] is None


@pytest.mark.parametrize("field,value", [("pace", "35"), ("passing_order", "4-5"),
                                         ("date", "2026-09-18"), ("horse_id", "wrong"),
                                         ("horse_name", "B")])
def test_source_identity_or_value_conflicts_stop_without_repair(tmp_path, field, value):
    row = source_row(tmp_path);row[field] = value
    with pytest.raises(PaceObservationError):
        observed_history_pace_context([row], target_race_date="2026-10-25")


def test_future_history_and_duplicate_history_are_rejected(tmp_path):
    row = source_row(tmp_path)
    with pytest.raises(PaceObservationError, match="not before"):
        observed_history_pace_context([row], target_race_date="2026-09-19")
    with pytest.raises(PaceObservationError, match="Duplicate"):
        observed_history_pace_context([row, row], target_race_date="2026-10-25")


def test_raw_source_tamper_and_forged_pace_scope_are_rejected(tmp_path):
    row = source_row(tmp_path)
    row["official_pace_observation"] = {"pace_scope":"horse_individual_split"}
    with pytest.raises(PaceObservationError, match="metadata"):
        observed_history_pace_context([row], target_race_date="2026-10-25")
    row.pop("official_pace_observation")
    with open(row["official_result_source"]["path"], "ab") as out:
        out.write(b"changed")
    with pytest.raises(PaceObservationError, match="bytes changed"):
        observed_history_pace_context([row], target_race_date="2026-10-25")


def test_source_date_header_accepts_official_meeting_suffix_but_not_two_dates(tmp_path):
    row = source_row(tmp_path, day="2026年9月19日（日曜） 4回東京2日")
    row["date"] = "2026-09-19"
    result = observed_history_pace_context([row], target_race_date="2026-10-25")
    assert result["complete_context_count"] == 1
    row = source_row(tmp_path, day="2026年9月19日 2026年9月20日")
    row["date"] = "2026-09-19"
    with pytest.raises(PaceObservationError, match="ambiguous"):
        observed_history_pace_context([row], target_race_date="2026-10-25")


@pytest.mark.parametrize("field,value", [("passing_order_status", "NOT_APPLICABLE"),
                                        ("pace_scope", "horse_individual_split")])
def test_conflicting_top_level_semantics_are_not_silently_repaired(tmp_path, field, value):
    row = source_row(tmp_path);row[field] = value
    with pytest.raises(PaceObservationError, match="semantics|metadata"):
        observed_history_pace_context([row], target_race_date="2026-10-25")


def test_nonfinisher_partial_corner_observation_is_not_a_verified_final_corner(tmp_path):
    row = source_row(tmp_path, place="中止")
    result = observed_history_pace_context([row], target_race_date="2026-10-25")
    observed = result["observations"][0]
    assert observed["last_recorded_corner_position"] == 5
    assert observed["recorded_corner_positions"] == [3, 5]
    assert observed["final_corner_position"] is None
    assert observed["final_corner_missing_reason"] == "NONFINISHER_FINAL_CORNER_NOT_VERIFIED"
    assert observed["seconds_x_final_corner_position"] is None
    assert result["retained_history_count"] == 1
    assert result["complete_context_count"] == 0
