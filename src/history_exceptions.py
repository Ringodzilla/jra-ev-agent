"""Explicit, source-bound history exceptions; never rewrite observed inputs."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re


class HistoryExceptionError(ValueError):
    """An exception lacks exact user, input, identity or source evidence."""


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def source_manifest_sha256(collected):
    """Bind the complete declared raw evidence set, including original HTML."""
    return _digest(collected.get("source_audit", {}))


def _day(value):
    text = str(value)
    match = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    return date(*map(int, match.groups())).isoformat() if match else date.fromisoformat(text).isoformat()


def _file(ref):
    if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
        raise HistoryExceptionError("Source artifact path required")
    path = Path(ref["path"])
    if not path.is_absolute() or not re.fullmatch(r"[0-9a-f]{64}", str(ref.get("sha256", ""))):
        raise HistoryExceptionError("Absolute source path and SHA256 required")
    if hashlib.sha256(path.read_bytes()).hexdigest() != ref["sha256"]:
        raise HistoryExceptionError("Source artifact changed")
    return path


def _references(value):
    if isinstance(value, dict):
        if "path" in value and "sha256" in value:
            _file(value)
        for key, item in value.items():
            if key.endswith("_path") and key[:-5] + "_sha256" in value:
                _file({"path": item, "sha256": value[key[:-5] + "_sha256"]})
            _references(item)
    elif isinstance(value, list):
        for item in value:
            _references(item)


def _attempts(record):
    report_path = _file(record["source_attempts"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (report.get("role") != "data_collector" or not report.get("routes")
            or not isinstance(report.get("unresolved_fields"), dict)):
        raise HistoryExceptionError("Source acquisition report is not explicit")
    refs = record["source_artifacts"]
    if not isinstance(refs, list) or not refs:
        raise HistoryExceptionError("Source evidence artifacts required")
    paths = {_file(ref) for ref in refs}
    for filename in report.get("evidence_files", []):
        path = report_path.parent / filename
        if path not in paths:
            raise HistoryExceptionError("Acquisition report evidence file is not SHA-bound")
        payload = json.loads(path.read_text(encoding="utf-8"))
        _references(payload)
    return report


def _unavailable_scope(record, report):
    """An unrelated acquisition failure cannot authorize this horse's proxy."""
    observations = report.get("unresolved_observations")
    if not isinstance(observations, list):
        raise HistoryExceptionError("Exact structured acquisition scope observations required")
    matches = [item for item in observations if isinstance(item, dict)
               and _key(item) == _key(record) and item.get("field") == record["field"]]
    if len(matches) != 1 or not str(matches[0].get("reason", "")).strip():
        raise HistoryExceptionError("Acquisition report does not cover exact history observation")


def _number(value):
    if isinstance(value, bool):
        raise HistoryExceptionError("Boolean is not an exception value")
    result = Decimal(str(value))
    if not result.is_finite() or result <= 0:
        raise HistoryExceptionError("Positive finite exception value required")
    return result


def _key(row):
    return tuple(str(row.get(k, "")) for k in ("race_id", "horse_id", "horse_number", "run_index")) + (_day(row["date"]),)


def _reject_projection_markers(collected):
    if any("approved_history_exceptions" in row or "approved_front_rate" in row
           for row in collected.get("rows", [])):
        raise HistoryExceptionError("Source histories must not contain model projection markers")


def _record(record, approval, collected, configs, purpose, digest, manifest):
    from src.skill_stage_contracts import race_identity
    if not isinstance(record, dict):
        raise HistoryExceptionError("Exception record must be an object")
    if (record.get("input_sha256") != digest or not str(record.get("evidence", "")).strip()
            or record["evidence"] not in approval["evidence"]):
        raise HistoryExceptionError("Exception evidence and exact input binding required")
    if record.get("source_manifest_sha256") != manifest:
        raise HistoryExceptionError("Exception source artifact manifest changed")
    race = str(record.get("race_id", ""))
    if record.get("purpose", purpose) != purpose or race != approval.get("race_id"):
        raise HistoryExceptionError("Exception race and purpose differ from approval")
    config_list = configs if isinstance(configs, list) else [configs]
    config = [cfg for cfg in config_list if race_identity(cfg) == race]
    if len(config) != 1:
        raise HistoryExceptionError("Exception race not in requested configuration")
    if (record["date"] != _day(record["date"])
            or _day(record["date"]) >= _day(config[0]["race_date"])):
        raise HistoryExceptionError("Exception history date must precede target race")
    matches = [row for row in collected.get("rows", []) if _key(row) == _key(record)]
    if len(matches) != 1:
        raise HistoryExceptionError("Exception must identify one exact source history")
    row = matches[0]
    if (str(row.get("history_surface", "")) == "障害" or "障害" in str(row.get("race_name", ""))
            or row.get(record["field"] + "_status") == "NOT_APPLICABLE"):
        raise HistoryExceptionError("Exception cannot redefine inapplicable or jump observations")
    field, method, value = record["field"], record["method"], record["value"]
    ids = record.get("finding_ids")
    if (not isinstance(ids, list) or len(ids) != len(set(ids))
            or any(not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{24}", item) for item in ids)
            or not set(ids).issubset(approval["finding_ids"])):
        raise HistoryExceptionError("Exception finding IDs must belong to exact approval")
    report = _attempts(record)
    _unavailable_scope(record, report)
    if field == "last_3f" and method == "imperial_3f_proxy_seconds":
        if (row.get("last_3f_source") != "fallback" and str(row.get("last_3f", "")).strip()):
            raise HistoryExceptionError("Observed sectional cannot be overridden")
        if not ids or Decimal(str(record["source_distance_m"])) != Decimal("603.504") or Decimal(str(record["target_distance_m"])) != 600:
            raise HistoryExceptionError("Only explicit imperial3f-to600m proxy is supported")
        expected = _number(value)
        observation_path = _file(record["proxy_observations"])
        if observation_path not in {_file(ref) for ref in record["source_artifacts"]}:
            raise HistoryExceptionError("Proxy observation must be in bound source evidence")
        observations = json.loads(observation_path.read_text(encoding="utf-8"))
        observed = [item for item in observations if item.get("horse_name") == row.get("horse_name")
                    and item.get("race_date") == record["date"]]
        if len(observed) != 1:
            raise HistoryExceptionError("Proxy source horse and date do not match")
        item = observed[0]
        _file(item["source"])
        from jra_scraper.foreign_history import parse_imperial_proxy_observation
        if parse_imperial_proxy_observation(item, row) != expected:
            raise HistoryExceptionError("Proxy differs from bound original sectional")
        splits = item["individual_final_3_imperial_furlongs_splits_seconds"]
        if (len(splits) != 3 or sum(_number(part) for part in splits) != expected
                or _number(item["individual_final_3_imperial_furlongs_seconds"]) != expected
                or Decimal(str(item["section_distance_m"])) != Decimal("603.504")):
            raise HistoryExceptionError("Proxy differs from official imperial sectional observation")
    elif field == "passing_order" and method == "approved_front_rate":
        if not ids or _number(value) != Decimal("0.5") or str(row.get("passing_order", "")).strip():
            raise HistoryExceptionError("Only missing corner history with approved front_rate0.5 is supported")
    elif field in {"weather", "pace"} and method == "not_used":
        if value is not None or str(row.get(field, "")).strip():
            raise HistoryExceptionError("not_used preserves only absent optional observations")
    else:
        raise HistoryExceptionError("Unsupported history exception field or method")
    result = deepcopy(record)
    result.update(_source_rows_sha256=_digest(collected.get("rows", [])),
                  _source_entries_sha256=_digest(collected.get("entries", [])))
    return result


def validate_history_exceptions(collected, configs, approvals, purpose="prediction"):
    """Validate scoped records; malformed evidence fails closed with ValueError."""
    from src.data_integrity import data_input_sha256
    valid = []
    seen = set()
    digest = data_input_sha256(collected, configs)
    manifest = source_manifest_sha256(collected)
    try:
        _reject_projection_markers(collected)
        for approval in approvals:
            records = approval.get("scoped_history_exceptions", [])
            if not isinstance(records, list):
                raise HistoryExceptionError("Scoped history exceptions must be a list")
            if not records:
                continue
            if (approval.get("approved_by") != "user" or approval.get("status") != "APPROVED"
                    or approval.get("input_sha256") != digest or approval.get("purpose") != purpose
                    or not isinstance(approval.get("evidence"), str) or not approval["evidence"].strip()
                    or not isinstance(approval.get("finding_ids"), list)):
                raise HistoryExceptionError("Explicit user approval with exact input and purpose required")
            for record in records:
                result = _record(record, approval, collected, configs, purpose, digest, manifest)
                identity = _key(result) + (result["field"],)
                if identity in seen:
                    raise HistoryExceptionError("Duplicate scoped history exception")
                seen.add(identity)
                valid.append(result)
    except (KeyError, TypeError, AttributeError, OSError, InvalidOperation, ValueError) as exc:
        raise HistoryExceptionError(str(exc)) from exc
    return valid


def model_history_rows(collected, audit):
    """Create the approved model view without changing source status or facts."""
    _reject_projection_markers(collected)
    rows = deepcopy(collected.get("rows", []))
    records = audit.get("approved_history_exceptions", [])
    if not records:
        return rows
    if audit.get("gate") != "PASS":
        raise HistoryExceptionError("Model exceptions require a PASS audit")
    expected = validate_history_exceptions(collected, collected["race_configs"],
                                          audit["approval_evidence"], audit["purpose"])
    if expected != records:
        raise HistoryExceptionError("Model exceptions differ from revalidated user evidence")
    for record in records:
        if (record["input_sha256"] != audit.get("input_sha256")
                or record.get("_source_rows_sha256") != _digest(collected.get("rows", []))
                or record.get("_source_entries_sha256") != _digest(collected.get("entries", []))):
            raise HistoryExceptionError("Approved model view input changed after audit")
        _attempts(record)
        # Revalidation above already requires one exact source identity.
        row = next(row for row in rows if _key(row) == _key(record))
        field = record["field"]
        row.setdefault("approved_history_exceptions", {})[field] = deepcopy(record)
        if field == "last_3f":
            row["last_3f"] = str(record["value"])
        elif field == "passing_order":
            row["approved_front_rate"] = float(record["value"])
    return rows
