import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from numbers import Real
from pathlib import Path
from typing import TextIO

import httpx


def post_lines(
    lines: Iterable[str],
    client: httpx.Client,
    api_url: str,
    error_stream: TextIO = sys.stderr,
    audio_directory: Path | None = None,
) -> int:
    errors = 0
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("SPEAK,"):
            cue_id = stripped.partition(",")[2]
            try:
                play_cached_voice_cue(
                    cue_id,
                    audio_directory or Path(__file__).resolve().parent / "audio",
                )
            except (OSError, ValueError) as exc:
                print(f"Line {line_number}: voice cue failed: {exc}", file=error_stream)
                errors += 1
            continue
        if stripped.startswith(("TAP,DOUBLE,", "EVENT,")):
            print(f"[base station] {stripped}", file=error_stream)
            continue
        # The ESP32 also writes human-readable startup, link, and status lines.
        # They are not telemetry; silently ignore them so only NDJSON reaches
        # the API. A line that looks like JSON is still validated below.
        if not stripped.startswith("{") and not (
            stripped.startswith("[") and stripped.endswith("]")
        ):
            continue
        try:
            record = json.loads(stripped, parse_constant=_reject_non_finite)
            if not isinstance(record, dict):
                raise ValueError("each JSON line must contain an object")
            record = normalize_base_station_record(record)
            if record is None:
                continue
        except json.JSONDecodeError as exc:
            # ESP32 diagnostics can be bracketed, e.g. [INFO] or [WARN].
            if stripped.startswith("["):
                continue
            print(f"Line {line_number}: invalid JSON record: {exc}", file=error_stream)
            errors += 1
            continue
        except ValueError as exc:
            print(f"Line {line_number}: invalid JSON record: {exc}", file=error_stream)
            errors += 1
            continue

        try:
            response = client.post(api_url, json=record)
            if not response.is_success:
                print(
                    f"Line {line_number}: API returned {response.status_code}: "
                    f"{response.text}",
                    file=error_stream,
                )
                errors += 1
        except httpx.RequestError as exc:
            print(f"Line {line_number}: API request failed: {exc}", file=error_stream)
            errors += 1
    return errors


def play_cached_voice_cue(cue_id: str, audio_directory: Path) -> None:
    """Open a cached cue with the laptop's default audio player.

    The laptop's normal audio output must be routed to the connected speaker.
    The Base Station never joins Wi-Fi or handles the ElevenLabs credentials.
    """
    if not re.fullmatch(r"V[0-9]{2}", cue_id):
        raise ValueError("expected a cue id such as V01")

    audio_path = (audio_directory / f"{cue_id}.mp3").resolve()
    if not audio_path.is_file():
        raise FileNotFoundError(f"cached audio file not found: {audio_path}")

    if os.name == "nt":
        os.startfile(str(audio_path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(audio_path)])
    else:
        subprocess.Popen(["xdg-open", str(audio_path)])


def model_parameters_command(values: str) -> str:
    """Format the agreed laptop-to-base P,mu_g,sigma_g,n,p_break line."""
    parts = [part.strip() for part in values.split(",")]
    if len(parts) != 4:
        raise ValueError("expected mu_g,sigma_g,n,p_break")
    try:
        mu_g, sigma_g, sample_count, p_break = (
            float(parts[0]),
            float(parts[1]),
            int(parts[2]),
            float(parts[3]),
        )
    except ValueError as exc:
        raise ValueError("model parameters must be numeric; n must be an integer") from exc
    if (
        not all(math.isfinite(value) for value in (mu_g, sigma_g, p_break))
        or sigma_g <= 0
        or sample_count <= 0
        or sample_count > 0xFFFFFFFF
        or not 0.0 <= p_break <= 1.0
    ):
        raise ValueError("require finite mu_g, sigma_g > 0, n > 0, and p_break in [0,1]")
    return f"P,{mu_g:.9g},{sigma_g:.9g},{sample_count},{p_break:.9g}\n"


def _validate_base_command(command: str) -> str:
    normalized = command.strip()
    if not normalized or len(normalized) > 100 or any(char in normalized for char in "\r\n\0"):
        raise ValueError("base commands must be one non-empty line of at most 100 characters")
    try:
        normalized.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("base commands must contain ASCII characters only") from exc
    return normalized + "\n"


def _finite_number(value: object) -> float | None:
    if isinstance(value, Real) and not isinstance(value, bool):
        number = float(value)
        if math.isfinite(number):
            return number
    return None


def normalize_base_station_record(record: dict[str, object]) -> dict[str, object] | None:
    """Adapt the base-station's compact NDJSON into telemetry schema 1.0.

    Existing simulator records already use the versioned contract and pass
    through unchanged. Hardware records require the explicitly configured
    simulated clock; records arriving before SIM_CLOCK is set are skipped.
    """
    if record.get("src") != "unified":
        return record

    wearable = record.get("wearable")
    base = record.get("base")
    clock = record.get("clock")
    if not isinstance(wearable, dict) or not isinstance(base, dict) or not isinstance(clock, dict):
        raise ValueError("base-station record is missing wearable, base, or clock data")
    if clock.get("mode") != "simulated":
        return None

    epoch = _finite_number(clock.get("unix_s"))
    packet_id = _finite_number(wearable.get("pkt"))
    if (
        epoch is None
        or epoch < 1
        or epoch > 253402300799
        or not epoch.is_integer()
        or packet_id is None
        or packet_id < 0
        or not packet_id.is_integer()
    ):
        raise ValueError("base-station record has an invalid clock or packet sequence")

    timestamp = datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    mpu_valid = wearable.get("mpu_ok") is True
    max_valid = wearable.get("max_ok") is True
    ds_valid = wearable.get("ds_ok") is True
    finger_detected = wearable.get("finger") is True

    motion_fields = (
        ("acceleration_x_m_s2", "ax"),
        ("acceleration_y_m_s2", "ay"),
        ("acceleration_z_m_s2", "az"),
        ("angular_velocity_x_rad_s", "gx"),
        ("angular_velocity_y_rad_s", "gy"),
        ("angular_velocity_z_rad_s", "gz"),
    )
    normalized: dict[str, object] = {
        "schema_version": "1.0",
        "source": "hardware",
        "device_id": "routine-aware-base-station",
        "sequence": int(packet_id),
        "timestamp": timestamp,
        "sensor_validity": {
            "mpu6050": mpu_valid,
            "max30102": max_valid,
            "ds18b20": ds_valid,
        },
        "pulse_rate_bpm": None,
        "skin_temperature_c": None,
        "room_temperature_c": None,
        "room_humidity_percent": None,
        "pillbox_open": None,
    }

    for output_name, input_name in motion_fields:
        value = _finite_number(wearable.get(input_name)) if mpu_valid else None
        normalized[output_name] = value

    heart_rate = _finite_number(wearable.get("hr"))
    if max_valid and finger_detected and heart_rate is not None and 0 < heart_rate <= 300:
        normalized["pulse_rate_bpm"] = heart_rate

    skin_temperature = _finite_number(wearable.get("skin_t"))
    if ds_valid and skin_temperature is not None and -55 <= skin_temperature <= 125:
        normalized["skin_temperature_c"] = skin_temperature

    if base.get("dht_ok") is True:
        room_temperature = _finite_number(base.get("ambient_t"))
        humidity = _finite_number(base.get("humidity"))
        if room_temperature is not None and -50 <= room_temperature <= 100:
            normalized["room_temperature_c"] = room_temperature
        if humidity is not None and 0 <= humidity <= 100:
            normalized["room_humidity_percent"] = humidity

    pillbox_state = base.get("pillbox")
    if pillbox_state == "OPEN":
        normalized["pillbox_open"] = True
    elif pillbox_state == "CLOSED":
        normalized["pillbox_open"] = False

    return normalized


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"{value} is not a valid JSON number")


def simulation_clock_command(start_time: str, rate: int = 60) -> str:
    """Create the base-station command for a timezone-aware simulation start."""
    normalized = start_time.strip()
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(
            "simulation start must be ISO-8601, for example "
            "2026-10-14T07:00:00+05:30"
        ) from exc

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("simulation start must include a UTC offset or Z")
    if not isinstance(rate, int) or rate <= 0 or rate > 3600:
        raise ValueError("simulation rate must be an integer from 1 through 3600")

    offset_seconds = int(parsed.utcoffset().total_seconds())
    if offset_seconds % 60 != 0 or abs(offset_seconds) > 14 * 60 * 60:
        raise ValueError(
            "simulation UTC offset must be within -14:00 to +14:00 and whole minutes"
        )
    epoch_seconds = int(parsed.astimezone(timezone.utc).timestamp())
    if epoch_seconds <= 0:
        raise ValueError("simulation start must be after 1970-01-01T00:00:00Z")

    return f"SIM_CLOCK,{epoch_seconds},{offset_seconds // 60},{rate}\n"


def _send_startup_commands(serial_port: object, commands: list[str]) -> None:
    if not commands:
        return
    # Opening USB serial resets the ESP32. The ESP32 bootloader, peripheral init,
    # and 7-LED diagnostic sweep take ~3.5s. Wait 3.8s so setup() is complete.
    time.sleep(3.8)
    for command in commands:
        serial_port.write(command.encode("ascii"))  # type: ignore[attr-defined]
        time.sleep(0.15)
    serial_port.flush()  # type: ignore[attr-defined]
    print(f"Sent {len(commands)} startup command(s) to base station.", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Post newline-delimited telemetry to the local API."
    )
    parser.add_argument(
        "--api-url",
        default="http://127.0.0.1:8000/api/v1/telemetry",
        help="Telemetry ingestion endpoint",
    )
    parser.add_argument("--serial-port", help="Optional serial port, for example COM3")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument(
        "--audio-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "audio",
        help="Directory containing cached ElevenLabs cue files such as V01.mp3",
    )
    parser.add_argument(
        "--model-parameters",
        help="Send mu_g,sigma_g,n,p_break to the base and save them in flash",
    )
    parser.add_argument(
        "--speak-test",
        choices=("V01",),
        help="Ask the base to emit SPEAK,V01 once after startup for audio-path testing",
    )
    parser.add_argument(
        "--base-command",
        action="append",
        default=[],
        help="Send a Base Station serial command at startup; repeat for multiple commands",
    )
    parser.add_argument(
        "--simulation-start",
        help=(
            "Set base-station simulation time before reading, using an "
            "ISO-8601 date/time with UTC offset, e.g. "
            "2026-10-14T07:00:00+05:30"
        ),
    )
    parser.add_argument(
        "--simulation-rate",
        type=int,
        help="Whole virtual seconds per real second (default: 60; maximum: 3600)",
    )
    parser.add_argument(
        "--clock-only",
        action="store_true",
        help="Set the clock and print serial output without posting telemetry to the API",
    )
    args = parser.parse_args()
    if args.baudrate < 1:
        parser.error("--baudrate must be positive")
    if args.simulation_start and not args.serial_port:
        parser.error("--simulation-start requires --serial-port")
    if args.simulation_rate is not None and not args.simulation_start:
        parser.error("--simulation-rate requires --simulation-start")
    if args.clock_only and (not args.serial_port or not args.simulation_start):
        parser.error("--clock-only requires --serial-port and --simulation-start")
    if (args.model_parameters or args.speak_test or args.base_command) and not args.serial_port:
        parser.error("base-station commands require --serial-port")

    simulation_rate = args.simulation_rate if args.simulation_rate is not None else 60
    startup_commands: list[str] = []
    if args.simulation_start:
        try:
            startup_commands.append(
                simulation_clock_command(args.simulation_start, simulation_rate)
            )
        except ValueError as exc:
            parser.error(str(exc))
    if args.model_parameters:
        try:
            startup_commands.append(model_parameters_command(args.model_parameters))
        except ValueError as exc:
            parser.error(str(exc))
    if args.speak_test:
        startup_commands.append(f"TEST_SPEAK,{args.speak_test}\n")
    try:
        startup_commands.extend(_validate_base_command(command) for command in args.base_command)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        if args.clock_only:
            import serial

            with serial.Serial(
                args.serial_port, args.baudrate, timeout=1, write_timeout=2
            ) as serial_port:
                _send_startup_commands(serial_port, startup_commands)
                for raw_line in serial_port:
                    sys.stdout.write(raw_line.decode("utf-8", errors="replace"))
                    sys.stdout.flush()
            return 0

        with httpx.Client(timeout=10.0) as client:
            if args.serial_port:
                import serial

                with serial.Serial(
                    args.serial_port, args.baudrate, timeout=1, write_timeout=2
                ) as serial_port:
                    _send_startup_commands(serial_port, startup_commands)
                    errors = post_lines(
                        (line.decode("utf-8", errors="replace") for line in serial_port),
                        client,
                        args.api_url,
                        audio_directory=args.audio_dir,
                    )
            else:
                errors = post_lines(sys.stdin, client, args.api_url)
    except (httpx.RequestError, OSError) as exc:
        print(f"Collector connection failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Collector stopped.", file=sys.stderr)
        return 130
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
