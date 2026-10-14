"""Conservative per-person summaries from explicitly observed routine records.

No sleep or wake event is inferred from raw movement. Sleep duration exists only
when both independently observed timestamps are supplied for that person-day.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
import re
from statistics import median
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any


def _aware(value: str | datetime, name: str) -> datetime:
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be ISO-8601") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return result.astimezone(timezone.utc)


def summarize_personal_baseline(days: list[dict[str, Any]], *, timezone_name: str,
                                at: datetime | None = None, minimum_days: int = 3) -> dict[str, Any]:
    """Summarize prior observations; unsupported components are returned as null."""
    zone: tzinfo
    if timezone_name.upper() in {"UTC", "Z", "+00:00", "-00:00"}:
        zone = timezone.utc
    elif re.fullmatch(r"[+-]\d{2}:\d{2}", timezone_name):
        sign = -1 if timezone_name[0] == "-" else 1
        hours, minutes = int(timezone_name[1:3]), int(timezone_name[4:6])
        if hours > 14 or minutes > 59 or (hours == 14 and minutes):
            raise ValueError("timezone offset must be within ±14:00")
        zone = timezone(sign * timedelta(hours=hours, minutes=minutes))
    else:
        try:
            zone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone_name must be UTC, a fixed offset, or a valid IANA timezone") from exc
    current = _aware(at or datetime.now(timezone.utc), "at")
    by_date: dict[str, tuple[datetime, datetime | None, dict[int, float]]] = {}
    for index, entry in enumerate(days):
        date = str(entry.get("local_date", ""))
        if not date or date in by_date:
            raise ValueError("each local_date must be present and unique")
        wake = _aware(entry["wake_at"], f"days[{index}].wake_at") if entry.get("wake_at") else None
        onset = _aware(entry["sleep_onset_at"], f"days[{index}].sleep_onset_at") if entry.get("sleep_onset_at") else None
        if wake and wake.astimezone(zone).date().isoformat() != date:
            raise ValueError("wake_at must belong to local_date in timezone_name")
        activity: dict[int, float] = {}
        for hour, value in (entry.get("activity_by_local_hour") or {}).items():
            parsed_hour, parsed_value = int(hour), float(value)
            if not 0 <= parsed_hour <= 23 or not 0 <= parsed_value < float("inf"):
                raise ValueError("activity values must be finite, nonnegative local-hour summaries")
            activity[parsed_hour] = parsed_value
        by_date[date] = (wake, onset, activity)
    observed = [(date, values) for date, values in by_date.items()
                if values[0] and values[0] < current]
    observed.sort(key=lambda item: item[0])
    eligible = observed[-minimum_days:]
    wake_minutes = [values[0].astimezone(zone).hour * 60 + values[0].astimezone(zone).minute
                    for _, values in eligible if values[0]]
    # Circular median is meaningful only when the observed wakes fit within one half-day arc.
    wake_baseline = None
    if len(wake_minutes) >= minimum_days:
        anchor = wake_minutes[0]
        unwrapped = [anchor + ((minute - anchor + 720) % 1440) - 720 for minute in wake_minutes]
        if max(unwrapped) - min(unwrapped) <= 720:
            wake_baseline = int(round(median(unwrapped) % 1440))

    durations = []
    for _, (wake, onset, _) in eligible:
        if wake is not None and onset is not None and onset < wake:
            hours = (wake - onset).total_seconds() / 3600
            if 0 < hours <= 24:
                durations.append(hours)
    activity_values: dict[int, list[float]] = {hour: [] for hour in range(24)}
    for _, (_, _, activity) in eligible:
        for hour, value in activity.items():
            activity_values[hour].append(value)
    activity_baseline = {str(hour): median(values) for hour, values in activity_values.items()
                         if len(values) >= minimum_days}
    latest_wake = observed[-1][1][0] if observed else None
    wake_deviation = None
    latest_wake_age_days = None
    if latest_wake:
        latest_wake_age_days = max(0, (current.astimezone(zone).date() - latest_wake.astimezone(zone).date()).days)
    if latest_wake and wake_baseline is not None and latest_wake_age_days is not None and latest_wake_age_days <= 3:
        minute = latest_wake.astimezone(zone).hour * 60 + latest_wake.astimezone(zone).minute
        wake_deviation = ((minute - wake_baseline + 720) % 1440) - 720
    return {
        "timezone": timezone_name,
        "supporting_days": len(eligible),
        "minimum_days": minimum_days,
        "wake_time_baseline_local": (f"{wake_baseline // 60:02d}:{wake_baseline % 60:02d}" if wake_baseline is not None else None),
        "latest_wake_deviation_minutes": wake_deviation,
        "latest_wake_age_days": latest_wake_age_days,
        "sleep_duration_baseline_hours": median(durations) if len(durations) >= minimum_days else None,
        "activity_by_local_hour_baseline": activity_baseline,
        "status": "supported" if len(eligible) >= minimum_days else "insufficient_observations",
    }
