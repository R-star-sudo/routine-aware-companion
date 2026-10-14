#!/usr/bin/env python3
"""Live Terminal Dashboard for Routine-Aware Companion.

Streams JSON telemetry from Base Station over USB Serial (e.g. COM5) and renders
an ANSI-formatted, flicker-free terminal dashboard at 4 Hz.
"""

import argparse
import json
import os
import sys
import time

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
    lines.append(
        f"{BOLD}[RF LINK]{RESET} State: {link_col}{BOLD}{link_state}{RESET} | "
        f"Packets: {rf.get('rx', 0):>5} | Drops: {rf.get('drop', 0):>2} | "
        f"Gaps: {rf.get('pkt_gap', 0):>2} | Clock: {DIM}{clock.get('mode', 'unset')}{RESET}"
    )
    lines.append(f"{DIM}{'-' * 72}{RESET}")

    # Section 1: Wearable Node
    mpu_ok = w.get("mpu_ok", False)
    mpu_col = FG_GREEN if mpu_ok else FG_RED
    lines.append(
        f"{BOLD}{FG_YELLOW}[1] WEARABLE NODE{RESET} {DIM}(ESP-NOW Broadcast @ 10 Hz){RESET} "
        f"Pkt #{w.get('pkt', 0)} | Uptime: {w.get('ts', 0) // 1000}s"
    )
    lines.append(
        f"    {BOLD}Motion (MPU6050) [{mpu_col}{'OK' if mpu_ok else 'FAIL'}{RESET}]:\n"
        f"      Accel (m/s²):  X: {FG_WHITE}{w.get('ax', 0.0):+7.2f}{RESET}  "
        f"Y: {FG_WHITE}{w.get('ay', 0.0):+7.2f}{RESET}  "
        f"Z: {FG_WHITE}{w.get('az', 0.0):+7.2f}{RESET}\n"
        f"      Gyro (rad/s):  X: {DIM}{w.get('gx', 0.0):+7.2f}{RESET}  "
        f"Y: {DIM}{w.get('gy', 0.0):+7.2f}{RESET}  "
        f"Z: {DIM}{w.get('gz', 0.0):+7.2f}{RESET}"
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
        "--rate", type=float, default=4.0, help="Screen refresh rate in Hz (default: 4.0)"
    )
    args = parser.parse_args()

    print(f"Opening {args.port} at {args.baud} baud...")
    try:
        ser = serial.Serial(args.port, args.baud, timeout=1.0)
    except Exception as exc:
        print(f"Error opening {args.port}: {exc}", file=sys.stderr)
        return 1

    min_interval = 1.0 / max(1.0, args.rate)
    last_render = 0.0

    print("Connected! Waiting for telemetry stream...")
    try:
        while True:
            raw = ser.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
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
        ser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
