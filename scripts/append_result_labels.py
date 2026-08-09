#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


OUTPUT_COLUMNS = ["race_id", "式別", "組番", "馬番", "払戻金"]
DEFAULT_OUTPUT = ROOT / "data/processed/result_labels.csv"


def rows_from_review(review: dict[str, object]) -> list[dict[str, str]]:
    race = dict(review.get("race") or {})
    rows: list[dict[str, str]] = []
    result = dict(review.get("result") or {})
    win5_row = _win5_label_row(result)
    if win5_row:
        rows.append(win5_row)

    race_id = str(race.get("race_id") or _build_race_id(race)).strip()
    payouts = _normalize_payouts(result.get("payouts"))
    if not race_id and payouts:
        raise ValueError("review JSON does not contain enough race metadata to build race_id")

    for payout in payouts:
        row = _label_row(race_id, payout)
        if row:
            rows.append(row)
    return rows


def _normalize_payouts(value: object) -> list[dict[str, object]]:
    """Accept canonical lists plus persisted dict/string representations."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            try:
                value = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                return []

    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []

    normalized: list[dict[str, object]] = []
    for bet_type, details in value.items():
        if isinstance(details, list):
            for item in details:
                if isinstance(item, dict):
                    normalized.append({"bet_type": bet_type, **item})
                elif parsed := _parse_jra_payout_text(bet_type, item):
                    normalized.append(parsed)
        elif isinstance(details, dict):
            if any(key in details for key in ("combination", "payout_yen_per_100")):
                normalized.append({"bet_type": bet_type, **details})
            else:
                normalized.extend(
                    {
                        "bet_type": bet_type,
                        "combination": combination,
                        "payout_yen_per_100": payout,
                    }
                    for combination, payout in details.items()
                )
        elif parsed := _parse_jra_payout_text(bet_type, details):
            normalized.append(parsed)
    return normalized


def _parse_jra_payout_text(bet_type: object, value: object) -> dict[str, object]:
    match = re.fullmatch(r"\s*(\d+(?:-\d+)*)\s+([\d,]+)円\s*", str(value))
    if not match:
        return {}
    return {
        "bet_type": str(bet_type),
        "combination": match.group(1),
        "payout_yen_per_100": match.group(2).replace(",", ""),
    }


def append_rows(path: Path, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_rows(path)
    seen = {
        (row.get("race_id", ""), row.get("式別", ""), row.get("組番", ""), row.get("馬番", ""))
        for row in existing
    }
    pending = [
        row
        for row in rows
        if (row["race_id"], row["式別"], row["組番"], row["馬番"]) not in seen
    ]
    if not pending:
        return []

    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=OUTPUT_COLUMNS)
        if write_header:
            writer.writeheader()
        for row in pending:
            writer.writerow({key: row.get(key, "") for key in OUTPUT_COLUMNS})
    return pending


def _label_row(race_id: str, payout: dict[str, object]) -> dict[str, str]:
    bet_type = str(payout.get("bet_type", "")).strip()
    combination = str(payout.get("combination", "")).strip()
    payout_yen = str(payout.get("payout_yen_per_100", "")).replace(",", "").strip()
    if not bet_type or not combination or not payout_yen:
        return {}

    if bet_type in {"単勝", "複勝"}:
        return {"race_id": race_id, "式別": bet_type, "組番": "", "馬番": combination, "払戻金": payout_yen}
    return {"race_id": race_id, "式別": bet_type, "組番": combination, "馬番": "", "払戻金": payout_yen}


def _win5_label_row(result: dict[str, object]) -> dict[str, str]:
    win5 = dict(result.get("win5") or {})
    numbers = win5.get("numbers") or win5.get("horse_numbers") or win5.get("result_numbers")
    if not isinstance(numbers, list):
        return {}
    combination = "-".join(str(number).strip() for number in numbers if str(number).strip())
    payout_yen = str(win5.get("payout_yen_per_100") or win5.get("payout") or "").replace(",", "").strip()
    if not combination or not payout_yen:
        return {}
    return {"race_id": "WIN5", "式別": "WIN5", "組番": combination, "馬番": "", "払戻金": payout_yen}


def _build_race_id(race: dict[str, object]) -> str:
    race_date = str(race.get("date", "")).replace("-", "").strip()
    track = str(race.get("track", "")).strip()
    race_number = str(race.get("race_number", "")).strip()
    if not race_date or not track or not race_number:
        return ""
    return f"{race_date}_{track}_{int(float(race_number)):02d}"


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as file_obj:
        return list(csv.DictReader(file_obj))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Append race result payout labels for fixed strategy evaluation")
    parser.add_argument("--review-json", required=True, help="Path to a race review JSON with result.payouts")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output label CSV path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    review = json.loads(Path(args.review_json).read_text(encoding="utf-8"))
    rows = rows_from_review(review)
    appended = append_rows(Path(args.output), rows)
    print(json.dumps({"rows": len(rows), "appended": len(appended), "output": args.output}, ensure_ascii=False))


if __name__ == "__main__":
    main()
