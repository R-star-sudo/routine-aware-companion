import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "./Mpu6050Lab.css";

type DataSource = "api" | "serial";
type Axis = "ax" | "ay" | "az" | "gx" | "gy" | "gz";
type Reading = Record<Axis, number>;
type TaskStatus = "pending" | "active" | "completed" | "incomplete" | "cancelled";
type CalibrationMode = "idle" | "stationary" | "six-position";

interface SerialPortLike {
  open(options: { baudRate: number }): Promise<void>;
  close(): Promise<void>;
  readable: ReadableStream<Uint8Array> | null;
}

interface NavigatorWithSerial extends Navigator {
  serial?: { requestPort(): Promise<SerialPortLike> };
}

interface TaskDefinition {
  name: string;
  instructions: string;
}

interface LabSample {
  timestamp: string;
  capturedAt: string;
  source: DataSource;
  telemetrySource: string | null;
  sourceSequence: number | null;
  deviceId: string | null;
  elapsedMs: number;
  raw: Reading;
  calibrated: Reading;
  calibrationVersion: number;
  offsets: Reading;
}

interface LabTask extends TaskDefinition {
  status: TaskStatus;
  startedAt: string | null;
  startedCapturedAt: string | null;
  endedAt: string | null;
  endedCapturedAt: string | null;
  startedMonotonic: number | null;
  samples: LabSample[];
}

interface LabSession {
  id: string;
  status: "in-progress" | "completed" | "stopped";
  startedAt: string;
  startedCapturedAt: string;
  endedAt: string | null;
  endedCapturedAt: string | null;
  activeTaskIndex: number | null;
  tasks: LabTask[];
}

const AXES: Axis[] = ["ax", "ay", "az", "gx", "gy", "gz"];
const ACCEL_AXES = ["ax", "ay", "az"] as const;
const GYRO_AXES = ["gx", "gy", "gz"] as const;
const LABELS: Record<Axis, string> = {
  ax: "Accel X", ay: "Accel Y", az: "Accel Z",
  gx: "Gyro X", gy: "Gyro Y", gz: "Gyro Z",
};
const CSV_AXIS_NAMES: Record<Axis, string> = {
  ax: "acceleration_x_m_s2", ay: "acceleration_y_m_s2", az: "acceleration_z_m_s2",
  gx: "angular_velocity_x_rad_s", gy: "angular_velocity_y_rad_s", gz: "angular_velocity_z_rad_s",
};
const ZERO: Reading = { ax: 0, ay: 0, az: 0, gx: 0, gy: 0, gz: 0 };
const SIX_POSITIONS = [
  { axis: "ax" as const, direction: 1, label: "+X up" },
  { axis: "ax" as const, direction: -1, label: "−X up" },
  { axis: "ay" as const, direction: 1, label: "+Y up" },
  { axis: "ay" as const, direction: -1, label: "−Y up" },
  { axis: "az" as const, direction: 1, label: "+Z up" },
  { axis: "az" as const, direction: -1, label: "−Z up" },
];
const INITIAL_TASKS: TaskDefinition[] = [
  { name: "Stationary baseline", instructions: "Place the sensor on a stable, level surface and keep it still." },
  { name: "Gentle movement", instructions: "Slowly tilt the sensor through a comfortable range." },
  { name: "Rotation", instructions: "Rotate the sensor steadily around one axis." },
];

function parseReading(line: string): Reading | null {
  const asReading = (values: unknown[]): Reading | null => {
    if (values.length < AXES.length) return null;
    const firstSix = values.slice(0, AXES.length);
    if (firstSix.some((value) => {
      if (typeof value === "number") return false;
      return typeof value !== "string" || value.trim() === "";
    })) return null;
    const numbers = firstSix.map(Number);
    if (numbers.some((value) => !Number.isFinite(value))) return null;
    return Object.fromEntries(AXES.map((axis, index) => [axis, numbers[index]])) as Reading;
  };

  if (!line.startsWith("{")) return null;
  try {
    const parsed: unknown = JSON.parse(line);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return null;
    const root = parsed as Record<string, unknown>;
    const wearable = root.wearable;
    if (root.src !== "unified" || typeof wearable !== "object" || wearable === null || Array.isArray(wearable)) return null;
    const sensor = wearable as Record<string, unknown>;
    if (sensor.mpu_ok !== true) return null;
    return asReading(AXES.map((axis) => sensor[axis]));
  } catch {
    return null;
  }
}

function mean(samples: Reading[]): Reading {
  return Object.fromEntries(
    AXES.map((axis) => [axis, samples.reduce((sum, sample) => sum + sample[axis], 0) / samples.length]),
  ) as Reading;
}

function csvCell(value: unknown): string {
  const text = value === null || value === undefined
    ? ""
    : typeof value === "object" ? JSON.stringify(value) ?? "" : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

const TELEMETRY_CSV_FIELDS = [
  "schema_version", "source", "device_id", "sequence", "timestamp",
  "acceleration_x_m_s2", "acceleration_y_m_s2", "acceleration_z_m_s2",
  "angular_velocity_x_rad_s", "angular_velocity_y_rad_s", "angular_velocity_z_rad_s",
  "pulse_rate_bpm", "skin_temperature_c", "room_temperature_c",
  "room_humidity_percent", "pillbox_open", "sensor_validity",
];

function saveCsvFile(csv: string, filePrefix: string) {
  const url = URL.createObjectURL(new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  const exportedAt = new Date().toISOString().replace(/[:.]/g, "-");
  link.href = url;
  link.download = `${filePrefix}-${exportedAt}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function downloadTelemetryCsv(records: Record<string, unknown>[]) {
  const rows = records.map((record) => TELEMETRY_CSV_FIELDS.map((field) => csvCell(record[field])).join(","));
  saveCsvFile(`${TELEMETRY_CSV_FIELDS.join(",")}\r\n${rows.join("\r\n")}\r\n`, "routine-aware-telemetry");
}

function elapsedLabel(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function timestampLabel(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "—";
}

function elapsedBetween(later: string | null, earlier: string | null): number | string {
  if (!later || !earlier) return "";
  const elapsed = Date.parse(later) - Date.parse(earlier);
  return Number.isFinite(elapsed) ? elapsed : "";
}

function downloadCsv(sessions: LabSession[]) {
  const headers = [
    "session_id", "session_started_at", "session_started_captured_at",
    "session_ended_at", "session_ended_captured_at", "session_status",
    "session_sensor_clock_duration_ms", "session_wall_duration_ms",
    "task_id", "task_number", "task_total_count", "label", "task_name", "task_instructions", "task_status",
    "task_started_at", "task_started_captured_at", "task_ended_at", "task_ended_captured_at",
    "task_sensor_clock_duration_ms", "task_wall_duration_ms",
    "sample_index", "sample_timestamp", "sample_captured_at",
    "task_relative_elapsed_ms", "task_sensor_clock_elapsed_ms",
    "capture_source", "telemetry_source", "source_sequence", "device_id",
    ...AXES.map((axis) => `raw_${CSV_AXIS_NAMES[axis]}`),
    ...AXES.map((axis) => `calibrated_${CSV_AXIS_NAMES[axis]}`),
    "calibration_version", ...AXES.map((axis) => `offset_${CSV_AXIS_NAMES[axis]}`),
  ];
  const rows = sessions.flatMap((session) =>
    session.tasks.flatMap((task, taskIndex) =>
      task.samples.map((sample, sampleIndex) => [
        session.id, session.startedAt, session.startedCapturedAt,
        session.endedAt ?? "", session.endedCapturedAt ?? "", session.status,
        elapsedBetween(session.endedAt, session.startedAt),
        elapsedBetween(session.endedCapturedAt, session.startedCapturedAt),
        `${session.id}-task-${taskIndex + 1}`, taskIndex + 1, session.tasks.length, task.name, task.name,
        task.instructions, task.status,
        task.startedAt ?? "", task.startedCapturedAt ?? "",
        task.endedAt ?? "", task.endedCapturedAt ?? "",
        elapsedBetween(task.endedAt, task.startedAt),
        elapsedBetween(task.endedCapturedAt, task.startedCapturedAt),
        sampleIndex + 1, sample.timestamp, sample.capturedAt, sample.elapsedMs,
        elapsedBetween(sample.timestamp, task.startedAt),
        sample.source, sample.telemetrySource ?? "", sample.sourceSequence ?? "", sample.deviceId ?? "",
        ...AXES.map((axis) => sample.raw[axis]),
        ...AXES.map((axis) => sample.calibrated[axis]),
        sample.calibrationVersion,
        ...AXES.map((axis) => sample.offsets[axis]),
      ]),
    ),
  );
  const csv = [headers, ...rows].map((row) => row.map(csvCell).join(",")).join("\r\n");
  saveCsvFile(csv, "mpu6050-lab-dataset");
}

function samplesIn(session: LabSession): number {
  return session.tasks.reduce((count, task) => count + task.samples.length, 0);
}

const API_BASE = (import.meta.env.VITE_API_BASE_URL?.trim() || "http://127.0.0.1:8000").replace(/\/$/, "");
const API_STALE_AFTER_MS = 5000;

function readingFromTelemetry(record: Record<string, unknown>): Reading | null {
  const validity = record.sensor_validity;
  if (!validity || typeof validity !== "object" || !(validity as Record<string, unknown>).mpu6050) return null;
  const ax = record.acceleration_x_m_s2;
  const ay = record.acceleration_y_m_s2;
  const az = record.acceleration_z_m_s2;
  const gx = record.angular_velocity_x_rad_s;
  const gy = record.angular_velocity_y_rad_s;
  const gz = record.angular_velocity_z_rad_s;
  if ([ax, ay, az, gx, gy, gz].some((v) => typeof v !== "number" || !Number.isFinite(v as number))) return null;
  return { ax: ax as number, ay: ay as number, az: az as number, gx: gx as number, gy: gy as number, gz: gz as number };
}

export default function Mpu6050Lab({ active }: { active: boolean }) {
  const [dataSource, setDataSource] = useState<DataSource>("api");
  const [apiConnected, setApiConnected] = useState(false);
  const [apiTimestamp, setApiTimestamp] = useState<string | null>(null);
  const [apiLastSampleAt, setApiLastSampleAt] = useState<number | null>(null);
  const [apiRecordCount, setApiRecordCount] = useState(0);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState("");
  const [latest, setLatest] = useState<Reading | null>(null);
  const [invalidLines, setInvalidLines] = useState(0);
  const [baudRate, setBaudRate] = useState("115200");
  const [offsets, setOffsets] = useState<Reading>(ZERO);
  const [draftOffsets, setDraftOffsets] = useState<Record<Axis, string>>({
    ax: "0", ay: "0", az: "0", gx: "0", gy: "0", gz: "0",
  });
  const [calibrationVersion, setCalibrationVersion] = useState(1);
  const [calibrationMode, setCalibrationMode] = useState<CalibrationMode>("idle");
  const [gravityTarget, setGravityTarget] = useState("");
  const [positionMeans, setPositionMeans] = useState<Reading[]>([]);
  const [capturingPosition, setCapturingPosition] = useState(false);
  const [calibrationMessage, setCalibrationMessage] = useState("");
  const [taskCount, setTaskCount] = useState(3);
  const [definitions, setDefinitions] = useState(INITIAL_TASKS);
  const [selectedTask, setSelectedTask] = useState(0);
  const [sessions, setSessions] = useState<LabSession[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [clock, setClock] = useState(() => Date.now());

  const offsetsRef = useRef(offsets);
  const versionRef = useRef(calibrationVersion);
  const portRef = useRef<SerialPortLike | null>(null);
  const readerRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null);
  const sessionIdRef = useRef(sessionId);
  const captureRef = useRef<Reading[] | null>(null);
  const timerRef = useRef<number | null>(null);
  const positionMeansRef = useRef<Reading[]>([]);
  const wasConnectedRef = useRef(false);
  const lastApiSeqRef = useRef<number | null>(null);
  const apiTimestampRef = useRef<string | null>(null);
  const apiHistoryRef = useRef<Record<string, unknown>[]>([]);
  const pollInFlightRef = useRef(false);

  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => { offsetsRef.current = offsets; }, [offsets]);
  useEffect(() => { versionRef.current = calibrationVersion; }, [calibrationVersion]);
  useEffect(() => { sessionIdRef.current = sessionId; }, [sessionId]);

  const receiveReading = useCallback((
    reading: Reading,
    source: DataSource = "serial",
    sourceTimestamp: string | null = null,
    sourceSequence: number | null = null,
    deviceId: string | null = null,
    telemetrySource: string | null = null,
  ) => {
    setLatest(reading);
    captureRef.current?.push(reading);
    const id = sessionIdRef.current;
    if (!id) return;
    const now = performance.now();
    const capturedAt = new Date().toISOString();
    const sampleOffsets = { ...offsetsRef.current };
    const sample: LabSample = {
      timestamp: sourceTimestamp ?? capturedAt,
      capturedAt,
      source,
      telemetrySource,
      sourceSequence,
      deviceId,
      elapsedMs: 0,
      raw: reading,
      calibrated: Object.fromEntries(
        AXES.map((axis) => [axis, reading[axis] - sampleOffsets[axis]]),
      ) as Reading,
      calibrationVersion: versionRef.current,
      offsets: sampleOffsets,
    };
    setSessions((previous) => previous.map((session) => {
      if (session.id !== id) return session;
      const index = session.activeTaskIndex;
      if (session.status !== "in-progress" || index === null) return session;
      const task = session.tasks[index];
      if (task?.status !== "active" || task.startedMonotonic === null) return session;
      const tasks = session.tasks.slice();
      tasks[index] = {
        ...task,
        samples: [...task.samples, { ...sample, elapsedMs: Math.max(0, Math.round(now - task.startedMonotonic)) }],
      };
      return { ...session, tasks };
    }));
  }, []);

  // ── API polling: catch up all new telemetry rows so task captures retain samples between polls ──
  const pollApi = useCallback(async () => {
    if (pollInFlightRef.current) return;
    pollInFlightRef.current = true;
    try {
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 3000);
      const res = await fetch(`${API_BASE}/api/v1/telemetry/history?limit=500`, {
        cache: "no-store",
        signal: controller.signal,
      }).finally(() => window.clearTimeout(timeout));
      if (!res.ok) { setApiConnected(false); return; }
      const payload: unknown = await res.json();
      if (!Array.isArray(payload)) { setApiConnected(false); return; }
      const records = payload
        .filter((record): record is Record<string, unknown> =>
          typeof record === "object" && record !== null && !Array.isArray(record)
          && typeof (record as Record<string, unknown>).sequence === "number"
          && Number.isInteger((record as Record<string, unknown>).sequence),
        );
      // The API returns newest-first. Preserve that order for export and reverse it
      // only when replaying records so sensor packet-counter resets are detectable.
      apiHistoryRef.current = records;
      setApiRecordCount(records.length);

      if (records.length === 0) { setApiConnected(true); return; }
      const chronological = [...records].reverse();
      let currentBootStart = 0;
      for (let index = 1; index < chronological.length; index += 1) {
        if (Number(chronological[index].sequence) < Number(chronological[index - 1].sequence)) {
          currentBootStart = index;
        }
      }
      const currentBootRecords = chronological.slice(currentBootStart);
      const newest = records[0];
      const newestSequence = Number(newest.sequence);
      const newestTimestamp = typeof newest.timestamp === "string" ? newest.timestamp : null;

      const previousSequence = lastApiSeqRef.current;
      if (previousSequence === null) {
        // Seed the cursor with the newest existing row; don't attach pre-session history to a new task.
        lastApiSeqRef.current = newestSequence;
        setApiTimestamp(newestTimestamp);
        apiTimestampRef.current = newestTimestamp;
        const reading = readingFromTelemetry(newest);
        if (reading) setLatest(reading);
        setApiConnected(true);
        return;
      }

      const sequenceReset = newestSequence < previousSequence;
      // The simulated clock can be deliberately moved backwards, so sequence is
      // the reset signal; timestamps alone must not replay old task samples.
      const newBoot = sequenceReset;
      const newRecords = newBoot
        ? currentBootRecords
        : currentBootRecords.filter((record) => Number(record.sequence) > previousSequence);

      for (const record of newRecords) {
        const sequence = Number(record.sequence);
        lastApiSeqRef.current = sequence;
        const timestamp = typeof record.timestamp === "string" ? record.timestamp : null;
        setApiTimestamp(timestamp);
        apiTimestampRef.current = timestamp;
        const reading = readingFromTelemetry(record);
        if (!reading) continue;
        setApiLastSampleAt(Date.now());
        receiveReading(reading, "api", timestamp, sequence,
          typeof record.device_id === "string" ? record.device_id : null,
          typeof record.source === "string" ? record.source : null);
      }
      // Record a reset even when the newest packets do not yet contain valid MPU data.
      if (newBoot && newRecords.length === 0) {
        lastApiSeqRef.current = newestSequence;
        setApiTimestamp(newestTimestamp);
        apiTimestampRef.current = newestTimestamp;
      }
      setApiConnected(true);
    } catch {
      setApiConnected(false);
    } finally {
      pollInFlightRef.current = false;
    }
  }, [receiveReading]);

  useEffect(() => {
    if (!active || dataSource !== "api") { setApiConnected(false); return; }
    // Initial poll immediately
    void pollApi();
    const timer = window.setInterval(() => void pollApi(), 500);
    return () => { window.clearInterval(timer); };
  }, [active, dataSource, pollApi]);

  // Effective connection status: either serial or API
  const apiFresh = apiConnected && apiLastSampleAt !== null && clock - apiLastSampleAt <= API_STALE_AFTER_MS;
  const isConnected = dataSource === "serial" ? connected : apiFresh;

  const currentSession = sessions.find((item) => item.id === sessionId) ?? null;
  const activeTask = currentSession?.activeTaskIndex == null
    ? null
    : currentSession.tasks[currentSession.activeTaskIndex];
  const activeElapsed = activeTask?.startedMonotonic == null
    ? 0
    : performance.now() - activeTask.startedMonotonic;
  const allSamples = useMemo(() => sessions.reduce((sum, session) => sum + samplesIn(session), 0), [sessions]);
  const shownSession = currentSession ?? sessions.at(-1) ?? null;
  const shownTasks = shownSession?.tasks ?? [];
  const completedTasks = shownTasks.filter((task) => task.status === "completed").length;
  const incompleteTasks = shownTasks.filter((task) => task.status === "incomplete" || task.status === "cancelled").length;
  const liveCalibrated = latest
    ? Object.fromEntries(AXES.map((axis) => [axis, latest[axis] - offsets[axis]])) as Reading
    : null;

  function exportTelemetryHistory() {
    const records = apiHistoryRef.current;
    if (records.length === 0) {
      setError("The telemetry API has no records to export yet. Start the collector and wait for sensor data.");
      return;
    }
    try {
      downloadTelemetryCsv(records);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? `Could not download telemetry CSV: ${cause.message}` : "Could not download telemetry CSV.");
    }
  }

  function updateSession(id: string, update: (session: LabSession) => LabSession) {
    setSessions((previous) => previous.map((session) => session.id === id ? update(session) : session));
  }

  function finishSession(stopActive: boolean) {
    const id = sessionIdRef.current;
    if (!id) return;
    const endedAt = (dataSource === "api" && apiTimestampRef.current)
      ? apiTimestampRef.current
      : new Date().toISOString();
    const endedCapturedAt = new Date().toISOString();
    updateSession(id, (session) => session.status !== "in-progress" ? session : ({
      ...session,
      status: "stopped",
      endedAt,
      endedCapturedAt,
      activeTaskIndex: null,
      tasks: session.tasks.map((task) => {
        if (task.status === "active" && stopActive) return { ...task, status: "incomplete", endedAt, endedCapturedAt };
        if (task.status === "pending") return { ...task, status: "cancelled" };
        return task;
      }),
    }));
  }

  function disconnectForNavigation() {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = null;
    captureRef.current = null;
    positionMeansRef.current = [];
    setPositionMeans([]);
    setCapturingPosition(false);
    setCalibrationMode("idle");
    wasConnectedRef.current = false;
    void readerRef.current?.cancel().catch((cause: unknown) => {
      setError(cause instanceof Error ? cause.message : "Could not close the serial reader.");
    });
    if (sessionIdRef.current) finishSession(true);
  }

  useEffect(() => {
    if (!active) disconnectForNavigation();
  }, [active]);

  useEffect(() => {
    if (connected) return;
    if (wasConnectedRef.current) {
      wasConnectedRef.current = false;
      finishSession(true);
    }
    if (calibrationMode !== "idle" && dataSource === "serial") {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      timerRef.current = null;
      captureRef.current = null;
      setCalibrationMode("idle");
      setCapturingPosition(false);
      setCalibrationMessage("Automatic calibration stopped because the serial device disconnected.");
    }
  }, [connected, calibrationMode, dataSource]);

  useEffect(() => {
    if (!sessions.some((session) => samplesIn(session) > 0)) return;
    const protectSamples = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", protectSamples);
    return () => window.removeEventListener("beforeunload", protectSamples);
  }, [sessions]);

  useEffect(() => () => {
    void readerRef.current?.cancel();
    void portRef.current?.close();
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
  }, []);

  async function connect() {
    setError("");
    if (!active) return;
    const serial = (navigator as NavigatorWithSerial).serial;
    if (!serial) {
      setError("Web Serial is unavailable in this browser. Use a supported Chromium browser over HTTPS or localhost.");
      return;
    }
    const baud = Number(baudRate);
    if (!Number.isInteger(baud) || baud < 300 || baud > 4000000) {
      setError("Enter a valid baud rate between 300 and 4,000,000.");
      return;
    }

    try {
      const port = await serial.requestPort();
      await port.open({ baudRate: baud });
      portRef.current = port;
      wasConnectedRef.current = true;
      const reader = port.readable?.getReader();
      if (!reader) {
        wasConnectedRef.current = false;
        throw new Error("The selected serial device has no readable stream.");
      }
      readerRef.current = reader;
      setConnected(true);
      setLatest(null);
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split(/\r?\n/);
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.trim()) continue;
          const reading = parseReading(line.trim());
          if (reading) receiveReading(reading, "serial", null, null, null, "hardware");
          else setInvalidLines((count) => count + 1);
        }
      }
    } catch (cause) {
      if (!(cause instanceof DOMException && cause.name === "AbortError")) {
        setError(cause instanceof Error ? cause.message : "Unable to read from the selected serial device.");
      }
    } finally {
      readerRef.current?.releaseLock();
      readerRef.current = null;
      try {
        await portRef.current?.close();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "Unable to close the serial device.");
      }
      portRef.current = null;
      setConnected(false);
      setLatest(null);
    }
  }

  async function disconnect() {
    try {
      await readerRef.current?.cancel();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to stop the serial reader.");
    }
  }

  function saveManualOffsets() {
    const next = {} as Reading;
    for (const axis of AXES) {
      if (!draftOffsets[axis].trim()) {
        setError(`Calibration offset for ${LABELS[axis]} must be a finite number.`);
        return;
      }
      const value = Number(draftOffsets[axis]);
      if (!Number.isFinite(value)) {
        setError(`Calibration offset for ${LABELS[axis]} must be a finite number.`);
        return;
      }
      next[axis] = value;
    }
    saveOffsets(next, "Manual calibration offsets saved.");
  }

  function saveOffsets(next: Reading, message: string) {
    setError("");
    setOffsets(next);
    offsetsRef.current = next;
    setDraftOffsets(Object.fromEntries(AXES.map((axis) => [axis, String(next[axis])])) as Record<Axis, string>);
    const nextVersion = versionRef.current + 1;
    setCalibrationVersion(nextVersion);
    versionRef.current = nextVersion;
    setCalibrationMessage(`${message} Applied as calibration v${nextVersion}.`);
    setCalibrationMode("idle");
    setCapturingPosition(false);
  }

  function beginStationaryCalibration() {
    if (!isConnected || !latest || currentSession?.status === "in-progress") return;
    if (!gravityTarget.trim() || !Number.isFinite(Number(gravityTarget))) {
      setCalibrationMessage("Enter the signed level Z-axis gravity value in m/s².");
      return;
    }
    setCalibrationMessage("");
    setCalibrationMode("stationary");
    captureRef.current = [];
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      const captured = captureRef.current ?? [];
      captureRef.current = null;
      setCalibrationMode("idle");
      if (captured.length < 5) {
        setCalibrationMessage(`Only ${captured.length} live samples received; at least 5 are needed.`);
        return;
      }
      const average = mean(captured);
      const target = Number(gravityTarget);
      saveOffsets({
        ax: average.ax, ay: average.ay, az: average.az - target,
        gx: average.gx, gy: average.gy, gz: average.gz,
      }, `Stationary calibration averaged ${captured.length} live samples.`);
    }, 2000);
  }

  function beginSixPositionCalibration() {
    if (!isConnected || !latest || currentSession?.status === "in-progress") return;
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = null;
    captureRef.current = null;
    positionMeansRef.current = [];
    setPositionMeans([]);
    setCapturingPosition(false);
    setCalibrationMessage("");
    setCalibrationMode("six-position");
  }

  function capturePosition() {
    if (calibrationMode !== "six-position" || capturingPosition || positionMeans.length >= SIX_POSITIONS.length) return;
    captureRef.current = [];
    setCapturingPosition(true);
    setCalibrationMessage("");
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      const captured = captureRef.current ?? [];
      captureRef.current = null;
      setCapturingPosition(false);
      if (captured.length < 3) {
        setCalibrationMessage(`Only ${captured.length} live samples received; at least 3 are needed. Keep the sensor connected and retry.`);
        return;
      }
      const means = [...positionMeansRef.current, mean(captured)];
      positionMeansRef.current = means;
      setPositionMeans(means);
      if (means.length < SIX_POSITIONS.length) {
        setCalibrationMessage(`${captured.length} samples captured. Set the next face up and let the sensor settle.`);
        return;
      }
      const next = { ...offsetsRef.current };
      for (const axis of ACCEL_AXES) {
        const positive = SIX_POSITIONS.findIndex((position) => position.axis === axis && position.direction === 1);
        const negative = SIX_POSITIONS.findIndex((position) => position.axis === axis && position.direction === -1);
        next[axis] = (means[positive][axis] + means[negative][axis]) / 2;
      }
      for (const axis of GYRO_AXES) next[axis] = means.reduce((sum, reading) => sum + reading[axis], 0) / means.length;
      saveOffsets(next, "Six-position calibration calculated accelerometer and gyro bias.");
      positionMeansRef.current = [];
      setPositionMeans([]);
    }, 1200);
  }

  function cancelCalibration() {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = null;
    captureRef.current = null;
    positionMeansRef.current = [];
    setPositionMeans([]);
    setCapturingPosition(false);
    setCalibrationMode("idle");
    setCalibrationMessage("Calibration cancelled; saved offsets were not changed.");
  }

  function resizeTasks(raw: string) {
    if (!raw) return;
    const count = Math.min(100, Math.max(1, Math.trunc(Number(raw))));
    if (!Number.isFinite(count)) return;
    setTaskCount(count);
    setDefinitions((previous) => Array.from({ length: count }, (_, index) =>
      previous[index] ?? { name: `Task ${index + 1}`, instructions: "Follow the movement instructions for this task." },
    ));
    setSelectedTask((index) => Math.min(index, count - 1));
  }

  function startTask(index: number) {
    if (!isConnected || !latest) {
      setError("Connect the sensor (via API or Serial) and wait for a valid reading before starting a task.");
      return;
    }
    setError("");
    const startedAt = (dataSource === "api" && apiTimestampRef.current)
      ? apiTimestampRef.current
      : new Date().toISOString();
    const startedCapturedAt = new Date().toISOString();
    const startedMonotonic = performance.now();
    if (!currentSession || currentSession.status !== "in-progress") {
      const id = crypto.randomUUID();
      const tasks = definitions.slice(0, taskCount).map((definition, taskIndex) => ({
        ...definition,
        status: taskIndex === index ? "active" as const : "pending" as const,
        startedAt: taskIndex === index ? startedAt : null,
        startedCapturedAt: taskIndex === index ? startedCapturedAt : null,
        endedAt: null,
        endedCapturedAt: null,
        startedMonotonic: taskIndex === index ? startedMonotonic : null,
        samples: [],
      }));
      setSessions((previous) => [...previous, {
        id, status: "in-progress", startedAt, startedCapturedAt,
        endedAt: null, endedCapturedAt: null, activeTaskIndex: index, tasks,
      }]);
      setSessionId(id);
      sessionIdRef.current = id;
      return;
    }
    updateSession(currentSession.id, (session) => {
      const task = session.tasks[index];
      if (!task || task.status !== "pending") return session;
      const tasks = session.tasks.slice();
      tasks[index] = { ...task, status: "active", startedAt, startedCapturedAt, startedMonotonic };
      return { ...session, activeTaskIndex: index, tasks };
    });
  }

  function finishCurrentTask() {
    if (!currentSession || currentSession.activeTaskIndex === null) return;
    const currentIndex = currentSession.activeTaskIndex;
    const hasPendingNext = currentSession.tasks.some((task, index) => index > currentIndex && task.status === "pending");
    const endedAt = (dataSource === "api" && apiTimestampRef.current)
      ? apiTimestampRef.current
      : new Date().toISOString();
    const endedCapturedAt = new Date().toISOString();
    updateSession(currentSession.id, (session) => {
      const tasks = session.tasks.slice();
      tasks[currentIndex] = { ...tasks[currentIndex], status: "completed", endedAt, endedCapturedAt };
      const complete = tasks.every((task) => task.status === "completed");
      return {
        ...session, tasks, activeTaskIndex: null,
        status: complete ? "completed" : "in-progress",
        endedAt: complete ? endedAt : null,
        endedCapturedAt: complete ? endedCapturedAt : null,
      };
    });
    if (hasPendingNext && !window.confirm("Task finished. Continue to the next task? Select Cancel to stop the entire sequence.")) {
      finishSession(false);
    }
  }

  function stopCurrentTask() {
    if (!currentSession || currentSession.activeTaskIndex === null) return;
    const endedAt = (dataSource === "api" && apiTimestampRef.current)
      ? apiTimestampRef.current
      : new Date().toISOString();
    const endedCapturedAt = new Date().toISOString();
    updateSession(currentSession.id, (session) => {
      const index = session.activeTaskIndex;
      if (index === null) return session;
      const tasks = session.tasks.slice();
      tasks[index] = { ...tasks[index], status: "incomplete", endedAt, endedCapturedAt };
      return { ...session, tasks, activeTaskIndex: null };
    });
  }

  function stopEntireSession() {
    if (!currentSession || currentSession.status !== "in-progress") return;
    finishSession(true);
  }

  const nextTaskIndex = currentSession?.status === "in-progress"
    ? currentSession.tasks.findIndex((task) => task.status === "pending")
    : -1;
  const sessionDuration = shownSession
    ? Math.max(0, new Date(shownSession.endedAt ?? clock).getTime() - new Date(shownSession.startedAt).getTime())
    : 0;

  const switchToSerial = () => {
    setDataSource("serial");
    setLatest(null);
    lastApiSeqRef.current = null;
    setApiLastSampleAt(null);
  };
  const switchToApi = () => {
    if (connected) void disconnect();
    setDataSource("api");
    setLatest(null);
    lastApiSeqRef.current = null;
    setApiLastSampleAt(null);
  };

  const apiConnectionLabel = !apiConnected ? "Offline" : apiFresh ? "Live" : latest ? "Stale" : "Waiting";
  const bannerText = dataSource === "api"
    ? apiConnected
      ? apiFresh && latest ? "MPU6050 readings via API" : latest ? "API connected · telemetry is stale" : "API connected · waiting for MPU data"
      : "Connecting to telemetry API…"
    : connected
      ? latest ? "MPU6050 readings received" : "Serial connected · waiting for valid MPU data"
      : "Sensor disconnected";
  const bannerDetail = dataSource === "api"
    ? apiConnected && apiFresh && latest
      ? `New sensor packets received · ${apiTimestamp ? new Date(apiTimestamp).toLocaleTimeString() : ""}`
      : apiConnected && latest
        ? `Last sensor packet: ${apiTimestamp ? new Date(apiTimestamp).toLocaleTimeString() : "unknown time"}. Waiting for a new packet from the collector.`
        : "Readings appear automatically when the collector is running."
    : connected && latest
      ? "Live firmware readings · m/s² and rad/s"
      : "No readings are shown until a valid sensor packet arrives.";

  return (
    <div className="mpu6050-lab">
      <div className="lab-banner">
        <span className={`lab-status-dot ${isConnected ? "is-connected" : ""}`} />
        <span><b>{bannerText}</b><small>{bannerDetail}</small></span>
        <div className="lab-source-toggle">
          <button className={`lab-source-btn ${dataSource === "api" ? "active" : ""}`} onClick={switchToApi} disabled={dataSource === "api" || currentSession?.status === "in-progress"}>API (auto)</button>
          <button className={`lab-source-btn ${dataSource === "serial" ? "active" : ""}`} onClick={switchToSerial} disabled={dataSource === "serial" || currentSession?.status === "in-progress"}>Serial (manual)</button>
        </div>
        {dataSource === "serial" && <>
          <label className="lab-baud-label">BAUD
            <select value={baudRate} onChange={(event) => setBaudRate(event.target.value)} disabled={connected}>
              <option value="9600">9600</option><option value="57600">57600</option><option value="115200">115200</option><option value="230400">230400</option>
            </select>
          </label>
          <button className="lab-button lab-primary" onClick={connected ? () => void disconnect() : () => void connect()} disabled={!active}>
            {connected ? "Disconnect" : "Connect sensor"}
          </button>
        </>}
      </div>
      {dataSource === "serial" && <div className="lab-port-warning"><b>Serial port access is exclusive.</b> Stop the companion collector before connecting here. The collector and this browser page cannot read the same USB port simultaneously. Lab captures are separate from the routine telemetry API.</div>}
      {dataSource === "api" && !apiConnected && <div className="lab-warning">Cannot reach telemetry API at {API_BASE}. Make sure the backend and collector are running (use <code>.\dashboard</code>).</div>}
      {dataSource === "api" && apiConnected && latest && !apiFresh && <div className="lab-warning">The API is reachable, but no new valid MPU6050 packet has arrived recently. Check the collector window for serial or posting errors; old stored readings are marked stale.</div>}
      {error && <div className="lab-error" role="alert">{error}</div>}
      {invalidLines > 0 && dataSource === "serial" && <div className="lab-warning">{invalidLines} serial line(s) ignored because they did not contain valid MPU6050 axes.</div>}

      <div className="lab-stats">
        <div><small>DATA SOURCE</small><b>{dataSource === "api" ? "API" : "Serial"}</b></div>
        <div><small>CONNECTION</small><b>{dataSource === "api" ? apiConnectionLabel : connected ? "Connected" : "Offline"}</b></div>
        <div><small>CALIBRATION</small><b>Version {calibrationVersion}</b></div>
        <div><small>TASKS COMPLETED</small><b>{completedTasks} / {shownTasks.length || taskCount}</b></div>
        <div><small>COLLECTED SAMPLES</small><b>{(shownSession ? samplesIn(shownSession) : 0).toLocaleString()}</b></div>
        <div><small>SESSION DURATION</small><b>{elapsedLabel(sessionDuration)}</b></div>
      </div>

      <section className="lab-card" id="lab-live-data">
        <div className="lab-section-heading"><div><span>REAL-TIME MONITORING</span><h2>Live sensor data</h2><p>{dataSource === "api" ? "Values from the telemetry API (collector → backend → dashboard)" : "Raw values from the firmware and values after the saved offsets."}</p></div><span className={`lab-stream-badge ${isConnected ? "is-connected" : apiConnected && latest ? "is-stale" : ""}`}>{isConnected ? (dataSource === "api" ? "API LIVE" : "SERIAL LIVE") : apiConnected && latest ? "API STALE" : "NO SIGNAL"}</span></div>
        {!isConnected && <div className="lab-empty"><b>{dataSource === "api" ? apiConnected && latest ? "Sensor data is stale" : "Waiting for API data" : "No live sensor readings"}</b><span>{dataSource === "api" ? apiConnected && latest ? "The displayed values are the last saved reading. A new packet will restore live status." : "The telemetry API will be polled automatically. Launch the dashboard with .\\dashboard to start the collector." : "Connect the base station's USB serial port to display actual MPU6050 readings."}</span></div>}
        <div className="lab-reading-grid">
          <ReadingCard title="RAW ACCELEROMETER" axes={ACCEL_AXES} reading={latest} unit="m/s²" />
          <ReadingCard title="CALIBRATED ACCELEROMETER" axes={ACCEL_AXES} reading={liveCalibrated} unit="m/s²" />
          <ReadingCard title="RAW GYROSCOPE" axes={GYRO_AXES} reading={latest} unit="rad/s" />
          <ReadingCard title="CALIBRATED GYROSCOPE" axes={GYRO_AXES} reading={liveCalibrated} unit="rad/s" />
        </div>
      </section>

      <section className="lab-card" id="lab-calibration">
        <div className="lab-section-heading"><div><span>SENSOR SETUP</span><h2>Calibration offsets</h2><p>Offsets are subtracted from raw readings. Each save versions future samples.</p></div><span className="lab-version">VERSION {calibrationVersion}</span></div>
        <div className="lab-offset-grid">
          {AXES.map((axis) => <label key={axis}><span>{LABELS[axis]} ({axis.toUpperCase()}) <small>{ACCEL_AXES.includes(axis as typeof ACCEL_AXES[number]) ? "m/s²" : "rad/s"}</small></span><input inputMode="decimal" value={draftOffsets[axis]} onChange={(event) => setDraftOffsets((draft) => ({ ...draft, [axis]: event.target.value }))} aria-label={`${LABELS[axis]} calibration offset`} /><small>Applied: {offsets[axis].toFixed(4)}</small></label>)}
        </div>
        <div className="lab-action-row"><span className="lab-muted">Manual offsets work independently of automatic calibration.</span><button className="lab-button lab-primary" onClick={saveManualOffsets}>Save offsets</button></div>

        <div className="lab-auto-calibration">
          <div className="lab-section-heading"><div><h3>Automatic calibration</h3><p>Captures use only valid live readings from the connected sensor.</p></div>{calibrationMode !== "idle" && <button className="lab-button lab-outline" onClick={cancelCalibration}>Cancel capture</button>}</div>
          <div className="lab-auto-grid">
            <div className="lab-method">
              <b>Stationary & level</b>
              <p>Hold the sensor still for two seconds. Gyro axes and accel X/Y are zeroed; accel Z is aligned to the target you provide.</p>
              <label>EXPECTED LEVEL ACCEL Z <small>m/s², signed</small><input inputMode="decimal" placeholder="For example: 9.80665" value={gravityTarget} onChange={(event) => setGravityTarget(event.target.value)} disabled={calibrationMode !== "idle"} /></label>
              <button className="lab-button lab-primary" onClick={beginStationaryCalibration} disabled={!isConnected || !latest || calibrationMode !== "idle" || currentSession?.status === "in-progress"}>{calibrationMode === "stationary" ? "Capturing live samples…" : "Calibrate level position"}</button>
            </div>
            <div className="lab-method">
              <b>Six-position</b>
              <p>Place each of the six faces upward and capture a still sample set to estimate accelerometer bias and gyro bias.</p>
              {calibrationMode !== "six-position" ? <button className="lab-button lab-outline" onClick={beginSixPositionCalibration} disabled={!isConnected || !latest || calibrationMode !== "idle" || currentSession?.status === "in-progress"}>Begin six-position guide</button> : <div className="lab-position-guide"><small>POSITION {Math.min(positionMeans.length + 1, SIX_POSITIONS.length)} OF 6</small><b>{capturingPosition ? "Capturing — keep the sensor still…" : `Set ${SIX_POSITIONS[positionMeans.length]?.label ?? "the sensor"} facing up.`}</b><button className="lab-button lab-primary" onClick={capturePosition} disabled={capturingPosition}>{capturingPosition ? "Collecting readings…" : `Capture ${SIX_POSITIONS[positionMeans.length]?.label ?? "position"}`}</button></div>}
            </div>
          </div>
          <div className="lab-muted">Automatic calibration requires live sensor data and is disabled during an active collection session. Six-position calibration corrects bias offsets only; it does not estimate scale or cross-axis errors.</div>
          {calibrationMessage && <div className="lab-message" role="status" aria-live="polite">{calibrationMessage}</div>}
        </div>
      </section>

      <section className="lab-card" id="lab-collection">
        <div className="lab-section-heading"><div><span>LAB WORKFLOW</span><h2>Sequential task collection</h2><p>Every task requires an explicit START. Timers restart at 00:00.</p></div>{currentSession?.status === "in-progress" && <button className="lab-button lab-danger" onClick={stopEntireSession}>Stop entire session</button>}</div>
        <div className="lab-collection-grid">
          <div className="lab-task-setup">
            <label>NUMBER OF TASKS <small>1–100</small><input type="number" min="1" max="100" value={taskCount} onChange={(event) => resizeTasks(event.target.value)} disabled={currentSession?.status === "in-progress"} /></label>
            <label>EDIT TASK<select value={selectedTask} onChange={(event) => setSelectedTask(Number(event.target.value))} disabled={currentSession?.status === "in-progress"}>{definitions.slice(0, taskCount).map((task, index) => <option value={index} key={index}>Task {index + 1} · {task.name}</option>)}</select></label>
            <label>TASK NAME<input value={definitions[selectedTask]?.name ?? ""} onChange={(event) => setDefinitions((previous) => previous.map((task, index) => index === selectedTask ? { ...task, name: event.target.value } : task))} disabled={currentSession?.status === "in-progress"} /></label>
            <label>INSTRUCTIONS<textarea rows={3} value={definitions[selectedTask]?.instructions ?? ""} onChange={(event) => setDefinitions((previous) => previous.map((task, index) => index === selectedTask ? { ...task, instructions: event.target.value } : task))} disabled={currentSession?.status === "in-progress"} /></label>
            <small>Collection requires a connected data source (API or Serial) and a valid live reading. Samples are recorded only while a task is active.</small>
          </div>
          <div className="lab-task-stage">
            {!currentSession || currentSession.status !== "in-progress" ? currentSession ? <div className="lab-session-ended"><span>{currentSession.status === "completed" ? "SESSION COMPLETE" : "SESSION STOPPED"}</span><h3>{currentSession.status === "completed" ? "All tasks finished" : "Collection stopped safely"}</h3><p>{samplesIn(currentSession)} samples preserved · {currentSession.tasks.filter((task) => task.status === "incomplete" || task.status === "cancelled").length} incomplete</p><button className="lab-button lab-primary" onClick={() => { setSessionId(null); sessionIdRef.current = null; }}>Configure a new session</button></div> : <div className="lab-session-ready"><span>READY TO COLLECT</span><h3>{definitions[0]?.name ?? "Task 1"}</h3><p>{definitions[0]?.instructions}</p><div className="lab-timer">00:00</div><button className="lab-button lab-primary" onClick={() => startTask(0)} disabled={!isConnected || !latest}>START TASK 1</button>{(!isConnected || !latest) && <small>Connect a data source and wait for a valid reading before collection.</small>}</div> : <>
              <div className="lab-session-meta"><span>SESSION <b>{currentSession.id.slice(0, 8).toUpperCase()}</b></span><span>{completedTasks} OF {currentSession.tasks.length} COMPLETED · {incompleteTasks} INCOMPLETE</span></div>
              {activeTask && currentSession.activeTaskIndex !== null ? <div className="lab-active-task"><span>TASK {currentSession.activeTaskIndex + 1} OF {currentSession.tasks.length}</span><h3>{activeTask.name}</h3><p>{activeTask.instructions}</p><div className="lab-timer">{elapsedLabel(activeElapsed)}</div><div className="lab-task-times"><span>Started<b>{timestampLabel(activeTask.startedAt)}</b></span><span>Samples<b>{activeTask.samples.length}</b></span></div><div className="lab-task-buttons"><button className="lab-button lab-primary" onClick={finishCurrentTask}>Finish task</button><button className="lab-button lab-outline" onClick={stopCurrentTask}>Stop current task</button></div></div> : <div className="lab-next-task"><span>READY FOR NEXT TASK</span><h3>{nextTaskIndex >= 0 ? currentSession.tasks[nextTaskIndex].name : "No pending tasks"}</h3><p>{nextTaskIndex >= 0 ? currentSession.tasks[nextTaskIndex].instructions : "Stop the session to mark remaining tasks cancelled."}</p>{nextTaskIndex >= 0 && <button className="lab-button lab-primary" onClick={() => startTask(nextTaskIndex)}>START NEXT TASK</button>}</div>}
              <div className="lab-task-list">{currentSession.tasks.map((task, index) => <div key={`${currentSession.id}-${index}`}><span className={`lab-task-state state-${task.status}`}>{task.status === "completed" ? "✓" : task.status === "active" ? "●" : task.status === "incomplete" || task.status === "cancelled" ? "!" : index + 1}</span><span><b>{task.name}</b><small>{task.status} · {task.samples.length} samples</small><small>{timestampLabel(task.startedAt)} → {timestampLabel(task.endedAt)}</small></span></div>)}</div>
            </>}
            <small className="lab-muted">On leaving this page, serial is disconnected and any active task is preserved as incomplete.</small>
          </div>
        </div>
      </section>

      <section className="lab-card" id="lab-history">
        <div className="lab-section-heading"><div><span>COLLECTION LOG</span><h2>Session history & export</h2><p>Telemetry export contains the latest API records across sensors. Task export adds task labels and calibration for samples captured during started tasks.</p></div><div className="lab-export-actions"><button className="lab-button lab-primary" onClick={exportTelemetryHistory} disabled={dataSource !== "api" || apiRecordCount === 0} title="Download the latest telemetry API records, up to 500 rows.">Download sensor CSV · {apiRecordCount} rows</button><button className="lab-button lab-outline" onClick={() => downloadCsv(sessions)} disabled={allSamples === 0} title="Download only MPU6050 readings captured while a lab task was active.">Download task CSV · {allSamples} samples</button></div></div>
        {allSamples === 0 && <small className="lab-muted">Task CSV is unavailable until you start a task and fresh sensor samples arrive. Sensor CSV exports API history independently.</small>}
        {!sessions.length ? <div className="lab-empty">No lab sessions yet.</div> : <div className="lab-table-wrap"><table className="lab-table"><thead><tr><th>SESSION</th><th>STARTED</th><th>STATUS</th><th>TASKS</th><th>SAMPLES</th><th>DURATION</th></tr></thead><tbody>{[...sessions].reverse().map((session) => <tr key={session.id}><td>{session.id.slice(0, 8).toUpperCase()}</td><td>{timestampLabel(session.startedAt)}</td><td>{session.status}</td><td>{session.tasks.filter((task) => task.status === "completed").length} completed · {session.tasks.filter((task) => task.status === "incomplete" || task.status === "cancelled").length} incomplete</td><td>{samplesIn(session)}</td><td>{elapsedLabel(Math.max(0, new Date(session.endedAt ?? clock).getTime() - new Date(session.startedAt).getTime()))}</td></tr>)}</tbody></table></div>}
      </section>
      <p className="lab-footer">{dataSource === "api" ? "Data source: telemetry API (collector → backend). The collector reads the base station serial port and posts records to the API. No direct browser serial connection is needed." : "Companion firmware serial format: newline-delimited unified JSON with valid wearable.mpu_ok and axes ax…gz. No sensor readings are simulated."}</p>
    </div>
  );
}

function ReadingCard({ title, axes, reading, unit }: {
  title: string;
  axes: readonly Axis[];
  reading: Reading | null;
  unit: string;
}) {
  return <div className="lab-reading-card"><b>{title}</b>{axes.map((axis) => <div key={axis}><span>{axis.slice(1).toUpperCase()}</span><strong>{reading ? reading[axis].toFixed(4) : "—"}</strong><small>{unit}</small></div>)}</div>;
}
