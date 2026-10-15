from fastapi.testclient import TestClient

from app import main


def test_voice_endpoint_returns_mp3_audio_when_provider_succeeds(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(main, "generate_speech", lambda text: b"fake-mp3-audio")
    client = TestClient(main.create_app(tmp_path / "telemetry.csv"))

    response = client.post(
        "/api/v1/voice/speech",
        json={"text": "  Good morning.  "},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.headers["cache-control"] == "no-store"
    assert response.content == b"fake-mp3-audio"


def test_voice_endpoint_rejects_empty_or_whitespace_text(tmp_path) -> None:
    client = TestClient(main.create_app(tmp_path / "telemetry.csv"))

    empty = client.post("/api/v1/voice/speech", json={"text": ""})
    whitespace = client.post("/api/v1/voice/speech", json={"text": "   \n "})

    assert empty.status_code == 422
    assert whitespace.status_code == 422


def test_voice_endpoint_explains_missing_server_configuration(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    client = TestClient(main.create_app(tmp_path / "telemetry.csv"))

    response = client.post(
        "/api/v1/voice/speech",
        json={"text": "Good morning. It is time for your usual morning routine."},
    )

    assert response.status_code == 503
    assert "ELEVENLABS_API_KEY" in response.json()["detail"]
