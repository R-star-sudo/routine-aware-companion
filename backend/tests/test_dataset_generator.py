import csv
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

from app.dataset_generator import (
    CSV_COLUMNS,
    FEATURE_COLUMNS,
    LABEL_COLUMNS,
    METADATA_COLUMNS,
    generate_dataset,
    main,
)


def make_dataset(**overrides: object) -> list[dict[str, object]]:
    options: dict[str, object] = {
        "days": 4,
        "interval_seconds": 300,
        "seed": 42,
        "scenario_rate": 0.5,
        "start_date": date(2026, 1, 1),
        "local_timezone": timezone.utc,
    }
    options.update(overrides)
    return list(generate_dataset(**options))


def test_generation_is_reproducible_for_same_configuration() -> None:
    assert make_dataset() == make_dataset()


def test_different_seed_changes_generated_records() -> None:
    assert make_dataset(seed=42) != make_dataset(seed=43)


def test_days_and_sampling_interval_determine_record_count() -> None:
    rows = make_dataset(days=2, interval_seconds=600)

    assert len(rows) == 3 * 2 * (24 * 60 * 60 // 600)
    assert len({row["sequence_id"] for row in rows}) == 6


def test_timestamps_are_ordered_utc_and_sources_are_simulated() -> None:
    rows = make_dataset(days=2)
    profile_rows = [row for row in rows if row["profile_id"] == "synthetic-profile-01"]
    timestamps = [datetime.fromisoformat(str(row["timestamp_utc"]).replace("Z", "+00:00")) for row in profile_rows]

    assert timestamps == sorted(timestamps)
    assert all(timestamp.utcoffset() == timezone.utc.utcoffset(None) for timestamp in timestamps)
    assert all(row["source"] == "simulated" for row in rows)


def test_csv_columns_match_documented_groups_and_labels_are_not_features(
    tmp_path, monkeypatch
) -> None:
    output = tmp_path / "nested" / "dataset.csv"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "dataset_generator",
            "--days",
            "1",
            "--interval-minutes",
            "60",
            "--seed",
            "4",
            "--output",
            str(output),
        ],
    )
    assert main() == 0

    with output.open(newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        rows = list(reader)
        assert tuple(reader.fieldnames or ()) == CSV_COLUMNS
    assert not set(LABEL_COLUMNS).intersection(FEATURE_COLUMNS)
    assert all(column.startswith("metadata_") for column in METADATA_COLUMNS)
    assert len(rows) == 3 * 24


def test_labels_match_future_event_horizons_and_end_rows_are_unavailable() -> None:
    rows = make_dataset(days=5, interval_seconds=300, scenario_rate=1.0)
    sequence_ids = {
        str(row["sequence_id"])
        for row in rows
        if row["profile_id"] == "synthetic-profile-01"
    }
    for sequence_id in sequence_ids:
        sequence_rows = [row for row in rows if row["sequence_id"] == sequence_id]
        for index, row in enumerate(sequence_rows):
            timestamp = datetime.fromisoformat(
                str(row["timestamp_utc"]).replace("Z", "+00:00")
            )
            if index >= len(sequence_rows) - 12:
                assert all(row[label] is None for label in LABEL_COLUMNS)
                continue

            actual_wake = datetime.fromisoformat(
                str(row["metadata_actual_wake_time_utc"]).replace("Z", "+00:00")
            )
            expected_wake = datetime.fromisoformat(
                str(row["metadata_expected_wake_time_utc"]).replace("Z", "+00:00")
            )
            unusual_wake = (
                row["metadata_scenario_early_wake"]
                and actual_wake < expected_wake - timedelta(minutes=30)
            )
            expected_wake_label = int(
                unusual_wake
                and timestamp < actual_wake <= timestamp + timedelta(minutes=60)
            )
            assert row["label_unusual_wake_within_60m"] == expected_wake_label

            expected_access = datetime.fromisoformat(
                str(row["metadata_expected_pillbox_time_utc"]).replace("Z", "+00:00")
            )
            actual_access_value = row["metadata_actual_pillbox_time_utc"]
            access_is_delayed = (
                row["metadata_scenario_delayed_access"]
                and (
                    not actual_access_value
                    or datetime.fromisoformat(
                        str(actual_access_value).replace("Z", "+00:00")
                    )
                    > expected_access + timedelta(minutes=15)
                )
            )
            expected_access_label = int(
                access_is_delayed
                and timestamp < expected_access <= timestamp + timedelta(minutes=30)
            )
            assert (
                row["label_delayed_medication_access_within_30m"]
                == expected_access_label
            )

            crossing_value = row["metadata_room_crossing_time_utc"]
            expected_room_label = int(
                bool(crossing_value)
                and timestamp
                < datetime.fromisoformat(str(crossing_value).replace("Z", "+00:00"))
                <= timestamp + timedelta(minutes=30)
            )
            assert row["label_room_temp_out_of_range_within_30m"] == expected_room_label

    assert all(label not in FEATURE_COLUMNS for label in LABEL_COLUMNS)


def test_missing_sensor_readings_remain_null_not_zero() -> None:
    rows = make_dataset(days=2, interval_seconds=300)
    missing_mpu = next(row for row in rows if row["sensor_validity_mpu6050"] is False)
    assert all(
        missing_mpu[field] is None
        for field in (
            "acceleration_x_m_s2",
            "acceleration_y_m_s2",
            "acceleration_z_m_s2",
            "angular_velocity_x_rad_s",
            "angular_velocity_y_rad_s",
            "angular_velocity_z_rad_s",
        )
    )
    assert any(row["pulse_rate_bpm"] is None for row in rows)
    assert any(row["skin_temperature_c"] is None for row in rows)


@pytest.mark.parametrize(
    "arguments",
    [
        ["--days", "0"],
        ["--interval-minutes", "0"],
        ["--interval-minutes", "7"],
        ["--scenario-rate", "1.1"],
        ["--timezone", "not-a-timezone"],
        ["--interval-minutes", "5", "--interval-seconds", "300"],
    ],
)
def test_invalid_cli_parameters_fail_clearly(arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2


def test_labels_are_binary_or_unavailable_and_scenarios_are_auditable() -> None:
    rows = make_dataset(days=7, scenario_rate=0.6)
    for row in rows:
        for label in LABEL_COLUMNS:
            assert row[label] in (0, 1, None)
        for metadata in METADATA_COLUMNS[:3]:
            assert isinstance(row[metadata], bool)
    assert any(row["label_unusual_wake_within_60m"] == 1 for row in rows)
    assert any(row["label_delayed_medication_access_within_30m"] == 1 for row in rows)
    assert any(row["label_room_temp_out_of_range_within_30m"] == 1 for row in rows)
