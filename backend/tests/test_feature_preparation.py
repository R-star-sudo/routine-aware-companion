from datetime import datetime, timedelta, timezone

from app.dataset_generator import FEATURE_COLUMNS
from app.schemas.telemetry import SensorValidity, TelemetryRecord
from app.services.feature_preparation import prepare_features


def make_record(index: int, *, now: datetime, mpu_ok: bool = True, pillbox_open: bool = False) -> TelemetryRecord:
    timestamp = now - timedelta(minutes=30 - index)
    return TelemetryRecord(
        schema_version="1.0", source="simulated", device_id="demo-device", sequence=index,
        timestamp=timestamp,
        acceleration_x_m_s2=0.1 if mpu_ok else None,
        acceleration_y_m_s2=0.2 if mpu_ok else None,
        acceleration_z_m_s2=9.81 if mpu_ok else None,
        angular_velocity_x_rad_s=0.0 if mpu_ok else None,
        angular_velocity_y_rad_s=0.0 if mpu_ok else None,
        angular_velocity_z_rad_s=0.0 if mpu_ok else None,
        pulse_rate_bpm=None, skin_temperature_c=None,
        room_temperature_c=22.0, room_humidity_percent=45.0,
        pillbox_open=pillbox_open,
        sensor_validity=SensorValidity(mpu6050=mpu_ok, max30102=False, ds18b20=False),
    )


def test_preparation_matches_training_feature_order_and_causal_values() -> None:
    now = datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)
    records = [make_record(i, now=now, pillbox_open=(i == 2)) for i in range(31)]
    result = prepare_features(records, timezone_name="UTC", expected_wake_time="07:30", now=now)
    assert tuple(result["features"]) == FEATURE_COLUMNS
    assert result["unavailable"] == {}
    values = result["features"]
    assert values["feature_expected_wake_hour_local"] == 7.5
    assert values["feature_minutes_from_expected_wake"] == -30
    assert values["feature_minutes_since_pillbox_open"] == 28
    assert values["feature_motion_intensity_m_s2"] > 0


def test_missing_history_or_schedule_marks_features_unavailable() -> None:
    now = datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)
    result = prepare_features([make_record(30, now=now)], timezone_name=None, expected_wake_time=None, now=now)
    assert result["unavailable"]["feature_recent_motion_mean_30m_m_s2"] == "insufficient_or_gapped_30m_history"
    assert result["unavailable"]["feature_minutes_since_pillbox_open"] == "no_observed_open_in_supplied_history"
    assert result["unavailable"]["feature_expected_wake_hour_local"] == "routine_wake_time_missing_or_invalid"
    assert set(result["unavailable"]) == set(FEATURE_COLUMNS) - {"feature_motion_intensity_m_s2"}


def test_invalid_current_motion_and_stale_observation_are_not_imputed() -> None:
    now = datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)
    invalid = [make_record(i, now=now, mpu_ok=(i != 30), pillbox_open=(i == 2)) for i in range(31)]
    result = prepare_features(invalid, timezone_name="UTC", expected_wake_time="07:30", now=now)
    assert result["features"]["feature_motion_intensity_m_s2"] is None
    assert result["unavailable"]["feature_motion_intensity_m_s2"] == "current_motion_sensor_invalid_or_missing"

    stale = prepare_features([make_record(i, now=now - timedelta(hours=1), pillbox_open=(i == 2)) for i in range(31)], timezone_name="UTC", expected_wake_time="07:30", now=now)
    assert set(stale["unavailable"].values()) == {"stale_latest_observation"}


def test_missing_pillbox_observation_after_open_suppresses_elapsed_feature() -> None:
    now = datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)
    records = [make_record(i, now=now, pillbox_open=(i == 2)) for i in range(31)]
    records[20] = records[20].model_copy(update={"pillbox_open": None})
    result = prepare_features(records, timezone_name="UTC", expected_wake_time="07:30", now=now)
    assert result["features"]["feature_minutes_since_pillbox_open"] is None
    assert result["unavailable"]["feature_minutes_since_pillbox_open"] == "missing_pillbox_observation_since_last_open"


def test_schedule_timezone_and_wake_time_are_validated() -> None:
    now = datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)
    records = [make_record(i, now=now, pillbox_open=(i == 2)) for i in range(31)]
    bad_zone = prepare_features(records, timezone_name="No/Such_Zone", expected_wake_time="07:30", now=now)
    assert bad_zone["unavailable"]["feature_hour_sin"] == "routine_timezone_missing_or_invalid"
    assert bad_zone["unavailable"]["feature_expected_wake_hour_local"] == "unknown_schedule_timezone"
    bad_time = prepare_features(records, timezone_name="UTC", expected_wake_time="25:99", now=now)
    assert bad_time["unavailable"]["feature_minutes_from_expected_wake"] == "routine_wake_time_missing_or_invalid"
