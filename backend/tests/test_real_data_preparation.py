import csv
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.dataset_generator import LABEL_COLUMNS
from app.model_evaluation import (
    REAL_FEATURE_ALLOWLIST,
    DatasetValidationError,
    chronological_group_split,
    load_dataset,
)
from app.real_data_preparation import (
    ANNOTATION_COLUMNS,
    OUTPUT_COLUMNS,
    SCHEDULE_COLUMNS,
    TELEMETRY_COLUMNS,
    RealDataPreparationError,
    prepare_real_dataset,
)

DEVICE_ID = "hardware-device-test"
PROFILE_ID = "pseudonymous-profile-test"
START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _inputs(
    tmp_path: Path,
    *,
    days: int = 4,
    annotation_times: list[datetime] | None = None,
    room_range: tuple[float, float] | None = (18.0, 26.0),
    missing_room_at: datetime | None = None,
    missing_pillbox_at: datetime | None = None,
) -> tuple[Path, Path, Path]:
    telemetry_path = tmp_path / "raw" / "telemetry.csv"
    schedule_path = tmp_path / "context" / "schedules.csv"
    annotation_path = tmp_path / "context" / "annotations.csv"
    telemetry_rows: list[dict[str, object]] = []
    schedule_rows: list[dict[str, object]] = []
    annotation_rows: list[dict[str, object]] = []
    sequence = 0

    for day_index in range(days):
        day_start = START + timedelta(days=day_index)
        local_day = day_start.date()
        room_min, room_max = room_range or (None, None)
        schedule_rows.append(
            {
                "profile_id": PROFILE_ID,
                "device_id": DEVICE_ID,
                "local_date": local_day.isoformat(),
                "timezone": "UTC",
                "expected_wake_at": (day_start + timedelta(hours=7)).isoformat(),
                "scheduled_pillbox_at": (day_start + timedelta(hours=8)).isoformat(),
                "room_min_c": room_min,
                "room_max_c": room_max,
            }
        )
        wake_time = (
            annotation_times[day_index]
            if annotation_times is not None
            else day_start + timedelta(hours=6, minutes=20)
        )
        annotation_rows.append(
            {
                "profile_id": PROFILE_ID,
                "event_type": "wake",
                "event_timestamp": wake_time.isoformat(),
                "evidence": "caregiver-confirmed observation",
            }
        )

        for sample in range(24 * 12):
            timestamp = day_start + timedelta(minutes=sample * 5)
            room_temperature: float | None = (
                27.0 if timestamp.hour >= 12 else 22.0
            )
            if timestamp == missing_room_at:
                room_temperature = None
            pillbox_open = (
                True
                if timestamp == day_start + timedelta(hours=8, minutes=20)
                else False
            )
            if timestamp == missing_pillbox_at:
                pillbox_open = None
            telemetry_rows.append(
                {
                    "schema_version": "1.0",
                    "source": "hardware",
                    "device_id": DEVICE_ID,
                    "sequence": sequence,
                    "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                    "acceleration_x_m_s2": 0.1,
                    "acceleration_y_m_s2": 0.0,
                    "acceleration_z_m_s2": 9.81,
                    "angular_velocity_x_rad_s": 0.01,
                    "angular_velocity_y_rad_s": 0.02,
                    "angular_velocity_z_rad_s": 0.03,
                    "pulse_rate_bpm": 72.0,
                    "skin_temperature_c": 33.0,
                    "room_temperature_c": room_temperature,
                    "room_humidity_percent": 45.0,
                    "pillbox_open": pillbox_open,
                    "sensor_validity": json.dumps(
                        {"mpu6050": True, "max30102": True, "ds18b20": True}
                    ),
                }
            )
            sequence += 1

    _write_csv(telemetry_path, TELEMETRY_COLUMNS, telemetry_rows)
    _write_csv(schedule_path, SCHEDULE_COLUMNS, schedule_rows)
    _write_csv(annotation_path, ANNOTATION_COLUMNS, annotation_rows)
    return telemetry_path, schedule_path, annotation_path


def _prepare(
    tmp_path: Path,
    *,
    annotations: bool = True,
    **input_options: object,
) -> tuple[Path, list[dict[str, str]]]:
    telemetry, schedules, annotation = _inputs(tmp_path, **input_options)
    output = tmp_path / "prepared" / "training.csv"
    count = prepare_real_dataset(
        telemetry,
        schedules,
        output,
        annotation_path=annotation if annotations else None,
    )
    with output.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        rows = list(reader)
        assert tuple(reader.fieldnames or ()) == OUTPUT_COLUMNS
    assert count == len(rows)
    return output, rows


def _row_at(rows: list[dict[str, str]], timestamp: datetime) -> dict[str, str]:
    expected = timestamp.isoformat().replace("+00:00", "Z")
    return next(row for row in rows if row["timestamp_utc"] == expected)


def test_preparation_generates_targets_with_provenance_and_causal_features(
    tmp_path: Path,
) -> None:
    output, rows = _prepare(tmp_path)
    wake_prediction = _row_at(rows, START + timedelta(hours=6))
    medication_prediction = _row_at(rows, START + timedelta(hours=7, minutes=30))
    room_prediction = _row_at(rows, START + timedelta(hours=11, minutes=30))

    assert wake_prediction["label_unusual_wake_within_60m"] == "1"
    assert medication_prediction["label_delayed_medication_access_within_30m"] == "1"
    assert room_prediction["label_room_temp_out_of_range_within_30m"] == "1"
    assert float(room_prediction["feature_room_temperature_c"]) == 22.0
    assert wake_prediction["preparation_tool"] == "app.real_data_preparation"
    assert len(wake_prediction["preparation_raw_sha256"]) == 64
    assert "event_timestamp" not in rows[0]
    assert "evidence" not in rows[0]
    assert output.is_file()
    assert all(row["source"] == "hardware" for row in rows)


def test_absent_wake_annotations_leave_only_wake_targets_unavailable(
    tmp_path: Path,
) -> None:
    _, rows = _prepare(tmp_path, annotations=False)

    assert all(row["label_unusual_wake_within_60m"] == "" for row in rows)
    assert any(row["label_delayed_medication_access_within_30m"] == "1" for row in rows)
    assert any(row["label_room_temp_out_of_range_within_30m"] == "1" for row in rows)


def test_missing_schedule_details_leave_affected_targets_unavailable(
    tmp_path: Path,
) -> None:
    telemetry, schedules, annotations = _inputs(tmp_path)
    with schedules.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        fields = tuple(reader.fieldnames or ())
        rows = list(reader)
    for row in rows:
        row["expected_wake_at"] = ""
        row["scheduled_pillbox_at"] = ""
        row["room_min_c"] = ""
        row["room_max_c"] = ""
    _write_csv(schedules, fields, rows)

    output = tmp_path / "prepared.csv"
    prepare_real_dataset(telemetry, schedules, output, annotation_path=annotations)
    with output.open(newline="", encoding="utf-8") as source:
        prepared_rows = list(csv.DictReader(source))

    assert all(row[label] == "" for row in prepared_rows for label in LABEL_COLUMNS)
    assert all(row["feature_expected_wake_hour_local"] == "" for row in prepared_rows)


def test_missing_room_readings_remain_missing_and_make_room_labels_unavailable(
    tmp_path: Path,
) -> None:
    missing_time = START + timedelta(hours=11, minutes=35)
    _, rows = _prepare(tmp_path, missing_room_at=missing_time)
    missing_row = _row_at(rows, missing_time)
    before_crossing = _row_at(rows, START + timedelta(hours=11, minutes=30))

    assert missing_row["room_temperature_c"] == ""
    assert missing_row["feature_room_temperature_c"] == ""
    assert missing_row["quality_room_temperature_valid"] == "False"
    assert before_crossing["label_room_temp_out_of_range_within_30m"] == ""


def test_room_crossing_needs_no_reading_before_prediction_timestamp(
    tmp_path: Path,
) -> None:
    missing_previous = START + timedelta(hours=11, minutes=25)
    _, rows = _prepare(tmp_path, missing_room_at=missing_previous)
    prediction = _row_at(rows, START + timedelta(hours=11, minutes=30))

    assert prediction["room_temperature_c"] == "22.0"
    assert prediction["label_room_temp_out_of_range_within_30m"] == "1"


def test_missing_pillbox_observations_do_not_become_negative_or_positive_labels(
    tmp_path: Path,
) -> None:
    missing_time = START + timedelta(hours=8)
    _, rows = _prepare(tmp_path, missing_pillbox_at=missing_time)
    prediction = _row_at(rows, START + timedelta(hours=7, minutes=30))

    assert prediction["label_delayed_medication_access_within_30m"] == ""
    assert prediction["quality_pillbox_complete_to_grace"] == "False"


def test_offset_timestamp_is_normalized_to_utc_and_raw_inputs_are_unchanged(
    tmp_path: Path,
) -> None:
    telemetry, schedules, annotations = _inputs(tmp_path)
    original_raw = telemetry.read_bytes()
    original_schedule = schedules.read_bytes()
    original_annotations = annotations.read_bytes()
    with telemetry.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        fields = tuple(reader.fieldnames or ())
        rows = list(reader)
    rows[0]["timestamp"] = "2026-01-01T05:30:00+05:30"
    _write_csv(telemetry, fields, rows)
    changed_raw = telemetry.read_bytes()

    output = tmp_path / "prepared.csv"
    prepare_real_dataset(
        telemetry, schedules, output, annotation_path=annotations
    )
    with output.open(newline="", encoding="utf-8") as source:
        prepared_rows = list(csv.DictReader(source))

    assert prepared_rows[0]["timestamp_utc"] == "2026-01-01T00:00:00Z"
    assert telemetry.read_bytes() == changed_raw
    assert schedules.read_bytes() == original_schedule
    assert annotations.read_bytes() == original_annotations
    assert changed_raw != original_raw


def test_large_sampling_gap_makes_future_labels_unavailable(tmp_path: Path) -> None:
    telemetry, schedules, annotations = _inputs(tmp_path)
    with telemetry.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        fields = tuple(reader.fieldnames or ())
        rows = list(reader)
    remove_timestamps = {
        "2026-01-01T08:00:00Z",
        "2026-01-01T08:05:00Z",
    }
    _write_csv(
        telemetry,
        fields,
        [row for row in rows if row["timestamp"] not in remove_timestamps],
    )

    output = tmp_path / "prepared.csv"
    prepare_real_dataset(telemetry, schedules, output, annotation_path=annotations)
    with output.open(newline="", encoding="utf-8") as source:
        prepared_rows = list(csv.DictReader(source))
    prediction = _row_at(prepared_rows, START + timedelta(hours=7, minutes=30))

    assert prediction["label_delayed_medication_access_within_30m"] == ""
    assert prediction["quality_pillbox_complete_to_grace"] == "False"


def test_incomplete_future_horizons_and_schedule_boundaries_are_unavailable_or_closed(
    tmp_path: Path,
) -> None:
    _, rows = _prepare(tmp_path)
    last_row = _row_at(rows, START + timedelta(hours=23, minutes=55))
    at_thirty_minute_schedule_boundary = _row_at(
        rows, START + timedelta(hours=7, minutes=30)
    )

    assert last_row["label_unusual_wake_within_60m"] == ""
    assert last_row["label_delayed_medication_access_within_30m"] == ""
    assert last_row["label_room_temp_out_of_range_within_30m"] == ""
    assert at_thirty_minute_schedule_boundary[
        "label_delayed_medication_access_within_30m"
    ] == "1"


def test_future_annotation_changes_labels_but_never_features(tmp_path: Path) -> None:
    telemetry, schedules, annotation = _inputs(tmp_path)
    first_output = tmp_path / "prepared" / "first.csv"
    prepare_real_dataset(telemetry, schedules, first_output, annotation_path=annotation)
    before_event = START + timedelta(hours=6, minutes=20)
    with first_output.open(newline="", encoding="utf-8") as source:
        first_rows = list(csv.DictReader(source))
    first_prediction = _row_at(first_rows, START + timedelta(hours=6))

    _write_csv(
        annotation,
        ANNOTATION_COLUMNS,
        [
            {
                "profile_id": PROFILE_ID,
                "event_type": "wake",
                "event_timestamp": (
                    START + timedelta(days=day_index, hours=6, minutes=40)
                ).isoformat(),
                "evidence": "updated independent annotation",
            }
            for day_index in range(4)
        ],
    )
    second_output = tmp_path / "prepared" / "second.csv"
    prepare_real_dataset(telemetry, schedules, second_output, annotation_path=annotation)
    with second_output.open(newline="", encoding="utf-8") as source:
        second_rows = list(csv.DictReader(source))
    second_prediction = _row_at(second_rows, START + timedelta(hours=6))

    assert first_prediction["label_unusual_wake_within_60m"] == "1"
    assert second_prediction["label_unusual_wake_within_60m"] == "0"
    assert [
        first_prediction[name] for name in REAL_FEATURE_ALLOWLIST
    ] == [second_prediction[name] for name in REAL_FEATURE_ALLOWLIST]
    assert before_event < datetime.fromisoformat(
        second_prediction["timestamp_utc"].replace("Z", "+00:00")
    ) + timedelta(minutes=60)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda row: row.update(source="simulated"), "source must be 'hardware'"),
        (lambda row: row.update(timestamp="not-a-timestamp"), "invalid telemetry"),
        (lambda row: row.update(sensor_validity="{bad"), "sensor_validity must be JSON"),
    ],
)
def test_invalid_raw_records_fail_clearly(
    tmp_path: Path, mutator, message: str
) -> None:
    telemetry, schedules, annotation = _inputs(tmp_path)
    with telemetry.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        fields = tuple(reader.fieldnames or ())
        rows = list(reader)
    row: dict[str, object] = dict(rows[0])
    mutator(row)
    _write_csv(telemetry, fields, [row, *rows[1:]])

    with pytest.raises(RealDataPreparationError, match=message):
        prepare_real_dataset(
            telemetry,
            schedules,
            tmp_path / "prepared.csv",
            annotation_path=annotation,
        )


def test_missing_schedule_for_observed_profile_day_fails(tmp_path: Path) -> None:
    telemetry, schedules, annotation = _inputs(tmp_path)
    with schedules.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        fields = tuple(reader.fieldnames or ())
        rows = list(reader)
    _write_csv(schedules, fields, rows[:-1])

    with pytest.raises(RealDataPreparationError, match="missing routine-schedule row"):
        prepare_real_dataset(
            telemetry,
            schedules,
            tmp_path / "prepared.csv",
            annotation_path=None,
        )


def test_existing_output_is_not_overwritten(tmp_path: Path) -> None:
    telemetry, schedules, annotation = _inputs(tmp_path)
    output = tmp_path / "prepared.csv"
    output.write_text("preserve this file\n", encoding="utf-8")

    with pytest.raises(RealDataPreparationError, match="output_path already exists"):
        prepare_real_dataset(
            telemetry, schedules, output, annotation_path=annotation
        )

    assert output.read_text(encoding="utf-8") == "preserve this file\n"


def test_prepared_real_evaluator_requires_provenance_and_splits_whole_sequences(
    tmp_path: Path,
) -> None:
    output, _ = _prepare(tmp_path)
    dataset = load_dataset(output, dataset_mode="prepared-real")

    assert dataset.feature_columns == REAL_FEATURE_ALLOWLIST
    assert "feature_room_temperature_c" in dataset.feature_columns
    assert not any(column.startswith("label_") for column in dataset.feature_columns)
    assert dataset.provenance["preparation_tool"] == "app.real_data_preparation"
    train, validation, test = chronological_group_split(dataset)
    group_sets = [
        {dataset.groups[index] for index in partition}
        for partition in (train, validation, test)
    ]
    assert not group_sets[0] & group_sets[1]
    assert not group_sets[0] & group_sets[2]
    assert not group_sets[1] & group_sets[2]
    assert set.union(*group_sets) == set(dataset.groups)


def test_raw_hardware_csv_is_not_accepted_as_a_prepared_training_dataset(
    tmp_path: Path,
) -> None:
    telemetry, _, _ = _inputs(tmp_path)

    with pytest.raises(DatasetValidationError):
        load_dataset(telemetry)
    with pytest.raises(DatasetValidationError, match="missing required columns"):
        load_dataset(telemetry, dataset_mode="prepared-real")


def test_prepared_real_rejects_unapproved_feature_columns(tmp_path: Path) -> None:
    output, _ = _prepare(tmp_path)
    with output.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        fields = tuple(reader.fieldnames or ())
        rows = list(reader)
    fields_with_leak = (*fields, "feature_future_event_time")
    for row in rows:
        row["feature_future_event_time"] = "999"
    _write_csv(output, fields_with_leak, rows)

    with pytest.raises(
        DatasetValidationError, match="unapproved feature columns"
    ):
        load_dataset(output, dataset_mode="prepared-real")
