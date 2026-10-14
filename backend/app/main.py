import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, status

from app.schemas.telemetry import TelemetryRecord
from app.services.telemetry_storage import TelemetryStorageError, TelemetryStore

logger = logging.getLogger(__name__)


def create_app(storage_path: Path | None = None) -> FastAPI:
    application = FastAPI(title="Routine-Aware Companion API")
    application.state.telemetry_store = TelemetryStore(storage_path)

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

    return application


app = create_app()
