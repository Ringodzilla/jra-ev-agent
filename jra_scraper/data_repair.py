from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .models import ParserIssue


MANUAL_HISTORY_COLUMNS = [
    "horse_id",
    "horse_name",
    "date",
    "race_name",
    "course",
    "distance",
    "position",
    "time",
    "weight",
    "jockey",
    "pace",
    "last_3f",
    "track_condition",
    "weather",
    "passing_order",
    "odds",
    "popularity",
]


@dataclass
class MissingDataRepairResult:
    rows: list[dict[str, str]]
    issues: list[ParserIssue] = field(default_factory=list)
    manual_requests: list[dict[str, object]] = field(default_factory=list)
    repair_actions: list[dict[str, object]] = field(default_factory=list)
    manual_template_rows: list[dict[str, str]] = field(default_factory=list)

    @property
    def requires_manual_input(self) -> bool:
        return bool(self.manual_requests)


class MissingHistoryRepairAction:
    """Repair history gaps without mixing collection with downstream analysis."""

    def __init__(self, manual_history_csv: Path, manual_template_csv: Path) -> None:
        self.manual_history_csv = manual_history_csv
        self.manual_template_csv = manual_template_csv

    def build_result(
        self,
        horse,
        *,
        rows: list[dict[str, str]],
        reason: str,
        source_counts: dict[str, int],
        attempted_urls: list[str] | None = None,
        required_history_count: int = 5,
    ) -> MissingDataRepairResult:
        required_count = max(0, min(5, int(required_history_count)))
        missing_count = max(0, required_count - len(rows))
        actions: list[dict[str, object]] = [
            {
                "type": "history_gap_repair",
                "race_id": horse.race_id,
                "horse_id": horse.horse_id,
                "horse_name": horse.horse_name,
                "status": "repaired" if not missing_count else "manual_required",
                "reason": reason,
                "history_count": len(rows),
                "required_history_count": required_count,
                "missing_count": missing_count,
                "source_counts": source_counts,
                "attempted_urls": list(attempted_urls or [horse.horse_url]),
            }
        ]
        if not missing_count:
            return MissingDataRepairResult(rows=rows, repair_actions=actions)

        request = missing_history_request(
            horse,
            history_count=len(rows),
            required_history_count=required_count,
            manual_history_csv=self.manual_history_csv,
            manual_template_csv=self.manual_template_csv,
            reason=reason,
            attempted_urls=attempted_urls,
        )
        issue = ParserIssue(
            stage="pipeline.history_repair",
            severity="medium",
            code="history_manual_input_required",
            message="Fewer than five history rows were available after automatic repair.",
            context={
                "race_id": horse.race_id,
                "horse_name": horse.horse_name,
                "history_count": str(len(rows)),
                "required_history_count": str(required_count),
                "missing_count": str(missing_count),
            },
        )
        return MissingDataRepairResult(
            rows=rows,
            issues=[issue],
            manual_requests=[request],
            repair_actions=actions,
            manual_template_rows=manual_template_rows(horse, missing_count),
        )


def missing_history_request(
    horse,
    *,
    history_count: int,
    required_history_count: int = 5,
    manual_history_csv: Path,
    manual_template_csv: Path,
    reason: str,
    attempted_urls: list[str] | None = None,
) -> dict[str, object]:
    required_count = max(0, min(5, int(required_history_count)))
    return {
        "race_id": horse.race_id,
        "horse_id": horse.horse_id,
        "horse_name": horse.horse_name,
        "horse_url": horse.horse_url,
        "history_count": history_count,
        "required_history_count": required_count,
        "missing_count": max(0, required_count - history_count),
        "fallback_score": 0.5,
        "fallback_reason": reason,
        "attempted_urls": list(attempted_urls or [horse.horse_url]),
        "status": "manual_input_required",
        "action": "Fill the template rows and append completed observations to the manual history CSV.",
        "manual_history_csv": str(manual_history_csv),
        "manual_template_csv": str(manual_template_csv),
        "required_columns": MANUAL_HISTORY_COLUMNS,
    }


def manual_template_rows(horse, missing_count: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for slot in range(1, max(0, missing_count) + 1):
        rows.append(
            {
                "horse_id": horse.horse_id,
                "horse_name": horse.horse_name,
                "_missing_slot": str(slot),
                "date": "",
                "race_name": "",
                "course": "",
                "distance": "",
                "position": "",
                "time": "",
                "weight": "",
                "jockey": "",
                "pace": "",
                "last_3f": "",
                "track_condition": "",
                "weather": "",
                "passing_order": "",
                "odds": "",
                "popularity": "",
            }
        )
    return rows
