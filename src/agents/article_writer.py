from __future__ import annotations

from report.note import build_note_article


class ArticleWriterAgent:
    def run(
        self,
        race_configs: list[dict[str, object]],
        *,
        ev_rows: list[dict[str, object]],
        ticket_plan: dict[str, object],
        review: dict[str, object],
        quality_report: dict[str, object],
        odds_snapshots: list[dict[str, object]] | list[dict[str, str]] | None = None,
    ) -> dict[str, object]:
        primary_race = dict(race_configs[0] if race_configs else {})
        race_name = str(primary_race.get("race_name") or "JRAレース")
        prediction_context = {
            "odds_captured_at_latest": _latest_snapshot_timestamp(list(odds_snapshots or [])),
        }
        return build_note_article(
            race_name,
            ev_rows,
            ticket_plan,
            review=review,
            quality_report=quality_report,
            race_config=primary_race,
            prediction_context=prediction_context,
        )


def _latest_snapshot_timestamp(
    rows: list[dict[str, object]] | list[dict[str, str]],
) -> str:
    timestamps = [
        str(row.get("captured_at", "")).strip()
        for row in rows
        if str(row.get("captured_at", "")).strip()
    ]
    return max(timestamps) if timestamps else ""
