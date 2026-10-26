"""All-stage payload and evidence regressions; no prediction model is run."""
from copy import deepcopy
from datetime import datetime
import hashlib
from pathlib import Path

import pytest

from jra_scraper.parser import JRAParser
from jra_scraper.validation import _normalize_row
from src.data_integrity import audit_collected_data
from src.skill_workflow import STEPS, SkillGateBlocked, SkillSequence


def stage_payloads(*, mode="final", review_status="OK"):
    config = dict(race_id="R", race_date="2026-10-24", track="東京", race_number=11,
                  source_url="https://www.jra.go.jp/JRADB/accessD.html", post_time="15:25",
                  career_starts_by_horse_number={"1": 5})
    html = Path("tests/fixtures/horse_history.html").read_text()
    rows = [_normalize_row(r) for r in JRAParser(base_url="https://jra.jp").parse_horse_last5(
        html, "R", "H", "A", "https://jra.jp/history")]
    for row in rows:
        row.update(horse_number="1", target_race_date="2026-10-24")
    entry = dict(race_id="R", horse_id="H", horse_number="1", current_odds="2",
                 current_popularity="1", assigned_weight="55", target_race_date="2026-10-24",
                 target_track="東京", target_surface="芝", target_distance="2000")
    collected = dict(rows=rows, entries=[entry], quality_report={}, source_audit={"artifacts": [
        dict(kind="horse_history", html=html, sha256=hashlib.sha256(html.encode()).hexdigest(),
             race_id="R", horse_id="H", horse_name="A", horse_url="https://jra.jp/history")]})
    collected["combo_odds"] = [dict(race_id="R", bet_type="win", combination="1", odds="2")]
    audit = audit_collected_data(collected, [config], purpose=mode)
    feature = dict(entry, ability_score="0.5", course_score="0.5", pace_score="0.5",
                   weight_score="0.5", jockey_score="0.5", environment_score=0,
                   environment_model_status="unpromoted", environment_adjustment_applied=False,
                   environment_not_applied_reason="NO_PROMOTED_MODEL")
    scenario = dict(feature, pace_high="0.4", pace_mid="0.5", pace_slow="0.6",
                    pace_mix_high="0.3", pace_mix_mid="0.4", pace_mix_slow="0.3")
    evrow = dict(scenario, win_prob="1", p_base="1", p_final="1", ev_current="2")
    from analysis.candidate_ev import build_candidate_evaluations
    calculated = dict(ev_rows=[evrow], **build_candidate_evaluations([evrow], collected["combo_odds"]))
    candidate = calculated["candidate_evaluations"][0]
    builder_candidate = dict(race_id="R", bet_type="win", source_candidate_key=candidate["key"],
        probability_source="04_ev_calculator", raw_hit_prob=1.0, hit_prob="1",
        probability_lineage={"source": "04_ev_calculator", "candidate_key": candidate["key"], "raw_hit_prob": 1.0})
    plan = dict(tickets=[], candidate_evaluations=[builder_candidate], bet_types_considered=["win"],
                candidate_counts={"win": 1}, optimization_mode="canonical_probability_robust_odds",
                probability_lineage={"status": "OK"})
    from src.agents.reviewer import ReviewerAgent
    from src.agents.settings import WorkflowSettings
    from src.deadline import DeadlineSettings, build_deadline_plan
    from src.final_workflow import FinalReviewerAgent
    if review_status == "NG":
        collected["quality_report"]["issues_by_severity"] = {"high": 1}
    quantitative = ReviewerAgent(WorkflowSettings()).run(collected, [scenario], [evrow], plan, attempt=0)
    now = datetime.fromisoformat("2026-10-24T14:00:00+09:00")
    if mode == "final":
        review_result = FinalReviewerAgent(settings=DeadlineSettings(), now=lambda: now).run(
            plan=build_deadline_plan(config, now=now), collected=collected,
            ticket_plan=plan, quantitative_review=quantitative)
        review = dict(final_review=review_result, quantitative_review=quantitative, reviewed_ticket_plan=plan)
    else:
        review = review_result = quantitative
    decision = dict(decision="NO_GO", tickets=[], candidate_review=review_result["status"], issued_at=now.isoformat())
    report = dict(decision) if mode == "final" else dict(status="draft", title="Race preview",
        markdown="No formal tickets issued.", review_status=review_result["status"])
    return dict(intake={"race_configs": [config]}, collection=collected, integrity_audit=audit,
                approval_gate=deepcopy(audit), analysis={"feature_rows": [feature]},
                simulation={"scenario_rows": [scenario]}, probability=deepcopy(calculated), ev=calculated,
                tickets=plan, review=review, decision=decision, report=report)


def run_until(sequence, payloads, stop=None):
    for step, _ in STEPS:
        if step == stop:
            break
        sequence.record(step, sequence.bind(payloads[step]), checks={"caller_checked": True})


@pytest.mark.parametrize("mode,status", [("final", "OK"), ("final", "NG"), ("prediction", "NG")])
def test_all_stages_with_real_source_and_legitimate_no_go(tmp_path, mode, status):
    sequence = SkillSequence(tmp_path, mode=mode)
    run_until(sequence, stage_payloads(mode=mode, review_status=status))
    assert sequence.verify()
    assert sequence.summary()["not_executed"] == []
    assert sequence.summary()["execution_kind"] == "deterministic_code_gates_not_llm_skill_execution"


@pytest.mark.parametrize("step", [step for step, _ in STEPS])
def test_arbitrary_payload_never_passes_even_with_caller_true(tmp_path, step):
    sequence = SkillSequence(tmp_path)
    payloads = stage_payloads(mode="prediction")
    run_until(sequence, payloads, step)
    with pytest.raises(SkillGateBlocked):
        sequence.record(step, sequence.bind({"value": step}), checks={"ok": True})
    assert sequence.summary()["blocked_step"] == step
    assert sequence.records[-1]["status"] == "BLOCKED"
    with pytest.raises(SkillGateBlocked, match="terminal"):
        sequence.record(step, sequence.bind(payloads[step]), checks={"ok": True})


@pytest.mark.parametrize("checks", [{}, {"ok": 1}, {"ok": "PASS"}])
def test_no_truthy_or_empty_caller_checks(tmp_path, checks):
    sequence = SkillSequence(tmp_path)
    with pytest.raises(SkillGateBlocked):
        sequence.record("intake", sequence.bind(stage_payloads()["intake"]), checks=checks)


def test_wrong_input_audit_and_cross_run_binding_are_rejected(tmp_path):
    payloads = stage_payloads()
    first, second = SkillSequence(tmp_path / "a"), SkillSequence(tmp_path / "b")
    foreign = first.bind(payloads["intake"])
    with pytest.raises(SkillGateBlocked):
        second.record("intake", second.bind(foreign), checks={"ok": True})
    run_until(first, payloads, "integrity_audit")
    wrong = dict(payloads["integrity_audit"], input_sha256="0" * 64)
    with pytest.raises(SkillGateBlocked):
        first.record("integrity_audit", first.bind(wrong), checks={"ok": True})


def test_deleted_evidence_records_blocked_stop(tmp_path):
    sequence = SkillSequence(tmp_path)
    payloads = stage_payloads()
    run_until(sequence, payloads, "collection")
    Path(sequence.records[0]["output_artifacts"][0]["path"]).unlink()
    with pytest.raises(SkillGateBlocked):
        sequence.record("collection", sequence.bind(payloads["collection"]), checks={"ok": True})
    assert sequence.summary()["blocked_step"] == "collection"


def test_unknown_stage_contract_rejected_and_malformed_numeric_feature_fails(tmp_path):
    from src.skill_stage_contracts import stage_checks
    assert stage_checks("unknown", {}, {}, mode="final") == {"known_stage": False}
    sequence = SkillSequence(tmp_path)
    payloads = stage_payloads()
    run_until(sequence, payloads, "analysis")
    payloads["analysis"]["feature_rows"][0]["ability_score"] = "not numeric"
    with pytest.raises(SkillGateBlocked):
        sequence.record("analysis", sequence.bind(payloads["analysis"]), checks={"ok": True})


@pytest.mark.parametrize("mutation", ["score_missing", "promoted_missing_file", "promoted_hash", "unapplied_score"])
def test_environment_promotion_metadata_cannot_authorize_an_unverified_score(tmp_path, mutation):
    import json
    from src.skill_stage_contracts import _environment_policy
    payload = stage_payloads()["analysis"]
    row = payload["feature_rows"][0]
    if mutation == "score_missing":
        row.pop("environment_score")
    else:
        path = tmp_path / "model.json"
        if mutation != "promoted_missing_file":
            path.write_text(json.dumps({"status": "PROMOTED", "production_model_changed": True,
                                        "model": {"schema_version": 1}}))
        payload["environment_application"] = {"production_model_path": str(path)}
        row["environment_model_status"] = "promoted"
        row["environment_model_sha256"] = "wrong" if mutation == "promoted_hash" else (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "")
        row["environment_score"] = 0.1
    assert not _environment_policy([row], payload)
