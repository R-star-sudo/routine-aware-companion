export type DataMode = "DEMO" | "LIVE" | "CSV";

export type Observation = {
  timestamp: string;
  heartRate: number | null;
  heartRateValid: boolean;
  sensorTemperature: number | null;
  ambientTemperature: number | null;
  humidity: number | null;
  movement: number | null;
  pillboxState: "Open" | "Closed" | "Unknown";
  routineState: string | null;
  source: DataMode;
};

export type Forecast = {
  targetDate: string;
  generatedAt: string;
  typicalWakeTime?: string | null;
  wakeTime: string | null;
  wakeWindow: string | null;
  earlyRiseProbability: number | null;
  wakeConfidence?: number | null;
  missedInteractionProbability: number | null;
  medicationWindow: string | null;
  hourly?: HourlyForecast[];
  status: "Saved demo forecast" | "Outcome pending" | "Evaluated";
  source: DataMode;
};

export type HourlyForecast = {
  window: string;
  routineState: string;
  event: string;
  probability: number | null;
  confidence: number | null;
  status: "Forecast" | "Outcome pending";
};

export type ActivityEvent = {
  id: string;
  timestamp: string;
  type: string;
  description: string;
  status: "Observed" | "Predicted" | "Demo action" | "Unavailable";
  device: string;
  source: DataMode;
};

export type DashboardData = {
  observations: Observation[];
  forecasts: Forecast[];
  events: ActivityEvent[];
};

export type CsvParseResult = {
  observations: Observation[];
  rejectedRows: number;
  duplicateRows: number;
};

type ApiTelemetryRecord = {
  schema_version: "1.0";
  source: "simulated" | "hardware";
  device_id: string;
  sequence: number;
  timestamp: string;
  acceleration_x_m_s2: number | null;
  acceleration_y_m_s2: number | null;
  acceleration_z_m_s2: number | null;
  angular_velocity_x_rad_s: number | null;
  angular_velocity_y_rad_s: number | null;
  angular_velocity_z_rad_s: number | null;
  pulse_rate_bpm: number | null;
  skin_temperature_c: number | null;
  room_temperature_c: number | null;
  room_humidity_percent: number | null;
  pillbox_open: boolean | null;
  sensor_validity: { mpu6050: boolean; max30102: boolean; ds18b20: boolean };
};

const localDateKey = (date: Date) => {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
};

const atLocalTime = (date: Date, hour: number, minute = 0) => {
  const value = new Date(date);
  value.setHours(hour, minute, 0, 0);
  return value.toISOString();
};

export function createDemoData(): DashboardData {
  const now = new Date();
  const observations: Observation[] = [];
  const forecasts: Forecast[] = [];
  const events: ActivityEvent[] = [];

  for (let dayOffset = -6; dayOffset <= 0; dayOffset += 1) {
    const day = new Date(now);
    day.setDate(now.getDate() + dayOffset);
    const count = dayOffset === 0 ? Math.max(1, now.getHours() + 1) : 24;

    for (let hour = 0; hour < count; hour += 1) {
      const timestamp = atLocalTime(day, hour);
      const morning = hour >= 6 && hour <= 9;
      const movement = hour >= 7 && hour <= 20
        ? Number((0.16 + Math.abs(Math.sin(hour * 0.71 + dayOffset)) * 0.67).toFixed(2))
        : Number((0.04 + Math.abs(Math.sin(hour)) * 0.08).toFixed(2));

      observations.push({
        timestamp,
        heartRate: hour % 7 === 0 ? null : 64 + Math.round(Math.abs(Math.sin(hour * 0.9 + dayOffset)) * 18),
        heartRateValid: hour % 7 !== 0,
        sensorTemperature: hour % 11 === 0 ? null : Number((32.8 + Math.sin(hour / 4) * 0.35).toFixed(1)),
        ambientTemperature: Number((21 + Math.sin(hour / 5) * 2).toFixed(1)),
        humidity: 43 + Math.round(Math.abs(Math.cos(hour / 6)) * 16),
        movement,
        pillboxState: hour === 8 && dayOffset < 0 ? "Open" : "Closed",
        routineState: morning ? "Morning routine" : movement > 0.5 ? "Active period" : "Rest period",
        source: "DEMO",
      });
    }

    if (dayOffset >= -3 && dayOffset <= 2) {
      forecasts.push({
        targetDate: localDateKey(day),
        generatedAt: atLocalTime(new Date(day.getTime() - 24 * 60 * 60 * 1000), 18, 30),
        typicalWakeTime: "07:00",
        wakeTime: "06:45",
        wakeWindow: "06:30–07:15",
        earlyRiseProbability: dayOffset === 1 || dayOffset === 2 ? null : 0.18 + Math.abs(dayOffset) * 0.035,
        wakeConfidence: dayOffset > 0 ? null : 0.74,
        missedInteractionProbability: dayOffset > 0 ? null : 0.12,
        medicationWindow: "08:00–09:00",
        hourly: [
          { window: "06:00–07:00", routineState: "Rest period", event: "Possible morning transition", probability: 0.71, confidence: 0.68, status: dayOffset > 0 ? "Outcome pending" : "Forecast" },
          { window: "07:00–08:00", routineState: "Morning routine", event: "Expected morning activity", probability: 0.68, confidence: 0.72, status: dayOffset > 0 ? "Outcome pending" : "Forecast" },
          { window: "08:00–09:00", routineState: "Medication window", event: "Possible pillbox interaction", probability: 0.62, confidence: 0.64, status: dayOffset > 0 ? "Outcome pending" : "Forecast" },
          { window: "09:00–10:00", routineState: "Active period", event: "Routine activity window", probability: null, confidence: null, status: dayOffset > 0 ? "Outcome pending" : "Forecast" },
        ],
        status: dayOffset > 0 ? "Outcome pending" : "Saved demo forecast",
        source: "DEMO",
      });
    }

    if (dayOffset < 0) {
      events.push({
        id: `pillbox-${localDateKey(day)}`,
        timestamp: atLocalTime(day, 8, 4),
        type: "Pillbox interaction detected",
        description: "Pillbox state changed to open. This does not confirm medication was taken.",
        status: "Observed",
        device: "Base station",
        source: "DEMO",
      });
    }
  }

  events.push({
    id: "demo-forecast-today",
    timestamp: atLocalTime(now, 6, 15),
    type: "Prediction generated",
    description: "A demonstration forecast was saved for the selected date.",
    status: "Predicted",
    device: "Demo prediction source",
    source: "DEMO",
  });

  return { observations, forecasts, events };
}

export async function loadLiveData(): Promise<DashboardData> {
  const baseUrl = import.meta.env.VITE_API_BASE_URL?.trim() || "http://127.0.0.1:8000";

  let response: Response;
  try {
    response = await fetch(`${baseUrl.replace(/\/$/, "")}/api/v1/telemetry/history?limit=500`);
  } catch {
    throw new Error("Could not reach the dashboard API. Check the API service and connection.");
  }

  if (!response.ok) {
    throw new Error(`Telemetry API returned ${response.status} ${response.statusText}.`);
  }

  const payload: unknown = await response.json();
  if (!Array.isArray(payload) || !payload.every(isTelemetryRecord)) {
    throw new Error("Telemetry API response did not match schema version 1.0.");
  }

  const observations: Observation[] = payload.map((record) => {
    const heartRate = record.sensor_validity.max30102 ? record.pulse_rate_bpm : null;
    return {
      timestamp: record.timestamp,
      heartRate,
      heartRateValid: heartRate !== null,
      sensorTemperature: record.sensor_validity.ds18b20 ? record.skin_temperature_c : null,
      ambientTemperature: record.room_temperature_c,
      humidity: record.room_humidity_percent,
      movement: movementFromAcceleration(record),
      pillboxState: (record.pillbox_open === null ? "Unknown" : record.pillbox_open ? "Open" : "Closed") as "Open" | "Closed" | "Unknown",
      routineState: null,
      source: "LIVE" as const,
    };
  }).sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp));

  return {
    observations,
    forecasts: [],
    events: [],
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function isNullableFiniteNumber(value: unknown): value is number | null {
  return value === null || (typeof value === "number" && Number.isFinite(value));
}

function isTelemetryRecord(value: unknown): value is ApiTelemetryRecord {
  if (!isRecord(value) || !isRecord(value.sensor_validity)) return false;
  const validity = value.sensor_validity;
  const measurementFields = [
    "acceleration_x_m_s2", "acceleration_y_m_s2", "acceleration_z_m_s2",
    "angular_velocity_x_rad_s", "angular_velocity_y_rad_s", "angular_velocity_z_rad_s",
    "pulse_rate_bpm", "skin_temperature_c", "room_temperature_c", "room_humidity_percent",
  ];
  return value.schema_version === "1.0"
    && (value.source === "hardware" || value.source === "simulated")
    && typeof value.device_id === "string"
    && Number.isInteger(value.sequence)
    && typeof value.timestamp === "string"
    && !Number.isNaN(Date.parse(value.timestamp))
    && measurementFields.every((field) => isNullableFiniteNumber(value[field]))
    && (value.pillbox_open === null || typeof value.pillbox_open === "boolean")
    && typeof validity.mpu6050 === "boolean"
    && typeof validity.max30102 === "boolean"
    && typeof validity.ds18b20 === "boolean";
}

function movementFromAcceleration(record: ApiTelemetryRecord): number | null {
  const axes = [record.acceleration_x_m_s2, record.acceleration_y_m_s2, record.acceleration_z_m_s2];
  if (!record.sensor_validity.mpu6050 || axes.some((axis) => axis === null)) return null;
  const magnitude = Math.hypot(axes[0]!, axes[1]!, axes[2]!);
  // Approximate activity as deviation from gravity, normalized to 0..1.
  return Math.min(Math.max((magnitude - 9.80665) / 9.80665, 0), 1);
}

function parseCsvRows(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;

  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (character === '"' && quoted && text[index + 1] === '"') {
      field += '"';
      index += 1;
    } else if (character === '"') {
      quoted = !quoted;
    } else if (character === "," && !quoted) {
      row.push(field.trim());
      field = "";
    } else if ((character === "\n" || character === "\r") && !quoted) {
      if (character === "\r" && text[index + 1] === "\n") index += 1;
      row.push(field.trim());
      if (row.some((cell) => cell.length > 0)) rows.push(row);
      row = [];
      field = "";
    } else {
      field += character;
    }
  }
  row.push(field.trim());
  if (row.some((cell) => cell.length > 0)) rows.push(row);
  if (quoted) throw new Error("CSV contains an unterminated quoted field.");
  return rows;
}

function optionalNumber(raw: string | undefined, min: number, max: number): number | null {
  if (!raw?.trim()) return null;
  const value = Number(raw);
  return Number.isFinite(value) && value >= min && value <= max ? value : null;
}

export function parseObservationCsv(text: string): CsvParseResult {
  const rows = parseCsvRows(text);
  if (rows.length < 2) throw new Error("CSV must include a header and at least one data row.");
  const headers = rows[0].map((header) => header.replace(/^\uFEFF/, "").toLowerCase().replace(/[\s-]+/g, "_"));
  const column = (...names: string[]) => headers.findIndex((header) => names.includes(header));
  const timestampIndex = column("timestamp", "observed_at", "time");
  if (timestampIndex < 0) throw new Error("CSV needs a timestamp, observed_at, or time column.");

  const numberAt = (cells: string[], names: string[], min: number, max: number) => {
    const index = column(...names);
    return index < 0 ? null : optionalNumber(cells[index], min, max);
  };
  const observations: Observation[] = [];
  let rejectedRows = 0;
  let duplicateRows = 0;
  const seen = new Set<string>();

  for (const cells of rows.slice(1)) {
    const timestamp = cells[timestampIndex];
    if (!timestamp || Number.isNaN(Date.parse(timestamp))) {
      rejectedRows += 1;
      continue;
    }
    const normalizedTimestamp = new Date(timestamp).toISOString();
    if (seen.has(normalizedTimestamp)) {
      duplicateRows += 1;
      continue;
    }
    seen.add(normalizedTimestamp);
    const heartRateValue = numberAt(cells, ["heart_rate", "pulse_rate_bpm", "bpm"], 0, 300);
    const heartRate = heartRateValue !== null && heartRateValue > 0 ? heartRateValue : null;
    const validityIndex = column("sensor_validity");
    let sensorValidity: Record<string, unknown> | null = null;
    if (validityIndex >= 0 && cells[validityIndex]) {
      try {
        const parsed: unknown = JSON.parse(cells[validityIndex]);
        if (isRecord(parsed)) sensorValidity = parsed;
      } catch {
        sensorValidity = null;
      }
    }
    const explicitHeartValidityIndex = column("heart_rate_valid");
    const explicitHeartValidity = explicitHeartValidityIndex < 0
      ? undefined
      : cells[explicitHeartValidityIndex]?.toLowerCase();
    const heartRateValid = heartRate !== null
      && explicitHeartValidity !== "false"
      && explicitHeartValidity !== "0"
      && sensorValidity?.max30102 !== false;

    const stateIndex = column("pillbox_state");
    const pillboxOpenIndex = column("pillbox_open");
    const rawPillbox = stateIndex >= 0
      ? cells[stateIndex]?.toLowerCase()
      : pillboxOpenIndex >= 0
        ? cells[pillboxOpenIndex]?.toLowerCase()
        : "";
    const pillboxState = ["open", "true", "1", "yes"].includes(rawPillbox)
      ? "Open"
      : ["closed", "false", "0", "no"].includes(rawPillbox)
        ? "Closed"
        : "Unknown";

    const acceleration = [
      numberAt(cells, ["acceleration_x_m_s2"], -1000, 1000),
      numberAt(cells, ["acceleration_y_m_s2"], -1000, 1000),
      numberAt(cells, ["acceleration_z_m_s2"], -1000, 1000),
    ];
    let movement = numberAt(cells, ["movement_intensity", "movement"], 0, 10000);
    if (movement === null && sensorValidity?.mpu6050 !== false && acceleration.every((axis) => axis !== null)) {
      const magnitude = Math.hypot(acceleration[0]!, acceleration[1]!, acceleration[2]!);
      movement = Math.min(Math.max((magnitude - 9.80665) / 9.80665, 0), 1);
    }

    const skinTemperature = sensorValidity?.ds18b20 === false
      ? null
      : numberAt(cells, ["sensor_temperature", "skin_site_temperature", "skin_temperature_c"], -55, 125);
    observations.push({
      timestamp: normalizedTimestamp,
      heartRate,
      heartRateValid,
      sensorTemperature: skinTemperature,
      ambientTemperature: numberAt(cells, ["ambient_temperature", "room_temperature_c"], -50, 100),
      humidity: numberAt(cells, ["humidity", "relative_humidity", "room_humidity_percent"], 0, 100),
      movement,
      pillboxState,
      routineState: (column("routine_state") >= 0 ? cells[column("routine_state")] : "") || null,
      source: "CSV",
    });
  }

  if (observations.length === 0) throw new Error("CSV contains no valid timestamped observation rows.");
  observations.sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
  return { observations, rejectedRows, duplicateRows };
}

const CSV_STORAGE_KEY = "rac.csv-observations.v1";
const EVENT_STORAGE_KEY = "rac.demo-events.v1";

function readStored<T>(key: string, validate: (value: unknown) => value is T): T[] {
  const raw = localStorage.getItem(key);
  if (!raw) return [];
  const parsed: unknown = JSON.parse(raw);
  if (!Array.isArray(parsed) || !parsed.every(validate)) {
    throw new Error(`Saved browser data "${key}" is invalid. Clear the saved data and import it again.`);
  }
  return parsed as T[];
}

export function getImportedObservations(): Observation[] {
  return readStored<Observation>(CSV_STORAGE_KEY, isObservation);
}

export function saveImportedObservations(observations: Observation[]): void {
  localStorage.setItem(CSV_STORAGE_KEY, JSON.stringify(observations));
}

export function getSavedEvents(): ActivityEvent[] {
  return readStored<ActivityEvent>(EVENT_STORAGE_KEY, isActivityEvent);
}

export function saveEvents(events: ActivityEvent[]): void {
  localStorage.setItem(EVENT_STORAGE_KEY, JSON.stringify(events));
}

export function clearImportedObservations(): void {
  localStorage.removeItem(CSV_STORAGE_KEY);
}

function isObservation(value: unknown): value is Observation {
  return Boolean(value && typeof value === "object" && "timestamp" in value && "source" in value);
}

function isActivityEvent(value: unknown): value is ActivityEvent {
  return Boolean(value && typeof value === "object" && "id" in value && "timestamp" in value);
}

export function dateKey(value: string | Date): string {
  return localDateKey(typeof value === "string" ? new Date(value) : value);
}

export function formatTime(value: string): string {
  return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function formatDate(value: string): string {
  return new Date(`${value}T12:00:00`).toLocaleDateString([], {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}
