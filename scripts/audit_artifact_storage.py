#!/usr/bin/env python3
"""Report artifact directories that have exceeded their retention period."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, TextIO


DATE_PREFIX = re.compile(r"^(?P<date>\d{8})(?:_|$)")


@dataclass(frozen=True)
class StoragePolicy:
    relative_path: Path
    retention_days: int


DEFAULT_POLICIES = (
    StoragePolicy(Path("report/races"), 30),
    StoragePolicy(Path("report/win5"), 30),
    StoragePolicy(Path("data/collected"), 180),
    StoragePolicy(Path("report/final_predictions"), 365),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List artifact directories that are ready for archive review."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root. Defaults to the directory above scripts/.",
    )
    parser.add_argument(
        "--as-of",
        type=lambda value: datetime.strptime(value, "%Y-%m-%d").date(),
        default=date.today(),
        help="Reference date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        help="Override every policy retention period for a what-if audit.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional TSV output path. Defaults to standard output.",
    )
    return parser.parse_args()


def directory_size(path: Path) -> int:
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            total += entry.stat().st_size
    return total


def artifact_date(name: str) -> date | None:
    match = DATE_PREFIX.match(name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group("date"), "%Y%m%d").date()
    except ValueError:
        return None


def audit_rows(
    root: Path,
    as_of: date,
    retention_override: int | None = None,
) -> Iterable[dict[str, str | int]]:
    for policy in DEFAULT_POLICIES:
        scope = root / policy.relative_path
        if not scope.is_dir():
            continue
        retention_days = retention_override or policy.retention_days
        for artifact in sorted(path for path in scope.iterdir() if path.is_dir()):
            parsed_date = artifact_date(artifact.name)
            if parsed_date is None:
                age_days: str | int = ""
                action = "manual-review"
                date_value = ""
            else:
                age_days = (as_of - parsed_date).days
                date_value = parsed_date.isoformat()
                if age_days < 0:
                    action = "review-future-date"
                elif age_days > retention_days:
                    action = "archive-candidate"
                else:
                    action = "keep-active"

            yield {
                "scope": policy.relative_path.as_posix(),
                "artifact": artifact.name,
                "artifact_date": date_value,
                "age_days": age_days,
                "retention_days": retention_days,
                "size_bytes": directory_size(artifact),
                "action": action,
            }


def write_report(rows: Iterable[dict[str, str | int]], destination: TextIO) -> None:
    fieldnames = (
        "scope",
        "artifact",
        "artifact_date",
        "age_days",
        "retention_days",
        "size_bytes",
        "action",
    )
    writer = csv.DictWriter(
        destination,
        fieldnames=fieldnames,
        delimiter="\t",
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)


def main() -> int:
    args = parse_args()
    if args.retention_days is not None and args.retention_days <= 0:
        raise SystemExit("--retention-days must be greater than zero")

    rows = list(audit_rows(args.root.resolve(), args.as_of, args.retention_days))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8", newline="") as destination:
            write_report(rows, destination)
        print(f"Storage audit written to {args.output}")
    else:
        write_report(rows, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
