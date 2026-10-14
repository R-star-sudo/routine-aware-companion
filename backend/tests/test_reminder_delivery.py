from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main
from app.main import create_app
from app.schemas.telemetry import SensorValidity, TelemetryRecord


def _record(index: int, now: datetime, *, open_at: bool = False) -> dict[str, object]:
    at = now - timedelta(minutes=30 - index)
    return TelemetryRecord(
        schema_version="1.0", source="simulated", device_id="demo-wearable", sequence=index,
        timestamp=at, acceleration_x_m_s2=0.1, acceleration_y_m_s2=0.1,
        acceleration_z_m_s2=9.81, angular_velocity_x_rad_s=0,
        angular_velocity_y_rad_s=0, angular_velocity_z_rad_s=0,
        pulse_rate_bpm=None, skin_temperature_c=None, room_temperature_c=22,
        room_humidity_percent=45, pillbox_open=open_at,
        sensor_validity=SensorValidity(mpu6050=True, max30102=False, ds18b20=False),
    ).model_dump(mode="json")


def test_feature_api_prepares_causal_order_and_suppresses_model_reminder(monkeypatch) -> None:
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    captured = {}

    def fake_predict(target, features):
        captured.update(features)
        return {
            "target": target, "prediction": 1, "probability": 0.8,
            "decision_threshold": 0.5, "missing_feature_count": 0,
            "model_id": "test-model", "features_used": list(features),
            "synthetic_only": True, "reminder_eligible": False,
        }

    monkeypatch.setattr(main, "predict", fake_predict)
    client = TestClient(create_app())
    response = client.post("/api/v1/features/prepare", json={
        "target": "label_unusual_wake_within_60m",
        "timezone_name": "UTC", "expected_wake_time": "07:30",
        "observations": [_record(i, now, open_at=(i == 2)) for i in range(31)],
    })
    assert response.status_code == 200
    body = response.json()
    assert list(body["features"]) == [
        "feature_hour_sin", "feature_hour_cos", "feature_expected_wake_hour_local",
        "feature_minutes_from_expected_wake", "feature_motion_intensity_m_s2",
        "feature_recent_motion_mean_30m_m_s2", "feature_minutes_since_pillbox_open",
    ]
    assert body["unavailable"] == {}
    assert body["prediction"]["prediction"] == 1
    assert body["reminder"]["eligible"] is False
    assert "disabled" in body["reminder"]["reason"]
    assert captured["feature_minutes_since_pillbox_open"] == 28


def test_feature_api_suppresses_unavailable_features_and_delivers_reason_event() -> None:
    now = datetime.now(timezone.utc)
    client = TestClient(create_app())
    recent = _record(0, now)
    recent["timestamp"] = now.isoformat()
    response = client.post("/api/v1/features/prepare", json={
        "target": "label_unusual_wake_within_60m",
        "timezone_name": None, "expected_wake_time": None,
        "observations": [recent],
    })
    assert response.status_code == 200
    body = response.json()
    assert body["prediction"] is None
    assert body["reminder"]["eligible"] is False
    assert "insufficient_or_gapped_30m_history" in body["reminder"]["reason"]
    listed = client.get("/api/v1/reminders/events").json()
    assert len(listed) == 1
    assert listed[0]["source"] == "model"
    assert listed[0]["delivery_state"] == "not_eligible"


def test_explicit_demo_scenario_passes_policy_then_cooldown_suppresses_repeat() -> None:
    client = TestClient(create_app())
    first = client.post("/api/v1/demo/scenarios", json={"scenario_id": "scheduled_check_in"})
    second = client.post("/api/v1/demo/scenarios", json={"scenario_id": "scheduled_check_in"})
    assert first.status_code == second.status_code == 200
    assert first.json()["eligible"] is True
    assert first.json()["source"] == "simulated_demo"
    assert first.json()["decision_kind"] == "rule_based"
    assert second.json()["eligible"] is False
    assert "cooldown" in second.json()["reason"]
    events = client.get("/api/v1/reminders/events").json()
    assert len(events) == 2
    assert events[0]["delivery_state"] == "not_eligible"
    assert events[1]["delivery_state"] == "not_requested"


def test_eligible_demo_event_uses_mocked_voice_and_tracks_browser_playback(monkeypatch) -> None:
    monkeypatch.setattr(main, "generate_speech", lambda text: b"test-audio")
    client = TestClient(create_app())
    event = client.post("/api/v1/demo/scenarios", json={"scenario_id": "scheduled_check_in"}).json()["event"]
    speech = client.post("/api/v1/voice/speech", json={
        "event_id": event["id"],
        "text": "This is a simulated routine check-in reminder.",
    })
    assert speech.status_code == 200
    assert speech.content == b"test-audio"
    duplicate = client.post("/api/v1/voice/speech", json={
        "event_id": event["id"], "text": "This is a simulated routine check-in reminder.",
    })
    assert duplicate.status_code == 409
    current = client.get("/api/v1/reminders/events").json()[0]
    assert current["speech_state"] == "audio_generated"
    started = client.post(f"/api/v1/reminders/events/{event['id']}/delivery", json={"state": "playback_started"})
    assert started.status_code == 200
    assert started.json()["delivery_state"] == "playback_started"
    finished = client.post(f"/api/v1/reminders/events/{event['id']}/delivery", json={"state": "playback_finished"})
    assert finished.status_code == 200
    assert finished.json()["delivery_state"] == "playback_finished"


def test_provider_failure_updates_event_and_suppressed_event_cannot_speak(monkeypatch) -> None:
    def fail(text):
        raise HTTPException(status_code=503, detail="Voice is not configured.")

    monkeypatch.setattr(main, "generate_speech", fail)
    client = TestClient(create_app())
    first = client.post("/api/v1/demo/scenarios", json={"scenario_id": "scheduled_check_in"}).json()["event"]
    failed = client.post("/api/v1/voice/speech", json={"event_id": first["id"], "text": "Reminder"})
    assert failed.status_code == 503
    events = client.get("/api/v1/reminders/events").json()
    assert events[0]["speech_state"] == "failed"
    assert events[0]["delivery_state"] == "speech_failed"

    suppressed = client.post("/api/v1/demo/scenarios", json={"scenario_id": "scheduled_check_in"}).json()["event"]
    no_speech = client.post("/api/v1/voice/speech", json={"event_id": suppressed["id"], "text": "Reminder"})
    assert no_speech.status_code == 409


def test_playback_state_cannot_be_updated_before_audio_generation() -> None:
    client = TestClient(create_app())
    event = client.post("/api/v1/demo/scenarios", json={"scenario_id": "scheduled_check_in"}).json()["event"]
    response = client.post(f"/api/v1/reminders/events/{event['id']}/delivery", json={"state": "playback_started"})
    assert response.status_code == 409
