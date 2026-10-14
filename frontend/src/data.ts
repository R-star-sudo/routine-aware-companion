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

type ApiObservation = Pick<Observation, "timestamp"> & Partial<Omit<Observation, "timestamp" | "source">>;
type ApiForecast = Pick<Forecast, "targetDate" | "generatedAt" | "status"> & Partial<Omit<Forecast, "targetDate" | "generatedAt" | "status" | "source">>;
type ApiDashboardData = {
  observations: ApiObservation[];
  forecasts: ApiForecast[];
  events: Omit<ActivityEvent, "source">[];
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
  const baseUrl = import.meta.env.VITE_API_BASE_URL?.trim();
  if (!baseUrl) {
    throw new Error("Live data is not configured. Set VITE_API_BASE_URL when the dashboard API is available.");
  }

  let response: Response;
  try {
    response = await fetch(`${baseUrl.replace(/\/$/, "")}/api/dashboard`);
  } catch {
    throw new Error("Could not reach the dashboard API. Check the API service and connection.");
  }

  if (!response.ok) {
    throw new Error(`Dashboard API returned ${response.status} ${response.statusText}.`);
  }

  const payload: unknown = await response.json();
  if (!isDashboardData(payload)) {
    throw new Error("Dashboard API response did not match the expected data contract.");
  }

  return {
    observations: payload.observations.map((item) => ({
      ...item,
      heartRate: item.heartRate ?? null,
      heartRateValid: item.heartRateValid === true,
      sensorTemperature: item.sensorTemperature ?? null,
      ambientTemperature: item.ambientTemperature ?? null,
      humidity: item.humidity ?? null,
      movement: item.movement ?? null,
      pillboxState: item.pillboxState ?? "Unknown",
      routineState: item.routineState ?? null,
      source: "LIVE",
    })),
    forecasts: payload.forecasts.map((item) => ({
      ...item,
      typicalWakeTime: item.typicalWakeTime ?? null,
      wakeTime: item.wakeTime ?? null,
      wakeWindow: item.wakeWindow ?? null,
      earlyRiseProbability: item.earlyRiseProbability ?? null,
      wakeConfidence: item.wakeConfidence ?? null,
      missedInteractionProbability: item.missedInteractionProbability ?? null,
      medicationWindow: item.medicationWindow ?? null,
      hourly: item.hourly ?? [],
      source: "LIVE",
    })),
    events: payload.events.map((item) => ({ ...item, source: "LIVE" })),
  };
}

function isDashboardData(value: unknown): value is ApiDashboardData {
  if (!value || typeof value !== "object") return false;
  const data = value as Record<string, unknown>;
  return Array.isArray(data.observations)
    && Array.isArray(data.forecasts)
    && Array.isArray(data.events)
    && data.observations.every(isApiObservation)
    && data.forecasts.every(isApiForecast)
    && data.events.every(isApiEvent);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function isOptionalNumber(value: unknown): value is number | null | undefined {
  return value === undefined || value === null || (typeof value === "number" && Number.isFinite(value));
}

function isApiObservation(value: unknown): value is ApiObservation {
  if (!isRecord(value)) return false;
  return typeof value.timestamp === "string"
    && !Number.isNaN(Date.parse(value.timestamp))
    && isOptionalNumber(value.heartRate)
    && (value.heartRateValid === undefined || typeof value.heartRateValid === "boolean")
    && isOptionalNumber(value.sensorTemperature)
    && isOptionalNumber(value.ambientTemperature)
    && isOptionalNumber(value.humidity)
    && isOptionalNumber(value.movement)
    && (value.pillboxState === undefined || value.pillboxState === "Open" || value.pillboxState === "Closed" || value.pillboxState === "Unknown")
    && (value.routineState === undefined || value.routineState === null || typeof value.routineState === "string");
}

function isApiForecast(value: unknown): value is ApiForecast {
  if (!isRecord(value)) return false;
  const probability = (item: unknown) => item === undefined || item === null
    || (typeof item === "number" && Number.isFinite(item) && item >= 0 && item <= 1);
  return typeof value.targetDate === "string"
    && typeof value.generatedAt === "string"
    && !Number.isNaN(Date.parse(value.generatedAt))
    && ["Saved demo forecast", "Outcome pending", "Evaluated"].includes(String(value.status))
    && (value.wakeTime === undefined || value.wakeTime === null || typeof value.wakeTime === "string")
    && (value.wakeWindow === undefined || value.wakeWindow === null || typeof value.wakeWindow === "string")
    && (value.typicalWakeTime === undefined || value.typicalWakeTime === null || typeof value.typicalWakeTime === "string")
    && probability(value.earlyRiseProbability)
    && probability(value.wakeConfidence)
    && probability(value.missedInteractionProbability)
    && (value.medicationWindow === undefined || value.medicationWindow === null || typeof value.medicationWindow === "string")
    && (value.hourly === undefined || (Array.isArray(value.hourly) && value.hourly.every(isHourlyForecast)));
}

function isHourlyForecast(value: unknown): value is HourlyForecast {
  if (!isRecord(value)) return false;
  const probability = (item: unknown) => item === undefined || item === null
    || (typeof item === "number" && Number.isFinite(item) && item >= 0 && item <= 1);
  return typeof value.window === "string"
    && typeof value.routineState === "string"
    && typeof value.event === "string"
    && probability(value.probability)
    && probability(value.confidence)
    && (value.status === "Forecast" || value.status === "Outcome pending");
}

function isApiEvent(value: unknown): value is Omit<ActivityEvent, "source"> {
  if (!isRecord(value)) return false;
  return typeof value.id === "string"
    && typeof value.timestamp === "string"
    && !Number.isNaN(Date.parse(value.timestamp))
    && typeof value.type === "string"
    && typeof value.description === "string"
    && ["Observed", "Predicted", "Demo action", "Unavailable"].includes(String(value.status))
    && typeof value.device === "string";
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
  const headers = rows[0].map((header) => header.toLowerCase().replace(/[\s-]+/g, "_"));
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
    const validIndex = column("heart_rate_valid");
    const validCell = validIndex < 0 ? undefined : cells[validIndex]?.toLowerCase();
    const heartRate = numberAt(cells, ["heart_rate", "bpm"], 25, 240);
    const stateIndex = column("pillbox_state");
    const rawPillbox = stateIndex < 0 ? "" : cells[stateIndex]?.toLowerCase();
    observations.push({
      timestamp: normalizedTimestamp,
      heartRate,
      heartRateValid: heartRate !== null && validCell !== "false" && validCell !== "0",
      sensorTemperature: numberAt(cells, ["sensor_temperature", "skin_site_temperature"], -20, 80),
      ambientTemperature: numberAt(cells, ["ambient_temperature"], -20, 80),
      humidity: numberAt(cells, ["humidity", "relative_humidity"], 0, 100),
      movement: numberAt(cells, ["movement_intensity", "movement"], 0, 10000),
      pillboxState: rawPillbox === "open" ? "Open" : rawPillbox === "closed" ? "Closed" : "Unknown",
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
