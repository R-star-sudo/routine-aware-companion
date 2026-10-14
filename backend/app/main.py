import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from app.services.inference_service import predict
from app.services.personal_baseline import summarize_personal_baseline
from app.services.feature_preparation import prepare_features
from app.services.reminder_policy import DEMO_TARGET, ReminderPolicy
from app.services.voice_service import generate_speech, validate_speech_text
from app.dataset_generator import LABEL_COLUMNS

from app.schemas.telemetry import TelemetryRecord
from app.services.telemetry_storage import TelemetryStorageError, TelemetryStore

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

logger = logging.getLogger(__name__)


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1200)
    event_id: str | None = Field(default=None, max_length=64)


class PredictionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str
    features: dict[str, float | None] = Field(max_length=7)


class PredictionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str
    prediction: int = Field(ge=0, le=1)
    probability: float = Field(ge=0, le=1)
    missing_feature_count: int = Field(ge=0, le=7)
    model_id: str
    model_version: str | None = None
    data_quality: str | None = None
    data_freshness_minutes: float | None = None
    features_used: list[str]
    synthetic_only: bool
    reminder_eligible: bool
    decision_threshold: float = Field(ge=0, le=1)


class ReminderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prediction: PredictionResult
    observed_at: str = Field(max_length=64)


class FeaturePreparationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str
    observations: list[TelemetryRecord] = Field(min_length=1, max_length=500)
    timezone_name: str | None = Field(default=None, max_length=64)
    expected_wake_time: str | None = Field(default=None, max_length=5)


class PersonalBaselineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone_name: str = Field(min_length=1, max_length=64)
    days: list[dict[str, object]] = Field(max_length=365)


class DemoScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(pattern="^scheduled_check_in$")


class DeliveryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str = Field(pattern="^(playback_started|playback_required|playback_finished|playback_failed)$")


def _record_decision(request: Request, decision: dict[str, object], *, title: str) -> dict[str, object]:
    event: dict[str, object] = {
        "id": uuid4().hex,
        "timestamp": decision["decided_at"],
        "target": decision["target"],
        "type": title,
        "description": decision["reason"],
        "reason": decision["reason"],
        "eligible": decision["eligible"],
        "source": decision["source"],
        "decision_kind": decision["decision_kind"],
        "delivery_state": "not_requested" if decision["eligible"] else "not_eligible",
        "speech_state": "not_requested" if decision["eligible"] else "not_eligible",
        "speech_error": None,
        "model_id": decision.get("model_id"),
        "model_version": decision.get("model_version"),
        "prediction_score": decision.get("prediction_score"),
        "data_quality": decision.get("data_quality"),
        "observed_at": decision.get("observed_at"),
        "data_freshness_minutes": decision.get("data_freshness_minutes"),
    }
    with request.app.state.reminder_events_lock:
        request.app.state.reminder_events.insert(0, event)
        del request.app.state.reminder_events[100:]
    return event


def create_app(storage_path: Path | None = None) -> FastAPI:
    application = FastAPI(title="Routine-Aware Companion API")
    application.state.telemetry_store = TelemetryStore(storage_path)
    application.state.reminder_policy = ReminderPolicy()
    application.state.reminder_events = []
    application.state.reminder_events_lock = Lock()
    # Allow only the local Vite development origins. Production deployments
    # should replace these with the exact dashboard origin.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @application.get("/")
    def root() -> dict[str, str]:
        return {"message": "Welcome to Routine-Aware Companion"}

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "healthy"}

    @application.post(
        "/api/v1/telemetry",
        response_model=TelemetryRecord,
        status_code=status.HTTP_201_CREATED,
    )
    def ingest_telemetry(
        record: TelemetryRecord, request: Request
    ) -> TelemetryRecord:
        try:
            request.app.state.telemetry_store.append(record)
        except TelemetryStorageError as exc:
            logger.exception("Failed to store telemetry record")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return record

    @application.get("/api/v1/telemetry/latest", response_model=TelemetryRecord)
    def latest_telemetry(request: Request) -> TelemetryRecord:
        try:
            record = request.app.state.telemetry_store.latest()
        except TelemetryStorageError as exc:
            logger.exception("Failed to retrieve latest telemetry record")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if record is None:
            raise HTTPException(status_code=404, detail="No telemetry records found")
        return record

    @application.get(
        "/api/v1/telemetry/history", response_model=list[TelemetryRecord]
    )
    def telemetry_history(
        request: Request, limit: int = Query(default=100, ge=1, le=500)
    ) -> list[TelemetryRecord]:
        try:
            return request.app.state.telemetry_store.history(limit)
        except TelemetryStorageError as exc:
            logger.exception("Failed to retrieve telemetry history")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @application.post("/api/v1/voice/speech")
    def create_voice_speech(payload: SpeechRequest, request: Request) -> Response:
        """Generate a short ElevenLabs MP3 for the dashboard speaker."""
        spoken_text = validate_speech_text(payload.text)
        event = None
        if payload.event_id:
            with request.app.state.reminder_events_lock:
                event = _find_event_unlocked(request, payload.event_id)
                if not event["eligible"]:
                    raise HTTPException(status_code=409, detail="This reminder is not eligible for speech.")
                if event["speech_state"] not in {"not_requested", "failed"}:
                    raise HTTPException(status_code=409, detail="Speech was already requested for this reminder.")
                event["speech_state"] = "generating"
        try:
            audio = generate_speech(spoken_text)
        except HTTPException as exc:
            if event is not None:
                _update_event(request, payload.event_id, speech_state="failed", speech_error=exc.detail, delivery_state="speech_failed")
            raise
        except Exception as exc:
            if event is not None:
                _update_event(request, payload.event_id, speech_state="failed", speech_error="Speech generation failed unexpectedly.", delivery_state="speech_failed")
            logger.exception("Unexpected speech generation failure")
            raise HTTPException(status_code=502, detail="Speech generation failed.") from exc
        if event is not None:
            _update_event(request, payload.event_id, speech_state="audio_generated", delivery_state="audio_generated")
        return Response(
            content=audio,
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @application.post("/api/v1/predictions")
    def create_prediction(payload: PredictionRequest) -> dict[str, object]:
        return predict(payload.target, payload.features)

    @application.post("/api/v1/reminders/evaluate")
    def evaluate_reminder(payload: ReminderRequest, request: Request) -> dict[str, object]:
        try:
            observed_at = datetime.fromisoformat(payload.observed_at.replace("Z", "+00:00"))
            if observed_at.tzinfo is None or observed_at.utcoffset() is None:
                raise ValueError("timezone required")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="observed_at must be a timezone-aware ISO-8601 timestamp.") from exc
        decision = request.app.state.reminder_policy.decide(
            {**payload.prediction.model_dump(), "source": "model", "decision_kind": "model",
             "data_quality": "complete" if payload.prediction.missing_feature_count == 0 else "incomplete"},
            observed_at=observed_at,
        )
        event = _record_decision(request, decision, title=f"Model decision: {payload.prediction.target}")
        return {**decision, "event": event}

    @application.post("/api/v1/features/prepare")
    def prepare_observation_features(payload: FeaturePreparationRequest, request: Request) -> dict[str, object]:
        if payload.target not in LABEL_COLUMNS:
            raise HTTPException(status_code=422, detail="Unsupported prediction target.")
        prepared = prepare_features(
            payload.observations,
            timezone_name=payload.timezone_name,
            expected_wake_time=payload.expected_wake_time,
        )
        unavailable = prepared["unavailable"]
        if unavailable:
            decision = {
                "target": payload.target,
                "eligible": False,
                "reason": "Prediction suppressed because required features are unavailable: " + ", ".join(f"{name} ({reason})" for name, reason in unavailable.items()),
                "decided_at": datetime.now(timezone.utc).isoformat(),
                "source": "model",
                "decision_kind": "model",
                "data_quality": "unavailable",
                "data_freshness_minutes": max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(prepared["observed_at"].replace("Z", "+00:00")).astimezone(timezone.utc)).total_seconds() / 60) if prepared["observed_at"] else None,
            }
            event = _record_decision(request, decision, title=f"Prediction suppressed: {payload.target}")
            return {**prepared, "target": payload.target, "prediction": None, "reminder": {**decision, "event": event}}
        prediction = predict(payload.target, prepared["features"])
        observed_at = datetime.fromisoformat(prepared["observed_at"].replace("Z", "+00:00"))
        decision = request.app.state.reminder_policy.decide(
            {**prediction, "source": "model", "decision_kind": "model",
             "data_quality": "complete", "data_freshness_minutes": max(0.0, (datetime.now(timezone.utc) - observed_at).total_seconds() / 60)},
            observed_at=observed_at,
        )
        event = _record_decision(request, decision, title=f"Model decision: {payload.target}")
        return {**prepared, "target": payload.target, "prediction": prediction, "reminder": {**decision, "event": event}}

    @application.post("/api/v1/routines/baseline")
    def personal_routine_baseline(payload: PersonalBaselineRequest) -> dict[str, object]:
        try:
            return summarize_personal_baseline(payload.days, timezone_name=payload.timezone_name)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.post("/api/v1/demo/scenarios")
    def run_demo_scenario(payload: DemoScenarioRequest, request: Request) -> dict[str, object]:
        now = datetime.now(timezone.utc)
        decision = request.app.state.reminder_policy.decide(
            {
                "target": DEMO_TARGET,
                "prediction": 1,
                "missing_feature_count": 0,
                "source": "simulated_demo",
                "decision_kind": "rule_based",
                "scenario_id": payload.scenario_id,
            },
            observed_at=now,
            now=now,
        )
        event = _record_decision(request, decision, title="Simulated routine check-in")
        return {**decision, "event": event}

    @application.get("/api/v1/reminders/events")
    def list_reminder_events(request: Request) -> list[dict[str, object]]:
        with request.app.state.reminder_events_lock:
            return list(request.app.state.reminder_events)

    @application.post("/api/v1/reminders/events/{event_id}/delivery")
    def update_delivery(event_id: str, payload: DeliveryUpdate, request: Request) -> dict[str, object]:
        with request.app.state.reminder_events_lock:
            event = _find_event_unlocked(request, event_id)
            if not event["eligible"]:
                raise HTTPException(status_code=409, detail="A suppressed reminder has no delivery state to update.")
            transitions = {
                "audio_generated": {"playback_started", "playback_required", "playback_failed"},
                "playback_required": {"playback_started", "playback_failed"},
                "playback_started": {"playback_started", "playback_finished", "playback_failed"},
                "playback_finished": {"playback_started"},
                "playback_failed": {"playback_started"},
            }
            if payload.state not in transitions.get(event["speech_state"], set()):
                raise HTTPException(status_code=409, detail="Playback state transition is not valid for the current delivery state.")
            event["speech_state"] = payload.state
            event["delivery_state"] = payload.state
            return event

    return application


def _find_event_unlocked(request: Request, event_id: str) -> dict[str, object]:
    event = next((item for item in request.app.state.reminder_events if item["id"] == event_id), None)
    if event is None:
        raise HTTPException(status_code=404, detail="Reminder event was not found.")
    return event


def _update_event(request: Request, event_id: str | None, **values: object) -> None:
    if event_id is None:
        return
    with request.app.state.reminder_events_lock:
        event = _find_event_unlocked(request, event_id)
        event.update(values)


app = create_app()
