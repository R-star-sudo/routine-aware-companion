from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.inference_service import ARTIFACT_DIR, FEATURE_ALLOWLIST
from app.services.reminder_policy import ReminderPolicy

FEATURES = {
    "feature_hour_sin": 0.0,
    "feature_hour_cos": 1.0,
    "feature_expected_wake_hour_local": 7.0,
    "feature_minutes_from_expected_wake": -30.0,
    "feature_motion_intensity_m_s2": 0.1,
    "feature_recent_motion_mean_30m_m_s2": 0.1,
    "feature_minutes_since_pillbox_open": 30.0,
}


def test_prediction_uses_saved_artifact_and_approved_features() -> None:
    if not ARTIFACT_DIR.exists():
        return
    client = TestClient(create_app())
    response = client.post("/api/v1/predictions", json={"target": "label_unusual_wake_within_60m", "features": FEATURES})
    assert response.status_code == 200
    body = response.json()
    assert body["target"] == "label_unusual_wake_within_60m"
    assert 0 <= body["probability"] <= 1
    assert body["features_used"] == list(FEATURE_ALLOWLIST)
    assert body["synthetic_only"] is True
    assert body["reminder_eligible"] is False
    assert body["model_version"]
    assert body["data_quality"] == "complete"


def test_prediction_rejects_missing_unknown_and_out_of_range_features() -> None:
    client = TestClient(create_app())
    missing = dict(FEATURES)
    del missing["feature_hour_sin"]
    assert client.post("/api/v1/predictions", json={"target": "label_unusual_wake_within_60m", "features": missing}).status_code == 422
    unknown = {**FEATURES, "label_unusual_wake_within_60m": 1}
    assert client.post("/api/v1/predictions", json={"target": "label_unusual_wake_within_60m", "features": unknown}).status_code == 422
    invalid = {**FEATURES, "feature_hour_sin": 4}
    assert client.post("/api/v1/predictions", json={"target": "label_unusual_wake_within_60m", "features": invalid}).status_code == 422
    assert client.post("/api/v1/predictions", json={"target": "unsupported", "features": FEATURES}).status_code == 422
    incomplete = {**FEATURES, "feature_minutes_since_pillbox_open": None}
    assert client.post("/api/v1/predictions", json={"target": "label_unusual_wake_within_60m", "features": incomplete}).status_code == 422


def test_event_retains_score_model_quality_and_observation_freshness() -> None:
    client = TestClient(create_app())
    body = {"target": "label_unusual_wake_within_60m", "prediction": 1,
            "probability": 0.7, "missing_feature_count": 0, "model_id": "test-model",
            "model_version": "v1", "features_used": list(FEATURE_ALLOWLIST),
            "synthetic_only": True, "reminder_eligible": False, "decision_threshold": 0.5}
    response = client.post("/api/v1/reminders/evaluate", json={**{"prediction": body}, "observed_at": datetime.now(timezone.utc).isoformat()})
    assert response.status_code == 200
    event = response.json()["event"]
    assert event["prediction_score"] == 0.7
    assert event["model_version"] == "v1"
    assert event["data_quality"] == "complete"


def test_prediction_reports_missing_artifact(monkeypatch, tmp_path) -> None:
    import app.services.inference_service as service

    service._load_model.cache_clear()
    monkeypatch.setattr(service, "ARTIFACT_DIR", tmp_path)
    response = TestClient(create_app()).post("/api/v1/predictions", json={"target": "label_unusual_wake_within_60m", "features": FEATURES})
    assert response.status_code == 503
    service._load_model.cache_clear()


def test_reminder_policy_trigger_suppression_and_cooldown() -> None:
    now = datetime.now(timezone.utc)
    policy = ReminderPolicy(enabled={"label_unusual_wake_within_60m": True}, cooldown=timedelta(minutes=30))
    prediction = {"target": "label_unusual_wake_within_60m", "prediction": 1, "missing_feature_count": 0}
    assert policy.decide(prediction, observed_at=now, now=now)["eligible"] is True
    repeated = policy.decide(prediction, observed_at=now, now=now + timedelta(minutes=1))
    assert repeated["eligible"] is False and "cooldown" in repeated["reason"]
    expired = policy.decide(prediction, observed_at=now + timedelta(minutes=31), now=now + timedelta(minutes=31))
    assert expired["eligible"] is True


def test_reminder_policy_disables_targets_and_suppresses_stale_or_incomplete() -> None:
    now = datetime.now(timezone.utc)
    default_policy = ReminderPolicy()
    positive = {"target": "label_unusual_wake_within_60m", "prediction": 1, "missing_feature_count": 0}
    assert "disabled" in default_policy.decide(positive, observed_at=now, now=now)["reason"]
    enabled = ReminderPolicy(enabled={"label_unusual_wake_within_60m": True})
    assert "stale" in enabled.decide(positive, observed_at=now - timedelta(minutes=11), now=now)["reason"]
    assert "incomplete" in enabled.decide({**positive, "missing_feature_count": 1}, observed_at=now, now=now)["reason"]


def test_reminder_api_rejects_timezone_less_observation() -> None:
    response = TestClient(create_app()).post("/api/v1/reminders/evaluate", json={
        "prediction": {
            "target": "label_unusual_wake_within_60m", "prediction": 1,
            "probability": 0.8, "decision_threshold": 0.05,
            "missing_feature_count": 0, "model_id": "test",
            "features_used": list(FEATURE_ALLOWLIST), "synthetic_only": True,
            "reminder_eligible": False,
        },
        "observed_at": "2026-10-14T07:00:00",
    })
    assert response.status_code == 422


def test_reminder_api_uses_default_disabled_target_policy() -> None:
    response = TestClient(create_app()).post("/api/v1/reminders/evaluate", json={
        "prediction": {
            "target": "label_unusual_wake_within_60m", "prediction": 1,
            "probability": 0.8, "decision_threshold": 0.05,
            "missing_feature_count": 0, "model_id": "test",
            "features_used": list(FEATURE_ALLOWLIST), "synthetic_only": True,
            "reminder_eligible": False,
        },
        "observed_at": datetime.now(timezone.utc).isoformat(),
    })
    assert response.status_code == 200
    assert response.json()["eligible"] is False
    assert "disabled" in response.json()["reason"]
