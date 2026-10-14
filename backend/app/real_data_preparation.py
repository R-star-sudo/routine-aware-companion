"""Prepare validated hardware telemetry with causal features and observed labels."""

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from statistics import fmean
from typing import Any, Sequence

from pydantic import ValidationError

from app.dataset_generator import (
    ACCESS_HORIZON,
    LABEL_COLUMNS,
    PILLBOX_DELAY_TOLERANCE_MINUTES,
    WAKE_EARLY_THRESHOLD_MINUTES,
    WAKE_HORIZON,
)
from app.schemas.telemetry import TelemetryRecord

PREPARATION_VERSION = "1.0"
PREPARATION_TOOL = "app.real_data_preparation"
REAL_FEATURE_ALLOWLIST = (
    "feature_hour_sin",
    "feature_hour_cos",
    "feature_expected_wake_hour_local",
    "feature_minutes_from_expected_wake",
    "feature_motion_intensity_m_s2",
    "feature_recent_motion_mean_30m_m_s2",
    "feature_minutes_since_pillbox_open",
    "feature_room_temperature_c",
)
SCHEDULE_COLUMNS = (
    "profile_id",
    "device_id",
    "local_date",
    "timezone",
    "expected_wake_at",
    "scheduled_pillbox_at",
    "room_min_c",
    "room_max_c",
)
ANNOTATION_COLUMNS = (
    "profile_id",
    "event_type",
    "event_timestamp",
    "evidence",
)
QUALITY_COLUMNS = (
    "quality_mpu6050_valid",
    "quality_max30102_valid",
    "quality_ds18b20_valid",
    "quality_room_temperature_valid",
    "quality_pillbox_observed",
    "quality_observation_complete_30m",
    "quality_observation_complete_60m",
    "quality_room_temperature_complete_30m",
    "quality_pillbox_complete_to_grace",
)
PROVENANCE_COLUMNS = (
    "preparation_version",
    "preparation_tool",
    "preparation_raw_sha256",
    "preparation_schedule_sha256",
    "preparation_annotation_sha256",
)
TELEMETRY_COLUMNS = tuple(TelemetryRecord.model_fields)
OUTPUT_COLUMNS = (
    "profile_id",
    "sequence_id",
    "local_date",
    *TELEMETRY_COLUMNS,
    "timestamp_utc",
    *REAL_FEATURE_ALLOWLIST,
    *LABEL_COLUMNS,
    *QUALITY_COLUMNS,
    *PROVENANCE_COLUMNS,
)
ROOM_HORIZON = timedelta(minutes=30)
RECENT_MOTION_WINDOW = timedelta(minutes=30)
DEFAULT_MAX_GAP_SECONDS = 600


class RealDataPreparationError(ValueError):
    """Raised when hardware observations or their context are invalid."""


@dataclass(frozen=True)
class Schedule:
    profile_id: str
    device_id: str
    local_date: date
    timezone_name: str
    timezone: tzinfo
    expected_wake_at: datetime | None
    scheduled_pillbox_at: datetime | None
    room_min_c: float | None
    room_max_c: float | None


@dataclass(frozen=True)
class Observation:
    telemetry: TelemetryRecord
    profile_id: str
    local_date: date
    sequence_id: str

    @property
    def timestamp(self) -> datetime:
        return self.telemetry.timestamp


def _read_csv(
    path: Path,
    required_columns: Sequence[str],
    *,
    allow_empty: bool = False,
) -> list[dict[str, str]]:
    try:
        source = path.open("r", newline="", encoding="utf-8-sig")
    except OSError as exc:
        raise RealDataPreparationError(f"cannot read {path}: {exc}") from exc
    with source:
        reader = csv.DictReader(source)
        columns = reader.fieldnames or []
        if len(columns) != len(set(columns)):
            raise RealDataPreparationError(f"{path}: duplicate CSV column names")
        missing = [column for column in required_columns if column not in columns]
        if missing:
            raise RealDataPreparationError(
                f"{path}: missing required columns: {', '.join(missing)}"
            )
        rows = list(reader)
    for row_number, row in enumerate(rows, start=2):
        if None in row or any(value is None for value in row.values()):
            raise RealDataPreparationError(
                f"{path}: row {row_number}: field count does not match the CSV header"
            )
    if not rows and not allow_empty:
        raise RealDataPreparationError(f"{path}: CSV contains no data rows")
    return rows


def _parse_aware_datetime(value: str, description: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise RealDataPreparationError(
            f"{description} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RealDataPreparationError(f"{description} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _optional_float(value: str, description: str) -> float | None:
    if value.strip() == "":
        return None
    try:
        result = float(value)
    except ValueError as exc:
        raise RealDataPreparationError(f"{description} must be numeric or blank") from exc
    if not math.isfinite(result):
        raise RealDataPreparationError(f"{description} must be finite")
    return result


def _parse_timezone(value: str, description: str) -> tzinfo:
    name = value.strip()
    if name in {"UTC", "Z", "+00:00", "-00:00"}:
        return timezone.utc
    match = re.fullmatch(r"([+-])(\d{2}):(\d{2})", name)
    if match is None:
        raise RealDataPreparationError(
            f"{description} must be UTC or a fixed offset in ±HH:MM form"
        )
    hours, minutes = int(match.group(2)), int(match.group(3))
    if hours > 14 or minutes > 59 or (hours == 14 and minutes != 0):
        raise RealDataPreparationError(
            f"{description} fixed offset must be within ±14:00"
        )
    offset = timedelta(hours=hours, minutes=minutes)
    if match.group(1) == "-":
        offset = -offset
    return timezone(offset, name)


def _load_schedules(path: Path) -> tuple[dict[tuple[str, date], Schedule], dict[str, str]]:
    rows = _read_csv(path, SCHEDULE_COLUMNS)
    schedules: dict[tuple[str, date], Schedule] = {}
    profile_by_device: dict[str, str] = {}
    device_by_profile: dict[str, str] = {}
    timezone_by_profile: dict[str, str] = {}
    for row_number, row in enumerate(rows, start=2):
        profile_id = row["profile_id"].strip()
        device_id = row["device_id"].strip()
        if not profile_id or not device_id:
            raise RealDataPreparationError(
                f"{path}: row {row_number}: profile_id and device_id are required"
            )
        try:
            local_date = date.fromisoformat(row["local_date"].strip())
        except ValueError as exc:
            raise RealDataPreparationError(
                f"{path}: row {row_number}: local_date must be YYYY-MM-DD"
            ) from exc
        timezone_name = row["timezone"].strip()
        zone = _parse_timezone(
            timezone_name, f"{path}: row {row_number}: timezone"
        )

        previous_profile = profile_by_device.setdefault(device_id, profile_id)
        if previous_profile != profile_id:
            raise RealDataPreparationError(
                f"{path}: device_id {device_id!r} maps to multiple profiles"
            )
        previous_device = device_by_profile.setdefault(profile_id, device_id)
        if previous_device != device_id:
            raise RealDataPreparationError(
                f"{path}: profile_id {profile_id!r} maps to multiple devices"
            )
        previous_timezone = timezone_by_profile.setdefault(profile_id, timezone_name)
        if previous_timezone != timezone_name:
            raise RealDataPreparationError(
                f"{path}: profile_id {profile_id!r} uses multiple timezones"
            )

        expected_value = row["expected_wake_at"].strip()
        expected_wake_at = (
            _parse_aware_datetime(
                expected_value, f"{path}: row {row_number}: expected_wake_at"
            )
            if expected_value
            else None
        )
        scheduled_value = row["scheduled_pillbox_at"].strip()
        scheduled_pillbox_at = (
            _parse_aware_datetime(
                scheduled_value, f"{path}: row {row_number}: scheduled_pillbox_at"
            )
            if scheduled_value
            else None
        )
        for name, scheduled_at in (
            ("expected_wake_at", expected_wake_at),
            ("scheduled_pillbox_at", scheduled_pillbox_at),
        ):
            if scheduled_at is not None and scheduled_at.astimezone(zone).date() != local_date:
                raise RealDataPreparationError(
                    f"{path}: row {row_number}: {name} does not fall on local_date"
                )

        room_min_c = _optional_float(row["room_min_c"], "room_min_c")
        room_max_c = _optional_float(row["room_max_c"], "room_max_c")
        if (room_min_c is None) != (room_max_c is None):
            raise RealDataPreparationError(
                f"{path}: row {row_number}: room_min_c and room_max_c must both be set or blank"
            )
        if room_min_c is not None and (
            room_min_c >= room_max_c
            or room_min_c < -50
            or room_max_c > 100
        ):
            raise RealDataPreparationError(
                f"{path}: row {row_number}: room range must satisfy -50 <= min < max <= 100"
            )

        schedule = Schedule(
            profile_id=profile_id,
            device_id=device_id,
            local_date=local_date,
            timezone_name=timezone_name,
            timezone=zone,
            expected_wake_at=expected_wake_at,
            scheduled_pillbox_at=scheduled_pillbox_at,
            room_min_c=room_min_c,
            room_max_c=room_max_c,
        )
        key = (device_id, local_date)
        if key in schedules:
            raise RealDataPreparationError(
                f"{path}: duplicate schedule for device {device_id!r} on {local_date}"
            )
        schedules[key] = schedule
    return schedules, profile_by_device


def _load_annotations(
    path: Path | None,
    schedules: dict[tuple[str, date], Schedule],
) -> dict[tuple[str, date], datetime]:
    if path is None:
        return {}
    rows = _read_csv(path, ANNOTATION_COLUMNS, allow_empty=True)
    profile_timezones: dict[str, tzinfo] = {}
    for schedule in schedules.values():
        profile_timezones[schedule.profile_id] = schedule.timezone

    wake_events: dict[tuple[str, date], datetime] = {}
    for row_number, row in enumerate(rows, start=2):
        profile_id = row["profile_id"].strip()
        if row["event_type"].strip() != "wake":
            raise RealDataPreparationError(
                f"{path}: row {row_number}: event_type must be 'wake'; pillbox access is read from telemetry"
            )
        if not row["evidence"].strip():
            raise RealDataPreparationError(
                f"{path}: row {row_number}: evidence is required for a wake annotation"
            )
        zone = profile_timezones.get(profile_id)
        if zone is None:
            raise RealDataPreparationError(
                f"{path}: row {row_number}: profile_id {profile_id!r} has no schedule"
            )
        event_at = _parse_aware_datetime(
            row["event_timestamp"], f"{path}: row {row_number}: event_timestamp"
        )
        local_date = event_at.astimezone(zone).date()
        if not any(
            schedule.profile_id == profile_id and schedule.local_date == local_date
            for schedule in schedules.values()
        ):
            raise RealDataPreparationError(
                f"{path}: row {row_number}: no schedule for profile {profile_id!r} on {local_date}"
            )
        key = (profile_id, local_date)
        if key in wake_events:
            raise RealDataPreparationError(
                f"{path}: multiple wake annotations for {profile_id!r} on {local_date}"
            )
        wake_events[key] = event_at
    return wake_events


def _parse_raw_telemetry(path: Path) -> list[dict[str, Any]]:
    rows = _read_csv(path, TELEMETRY_COLUMNS)
    parsed: list[dict[str, Any]] = []
    last_timestamp: dict[str, datetime] = {}
    last_sequence: dict[str, int] = {}
    optional_measurements = {
        "pulse_rate_bpm",
        "skin_temperature_c",
        "room_temperature_c",
        "room_humidity_percent",
        "pillbox_open",
    }
    nullable_motion = {
        "acceleration_x_m_s2",
        "acceleration_y_m_s2",
        "acceleration_z_m_s2",
        "angular_velocity_x_rad_s",
        "angular_velocity_y_rad_s",
        "angular_velocity_z_rad_s",
    }
    for row_number, row in enumerate(rows, start=2):
        payload: dict[str, Any] = dict(row)
        try:
            payload["sensor_validity"] = json.loads(row["sensor_validity"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise RealDataPreparationError(
                f"{path}: row {row_number}: sensor_validity must be JSON"
            ) from exc
        for field in optional_measurements | nullable_motion:
            raw_value = payload.get(field)
            if raw_value is None or raw_value.strip() == "":
                payload[field] = None
        if payload.get("pillbox_open") is not None:
            value = str(payload["pillbox_open"]).strip().lower()
            if value in {"true", "1"}:
                payload["pillbox_open"] = True
            elif value in {"false", "0"}:
                payload["pillbox_open"] = False
            else:
                raise RealDataPreparationError(
                    f"{path}: row {row_number}: pillbox_open must be true, false, or blank"
                )
        try:
            telemetry = TelemetryRecord.model_validate(payload)
        except ValidationError as exc:
            raise RealDataPreparationError(
                f"{path}: row {row_number}: invalid telemetry: {exc}"
            ) from exc
        if telemetry.source != "hardware":
            raise RealDataPreparationError(
                f"{path}: row {row_number}: source must be 'hardware'"
            )
        previous_timestamp = last_timestamp.get(telemetry.device_id)
        previous_sequence = last_sequence.get(telemetry.device_id)
        if previous_timestamp is not None and telemetry.timestamp <= previous_timestamp:
            raise RealDataPreparationError(
                f"{path}: row {row_number}: timestamps must strictly increase per device"
            )
        if previous_sequence is not None and telemetry.sequence <= previous_sequence:
            raise RealDataPreparationError(
                f"{path}: row {row_number}: sequence numbers must strictly increase per device"
            )
        last_timestamp[telemetry.device_id] = telemetry.timestamp
        last_sequence[telemetry.device_id] = telemetry.sequence
        parsed.append({"telemetry": telemetry, "row_number": row_number})
    return parsed


def _has_coverage(
    observations: list[Observation],
    index: int,
    deadline: datetime,
    max_gap: timedelta,
    required_field: str | None = None,
) -> bool:
    previous: datetime | None = None
    for cursor in range(index, len(observations)):
        observation = observations[cursor]
        timestamp = observation.timestamp
        if timestamp > deadline and previous is None:
            return False
        if previous is not None and timestamp - previous > max_gap:
            return False
        if required_field is not None and timestamp <= deadline:
            value = getattr(observation.telemetry, required_field)
            if value is None:
                return False
        previous = timestamp
        if timestamp >= deadline:
            return True
    return False


def _motion_intensity(telemetry: TelemetryRecord) -> float | None:
    if not telemetry.sensor_validity.mpu6050:
        return None
    values = (
        telemetry.acceleration_x_m_s2,
        telemetry.acceleration_y_m_s2,
        telemetry.acceleration_z_m_s2,
    )
    if any(value is None for value in values):
        return None
    ax, ay, az = values
    return math.sqrt(ax * ax + ay * ay + (az - 9.81) ** 2)


def _prepare_sequence(
    observations: list[Observation],
    schedules: dict[tuple[str, date], Schedule],
    wake_events: dict[tuple[str, date], datetime],
    *,
    max_gap: timedelta,
    provenance: dict[str, str],
) -> list[dict[str, Any]]:
    first = observations[0]
    schedule = schedules[(first.telemetry.device_id, first.local_date)]
    profile_date_key = (first.profile_id, first.local_date)
    wake_at = wake_events.get(profile_date_key)
    motion_values = [_motion_intensity(item.telemetry) for item in observations]
    output: list[dict[str, Any]] = []
    recent_motion: deque[tuple[datetime, float]] = deque()
    last_pillbox_open: datetime | None = None

    for index, observation in enumerate(observations):
        telemetry = observation.telemetry
        timestamp = observation.timestamp
        local_timestamp = timestamp.astimezone(schedule.timezone)
        motion = motion_values[index]
        while recent_motion and timestamp - recent_motion[0][0] > RECENT_MOTION_WINDOW:
            recent_motion.popleft()
        if motion is not None:
            recent_motion.append((timestamp, motion))
        if telemetry.pillbox_open is True:
            last_pillbox_open = timestamp
        minutes_since_open = (
            (timestamp - last_pillbox_open).total_seconds() / 60
            if last_pillbox_open is not None
            else None
        )

        deadline_30 = timestamp + ACCESS_HORIZON
        deadline_60 = timestamp + WAKE_HORIZON
        complete_30 = _has_coverage(
            observations, index, deadline_30, max_gap
        )
        complete_60 = _has_coverage(
            observations, index, deadline_60, max_gap
        )
        expected_wake = schedule.expected_wake_at
        expected_pillbox = schedule.scheduled_pillbox_at

        wake_label: int | None = None
        if (
            expected_wake is not None
            and wake_at is not None
            and complete_60
        ):
            unusual = wake_at < expected_wake - timedelta(
                minutes=WAKE_EARLY_THRESHOLD_MINUTES
            )
            wake_label = int(
                unusual and timestamp < wake_at <= deadline_60
            )

        pillbox_deadline = deadline_30
        due_in_window = (
            expected_pillbox is not None
            and timestamp < expected_pillbox <= deadline_30
        )
        if due_in_window and expected_pillbox is not None:
            pillbox_deadline = max(
                deadline_30,
                expected_pillbox
                + timedelta(minutes=PILLBOX_DELAY_TOLERANCE_MINUTES),
            )
        pillbox_coverage = _has_coverage(
            observations,
            index,
            pillbox_deadline,
            max_gap,
            "pillbox_open" if due_in_window else None,
        )
        pillbox_label: int | None = None
        if expected_pillbox is not None and pillbox_coverage:
            if not due_in_window:
                pillbox_label = 0
            else:
                grace_deadline = expected_pillbox + timedelta(
                    minutes=PILLBOX_DELAY_TOLERANCE_MINUTES
                )
                opened_by_grace = False
                for cursor in range(index, len(observations)):
                    item = observations[cursor]
                    if item.timestamp > grace_deadline:
                        break
                    if item.telemetry.pillbox_open is True:
                        opened_by_grace = True
                        break
                pillbox_label = int(not opened_by_grace)

        room_coverage = _has_coverage(
            observations,
            index,
            timestamp + ROOM_HORIZON,
            max_gap,
            "room_temperature_c",
        )
        room_label: int | None = None
        if (
            schedule.room_min_c is not None
            and schedule.room_max_c is not None
            and room_coverage
            and telemetry.room_temperature_c is not None
        ):
            previous_temperature = telemetry.room_temperature_c
            crossed = False
            for cursor in range(index + 1, len(observations)):
                future = observations[cursor]
                if future.timestamp > timestamp + ROOM_HORIZON:
                    break
                current_temperature = future.telemetry.room_temperature_c
                if current_temperature is None:
                    crossed = False
                    break
                previous_in_range = (
                    schedule.room_min_c
                    <= previous_temperature
                    <= schedule.room_max_c
                )
                current_in_range = (
                    schedule.room_min_c
                    <= current_temperature
                    <= schedule.room_max_c
                )
                if previous_in_range and not current_in_range:
                    crossed = True
                    break
                previous_temperature = current_temperature
            room_label = int(crossed)

        row: dict[str, Any] = {
            "profile_id": observation.profile_id,
            "sequence_id": observation.sequence_id,
            "local_date": observation.local_date.isoformat(),
            **telemetry.model_dump(mode="json"),
            "timestamp_utc": timestamp.isoformat().replace("+00:00", "Z"),
            "feature_hour_sin": math.sin(
                2 * math.pi
                * (local_timestamp.hour + local_timestamp.minute / 60)
                / 24
            ),
            "feature_hour_cos": math.cos(
                2 * math.pi
                * (local_timestamp.hour + local_timestamp.minute / 60)
                / 24
            ),
            "feature_expected_wake_hour_local": (
                expected_wake.astimezone(schedule.timezone).hour
                + expected_wake.astimezone(schedule.timezone).minute / 60
                if expected_wake is not None
                else None
            ),
            "feature_minutes_from_expected_wake": (
                (timestamp - expected_wake).total_seconds() / 60
                if expected_wake is not None
                else None
            ),
            "feature_motion_intensity_m_s2": motion,
            "feature_recent_motion_mean_30m_m_s2": (
                fmean(value for _, value in recent_motion)
                if recent_motion
                else None
            ),
            "feature_minutes_since_pillbox_open": minutes_since_open,
            "feature_room_temperature_c": telemetry.room_temperature_c,
            "label_unusual_wake_within_60m": wake_label,
            "label_delayed_medication_access_within_30m": pillbox_label,
            "label_room_temp_out_of_range_within_30m": room_label,
            "quality_mpu6050_valid": telemetry.sensor_validity.mpu6050,
            "quality_max30102_valid": telemetry.sensor_validity.max30102,
            "quality_ds18b20_valid": telemetry.sensor_validity.ds18b20,
            "quality_room_temperature_valid": telemetry.room_temperature_c is not None,
            "quality_pillbox_observed": telemetry.pillbox_open is not None,
            "quality_observation_complete_30m": complete_30,
            "quality_observation_complete_60m": complete_60,
            "quality_room_temperature_complete_30m": room_coverage,
            "quality_pillbox_complete_to_grace": (
                due_in_window and pillbox_coverage
            ),
            **provenance,
        }
        row["sensor_validity"] = json.dumps(
            row["sensor_validity"], sort_keys=True, separators=(",", ":")
        )
        output.append(row)
    return output


def prepare_real_dataset(
    telemetry_path: Path,
    schedule_path: Path,
    output_path: Path,
    *,
    annotation_path: Path | None = None,
    max_gap_seconds: int = DEFAULT_MAX_GAP_SECONDS,
) -> int:
    """Validate raw hardware inputs and write a separate prepared dataset."""
    if max_gap_seconds < 1:
        raise RealDataPreparationError("max_gap_seconds must be at least 1")
    input_paths = [telemetry_path.resolve(), schedule_path.resolve()]
    if annotation_path is not None:
        input_paths.append(annotation_path.resolve())
    resolved_output = output_path.resolve()
    if resolved_output in input_paths:
        raise RealDataPreparationError("output_path must differ from every input path")
    if output_path.exists():
        raise RealDataPreparationError(
            f"output_path already exists; choose a new path to preserve existing data: {output_path}"
        )

    schedules, profile_by_device = _load_schedules(schedule_path)
    wake_events = _load_annotations(annotation_path, schedules)
    raw_rows = _parse_raw_telemetry(telemetry_path)
    timezone_by_device = {
        schedule.device_id: schedule.timezone for schedule in schedules.values()
    }
    observations_by_group: dict[tuple[str, date], list[Observation]] = {}
    for raw in raw_rows:
        telemetry: TelemetryRecord = raw["telemetry"]
        zone = timezone_by_device.get(telemetry.device_id)
        if zone is None:
            raise RealDataPreparationError(
                f"{telemetry.device_id}: no device/profile mapping in schedule input"
            )
        local_date = telemetry.timestamp.astimezone(zone).date()
        schedule = schedules.get((telemetry.device_id, local_date))
        if schedule is None:
            raise RealDataPreparationError(
                f"{telemetry.device_id}: missing routine-schedule row for local date {local_date}"
            )
        profile_id = profile_by_device[telemetry.device_id]
        sequence_id = f"{profile_id}-{local_date.isoformat()}"
        group_key = (profile_id, local_date)
        observations_by_group.setdefault(group_key, []).append(
            Observation(telemetry, profile_id, local_date, sequence_id)
        )

    provenance = {
        "preparation_version": PREPARATION_VERSION,
        "preparation_tool": PREPARATION_TOOL,
        "preparation_raw_sha256": hashlib.sha256(telemetry_path.read_bytes()).hexdigest(),
        "preparation_schedule_sha256": hashlib.sha256(schedule_path.read_bytes()).hexdigest(),
        "preparation_annotation_sha256": (
            hashlib.sha256(annotation_path.read_bytes()).hexdigest()
            if annotation_path is not None
            else "not-provided"
        ),
    }
    output_rows: list[dict[str, Any]] = []
    max_gap = timedelta(seconds=max_gap_seconds)
    for group_key in sorted(observations_by_group):
        output_rows.extend(
            _prepare_sequence(
                observations_by_group[group_key],
                schedules,
                wake_events,
                max_gap=max_gap,
                provenance=provenance,
            )
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output_path.open("w", newline="", encoding="utf-8") as destination:
            writer = csv.DictWriter(destination, fieldnames=OUTPUT_COLUMNS)
            writer.writeheader()
            writer.writerows(output_rows)
    except (OSError, csv.Error) as exc:
        raise RealDataPreparationError(
            f"cannot write prepared dataset {output_path}: {exc}"
        ) from exc
    return len(output_rows)


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare a real-data training CSV from raw hardware telemetry and independent context."
    )
    parser.add_argument("--telemetry", type=Path, required=True)
    parser.add_argument("--schedules", type=Path, required=True)
    parser.add_argument(
        "--annotations",
        type=Path,
        help="Optional event annotation CSV; without it, wake labels remain unavailable.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--max-gap-seconds",
        type=int,
        default=DEFAULT_MAX_GAP_SECONDS,
        help=f"Maximum observation gap for complete horizons (default: {DEFAULT_MAX_GAP_SECONDS})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    try:
        count = prepare_real_dataset(
            args.telemetry,
            args.schedules,
            args.output,
            annotation_path=args.annotations,
            max_gap_seconds=args.max_gap_seconds,
        )
    except (OSError, RealDataPreparationError) as exc:
        parser.error(str(exc))
    print(f"Wrote {count} prepared hardware rows to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
