from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SensorValidity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mpu6050: bool
    max30102: bool
    ds18b20: bool


class TelemetryRecord(BaseModel):
    """Version 1 telemetry; measurement units are encoded in field names."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    source: Literal["simulated", "hardware"]
    device_id: str = Field(min_length=1, max_length=100)
    sequence: int = Field(ge=0)
    timestamp: datetime

    acceleration_x_m_s2: float | None = Field(allow_inf_nan=False)
    acceleration_y_m_s2: float | None = Field(allow_inf_nan=False)
    acceleration_z_m_s2: float | None = Field(allow_inf_nan=False)
    angular_velocity_x_rad_s: float | None = Field(allow_inf_nan=False)
    angular_velocity_y_rad_s: float | None = Field(allow_inf_nan=False)
    angular_velocity_z_rad_s: float | None = Field(allow_inf_nan=False)

    pulse_rate_bpm: float | None = Field(default=None, gt=0, le=300, allow_inf_nan=False)
    skin_temperature_c: float | None = Field(
        default=None, ge=-55, le=125, allow_inf_nan=False
    )
    room_temperature_c: float | None = Field(
        default=None, ge=-50, le=100, allow_inf_nan=False
    )
    room_humidity_percent: float | None = Field(
        default=None, ge=0, le=100, allow_inf_nan=False
    )
    pillbox_open: bool | None = None
    sensor_validity: SensorValidity

    @field_validator("timestamp", mode="before")
    @classmethod
    def require_iso8601_timestamp(cls, value: object) -> object:
        if not isinstance(value, (str, datetime)):
            raise ValueError("timestamp must be an ISO-8601 string")
        return value

    @field_validator("timestamp")
    @classmethod
    def require_timezone_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def check_sensor_validity(self) -> "TelemetryRecord":
        if not self.sensor_validity.mpu6050 and any(
            value is not None
            for value in (
                self.acceleration_x_m_s2,
                self.acceleration_y_m_s2,
                self.acceleration_z_m_s2,
                self.angular_velocity_x_rad_s,
                self.angular_velocity_y_rad_s,
                self.angular_velocity_z_rad_s,
            )
        ):
            raise ValueError("MPU6050 measurements must be null when the sensor is invalid")
        if not self.sensor_validity.max30102 and self.pulse_rate_bpm is not None:
            raise ValueError("pulse_rate_bpm must be null when MAX30102 is invalid")
        if not self.sensor_validity.ds18b20 and self.skin_temperature_c is not None:
            raise ValueError("skin_temperature_c must be null when DS18B20 is invalid")
        return self
