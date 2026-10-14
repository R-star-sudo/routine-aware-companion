import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.services.voice_service import generate_speech, validate_speech_text

from app.schemas.telemetry import TelemetryRecord
from app.services.telemetry_storage import TelemetryStorageError, TelemetryStore

logger = logging.getLogger(__name__)


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1200)



def create_app(storage_path: Path | None = None) -> FastAPI:
    application = FastAPI(title="Routine-Aware Companion API")
    application.state.telemetry_store = TelemetryStore(storage_path)
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
    def create_voice_speech(payload: SpeechRequest) -> Response:
        """Generate a short ElevenLabs MP3 for the dashboard speaker."""
        spoken_text = validate_speech_text(payload.text)
        audio = generate_speech(spoken_text)
        return Response(
            content=audio,
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return application


app = create_app()
