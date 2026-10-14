"""Generate reproducible synthetic routine sequences and future-event labels."""

import argparse
import csv
import math
import random
import sys
from bisect import bisect_right
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import fmean
from typing import Iterator, Sequence

from app.schemas.telemetry import SensorValidity, TelemetryRecord

DAY_SECONDS = 24 * 60 * 60
WAKE_HORIZON = timedelta(minutes=60)
ACCESS_HORIZON = timedelta(minutes=30)
ROOM_HORIZON = timedelta(minutes=30)
MAX_LABEL_HORIZON = WAKE_HORIZON
WAKE_EARLY_THRESHOLD_MINUTES = 30
PILLBOX_DELAY_TOLERANCE_MINUTES = 15
ROOM_TREND_PERIOD_MINUTES = 240
OUTPUT_DEFAULT = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "synthetic_datasets"
    / "routine_dataset.csv"
)

IDENTITY_COLUMNS = (
    "profile_id",
    "sequence_id",
    "day_index",
    "schema_version",
    "source",
    "device_id",
    "sequence",
    "timestamp_utc",
)
MEASUREMENT_COLUMNS = (
    "acceleration_x_m_s2",
    "acceleration_y_m_s2",
    "acceleration_z_m_s2",
    "angular_velocity_x_rad_s",
    "angular_velocity_y_rad_s",
    "angular_velocity_z_rad_s",
    "pulse_rate_bpm",
    "skin_temperature_c",
    "room_temperature_c",
    "room_humidity_percent",
    "pillbox_open",
    "sensor_validity_mpu6050",
    "sensor_validity_max30102",
    "sensor_validity_ds18b20",
)
FEATURE_COLUMNS = (
    "feature_hour_sin",
    "feature_hour_cos",
    "feature_expected_wake_hour_local",
    "feature_minutes_from_expected_wake",
    "feature_motion_intensity_m_s2",
    "feature_recent_motion_mean_30m_m_s2",
    "feature_minutes_since_pillbox_open",
)
LABEL_COLUMNS = (
    "label_unusual_wake_within_60m",
    "label_delayed_medication_access_within_30m",
    "label_room_temp_out_of_range_within_30m",
)
METADATA_COLUMNS = (
    "metadata_scenario_early_wake",
    "metadata_scenario_delayed_access",
    "metadata_scenario_room_drift",
    "metadata_expected_wake_time_utc",
    "metadata_actual_wake_time_utc",
    "metadata_expected_pillbox_time_utc",
    "metadata_actual_pillbox_time_utc",
    "metadata_room_crossing_time_utc",
    "metadata_room_min_c",
    "metadata_room_max_c",
)
CSV_COLUMNS = (
    IDENTITY_COLUMNS + MEASUREMENT_COLUMNS + FEATURE_COLUMNS + LABEL_COLUMNS + METADATA_COLUMNS
)


@dataclass(frozen=True)
class RoutineProfile:
    profile_id: str
    expected_wake_minute: int


@dataclass(frozen=True)
class DayPlan:
    expected_wake_minute: int
    actual_wake_minute: int
    early_wake_scenario: bool
    expected_pillbox_minute: int
    actual_pillbox_minute: int | None
    delayed_access_scenario: bool
    room_drift_scenario: bool
    room_drift_start_minute: int
    room_drift_amplitude_c: float
    room_drift_direction: float
    room_drift_baseline_c: float


PROFILES = (
    RoutineProfile("synthetic-profile-01", 6 * 60 + 30),
    RoutineProfile("synthetic-profile-02", 7 * 60),
    RoutineProfile("synthetic-profile-03", 7 * 60 + 30),
)


def parse_fixed_timezone(value: str) -> timezone:
    if value.upper() in {"UTC", "Z"}:
        return timezone.utc
    if len(value) != 6 or value[0] not in "+-" or value[3] != ":":
        raise argparse.ArgumentTypeError(
            "timezone must be UTC or a fixed offset such as +05:30 or -04:00"
        )
    try:
        hours = int(value[1:3])
        minutes = int(value[4:6])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "timezone must be UTC or a fixed offset such as +05:30 or -04:00"
        ) from exc
    if hours > 14 or minutes > 59 or (hours == 14 and minutes != 0):
        raise argparse.ArgumentTypeError("timezone offset must be within -14:00 to +14:00")
    offset = timedelta(hours=hours, minutes=minutes)
    if value[0] == "-":
        offset = -offset
    return timezone(offset)


def _sample_index(minute_of_day: int, interval_seconds: int) -> int:
    return round(minute_of_day * 60 / interval_seconds)


def _make_day_plan(
    rng: random.Random,
    profile: RoutineProfile,
    scenario_rate: float,
    room_baseline_c: float,
) -> DayPlan:
    early_wake = rng.random() < scenario_rate
    if early_wake:
        actual_wake = profile.expected_wake_minute - rng.randint(45, 90)
    else:
        actual_wake = profile.expected_wake_minute + rng.randint(-20, 20)

    expected_pillbox = profile.expected_wake_minute + 90
    delayed_access = rng.random() < scenario_rate
    if delayed_access:
        actual_pillbox = (
            None
            if rng.random() < 0.25
            else expected_pillbox + rng.randint(45, 120)
        )
    else:
        actual_pillbox = expected_pillbox + rng.randint(-5, 10)

    room_drift = rng.random() < scenario_rate
    return DayPlan(
        expected_wake_minute=profile.expected_wake_minute,
        actual_wake_minute=actual_wake,
        early_wake_scenario=early_wake,
        expected_pillbox_minute=expected_pillbox,
        actual_pillbox_minute=actual_pillbox,
        delayed_access_scenario=delayed_access,
        room_drift_scenario=room_drift,
        room_drift_start_minute=rng.randint(10 * 60, 15 * 60),
        room_drift_amplitude_c=rng.uniform(5.5, 7.0) if room_drift else 0.0,
        room_drift_direction=rng.choice((-1.0, 1.0)),
        room_drift_baseline_c=room_baseline_c,
    )


def _room_temperature(
    minute_of_day: int,
    plan: DayPlan,
    day_index: int,
    room_min_c: float,
    room_max_c: float,
) -> float:
    comfort_midpoint = (room_min_c + room_max_c) / 2
    daily_cycle = 0.55 * math.sin(2 * math.pi * (minute_of_day - 8 * 60) / DAY_SECONDS * 60)
    drift_elapsed = minute_of_day - plan.room_drift_start_minute
    drift = 0.0
    if plan.room_drift_scenario and 0 <= drift_elapsed <= ROOM_TREND_PERIOD_MINUTES:
        progress = drift_elapsed / ROOM_TREND_PERIOD_MINUTES
        triangle = 1 - abs(2 * progress - 1)
        drift = plan.room_drift_direction * plan.room_drift_amplitude_c * triangle
    return (
        comfort_midpoint
        + plan.room_drift_baseline_c
        + 0.25 * math.sin(day_index * 0.7)
        + daily_cycle
        + drift
    )


def _timestamp_for_minute(day_start: datetime, minute: int) -> datetime:
    return day_start + timedelta(minutes=minute)


def _event_within(
    timestamp: datetime, events: Sequence[datetime], horizon: timedelta
) -> bool:
    event_index = bisect_right(events, timestamp)
    return event_index < len(events) and events[event_index] <= timestamp + horizon


def _build_profile_rows(
    profile: RoutineProfile,
    days: int,
    interval_seconds: int,
    scenario_rate: float,
    start_utc: datetime,
    local_timezone: timezone,
    rng: random.Random,
    room_min_c: float,
    room_max_c: float,
) -> Iterator[dict[str, object]]:
    samples_per_day = DAY_SECONDS // interval_seconds
    room_baseline = 0.0
    plans: list[DayPlan] = []
    timestamps_by_day: list[list[datetime]] = []
    room_values_by_day: list[list[float]] = []

    for day_index in range(days):
        day_start = start_utc + timedelta(days=day_index)
        room_baseline = max(-1.0, min(1.0, room_baseline + rng.gauss(0, 0.12)))
        plan = _make_day_plan(rng, profile, scenario_rate, room_baseline)
        plans.append(plan)
        day_timestamps = [
            day_start + timedelta(seconds=sample_index * interval_seconds)
            for sample_index in range(samples_per_day)
        ]
        timestamps_by_day.append(day_timestamps)
        room_values_by_day.append(
            [
                _room_temperature(
                    sample_index * interval_seconds // 60,
                    plan,
                    day_index,
                    room_min_c,
                    room_max_c,
                )
                for sample_index in range(samples_per_day)
            ]
        )

    actual_wake_events: list[datetime] = []
    unusual_wake_events: list[datetime] = []
    expected_pillbox_events: list[datetime] = []
    delayed_pillbox_due_events: list[datetime] = []
    actual_pillbox_events: list[datetime] = []
    room_crossing_events: list[datetime] = []

    for day_index, plan in enumerate(plans):
        day_start = timestamps_by_day[day_index][0]
        actual_wake_index = _sample_index(plan.actual_wake_minute, interval_seconds)
        actual_wake = day_start + timedelta(seconds=actual_wake_index * interval_seconds)
        actual_wake_events.append(actual_wake)
        if (
            plan.actual_wake_minute
            < plan.expected_wake_minute - WAKE_EARLY_THRESHOLD_MINUTES
        ):
            unusual_wake_events.append(actual_wake)

        expected_pillbox_index = _sample_index(
            plan.expected_pillbox_minute, interval_seconds
        )
        expected_pillbox = (
            day_start + timedelta(seconds=expected_pillbox_index * interval_seconds)
        )
        expected_pillbox_events.append(expected_pillbox)
        if (
            plan.actual_pillbox_minute is None
            or plan.actual_pillbox_minute
            > plan.expected_pillbox_minute + PILLBOX_DELAY_TOLERANCE_MINUTES
        ):
            delayed_pillbox_due_events.append(expected_pillbox)
        if plan.actual_pillbox_minute is not None:
            actual_pillbox_index = _sample_index(
                plan.actual_pillbox_minute, interval_seconds
            )
            actual_pillbox_events.append(
                day_start + timedelta(seconds=actual_pillbox_index * interval_seconds)
            )

    prior_room_temperature: float | None = None
    for day_index, values in enumerate(room_values_by_day):
        for sample_index, temperature in enumerate(values):
            if room_min_c <= temperature <= room_max_c:
                prior_room_temperature = temperature
                continue
            was_in_range = (
                prior_room_temperature is not None
                and room_min_c <= prior_room_temperature <= room_max_c
            )
            if was_in_range:
                room_crossing_events.append(timestamps_by_day[day_index][sample_index])
            prior_room_temperature = temperature

    sequence = 0
    recent_motion: deque[tuple[datetime, float]] = deque()
    last_pillbox_open: datetime | None = None
    for day_index, plan in enumerate(plans):
        day_start = timestamps_by_day[day_index][0]
        expected_wake = day_start + timedelta(minutes=plan.expected_wake_minute)
        actual_wake = actual_wake_events[day_index]
        expected_pillbox = expected_pillbox_events[day_index]
        actual_pillbox_today: datetime | None = None
        if plan.actual_pillbox_minute is not None:
            actual_pillbox_today = day_start + timedelta(
                seconds=_sample_index(plan.actual_pillbox_minute, interval_seconds)
                * interval_seconds
            )

        for sample_index, timestamp in enumerate(timestamps_by_day[day_index]):
            minute_of_day = sample_index * interval_seconds // 60
            awake = timestamp >= actual_wake and minute_of_day < 22 * 60
            sleep = not awake
            mpu_valid = sequence % 97 != 96
            max_valid = sequence % 113 != 112
            ds_valid = sequence % 131 != 130

            movement_factor = (
                0.05
                if sleep
                else 0.25
                + 0.35
                * abs(math.sin(2 * math.pi * (minute_of_day - 8 * 60) / DAY_SECONDS * 60))
            )
            ax_value = rng.gauss(0, movement_factor) if mpu_valid else None
            ay_value = rng.gauss(0, movement_factor) if mpu_valid else None
            az_value = rng.gauss(9.81, movement_factor * 0.8) if mpu_valid else None
            gx_value = rng.gauss(0, movement_factor * 0.12) if mpu_valid else None
            gy_value = rng.gauss(0, movement_factor * 0.12) if mpu_valid else None
            gz_value = rng.gauss(0, movement_factor * 0.12) if mpu_valid else None

            motion_intensity = (
                math.sqrt(
                    ax_value**2 + ay_value**2 + (az_value - 9.81) ** 2
                )
                if mpu_valid
                else None
            )
            while recent_motion and timestamp - recent_motion[0][0] > timedelta(minutes=30):
                recent_motion.popleft()
            if motion_intensity is not None:
                recent_motion.append((timestamp, motion_intensity))
            recent_motion_mean = (
                fmean(value for _, value in recent_motion) if recent_motion else None
            )

            pillbox_open = timestamp == actual_pillbox_today
            if pillbox_open:
                last_pillbox_open = timestamp
            elapsed_since_pillbox = (
                (timestamp - last_pillbox_open).total_seconds() / 60
                if last_pillbox_open is not None
                else None
            )
            local_timestamp = timestamp.astimezone(local_timezone)
            hour = (
                local_timestamp.hour
                + local_timestamp.minute / 60
                + local_timestamp.second / 3600
            )
            wake_deviation = (
                (actual_wake - expected_wake).total_seconds() / 60
                if timestamp >= actual_wake
                else None
            )
            pulse_rate = (
                round(
                    max(
                        48,
                        min(
                            115,
                            rng.gauss(62 if sleep else 74 + movement_factor * 10, 5),
                        ),
                    ),
                    1,
                )
                if max_valid and sequence % 17 != 0
                else None
            )
            skin_temperature = (
                round(rng.gauss(33.5 if awake else 33.0, 0.55), 2)
                if ds_valid
                else None
            )
            room_temperature = round(room_values_by_day[day_index][sample_index], 2)
            room_humidity = round(
                max(25, min(70, rng.gauss(48 - (room_temperature - 22) * 0.5, 3))),
                1,
            )

            telemetry = TelemetryRecord(
                schema_version="1.0",
                source="simulated",
                device_id=profile.profile_id,
                sequence=sequence,
                timestamp=timestamp,
                acceleration_x_m_s2=round(ax_value, 4) if ax_value is not None else None,
                acceleration_y_m_s2=round(ay_value, 4) if ay_value is not None else None,
                acceleration_z_m_s2=round(az_value, 4) if az_value is not None else None,
                angular_velocity_x_rad_s=round(gx_value, 5) if gx_value is not None else None,
                angular_velocity_y_rad_s=round(gy_value, 5) if gy_value is not None else None,
                angular_velocity_z_rad_s=round(gz_value, 5) if gz_value is not None else None,
                pulse_rate_bpm=pulse_rate,
                skin_temperature_c=skin_temperature,
                room_temperature_c=room_temperature,
                room_humidity_percent=room_humidity,
                pillbox_open=pillbox_open,
                sensor_validity=SensorValidity(
                    mpu6050=mpu_valid, max30102=max_valid, ds18b20=ds_valid
                ),
            )
            telemetry_values = telemetry.model_dump(mode="json")
            telemetry_values.pop("sensor_validity")
            telemetry_values.pop("timestamp")

            labels_available = (
                timestamp + MAX_LABEL_HORIZON <= timestamps_by_day[day_index][-1]
            )
            if labels_available:
                wake_label: int | None = int(
                    _event_within(timestamp, unusual_wake_events, WAKE_HORIZON)
                )
                pillbox_label: int | None = int(
                    _event_within(
                        timestamp, delayed_pillbox_due_events, ACCESS_HORIZON
                    )
                )
                room_label: int | None = int(
                    _event_within(timestamp, room_crossing_events, ROOM_HORIZON)
                )
            else:
                wake_label = pillbox_label = room_label = None

            actual_pillbox_metadata = (
                actual_pillbox_today.isoformat().replace("+00:00", "Z")
                if actual_pillbox_today is not None
                else ""
            )
            room_crossing = next(
                (
                    event
                    for event in room_crossing_events
                    if day_start <= event < day_start + timedelta(days=1)
                ),
                None,
            )
            row: dict[str, object] = {
                "profile_id": profile.profile_id,
                "sequence_id": f"{profile.profile_id}-day-{day_index + 1:03d}",
                "day_index": day_index + 1,
                **telemetry_values,
                "timestamp_utc": timestamp.isoformat().replace("+00:00", "Z"),
                "sensor_validity_mpu6050": mpu_valid,
                "sensor_validity_max30102": max_valid,
                "sensor_validity_ds18b20": ds_valid,
                "feature_hour_sin": round(math.sin(2 * math.pi * hour / 24), 6),
                "feature_hour_cos": round(math.cos(2 * math.pi * hour / 24), 6),
                "feature_expected_wake_hour_local": round(
                    plan.expected_wake_minute / 60, 2
                ),
                "feature_minutes_from_expected_wake": round(
                    (local_timestamp.hour * 60 + local_timestamp.minute)
                    - plan.expected_wake_minute,
                    2,
                ),
                "feature_motion_intensity_m_s2": (
                    round(motion_intensity, 4)
                    if motion_intensity is not None
                    else None
                ),
                "feature_recent_motion_mean_30m_m_s2": (
                    round(recent_motion_mean, 4)
                    if recent_motion_mean is not None
                    else None
                ),
                "feature_minutes_since_pillbox_open": (
                    round(elapsed_since_pillbox, 2)
                    if elapsed_since_pillbox is not None
                    else None
                ),
                "label_unusual_wake_within_60m": wake_label,
                "label_delayed_medication_access_within_30m": pillbox_label,
                "label_room_temp_out_of_range_within_30m": room_label,
                "metadata_scenario_early_wake": plan.early_wake_scenario,
                "metadata_scenario_delayed_access": plan.delayed_access_scenario,
                "metadata_scenario_room_drift": plan.room_drift_scenario,
                "metadata_expected_wake_time_utc": expected_wake.isoformat().replace(
                    "+00:00", "Z"
                ),
                "metadata_actual_wake_time_utc": actual_wake.isoformat().replace(
                    "+00:00", "Z"
                ),
                "metadata_expected_pillbox_time_utc": expected_pillbox.isoformat().replace(
                    "+00:00", "Z"
                ),
                "metadata_actual_pillbox_time_utc": actual_pillbox_metadata,
                "metadata_room_crossing_time_utc": (
                    room_crossing.isoformat().replace("+00:00", "Z")
                    if room_crossing is not None
                    else ""
                ),
                "metadata_room_min_c": room_min_c,
                "metadata_room_max_c": room_max_c,
            }
            yield row
            sequence += 1


def generate_dataset(
    days: int,
    interval_seconds: int,
    *,
    seed: int,
    scenario_rate: float = 0.15,
    start_date: date = date(2026, 1, 1),
    local_timezone: timezone = timezone.utc,
    room_min_c: float = 18.0,
    room_max_c: float = 26.0,
) -> Iterator[dict[str, object]]:
    if days < 1:
        raise ValueError("days must be at least 1")
    if interval_seconds < 1 or interval_seconds > 60 * 60:
        raise ValueError("interval_seconds must be between 1 and 3600")
    if DAY_SECONDS % interval_seconds != 0:
        raise ValueError("sampling interval must divide evenly into 24 hours")
    if not 0 <= scenario_rate <= 1:
        raise ValueError("scenario_rate must be between 0 and 1")
    if not -50 <= room_min_c < room_max_c <= 100:
        raise ValueError("room comfort bounds must satisfy -50 <= min < max <= 100")

    start_local = datetime.combine(start_date, time.min, tzinfo=local_timezone)
    start_utc = start_local.astimezone(timezone.utc)
    rng = random.Random(seed)
    for profile in PROFILES:
        yield from _build_profile_rows(
            profile,
            days,
            interval_seconds,
            scenario_rate,
            start_utc,
            local_timezone,
            rng,
            room_min_c,
            room_max_c,
        )


def _positive_integer(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a whole number") from exc
    if result < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return result


def _scenario_rate(value: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number from 0 to 1") from exc
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return result


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate reproducible simulated routine histories with future-event labels."
    )
    parser.add_argument("--days", type=_positive_integer, default=14)
    interval_group = parser.add_mutually_exclusive_group()
    interval_group.add_argument(
        "--interval-minutes",
        type=_positive_integer,
        default=None,
        help="Sampling interval in whole minutes (default: 5)",
    )
    interval_group.add_argument(
        "--interval-seconds",
        type=_positive_integer,
        help="Sampling interval in whole seconds",
    )
    parser.add_argument("--seed", type=int, default=20261014)
    parser.add_argument("--scenario-rate", type=_scenario_rate, default=0.15)
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2026, 1, 1))
    parser.add_argument("--timezone", type=parse_fixed_timezone, default=timezone.utc)
    parser.add_argument("--room-min-c", type=float, default=18.0)
    parser.add_argument("--room-max-c", type=float, default=26.0)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    if args.interval_seconds is not None:
        interval_seconds = args.interval_seconds
    else:
        interval_minutes = args.interval_minutes or 5
        interval_seconds = interval_minutes * 60
    if interval_seconds > 60 * 60:
        parser.error("sampling interval must not exceed 60 minutes")
    if DAY_SECONDS % interval_seconds != 0:
        parser.error("sampling interval must divide evenly into 24 hours")
    if not -50 <= args.room_min_c < args.room_max_c <= 100:
        parser.error("room bounds must satisfy -50 <= --room-min-c < --room-max-c <= 100")

    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        label_counts = {label: {0: 0, 1: 0, "unavailable": 0} for label in LABEL_COLUMNS}
        row_count = 0
        with args.output.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            for row in generate_dataset(
                args.days,
                interval_seconds,
                seed=args.seed,
                scenario_rate=args.scenario_rate,
                start_date=args.start_date,
                local_timezone=args.timezone,
                room_min_c=args.room_min_c,
                room_max_c=args.room_max_c,
            ):
                writer.writerow(
                    {
                        key: str(value).lower() if isinstance(value, bool) else value
                        for key, value in row.items()
                    }
                )
                row_count += 1
                for label in LABEL_COLUMNS:
                    value = row[label]
                    if value is None:
                        label_counts[label]["unavailable"] += 1
                    else:
                        label_counts[label][int(value)] += 1
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    print(f"Wrote {row_count} rows to {args.output}", file=sys.stderr)
    for label, counts in label_counts.items():
        print(
            f"{label}: positive={counts[1]}, negative={counts[0]}, "
            f"horizon_unavailable={counts['unavailable']}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
