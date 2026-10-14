import csv
import json
import os
import threading
from pathlib import Path

from pydantic import ValidationError

from app.schemas.telemetry import TelemetryRecord

CSV_FIELDS = list(TelemetryRecord.model_fields)
OPTIONAL_FIELDS = (
    "pulse_rate_bpm",
    "skin_temperature_c",
    "room_temperature_c",
    "room_humidity_percent",
    "pillbox_open",
)


class TelemetryStorageError(Exception):
    pass


class TelemetryStore:
    def __init__(self, path: Path | None = None) -> None:
        configured_path = os.environ.get("TELEMETRY_CSV_PATH")
        self.path = path or (
            Path(configured_path)
            if configured_path
            else Path(__file__).resolve().parents[2] / "data" / "telemetry.csv"
        )
        self._lock = threading.Lock()

    def append(self, record: TelemetryRecord) -> None:
        row = record.model_dump(mode="json")
        row["sensor_validity"] = json.dumps(row["sensor_validity"])
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, self.path.open(
                "a", newline="", encoding="utf-8"
            ) as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
                if csv_file.tell() == 0:
                    writer.writeheader()
                writer.writerow(row)
        except (OSError, csv.Error) as exc:
            raise TelemetryStorageError(f"Unable to append telemetry CSV: {exc}") from exc

    def history(self, limit: int = 100) -> list[TelemetryRecord]:
        if not self.path.exists():
            return []
        try:
            with self._lock, self.path.open(
                "r", newline="", encoding="utf-8"
            ) as csv_file:
                records = []
                for line_number, row in enumerate(csv.DictReader(csv_file), start=2):
                    try:
                        for field in OPTIONAL_FIELDS:
                            if row.get(field) == "":
                                row[field] = None
                        row["sensor_validity"] = json.loads(row["sensor_validity"])
                        records.append(TelemetryRecord.model_validate(row))
                    except (KeyError, json.JSONDecodeError, ValidationError, ValueError) as exc:
                        raise TelemetryStorageError(
                            f"Invalid telemetry CSV row {line_number}: {exc}"
                        ) from exc
        except (OSError, csv.Error) as exc:
            raise TelemetryStorageError(f"Unable to read telemetry CSV: {exc}") from exc
        return list(reversed(records[-limit:]))

    def latest(self) -> TelemetryRecord | None:
        records = self.history(limit=1)
        return records[0] if records else None
