from datetime import datetime, timezone
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.collector import post_lines
from app.main import create_app
from app.services.firmware_adapter import FirmwareAdapterError, adapt_firmware_line

FIRMWARE_LINE = (
    '{"src":"unified","wearable":{"pkt":42,"ts":12345,'
    '"ax":0.100,"ay":-0.200,"az":9.810,"gx":0.010,"gy":0.020,"gz":0.030,'
    '"hr":72.0,"ir":123456,"finger":true,"skin_t":33.50,'
    '"mpu_ok":true,"max_ok":true,"ds_ok":true},'
    '"base":{"ambient_t":22.0,"humidity":45.0,"dht_ok":true,"pillbox":"OPEN"},'
    '"rf":{"rx":42,"drop":0,"pkt_gap":0,"link":"OK"}}'
)


def test_adapter_maps_actual_firmware_shape_with_configured_identity_and_clock() -> None:
    boot = datetime(2026, 10, 14, tzinfo=timezone.utc)
    record = adapt_firmware_line(FIRMWARE_LINE, device_id="base-station-a", boot_time_utc=boot)
    assert record is not None
    assert record["source"] == "hardware"
    assert record["device_id"] == "base-station-a"
    assert record["sequence"] == 42
    assert record["timestamp"] == "2026-10-14T00:00:12.345000Z"
    assert record["acceleration_z_m_s2"] == pytest.approx(9.81)
    assert record["room_temperature_c"] == 22
    assert record["pillbox_open"] is True


def test_adapter_nulls_invalid_sensor_fields_without_substitution() -> None:
    line = json.loads(FIRMWARE_LINE)
    line["wearable"].update({"mpu_ok": False, "max_ok": False, "ds_ok": False, "finger": False})
    line["base"].update({"dht_ok": False})
    record = adapt_firmware_line(json.dumps(line), device_id="base-a", boot_time_utc=datetime(2026, 10, 14, tzinfo=timezone.utc))
    assert record is not None
    assert all(record[key] is None for key in (
        "acceleration_x_m_s2", "acceleration_y_m_s2", "acceleration_z_m_s2",
        "angular_velocity_x_rad_s", "angular_velocity_y_rad_s", "angular_velocity_z_rad_s",
        "pulse_rate_bpm", "skin_temperature_c", "room_temperature_c", "room_humidity_percent",
    ))
    assert record["sensor_validity"] == {"mpu6050": False, "max30102": False, "ds18b20": False}


def test_adapter_requires_clock_identity_and_rejects_invalid_values() -> None:
    with pytest.raises(FirmwareAdapterError, match="device_id"):
        adapt_firmware_line(FIRMWARE_LINE, device_id=None, boot_time_utc=datetime.now(timezone.utc))
    with pytest.raises(FirmwareAdapterError, match="boot_time_utc"):
        adapt_firmware_line(FIRMWARE_LINE, device_id="base-a", boot_time_utc=None)
    bad = json.loads(FIRMWARE_LINE)
    bad["base"]["humidity"] = 140
    with pytest.raises(FirmwareAdapterError):
        adapt_firmware_line(json.dumps(bad), device_id="base-a", boot_time_utc=datetime(2026, 10, 14, tzinfo=timezone.utc))


def test_collector_filters_logs_and_posts_only_adapted_records() -> None:
    class FakeClient:
        def __init__(self):
            self.records = []

        def post(self, url, *, json):
            self.records.append(json)
            return httpx.Response(201, request=httpx.Request("POST", url))

    client = FakeClient()
    errors = io.StringIO()
    converter = lambda line: adapt_firmware_line(
        line, device_id="base-a", boot_time_utc=datetime(2026, 10, 14, tzinfo=timezone.utc)
    )
    error_count = post_lines(["[READY] listening\n", FIRMWARE_LINE], client, "http://test/telemetry", errors, transform=converter)
    assert error_count == 0
    assert len(client.records) == 1
    assert client.records[0]["source"] == "hardware"


def test_firmware_fixture_adapter_collector_and_api_history_end_to_end(tmp_path) -> None:
    client = TestClient(create_app(tmp_path / "telemetry.csv"))
    errors = io.StringIO()
    converter = lambda line: adapt_firmware_line(
        line, device_id="base-a", boot_time_utc=datetime(2026, 10, 14, tzinfo=timezone.utc)
    )
    failed = post_lines(
        ["[BOOT] base station ready\n", FIRMWARE_LINE],
        client,
        "http://testserver/api/v1/telemetry",
        error_stream=errors,
        transform=converter,
    )
    assert failed == 0
    assert client.get("/api/v1/telemetry/latest").json()["source"] == "hardware"
    assert client.get("/api/v1/telemetry/history").json()[0]["device_id"] == "base-a"
