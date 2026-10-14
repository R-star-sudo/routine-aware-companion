import io
import json
from datetime import datetime, timezone

import httpx
from fastapi.testclient import TestClient

from app.collector import post_lines
from app.main import create_app
from app.simulator import generate_records


def make_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "schema_version": "1.0",
        "source": "simulated",
        "device_id": "test-wearable",
        "sequence": 1,
        "timestamp": "2026-10-14T00:00:00Z",
        "acceleration_x_m_s2": 0.1,
        "acceleration_y_m_s2": -0.2,
        "acceleration_z_m_s2": 9.8,
        "angular_velocity_x_rad_s": 0.01,
        "angular_velocity_y_rad_s": 0.02,
        "angular_velocity_z_rad_s": 0.03,
        "pulse_rate_bpm": 72.0,
        "skin_temperature_c": 33.5,
        "room_temperature_c": None,
        "room_humidity_percent": None,
        "pillbox_open": None,
        "sensor_validity": {"mpu6050": True, "max30102": True, "ds18b20": True},
    }
    record.update(overrides)
    return record


def test_root_returns_welcome_message(tmp_path) -> None:
    client = TestClient(create_app(tmp_path / "telemetry.csv"))
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"message": "Welcome to Routine-Aware Companion"}


def test_health_returns_healthy_status(tmp_path) -> None:
    client = TestClient(create_app(tmp_path / "telemetry.csv"))
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_ingestion_persists_record_and_latest_and_history(tmp_path) -> None:
    csv_path = tmp_path / "records" / "telemetry.csv"
    client = TestClient(create_app(csv_path))
    record = make_record()

    response = client.post("/api/v1/telemetry", json=record)

    assert response.status_code == 201
    assert response.json()["source"] == "simulated"
    assert client.get("/api/v1/telemetry/latest").json()["sequence"] == 1
    history = client.get("/api/v1/telemetry/history?limit=1")
    assert history.status_code == 200
    assert [entry["sequence"] for entry in history.json()] == [1]
    reopened_client = TestClient(create_app(csv_path))
    assert reopened_client.get("/api/v1/telemetry/latest").json()["sequence"] == 1


def test_invalid_records_and_unavailable_sensor_values_are_handled(tmp_path) -> None:
    client = TestClient(create_app(tmp_path / "telemetry.csv"))

    invalid_temperature = make_record(skin_temperature_c=200)
    response = client.post("/api/v1/telemetry", json=invalid_temperature)
    assert response.status_code == 422

    naive_timestamp = make_record(timestamp="2026-10-14T00:00:00")
    response = client.post("/api/v1/telemetry", json=naive_timestamp)
    assert response.status_code == 422

    malformed_json = client.post(
        "/api/v1/telemetry",
        content='{"schema_version":',
        headers={"content-type": "application/json"},
    )
    assert malformed_json.status_code == 422

    missing_sensor = make_record(
        pulse_rate_bpm=None,
        skin_temperature_c=None,
        sensor_validity={"mpu6050": True, "max30102": False, "ds18b20": False},
    )
    response = client.post("/api/v1/telemetry", json=missing_sensor)
    assert response.status_code == 201
    assert response.json()["pulse_rate_bpm"] is None
    assert response.json()["skin_temperature_c"] is None


def test_latest_empty_history_limit_validation_and_csv_contents(tmp_path) -> None:
    csv_path = tmp_path / "nested" / "telemetry.csv"
    client = TestClient(create_app(csv_path))
    assert client.get("/api/v1/telemetry/latest").status_code == 404

    for sequence in range(3):
        assert client.post(
            "/api/v1/telemetry", json=make_record(sequence=sequence)
        ).status_code == 201

    assert client.get("/api/v1/telemetry/history?limit=0").status_code == 422
    history = client.get("/api/v1/telemetry/history?limit=2").json()
    assert [entry["sequence"] for entry in history] == [2, 1]
    assert csv_path.exists()
    assert "sensor_validity" in csv_path.read_text(encoding="utf-8").splitlines()[0]


def test_simulator_records_are_labeled_and_emit_nulls() -> None:
    records = list(
        generate_records(
            31,
            0.5,
            start_time=datetime(2026, 10, 14, tzinfo=timezone.utc),
            seed=7,
        )
    )

    assert all(record.source == "simulated" for record in records)
    assert records[1].timestamp > records[0].timestamp
    assert records[29].pulse_rate_bpm is None
    assert records[29].sensor_validity.max30102 is False
    serialized = json.loads(records[29].model_dump_json())
    assert serialized["pulse_rate_bpm"] is None


def test_collector_posts_valid_json_and_reports_malformed_lines() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.records = []

        def post(self, url: str, *, json: dict[str, object]) -> httpx.Response:
            self.records.append((url, json))
            return httpx.Response(201, request=httpx.Request("POST", url))

    client = FakeClient()
    errors = io.StringIO()
    lines = [
        json.dumps(make_record()),
        "{not valid json",
        "[]",
    ]

    error_count = post_lines(
        lines, client, "http://localhost/api/v1/telemetry", error_stream=errors
    )

    assert len(client.records) == 1
    assert client.records[0][1]["source"] == "simulated"
    assert error_count == 2
    assert "Line 2" in errors.getvalue()
    assert "Line 3" in errors.getvalue()


def test_collector_forwards_ndjson_to_api_and_reports_api_rejections(tmp_path) -> None:
    client = TestClient(create_app(tmp_path / "telemetry.csv"))
    errors = io.StringIO()

    error_count = post_lines(
        [
            json.dumps(make_record(sequence=4)),
            json.dumps(make_record(schema_version="unsupported")),
        ],
        client,
        "http://testserver/api/v1/telemetry",
        error_stream=errors,
    )

    assert error_count == 1
    assert client.get("/api/v1/telemetry/latest").json()["sequence"] == 4
    assert "API returned 422" in errors.getvalue()
