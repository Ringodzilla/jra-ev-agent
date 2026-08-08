from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class DeadlineSettings:
    cutoff_minutes_before_post: int = 5
    normal_seconds_before_cutoff: int = 600
    emergency_seconds_before_cutoff: int = 120
    emit_reserve_seconds: int = 10
    minimum_live_refresh_seconds: int = 40
    odds_max_age_seconds: int = 180
    conditions_max_age_seconds: int = 300
    max_abs_body_weight_change_kg: int = 20


@dataclass(frozen=True)
class DeadlinePlan:
    post_time: datetime
    output_deadline: datetime
    evaluated_at: datetime
    seconds_until_output_deadline: float
    execution_mode: str
    may_start_network_refresh: bool

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        for key in ("post_time", "output_deadline", "evaluated_at"):
            payload[key] = getattr(self, key).isoformat()
        payload["seconds_until_output_deadline"] = round(self.seconds_until_output_deadline, 3)
        return payload


def race_post_datetime(
    race_config: dict[str, object],
    *,
    timezone: ZoneInfo = JST,
) -> datetime:
    race_date = str(race_config.get("race_date", "")).strip()
    post_time = str(race_config.get("post_time", "")).strip()
    if not race_date or not post_time:
        raise ValueError("final prediction requires race_date and post_time")
    try:
        parsed = datetime.fromisoformat(f"{race_date}T{post_time}")
    except ValueError as exc:
        raise ValueError("race_date/post_time must be YYYY-MM-DD and HH:MM") from exc
    return parsed.replace(tzinfo=timezone)


def build_deadline_plan(
    race_config: dict[str, object],
    *,
    now: datetime | None = None,
    settings: DeadlineSettings | None = None,
) -> DeadlinePlan:
    settings = settings or DeadlineSettings()
    post_time = race_post_datetime(race_config)
    evaluated_at = _aware_jst(now or datetime.now(tz=JST))
    output_deadline = post_time - timedelta(minutes=settings.cutoff_minutes_before_post)
    remaining = (output_deadline - evaluated_at).total_seconds()

    if remaining <= 0:
        execution_mode = "too_late"
        may_start = False
    elif remaining < settings.emergency_seconds_before_cutoff:
        execution_mode = "emergency"
        may_start = remaining >= (
            settings.emit_reserve_seconds + settings.minimum_live_refresh_seconds
        )
    elif remaining < settings.normal_seconds_before_cutoff:
        execution_mode = "fast"
        may_start = True
    else:
        execution_mode = "normal"
        may_start = True

    return DeadlinePlan(
        post_time=post_time,
        output_deadline=output_deadline,
        evaluated_at=evaluated_at,
        seconds_until_output_deadline=remaining,
        execution_mode=execution_mode,
        may_start_network_refresh=may_start,
    )


def _aware_jst(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=JST)
    return value.astimezone(JST)
