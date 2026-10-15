"""Validated adapter for the current base-station nested JSON serial record."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from math import isfinite

from pydantic import ValidationError

from app.schemas.telemetry import SensorValidity, TelemetryRecord


class FirmwareAdapterError(ValueError):
    pass


def adapt_firmware_line(
    line: str,
    *,
    device_id: str | None,
    boot_time_utc: datetime | None,
) -> dict[str, object] | None:
    """Ignore firmware log lines; convert actual nested telemetry with explicit clock identity.

    The firmware only emits uptime milliseconds and a sequence value. Device ID
    and the UTC time corresponding to uptime zero must therefore be supplied by
    the operator; this adapter never guesses them.
    """
    stripped = line.strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(stripped, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"non-finite JSON value: {value}")))
    except (json.JSONDecodeError, ValueError) as exc:
        raise FirmwareAdapterError(f"invalid firmware JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("src") != "unified":
        raise FirmwareAdapterError("JSON line is not the base-station unified record")
    if not device_id or len(device_id) > 100:
        raise FirmwareAdapterError("a configured device_id is required (1–100 characters)")
    if boot_time_utc is None or boot_time_utc.tzinfo is None or boot_time_utc.utcoffset() is None:
        raise FirmwareAdapterError("an explicit timezone-aware UTC boot_time_utc is required")
    wearable = data.get("wearable")
    base = data.get("base")
    if not isinstance(wearable, dict) or not isinstance(base, dict):
        raise FirmwareAdapterError("firmware JSON must include wearable and base objects")

    def bool_field(source: dict[str, object], name: str) -> bool:
        value = source.get(name)
        if not isinstance(value, bool):
            raise FirmwareAdapterError(f"{name} must be boolean")
        return value

    def finite_number(source: dict[str, object], name: str) -> float:
        value = source.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise FirmwareAdapterError(f"{name} must be a finite number")
        return float(value)

    try:
        sequence = wearable["pkt"]
        uptime_ms = wearable["ts"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or not 0 <= sequence <= 2**32 - 1:
            raise FirmwareAdapterError("wearable.pkt must be an unsigned 32-bit integer")
        if isinstance(uptime_ms, bool) or not isinstance(uptime_ms, int) or not 0 <= uptime_ms <= 2**32 - 1:
            raise FirmwareAdapterError("wearable.ts must be unsigned uptime milliseconds")
        mpu_ok = bool_field(wearable, "mpu_ok")
        max_ok = bool_field(wearable, "max_ok")
        ds_ok = bool_field(wearable, "ds_ok")
        dht_ok = bool_field(base, "dht_ok")
        finger = bool_field(wearable, "finger")
        acceleration = [finite_number(wearable, key) for key in ("ax", "ay", "az")] if mpu_ok else [None] * 3
        gyro = [finite_number(wearable, key) for key in ("gx", "gy", "gz")] if mpu_ok else [None] * 3
        heart_rate = finite_number(wearable, "hr") if max_ok and finger else None
        skin_temperature = finite_number(wearable, "skin_t") if ds_ok else None
        ambient_temperature = finite_number(base, "ambient_t") if dht_ok else None
        humidity = finite_number(base, "humidity") if dht_ok else None
        pillbox = base.get("pillbox")
        if pillbox not in {"OPEN", "CLOSED"}:
            raise FirmwareAdapterError("base.pillbox must be OPEN or CLOSED")
        payload = TelemetryRecord(
            schema_version="1.0",
            source="hardware",
            device_id=device_id,
            sequence=sequence,
            timestamp=boot_time_utc.astimezone(timezone.utc) + timedelta(milliseconds=uptime_ms),
            acceleration_x_m_s2=acceleration[0], acceleration_y_m_s2=acceleration[1], acceleration_z_m_s2=acceleration[2],
            angular_velocity_x_rad_s=gyro[0], angular_velocity_y_rad_s=gyro[1], angular_velocity_z_rad_s=gyro[2],
            pulse_rate_bpm=heart_rate, skin_temperature_c=skin_temperature,
            room_temperature_c=ambient_temperature, room_humidity_percent=humidity,
            pillbox_open=pillbox == "OPEN",
            sensor_validity=SensorValidity(mpu6050=mpu_ok, max30102=max_ok, ds18b20=ds_ok),
        )
    except (KeyError, TypeError, ValidationError) as exc:
        if isinstance(exc, FirmwareAdapterError):
            raise
        raise FirmwareAdapterError(f"invalid unified telemetry fields: {exc}") from exc
    return payload.model_dump(mode="json")
