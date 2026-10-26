"""Payload contracts for deterministic skill gates; not proof of LLM execution."""
from __future__ import annotations

import hashlib
from datetime import datetime
import math
from pathlib import Path
from urllib.parse import urlparse

from src.data_integrity import audit_collected_data


FEATURES = ("ability_score", "course_score", "pace_score", "weight_score", "jockey_score")


def configurations(payload: dict) -> list[dict]:
    values = payload.get("race_configs") or ([payload["race_config"]] if payload.get("race_config") else [])
    return values if isinstance(values, list) and all(isinstance(c, dict) for c in values) else []


def race_identity(config: dict) -> str:
    return str(config.get("race_id") or
               f"{str(config.get('race_date', '')).replace('-', '')}_{config.get('track', '')}_{config.get('race_number', '')}")


def identities(rows: list) -> set[tuple[str, str]]:
    return {(str(r.get("race_id", "")), str(r.get("horse_id") or r.get("horse_number") or "")) for r in rows}


def _rows(output: dict, key: str) -> list:
    value = output.get(key)
    return value if isinstance(value, list) and all(isinstance(r, dict) for r in value) else []


def _finite(value: object) -> bool:
    try:
        return not isinstance(value, bool) and math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _valid_rows(rows: list, races: set[str], previous: list | None = None) -> bool:
    keys = identities(rows)
    return (bool(rows) and len(keys) == len(rows) and all(race in races and horse for race, horse in keys)
            and (previous is None or keys == identities(previous)))


def _review(output: dict) -> dict:
    return output.get("final_review", output)


def _environment_policy(rows: list, output: dict) -> bool:
    from src.environment_prediction import DEFAULT_PRODUCTION_MODEL_PATH, _load_promoted_model
    for row in rows:
        if not _finite(row.get("environment_score")) or not isinstance(row.get("environment_adjustment_applied"), bool):
            return False
        if row.get("environment_model_status") == "promoted":
            path = Path((output.get("environment_application") or {}).get("production_model_path") or DEFAULT_PRODUCTION_MODEL_PATH)
            artifact, _ = _load_promoted_model(path)
            if artifact is None or row.get("environment_model_sha256") != hashlib.sha256(path.read_bytes()).hexdigest():
                return False
            if not row["environment_adjustment_applied"] and float(row["environment_score"]) != 0:
                return False
        elif not (row.get("environment_model_status") and float(row["environment_score"]) == 0
                  and row["environment_adjustment_applied"] is False and row.get("environment_not_applied_reason")):
            return False
    return bool(rows)


def _environment_probability_valid(rows: list, scenarios: list, weights: dict) -> bool:
    from analysis.ev import EVWeights
    from src.agents.ev_calculator import EVCalculatorAgent
    # Assert deterministic reproducibility; never replace saved predictions.
    expected = EVCalculatorAgent(EVWeights(**weights)).run(scenarios)["ev_rows"]
    expected_by_key = {next(iter(identities([r]))): r for r in expected}
    before = {next(iter(identities([r]))): r for r in scenarios}
    return bool(rows) and all(
        all(row.get(field) == expected_by_key.get(next(iter(identities([row]))), {}).get(field)
            for field in ("win_prob", "p_base", "p_final"))
        and all(row.get(key) == value for key, value in before.get(next(iter(identities([row]))), {}).items()
                if key.startswith("environment_") or key.startswith("target_environment_"))
        for row in rows)


def stage_checks(step: str, output: dict, prior: dict[str, dict], *, mode: str) -> dict[str, bool]:
    """Derive requirements internally, independent of caller-supplied assertions."""
    from src.skill_workflow import probability_checks, ev_checks

    configs = configurations(output if step == "intake" else prior.get("intake", {}))
    races = {race_identity(c) for c in configs}
    if step == "intake":
        return {"configs_complete": bool(configs) and len(races) == len(configs) and all(
            c.get("race_date") and c.get("track") and c.get("race_number")
            and urlparse(str(c.get("source_url", ""))).hostname in {"www.jra.go.jp", "www.jra.jp", "jra.jp", "jra.go.jp"}
            for c in configs)}
    collection = prior.get("collection", {})
    entries = _rows(collection, "entries")
    if step == "collection":
        rows, entries = _rows(output, "rows"), _rows(output, "entries")
        artifacts = (output.get("source_audit") or {}).get("artifacts")
        return {"entries_identified": _valid_rows(entries, races),
                "history_rows_scoped": isinstance(output.get("rows"), list) and bool(rows)
                and all(str(r.get("race_id", "")) in races and str(r.get("horse_id") or r.get("horse_number") or "") for r in rows)
                and identities(rows).issubset(identities(entries)),
                "quality_evidence_present": isinstance(output.get("quality_report"), dict),
                "raw_sources_bound": isinstance(artifacts, list) and bool(artifacts) and all(
                    isinstance(a, dict) and isinstance(a.get("html"), str) and bool(a["html"])
                    and a.get("kind") in {"horse_history", "race_detail", "history_result", "history_odds"}
                    and hashlib.sha256(a["html"].encode()).hexdigest() == a.get("sha256") for a in artifacts)}
    if step == "integrity_audit":
        expected = audit_collected_data(collection, configs, output.get("approval_evidence", []), purpose=mode)
        fields = ("input_sha256", "race_ids", "gate", "findings", "missing_fields", "history_counts",
                  "history_counts_verified", "unapproved_finding_count", "source_audit_status", "verification_issues")
        return {"audit_matches_current_source_reparse": all(key in output and output[key] == expected[key] for key in fields)}
    if step == "approval_gate":
        audit = prior.get("integrity_audit", {})
        return {"same_audit": all(output.get(k) == v for k, v in audit.items() if k != "_skill_binding"),
                "approved_inputs_only": output.get("gate") == "PASS" and output.get("unapproved_finding_count") == 0
                and output.get("history_counts_verified") is True and output.get("source_audit_status") == "VERIFIED_REPARSE"}
    if step == "analysis":
        rows = _rows(output, "feature_rows")
        return {"feature_lineup": _valid_rows(rows, races, entries),
                "ability_components_complete": bool(rows) and all(all(_finite(r.get(k)) for k in FEATURES) for r in rows),
                "environment_lineage_present": _environment_policy(rows, output)}
    if step == "simulation":
        rows = _rows(output, "scenario_rows")
        features = _rows(prior.get("analysis", {}), "feature_rows")
        before = {key: row for key, row in zip([next(iter(identities([r]))) for r in features], features)}
        preserved = ("ability_score", "course_score", "weight_score", "jockey_score", "environment_score", "environment_model_status")
        return {"scenario_lineup": _valid_rows(rows, races, features),
                "scenario_values_complete": bool(rows) and all(all(_finite(r.get(k)) for k in (
                    "pace_high", "pace_mid", "pace_slow", "pace_mix_high", "pace_mix_mid", "pace_mix_slow"))
                    and all(0 <= float(r[k]) <= 1 for k in ("pace_mix_high", "pace_mix_mid", "pace_mix_slow"))
                    and abs(sum(float(r[k]) for k in ("pace_mix_high", "pace_mix_mid", "pace_mix_slow")) - 1) <= 3e-6 for r in rows),
                "ability_environment_preserved": all(all(r.get(k) == before.get(next(iter(identities([r]))), {}).get(k)
                    for k in preserved) for r in rows)}
    if step in {"probability", "ev"}:
        rows = _rows(output, "ev_rows")
        checks = {"probability_lineup": _valid_rows(rows, races, _rows(prior.get("simulation", {}), "scenario_rows")),
                  **probability_checks(rows),
                  "two_stage_probability_lineage": _environment_probability_valid(rows, _rows(prior.get("simulation", {}), "scenario_rows"), prior["intake"].get("ev_weights", {}))}
        if step == "ev":
            checks.update(ev_checks(rows))
            checks["same_probability_rows"] = rows == _rows(prior.get("probability", {}), "ev_rows")
            from analysis.candidate_ev import build_candidate_evaluations
            expected = build_candidate_evaluations(rows, _rows(collection, "combo_odds"))
            checks["canonical_candidates_recalculated"] = (
                output.get("candidate_evaluations") == expected["candidate_evaluations"]
                and output.get("validation") == expected["validation"])
        return checks
    if step == "tickets":
        from src.agents.reviewer import _probability_lineage_errors
        tickets, candidates = _rows(output, "tickets"), _rows(output, "candidate_evaluations")
        originals = {r["key"]: r for r in _rows(prior.get("ev", {}), "candidate_evaluations")}
        ordinary = [r for r in candidates if r.get("ticket_shape") != "wide_box"]
        return {"ticket_plan_complete": isinstance(output.get("tickets"), list)
                and isinstance(output.get("candidate_evaluations"), list) and bool(output.get("bet_types_considered"))
                and isinstance(output.get("candidate_counts"), dict) and bool(output.get("candidate_counts")),
                "builder_source_contract": output.get("optimization_mode") == "canonical_probability_robust_odds"
                and not _probability_lineage_errors(output),
                "canonical_candidate_values_preserved": all(r.get("source_candidate_key") in originals
                    and _finite(r.get("raw_hit_prob")) and abs(float(r["raw_hit_prob"]) - float(originals[r["source_candidate_key"]]["hit_prob"])) <= 1e-9
                    for r in ordinary),
                "selected_tickets_numeric": all(_finite(t.get("stake")) and float(t["stake"]) > 0
                    and _finite(t.get("hit_prob")) and 0 <= float(t["hit_prob"]) <= 1 for t in tickets)}
    if step == "review":
        from src.agents.reviewer import ReviewerAgent
        from src.agents.settings import WorkflowSettings
        review = _review(output)
        quantitative = output.get("quantitative_review", output)
        plan = output.get("reviewed_ticket_plan", prior.get("tickets", {}))
        expected = ReviewerAgent(WorkflowSettings(**prior["intake"].get("workflow_settings", {}))).run(
            collection, _rows(prior.get("simulation", {}), "scenario_rows"),
            _rows(prior.get("ev", {}), "ev_rows"), plan, attempt=0)
        required = ("status", "reason_codes", "failures", "probability_lineage", "value_integrity", "stage_counts")
        checks = {"quantitative_review_recalculated": all(key in quantitative and quantitative[key] == expected[key] for key in required),
                  "review_completed": isinstance(review, dict) and review.get("status") in {"OK", "NG"}
                  and isinstance(review.get("reason"), str) and bool(review["reason"].strip())}
        if mode == "final":
            from src.deadline import DeadlineSettings, build_deadline_plan
            from src.final_workflow import FinalReviewerAgent
            now = datetime.fromisoformat(str(review.get("issued_at", "")))
            settings = DeadlineSettings(**prior["intake"].get("deadline_settings", {}))
            expected_final = FinalReviewerAgent(settings=settings, now=lambda: now).run(
                plan=build_deadline_plan(configs[0], now=now, settings=settings),
                collected=collection, ticket_plan=plan, quantitative_review=quantitative)
            checks["final_review_recalculated"] = ("final_review" in output
                and all(key in review and review[key] == expected_final[key] for key in ("status", "decision", "decision_code", "checks")))
        return checks
    if step == "decision":
        review = _review(prior.get("review", {}))
        tickets = output.get("tickets")
        return {"decision_complete": output.get("decision") in {"GO", "NO_GO"} and isinstance(tickets, list),
                "no_go_withholds_tickets": output.get("decision") != "NO_GO" or tickets == [],
                "go_requires_positive_review": output.get("decision") != "GO" or (review.get("status") == "OK"
                    and review.get("decision") == "GO" and bool(tickets)
                    and isinstance(review.get("checks"), dict) and bool(review["checks"])
                    and all(value is True for value in review["checks"].values())
                    and {"deadline", "final_go_window_open", "odds_fresh", "conditions_fresh", "combination_coverage",
                         "tickets_use_jra_live_odds", "quantitative_review", "snapshot_complete"}.issubset(review["checks"])
                    and tickets == prior.get("review", {}).get("reviewed_ticket_plan", prior.get("tickets", {})).get("tickets")),
                "review_bound": output.get("candidate_review", review.get("status")) == review.get("status")}
    if step == "report":
        decision = prior.get("decision", {})
        if mode == "final":
            return {"final_report_matches_decision": all(output.get(k) == v for k, v in decision.items() if k != "_skill_binding")
                    and bool(output.get("issued_at"))}
        return {"article_complete": all(isinstance(output.get(k), str) and bool(output[k].strip()) for k in ("status", "title", "markdown")),
                "article_review_bound": output.get("review_status") == _review(prior.get("review", {})).get("status")}
    return {"known_stage": False}
