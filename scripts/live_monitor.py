#!/usr/bin/env python3
"""Live Terminal Dashboard for Routine-Aware Companion.

Streams JSON telemetry from Base Station over USB Serial (e.g. COM5) and renders
an ANSI-formatted, flicker-free terminal dashboard at 4 Hz.
"""

import argparse
from datetime import datetime, timezone
import json
import os
import sys
import time
import urllib.request
import urllib.error


def api_record_to_dashboard_data(rec: dict) -> dict:
    """Converts a FastAPI TelemetryRecord into format_dashboard structure."""
    ts_str = rec.get("timestamp", "")
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        unix_s = int(dt.timestamp())
    except Exception:
        unix_s = int(time.time())

    seq = rec.get("sequence", 0)
    validity = rec.get("sensor_validity") or {}
    pill_open = rec.get("pillbox_open")
    pillbox_str = "OPEN" if pill_open is True else ("CLOSED" if pill_open is False else "UNKNOWN")
    hr = rec.get("pulse_rate_bpm")

    return {
        "src": "unified",
        "wearable": {
            "pkt": seq,
            "ts": unix_s * 1000,
            "ax": rec.get("acceleration_x_m_s2"),
            "ay": rec.get("acceleration_y_m_s2"),
            "az": rec.get("acceleration_z_m_s2"),
            "gx": rec.get("angular_velocity_x_rad_s"),
            "gy": rec.get("angular_velocity_y_rad_s"),
            "gz": rec.get("angular_velocity_z_rad_s"),
            "hr": hr,
            "ir": 18000 if hr else 0,
            "finger": hr is not None and hr > 0,
            "skin_t": rec.get("skin_temperature_c"),
            "mpu_ok": validity.get("mpu6050", False),
            "max_ok": validity.get("max30102", False),
            "ds_ok": validity.get("ds18b20", False),
        },
        "base": {
            "ambient_t": rec.get("room_temperature_c"),
            "humidity": rec.get("room_humidity_percent"),
            "dht_ok": rec.get("room_temperature_c") is not None,
            "ldr_raw": 0,
            "ldr_dark": False,
            "ldr_ok": True,
            "pillbox": pillbox_str,
            "leds": {
                "morning": False,
                "afternoon": False,
                "night": False,
                "caretaker": False,
                "path_wc": 0,
                "path_kit": 0,
                "status": True,
            },
        },
        "rf": {
            "rx": seq,
            "drop": 0,
            "pkt_gap": 0,
            "link": "OK",
        },
        "clock": {
            "mode": "simulated",
            "unix_s": unix_s,
            "rate": 1,
        },
    }


def check_backend_available(base_url: str = "http://127.0.0.1:8000") -> bool:
    """Check if the local FastAPI backend is responsive."""
    try:
        req = urllib.request.Request(f"{base_url}/health", headers={"User-Agent": "live_monitor"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def fetch_latest_api(api_url: str = "http://127.0.0.1:8000/api/v1/telemetry/latest") -> tuple[str, dict | None]:
    """Fetch latest telemetry record from the local FastAPI server.

    Returns:
        ("ok", data) if telemetry record exists
        ("empty", None) if backend is running but has no telemetry records yet (HTTP 404)
        ("offline", None) if backend is unreachable
    """
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "live_monitor"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            if resp.status == 200:
                raw = resp.read()
                record = json.loads(raw.decode("utf-8"))
                return "ok", api_record_to_dashboard_data(record)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return "empty", None
        return "offline", None
    except Exception:
        return "offline", None
    return "offline", None


def stream_from_api(api_url: str, fps: float) -> int:
    """Live stream dashboard directly from FastAPI when COM port is locked by dashboard."""
    min_interval = 1.0 / max(1.0, fps)
    last_render = 0.0
    first_render = True
    waiting_dots = 0

    try:
        while True:
            now = time.time()
            if now - last_render < min_interval:
                time.sleep(max(0.01, min_interval - (now - last_render)))
                continue

            status, data = fetch_latest_api(api_url)
            if status == "ok" and data is not None:
                last_render = now
                first_render = False
                dashboard = format_dashboard(data)
                sys.stdout.write(CLEAR_SCREEN + dashboard + "\n")
                sys.stdout.flush()
            elif status == "empty":
                if first_render:
                    waiting_dots = (waiting_dots + 1) % 4
                    dots = "." * waiting_dots + " " * (3 - waiting_dots)
                    sys.stdout.write(
                        f"\r{FG_CYAN}[API STREAM]{RESET} Dashboard is running. Waiting for wearable telemetry packets{dots}"
                    )
                    sys.stdout.flush()
                time.sleep(0.3)
            else:
                # Backend might be momentarily restarting
                time.sleep(0.5)

    except KeyboardInterrupt:
        print(f"\n{FG_YELLOW}Monitor stopped.{RESET}")
    return 0


def parse_clock_inputs(date_str: str | None, time_str: str | None) -> tuple[int, int, str]:
    """Parse date and time inputs into (unix_epoch_seconds, utc_offset_minutes, human_display)."""
    now = datetime.now().astimezone()
    
    # Normalize 'today'
    if date_str and date_str.strip().lower() == "today":
        date_str = now.strftime("%Y-%m-%d")

    # Check if user passed 'now'
    if (not date_str and not time_str) or (time_str and time_str.strip().lower() == "now" and not date_str):
        epoch = int(now.timestamp())
        offset_min = int(now.utcoffset().total_seconds() // 60)
        return epoch, offset_min, now.strftime("%Y-%m-%d %H:%M:%S")

    # Build raw string from date and time
    raw = ""
    if date_str and time_str:
        t = now.strftime("%H:%M:%S") if time_str.strip().lower() == "now" else time_str.strip()
        raw = f"{date_str.strip()} {t}"
    elif date_str and not time_str:
        raw = f"{date_str.strip()} 00:00:00"
    elif time_str and not date_str:
        t = time_str.strip()
        if " " in t or ("T" in t and len(t) > 10):
            raw = t
        else:
            raw = f"{now.strftime('%Y-%m-%d')} {t}"

    raw = raw.replace("T", " ").strip()

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d",
        "%d-%m-%Y",
    ]
    dt = None
    for fmt in formats:
        try:
            dt = datetime.strptime(raw, fmt)
            break
        except ValueError:
            pass

    if dt is None:
        raise ValueError(
            f"Could not parse date/time from date='{date_str}', time='{time_str}'.\n"
            "Supported formats:\n"
            "  --date 2026-10-15 --time 07:00:00\n"
            "  --time '2026-10-15 07:00:00'\n"
            "  --time 07:00:00"
        )

    if dt.tzinfo is None:
        dt = dt.astimezone()

    epoch = int(dt.timestamp())
    offset_min = int(dt.utcoffset().total_seconds() // 60)
    return epoch, offset_min, dt.strftime("%Y-%m-%d %H:%M:%S")

try:
    import serial
except ImportError:
    print(
        "pyserial is required. Run via PlatformIO's python or install pyserial:\n"
        '  & "$env:USERPROFILE\\.platformio\\penv\\Scripts\\python.exe" scripts/live_monitor.py',
        file=sys.stderr,
    )
    sys.exit(1)

# Enable ANSI colors on Windows conhost if needed
if os.name == "nt":
    os.system("")

# ANSI formatting constants
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CLEAR_SCREEN = "\033[2J\033[H"

FG_CYAN = "\033[36m"
FG_GREEN = "\033[32m"
FG_YELLOW = "\033[33m"
FG_RED = "\033[31m"
FG_WHITE = "\033[37m"
FG_MAGENTA = "\033[35m"
FG_BLUE = "\033[34m"
BG_BLUE = "\033[44m"


def format_dashboard(data: dict) -> str:
    w = data.get("wearable", {})
    b = data.get("base", {})
    rf = data.get("rf", {})
    clock = data.get("clock", {})

    link_state = rf.get("link", "UNKNOWN")
    link_col = FG_GREEN if link_state == "OK" else FG_RED

    lines = []
    lines.append(f"{FG_CYAN}{'=' * 72}{RESET}")
    lines.append(
        f"{BG_BLUE}{FG_WHITE}{BOLD}      ROUTINE-AWARE COMPANION — REAL-TIME SENSOR MONITOR      {RESET}"
    )
    lines.append(f"{FG_CYAN}{'=' * 72}{RESET}")

    # RF Link Status
    clock_mode = clock.get("mode", "unset")
    unix_s = clock.get("unix_s", 0)
    if clock_mode == "simulated" and unix_s > 0:
        local_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(unix_s))
        clock_disp = f"{local_time_str} ({clock.get('rate', 1):.0f}x)"
        clock_col = FG_GREEN
    else:
        clock_disp = "UNSET"
        clock_col = FG_YELLOW

    lines.append(
        f"{BOLD}[RF LINK]{RESET} State: {link_col}{BOLD}{link_state}{RESET} | "
        f"Packets: {rf.get('rx', 0):>5} | Drops: {rf.get('drop', 0):>2} | "
        f"Gaps: {rf.get('pkt_gap', 0):>2} | Clock: {clock_col}{clock_disp}{RESET}"
    )
    lines.append(f"{DIM}{'-' * 72}{RESET}")

    # Section 1: Wearable Node
    mpu_ok = w.get("mpu_ok", False)
    mpu_col = FG_GREEN if mpu_ok else FG_RED
    lines.append(
        f"{BOLD}{FG_YELLOW}[1] WEARABLE NODE{RESET} {DIM}(ESP-NOW Broadcast @ 10 Hz){RESET} "
        f"Pkt #{w.get('pkt', 0)} | Uptime: {w.get('ts', 0) // 1000}s"
    )
    ax = w.get("ax")
    ay = w.get("ay")
    az = w.get("az")
    gx = w.get("gx")
    gy = w.get("gy")
    gz = w.get("gz")
    ax_str = f"{ax:+7.2f}" if ax is not None else "   --  "
    ay_str = f"{ay:+7.2f}" if ay is not None else "   --  "
    az_str = f"{az:+7.2f}" if az is not None else "   --  "
    gx_str = f"{gx:+7.2f}" if gx is not None else "   --  "
    gy_str = f"{gy:+7.2f}" if gy is not None else "   --  "
    gz_str = f"{gz:+7.2f}" if gz is not None else "   --  "

    lines.append(
        f"    {BOLD}Motion (MPU6050) [{mpu_col}{'OK' if mpu_ok else 'FAIL'}{RESET}]:\n"
        f"      Accel (m/s²):  X: {FG_WHITE}{ax_str}{RESET}  "
        f"Y: {FG_WHITE}{ay_str}{RESET}  "
        f"Z: {FG_WHITE}{az_str}{RESET}\n"
        f"      Gyro (rad/s):  X: {DIM}{gx_str}{RESET}  "
        f"Y: {DIM}{gy_str}{RESET}  "
        f"Z: {DIM}{gz_str}{RESET}"
    )

    skin_t = w.get("skin_t")
    skin_str = f"{skin_t:.2f} °C" if skin_t is not None else "-- °C"
    ds_ok = w.get("ds_ok", False)
    ds_col = FG_GREEN if ds_ok else FG_RED

    finger = w.get("finger", False)
    contact_str = f"{FG_GREEN}DETECTED{RESET}" if finger else f"{FG_YELLOW}NO CONTACT{RESET}"
    hr = w.get("hr")
    hr_str = f"{hr:.0f} BPM" if hr is not None else "-- BPM"
    ir = w.get("ir", 0)

    lines.append(
        f"    {BOLD}Biometrics:{RESET}\n"
        f"      Skin Temp:     {FG_WHITE}{BOLD}{skin_str}{RESET} [{ds_col}DS18B20: {'OK' if ds_ok else 'FAIL'}{RESET}]\n"
        f"      Heart Rate:    {FG_WHITE}{BOLD}{hr_str}{RESET} (Finger: {contact_str} | IR Raw: {ir})"
    )
    lines.append(f"{DIM}{'-' * 72}{RESET}")

    # Section 2: Base Station Sensors
    dht_ok = b.get("dht_ok", False)
    dht_col = FG_GREEN if dht_ok else FG_RED
    amb_t = b.get("ambient_t")
    hum = b.get("humidity")
    amb_str = f"{amb_t:.1f} °C" if amb_t is not None else "-- °C"
    hum_str = f"{hum:.1f} % RH" if hum is not None else "-- %"

    ldr_raw = b.get("ldr_raw", 0)
    ldr_dark = b.get("ldr_dark", False)
    ldr_state = (
        f"{FG_YELLOW}DARK (Night/Dim){RESET}" if ldr_dark else f"{FG_GREEN}BRIGHT (Day/Lit){RESET}"
    )

    pillbox = b.get("pillbox", "UNKNOWN")
    pill_col = FG_GREEN if pillbox == "CLOSED" else FG_YELLOW

    lines.append(f"{BOLD}{FG_YELLOW}[2] BASE STATION SENSORS{RESET} {DIM}(Local Peripheral Bus){RESET}")
    lines.append(
        f"    Ambient Room:  {FG_WHITE}{BOLD}{amb_str}{RESET} | {FG_WHITE}{BOLD}{hum_str}{RESET} "
        f"[{dht_col}DHT11: {'OK' if dht_ok else 'FAIL'}{RESET}]\n"
        f"    Ambient Light: ADC Level: {FG_WHITE}{ldr_raw:>4}{RESET} | State: {ldr_state}\n"
        f"    Pillbox Lid:   {pill_col}{BOLD}[ {pillbox} ]{RESET} {DIM}(Single Hall Sensor on GPIO 32){RESET}"
    )
    lines.append(f"{DIM}{'-' * 72}{RESET}")

    # Section 3: 7 LEDs
    leds = b.get("leds", {})
    m_on = leds.get("morning", False)
    a_on = leds.get("afternoon", False)
    n_on = leds.get("night", False)
    c_on = leds.get("caretaker", False)
    s_on = leds.get("status", False)
    pw_wc = leds.get("path_wc", 0)
    pw_kit = leds.get("path_kit", 0)

    lines.append(f"{BOLD}{FG_YELLOW}[3] BASE STATION 7-LED CONTROLLER{RESET}")
    lines.append(
        f"    Pill Reminders: Morning: {FG_GREEN if m_on else DIM}{'ON ' if m_on else 'OFF'}{RESET} | "
        f"Afternoon: {FG_GREEN if a_on else DIM}{'ON ' if a_on else 'OFF'}{RESET} | "
        f"Night: {FG_GREEN if n_on else DIM}{'ON ' if n_on else 'OFF'}{RESET} | "
        f"Caretaker: {FG_RED if c_on else DIM}{'ALERT' if c_on else 'OFF'}{RESET}\n"
        f"    Path Lights:    Washroom (WC): {FG_WHITE}{pw_wc:>3}/255{RESET} | "
        f"Kitchen: {FG_WHITE}{pw_kit:>3}/255{RESET} | "
        f"Heartbeat: {FG_CYAN if s_on else DIM}{'PULSE' if s_on else 'IDLE'}{RESET}"
    )
    lines.append(f"{FG_CYAN}{'=' * 72}{RESET}")
    lines.append(f"{DIM}Streaming at 10 Hz from USB serial. Press Ctrl+C to exit.{RESET}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Real-time terminal dashboard for Routine-Aware Companion"
    )
    parser.add_argument("--port", default="COM5", help="Serial port (default: COM5)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument(
        "--fps", type=float, default=10.0, help="Screen refresh rate in Hz (default: 10.0)"
    )
    parser.add_argument(
        "--date", default=None,
        help="Start date (Year-Month-Day), e.g. '2026-10-15' or '15-10-2026' (default: today)"
    )
    parser.add_argument(
        "--time", default="now",
        help="Start time (HH:MM:SS) or full datetime, e.g. '07:00:00' or '2026-10-15 07:00:00' or 'now' (default: 'now')"
    )
    parser.add_argument(
        "--rate", type=int, default=1,
        help="Time passing speed multiplier, e.g. 1 = real-time, 10 = 10x (10s in 1s), 60 = 60x (1 min in 1s) (default: 1)"
    )
    parser.add_argument(
        "--no-clock", action="store_true",
        help="Do not initialize or alter the Base Station clock"
    )
    parser.add_argument(
        "--api-url", default="http://127.0.0.1:8000/api/v1/telemetry/latest",
        help="FastAPI endpoint to stream from if COM port is in use"
    )
    parser.add_argument(
        "inputs", nargs="*",
        help="Quick smart inputs: time (07:00:00), date (2026-10-15), rate (10 or 10x), or port (COM5) in any order"
    )
    args = parser.parse_args()

    # Smart positional argument resolver
    for item in args.inputs:
        val = item.strip()
        if not val:
            continue
        lower = val.lower()
        if lower.startswith("com") and (len(lower) == 3 or lower[3:].isdigit()):
            args.port = val.upper()
        elif lower.endswith("x") and lower[:-1].isdigit():
            args.rate = int(lower[:-1])
        elif val.isdigit():
            args.rate = int(val)
        elif ":" in val and " " not in val:
            args.time = val
        elif ("-" in val or "/" in val) and " " not in val:
            args.date = val
        elif " " in val:
            args.time = val
        elif lower == "now":
            args.time = "now"
        elif lower == "today":
            args.date = "today"

    print(f"Opening {args.port} at {args.baud} baud...")
    ser = None
    try:
        ser = serial.Serial(args.port, args.baud, timeout=1.0)
    except Exception as exc:
        # Check if backend API is running and streaming
        status, _ = fetch_latest_api(args.api_url)
        backend_alive = (status in ("ok", "empty")) or check_backend_available("http://127.0.0.1:8000")
        if backend_alive:
            print(f"\n{FG_CYAN}[INFO] {args.port} is in use (by Dashboard Collector).{RESET}")
            print(f"{FG_GREEN}>> Auto-switching: streaming live telemetry from Backend API ({args.api_url})...{RESET}\n")
            time.sleep(0.5)
            return stream_from_api(args.api_url, args.fps)

        print(f"Error opening {args.port}: {exc}", file=sys.stderr)
        print("Tip: If Dashboard is starting, wait 2 seconds or verify COM port.", file=sys.stderr)
        print("Tip: If another terminal or script has COM port open, close it first.", file=sys.stderr)
        return 1

    min_interval = 1.0 / max(1.0, args.fps)
    last_render = 0.0

    if not args.no_clock:
        try:
            epoch, offset_minutes, display_time = parse_clock_inputs(args.date, args.time)
            clock_cmd = f"SIM_CLOCK,{epoch},{offset_minutes},{args.rate}\n"
            # ESP32 resets on serial open; wait 2.5s for bootloader and setup() to complete
            print(f"Awaiting Base Station ready ({display_time}, {args.rate}x speed)...")
            time.sleep(2.5)
            ser.write(clock_cmd.encode("ascii"))
            ser.flush()
            time.sleep(0.2)
        except Exception as exc:
            print(f"[WARN] Could not set clock: {exc}", file=sys.stderr)

    print("Connected! Waiting for telemetry stream...")

    try:
        while True:
            raw = ser.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()

            # Show Base Station status heartbeats while waiting for first wearable packet
            if line.startswith("[STATUS]") and last_render == 0.0:
                sys.stdout.write(f"\r{FG_YELLOW}{line[:80]}{RESET}")
                sys.stdout.flush()
                continue

            if not line.startswith("{"):
                continue

            now = time.time()
            if now - last_render < min_interval:
                continue
            last_render = now

            try:
                data = json.loads(line)
            except Exception:
                continue

            if data.get("src") == "unified":
                dashboard = format_dashboard(data)
                sys.stdout.write(CLEAR_SCREEN + dashboard + "\n")
                sys.stdout.flush()

    except KeyboardInterrupt:
        print(f"\n{FG_YELLOW}Monitor stopped.{RESET}")
    finally:
        if ser is not None and ser.is_open:
            ser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
