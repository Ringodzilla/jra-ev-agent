from __future__ import annotations

from pathlib import Path

import pytest

from report.note import (
    NoteArtifactResult,
    _build_title_condition,
    _data_quality_lines,
    _dedupe_preserve_order,
    _formation_axis_display,
    _format_race_date_label,
    _format_timestamp,
    _humanize_review_reason_lines,
    _humanize_review_status,
    _invalidated_ticket_rows,
    _mark_lines,
    _normalize_distance,
    _normalize_post_time,
    _normalize_surface,
    _reference_candidate_labels,
    _reference_candidates,
    _ticket_detail_line,
    _ticket_horse_display,
    _ticket_summary,
    _to_float,
    build_note_article,
    validate_note_artifact,
)


def test_note_artifact_validation_failure_modes(tmp_path: Path) -> None:
    result = NoteArtifactResult("body", "artifact", True, 1, True)
    assert result.to_dict()["artifact_synced"] is True
    body = tmp_path / "body.md"
    artifact = tmp_path / "artifact.md"
    with pytest.raises(FileNotFoundError, match="note markdown"):
        validate_note_artifact(body, artifact)
    body.write_text("body", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="artifact markdown"):
        validate_note_artifact(body, artifact)
    artifact.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        validate_note_artifact(body, artifact)
    artifact.write_text("different", encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        validate_note_artifact(body, artifact, expected_text="expected")


def test_title_and_ok_without_tickets_edges() -> None:
    assert _build_title_condition({"surface": "芝"}, []) == "芝"
    assert _build_title_condition({"distance": "1200"}, []) == "1200m"
    article = build_note_article(
        "Race",
        [{"horse_id": "H", "horse_number": "1", "horse_name": "Horse", "win_prob": "0.4", "ev": "1.1"}],
        {"tickets": [], "wide": ["1-2"]},
        review={"status": "OK"},
    )
    assert "reviewer は OK" in article["markdown"]


def test_mark_and_quality_line_branch_details() -> None:
    rows = [
        {"horse_id": str(index), "horse_number": str(index), "horse_name": f"H{index}", "win_prob": str(1 / index), "ev": "1"}
        for index in range(1, 7)
    ]
    assert len(_mark_lines([], rows, rows)) == 4
    quality = {
        "issue_count": 2,
        "repaired_row_count": 1,
        "entry_count": 3,
        "missing_current_odds_entries": 0,
        "live_snapshot_count": 1,
        "live_combo_odds_count": 4,
    }
    text = "\n".join(_data_quality_lines({"status": "OK"}, quality, [{"ticket": 1}], []))
    assert "注意点が 2 件" in text and "全頭分" in text and "4 件保存" in text
    quality["missing_current_odds_entries"] = 2
    assert "3 頭中 1 頭" in "\n".join(_data_quality_lines({"status": "NG"}, quality, [], ["candidate"]))


def test_review_reason_humanization_all_known_and_fallback_paths() -> None:
    reason = "; ".join(
        [
            "predicted/current EV divergence detected",
            "current odds are missing for every entry",
            "high severity parser issues",
            "ticket plan contains low-confidence or sub-threshold tickets",
            "ticket plan overweights extreme longshots",
            "probability normalization drift detected",
        ]
    )
    lines = _humanize_review_reason_lines({"reason": reason, "divergent_rows": [{"horse_name": "Horse"}]})
    assert len(lines) >= 7
    assert _humanize_review_reason_lines({"reason": "custom reason"}) == ["custom reason"]
    assert _humanize_review_reason_lines({}) == ["現時点では無理に買うほどの裏付けが揃いませんでした。"]
    assert _humanize_review_status("PENDING") == "PENDING"
    assert _humanize_review_status("") == "判定保留"


def test_ticket_summary_detail_and_reference_edges() -> None:
    formation_ticket = {
        "bet_type": "sanrentan",
        "ticket_shape": "formation",
        "horse_number": "1>2>3",
        "formation": {
            "first": [{"horse_number": "1", "horse_name": "A"}],
            "second": [{"horse_number": "2"}],
            "third": [{"horse_name": "C"}],
        },
        "stake": "600",
        "point_count": "3",
        "stake_per_point": "200",
        "hit_prob": "0.1",
        "trifecta_odds_est": "20",
        "ev": "2",
        "portfolio_total_stake": "1000",
        "return_if_hit": "2000",
        "return_if_hit_max": "3000",
    }
    assert "3点×200円" in _ticket_summary(formation_ticket)
    detail = _ticket_detail_line(formation_ticket)
    assert "2000-3000円" in detail and "3点×200円" in detail
    equal_return = dict(formation_ticket, return_if_hit_max="2000")
    assert "的中時回収 2000円" in _ticket_detail_line(equal_return)
    assert "1着[1 A]" in _ticket_horse_display(formation_ticket)
    incomplete = dict(formation_ticket, formation={"first": [], "second": [], "third": []})
    assert _ticket_horse_display(incomplete) == "1>2>3"
    assert _formation_axis_display([{"horse_number": "1", "horse_name": "A"}, {"horse_number": "2"}, {"horse_name": "C"}, "bad"]) == "1 A, 2, C"

    assert _reference_candidates([], {"wide": ["", "1-2"]}) == ["ワイド 1-2"]
    nested = {"races": [{"invalidated_tickets": [{"bet_type": "win", "horse_number": "1", "horse_name": "A"}]}]}
    assert len(_invalidated_ticket_rows(nested)) == 1
    assert _reference_candidate_labels([], nested)[0].startswith("単勝")


def test_low_level_format_and_normalization_fallbacks() -> None:
    assert _dedupe_preserve_order(["", " A ", "A", "B"]) == ["A", "B"]
    assert _to_float(object(), 4.0) == 4.0
    assert _format_timestamp("not-a-time") == "not-a-time"
    assert _format_timestamp("2026-01-01T00:00:00") == "2026-01-01 09:00 JST"
    assert _format_race_date_label("not-a-date") == "not-a-date"
    assert _normalize_post_time("1530") == "15:30"
    assert _normalize_post_time("soon") == "soon"
    assert _normalize_surface("障害") == "障害"
    assert _normalize_surface("other") == "other"
    assert _normalize_distance("unknown") == "unknown"
