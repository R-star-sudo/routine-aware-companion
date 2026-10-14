"""Build the existing causal model features from telemetry and explicit routine context."""
from __future__ import annotations

import math
from datetime import datetime, time, timedelta, timezone, tzinfo
from statistics import fmean
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.model_evaluation import FEATURE_ALLOWLIST
from app.schemas.telemetry import TelemetryRecord

MAX_STALENESS = timedelta(minutes=10)
MAX_HISTORY_GAP = timedelta(minutes=10)
RECENT_WINDOW = timedelta(minutes=30)


def _motion(record: TelemetryRecord) -> float | None:
    if not record.sensor_validity.mpu6050:
        return None
    values = (record.acceleration_x_m_s2, record.acceleration_y_m_s2, record.acceleration_z_m_s2)
    if any(value is None for value in values):
        return None
    ax, ay, az = values
    return math.sqrt(ax * ax + ay * ay + (az - 9.81) ** 2)


def prepare_features(
    observations: list[TelemetryRecord],
    *,
    timezone_name: str | None,
    expected_wake_time: str | None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Return feature values in training order and explicit unavailability reasons.

    No future labels are read or derived. Incomplete inputs are returned as
    unavailable so callers can suppress inference instead of relying on imputers.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    current = current.astimezone(timezone.utc)
    features: dict[str, float | None] = {name: None for name in FEATURE_ALLOWLIST}
    unavailable: dict[str, str] = {}
    if not observations:
        return {"features": features, "unavailable": {name: "no_observations" for name in features}, "observed_at": None}

    ordered = sorted(observations, key=lambda item: item.timestamp)
    device_ids = {record.device_id for record in ordered}
    sources = {record.source for record in ordered}
    if len(device_ids) != 1 or len(sources) != 1:
        return {"features": features, "unavailable": {name: "mixed_device_or_source_history" for name in features}, "observed_at": None}
    for earlier, later in zip(ordered, ordered[1:]):
        if later.timestamp <= earlier.timestamp:
            return {"features": features, "unavailable": {name: "non_increasing_timestamps" for name in features}, "observed_at": None}

    latest = ordered[-1]
    timestamp = latest.timestamp.astimezone(timezone.utc)
    observed_at = timestamp.isoformat().replace("+00:00", "Z")
    if timestamp > current:
        return {"features": features, "unavailable": {name: "future_observation" for name in features}, "observed_at": observed_at}
    if current - timestamp > MAX_STALENESS:
        return {"features": features, "unavailable": {name: "stale_latest_observation" for name in features}, "observed_at": observed_at}

    zone: tzinfo | None = None
    wake: time | None = None
    if timezone_name:
        try:
            zone = _parse_timezone(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            unavailable["feature_expected_wake_hour_local"] = "unknown_schedule_timezone"
            unavailable["feature_minutes_from_expected_wake"] = "unknown_schedule_timezone"
    else:
        unavailable["feature_expected_wake_hour_local"] = "routine_timezone_missing"
        unavailable["feature_minutes_from_expected_wake"] = "routine_timezone_missing"
    if expected_wake_time:
        try:
            hour, minute = expected_wake_time.split(":", 1)
            wake = time(int(hour), int(minute))
            if len(hour) != 2 or len(minute) != 2:
                raise ValueError
        except ValueError:
            wake = None
    if wake is None:
        unavailable["feature_expected_wake_hour_local"] = "routine_wake_time_missing_or_invalid"
        unavailable["feature_minutes_from_expected_wake"] = "routine_wake_time_missing_or_invalid"
    elif zone is not None:
        local = timestamp.astimezone(zone)
        expected = datetime.combine(local.date(), wake, tzinfo=zone)
        features["feature_expected_wake_hour_local"] = wake.hour + wake.minute / 60
        features["feature_minutes_from_expected_wake"] = (timestamp - expected.astimezone(timezone.utc)).total_seconds() / 60

    if zone is not None:
        local = timestamp.astimezone(zone)
        hour_fraction = local.hour + local.minute / 60 + local.second / 3600
        features["feature_hour_sin"] = math.sin(2 * math.pi * hour_fraction / 24)
        features["feature_hour_cos"] = math.cos(2 * math.pi * hour_fraction / 24)
    else:
        unavailable["feature_hour_sin"] = "routine_timezone_missing_or_invalid"
        unavailable["feature_hour_cos"] = "routine_timezone_missing_or_invalid"

    current_motion = _motion(latest)
    if current_motion is None:
        unavailable["feature_motion_intensity_m_s2"] = "current_motion_sensor_invalid_or_missing"
    else:
        features["feature_motion_intensity_m_s2"] = current_motion

    recent = [record for record in ordered if timestamp - RECENT_WINDOW <= record.timestamp <= timestamp]
    covered_from = timestamp - RECENT_WINDOW
    coverage_ok = bool(recent and recent[0].timestamp <= covered_from + MAX_HISTORY_GAP)
    coverage_ok = coverage_ok and all(
        later.timestamp - earlier.timestamp <= MAX_HISTORY_GAP
        for earlier, later in zip(recent, recent[1:])
    )
    if not coverage_ok:
        unavailable["feature_recent_motion_mean_30m_m_s2"] = "insufficient_or_gapped_30m_history"
    else:
        recent_motion = [value for record in recent if (value := _motion(record)) is not None]
        if recent_motion:
            features["feature_recent_motion_mean_30m_m_s2"] = fmean(recent_motion)
        else:
            unavailable["feature_recent_motion_mean_30m_m_s2"] = "no_valid_motion_in_30m_history"

    opened_index = next((index for index in range(len(ordered) - 1, -1, -1) if ordered[index].pillbox_open is True), None)
    opened = ordered[opened_index] if opened_index is not None else None
    if opened is None:
        unavailable["feature_minutes_since_pillbox_open"] = "no_observed_open_in_supplied_history"
    else:
        since_open = ordered[opened_index:]
        if any(record.pillbox_open is None for record in since_open):
            unavailable["feature_minutes_since_pillbox_open"] = "missing_pillbox_observation_since_last_open"
        elif any(record.timestamp - previous.timestamp > MAX_HISTORY_GAP for previous, record in zip(since_open, since_open[1:])):
            unavailable["feature_minutes_since_pillbox_open"] = "gapped_history_since_last_open"
        else:
            features["feature_minutes_since_pillbox_open"] = (timestamp - opened.timestamp).total_seconds() / 60

    return {"features": features, "unavailable": unavailable, "observed_at": observed_at}


def _parse_timezone(name: str) -> tzinfo:
    if name.upper() in {"UTC", "Z", "+00:00", "-00:00"}:
        return timezone.utc
    if len(name) == 6 and name[0] in "+-" and name[3] == ":":
        hours, minutes = int(name[1:3]), int(name[4:6])
        if hours > 14 or minutes > 59 or (hours == 14 and minutes != 0):
            raise ValueError("fixed UTC offset is outside supported range")
        delta = timedelta(hours=hours, minutes=minutes)
        if name[0] == "-":
            delta = -delta
        return timezone(delta)
    return ZoneInfo(name)
