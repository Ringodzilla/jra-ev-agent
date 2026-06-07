#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategy.win5 import evaluate_win5_coverage


def main() -> None:
    args = parse_args()
    plan = json.loads(Path(args.plan_json).read_text(encoding="utf-8"))
    result_numbers = _resolve_result_numbers(args)
    metrics = evaluate_win5_coverage(plan, result_numbers)
    metrics["mode"] = str(plan.get("mode", ""))
    metrics["plan_json"] = args.plan_json
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate WIN5 selected/top5 coverage")
    parser.add_argument("--plan-json", required=True, help="Path to 05_bet_builder.json")
    parser.add_argument("--result-numbers", nargs="*", default=None, help="Five winning horse numbers in WIN5 order")
    parser.add_argument("--result-labels", default="", help="CSV containing a WIN5 row from append_result_labels.py")
    return parser.parse_args()


def _resolve_result_numbers(args: argparse.Namespace) -> list[str]:
    if args.result_numbers:
        return [str(number).strip() for number in args.result_numbers if str(number).strip()]
    if args.result_labels:
        numbers = _numbers_from_labels(Path(args.result_labels))
        if numbers:
            return numbers
    raise ValueError("provide --result-numbers or --result-labels with a WIN5 row")


def _numbers_from_labels(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as file_obj:
        rows = list(csv.DictReader(file_obj))
    for row in reversed(rows):
        if str(row.get("式別", "")).strip().upper() != "WIN5":
            continue
        combo = str(row.get("組番", "")).strip()
        if combo:
            return [part for part in combo.replace("→", "-").split("-") if part]
    return []


if __name__ == "__main__":
    main()
