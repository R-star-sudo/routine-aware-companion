import { ChangeEvent, useEffect, useMemo, useState } from "react";
import {
  Activity, ArrowDownRight, ArrowRight, ArrowUpRight, Bell, CalendarDays, Check,
  ChevronLeft, ChevronRight, CircleAlert, Clock3, CloudOff, Download, HeartPulse, Home,
  Leaf, Menu, Mic2, Pill, RefreshCw, Settings2, ShieldCheck, Thermometer,
  Upload, Watch, Waves, X,
} from "lucide-react";
import {
  ActivityEvent, createDemoData, DashboardData, DataMode, dateKey, formatDate,
  formatTime, Forecast, getImportedObservations, getSavedEvents, loadLiveData,
  Observation, parseObservationCsv, saveEvents, saveImportedObservations,
  serializeTelemetryCsv,
} from "./data";

type PageId = "Overview" | "Predictive Calendar" | "Live Monitoring" | "Routine Analytics"
  | "Medication Monitoring" | "Predictions & Events" | "Device Health" | "Settings";

const navigation: { label: PageId; icon: typeof Home }[] = [
  { label: "Overview", icon: Home },
  { label: "Predictive Calendar", icon: CalendarDays },
  { label: "Live Monitoring", icon: Activity },
  { label: "Routine Analytics", icon: Waves },
  { label: "Medication Monitoring", icon: Pill },
  { label: "Predictions & Events", icon: Clock3 },
  { label: "Device Health", icon: ShieldCheck },
  { label: "Settings", icon: Settings2 },
];

const todayKey = dateKey(new Date());

function App() {
  const [page, setPage] = useState<PageId>("Overview");
  const [mode, setMode] = useState<DataMode>("DEMO");
  const [selectedDate, setSelectedDate] = useState(todayKey);
  const [viewMonth, setViewMonth] = useState(() => new Date());
  const [now, setNow] = useState(new Date());
  const [data, setData] = useState<DashboardData>(() => createDemoData());
  const [loading, setLoading] = useState(false);
  const [liveAvailable, setLiveAvailable] = useState(false);
  const [sourceError, setSourceError] = useState("");
  const [notice, setNotice] = useState("");
  const [mobileMenu, setMobileMenu] = useState(false);
  const [chartRange, setChartRange] = useState("24 hours");
  const [movementMetric, setMovementMetric] = useState<"movement" | "heartRate">("movement");
  const [actionBusy, setActionBusy] = useState(false);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  // Probe for LIVE telemetry API on mount and auto-connect
  useEffect(() => {
    let cancelled = false;
    const probe = async () => {
      try {
        const liveData = await loadLiveData();
        if (cancelled) return;
        if (liveData.observations.length > 0) {
          setData(liveData);
          setMode("LIVE");
          setLiveAvailable(true);
          const newest = liveData.observations.at(-1);
          if (newest) {
            setSelectedDate(dateKey(newest.timestamp));
            setViewMonth(new Date(newest.timestamp));
          }
        }
      } catch {
        // Fall back to DEMO mode if API is not running
      }
    };
    void probe();
    return () => { cancelled = true; };
  }, []);

  // Continuous live polling every 2 seconds when in LIVE mode
  useEffect(() => {
    if (mode !== "LIVE") return;
    const pollTimer = window.setInterval(async () => {
      try {
        const liveData = await loadLiveData();
        setData(liveData);
        setLiveAvailable(true);
        setSourceError("");
      } catch {
        // Suppress transient poll errors so the UI stays stable
      }
    }, 2000);
    return () => window.clearInterval(pollTimer);
  }, [mode]);

  useEffect(() => {
    try {
      if (mode === "CSV") {
        setData((current) => ({ ...current, observations: getImportedObservations(), events: [] }));
        setSourceError("");
      } else if (mode === "DEMO") {
        const savedEvents = getSavedEvents();
        setData((current) => ({
          ...current,
          events: [...savedEvents, ...current.events.filter((event) => event.status !== "Demo action")],
        }));
      }
    } catch (error) {
      setSourceError(error instanceof Error ? error.message : "Could not read saved browser data.");
    }
  }, [mode]);

  const refresh = async () => {
    if (mode === "DEMO") {
      setLiveAvailable(false);
      setData(createDemoData());
      setSourceError("");
      setNotice("Demo dataset refreshed.");
      window.setTimeout(() => setNotice(""), 3500);
      return;
    }
    if (mode === "CSV") {
      setLiveAvailable(false);
      try {
        const observations = getImportedObservations();
        setData((current) => ({ ...current, observations }));
        setSourceError(observations.length ? "" : "No CSV observations are saved in this browser yet.");
      } catch (error) {
        setSourceError(error instanceof Error ? error.message : "Could not load saved CSV observations.");
      }
      return;
    }

    setLoading(true);
    setLiveAvailable(false);
    setSourceError("");
    try {
      const liveData = await loadLiveData();
      setData(liveData);
      const newest = liveData.observations.at(-1);
      if (newest) {
        setSelectedDate(dateKey(newest.timestamp));
        setViewMonth(new Date(newest.timestamp));
      }
      setLiveAvailable(true);
    } catch (error) {
      setData({ observations: [], forecasts: [], events: [] });
      setLiveAvailable(false);
      setSourceError(error instanceof Error ? error.message : "Live data could not be loaded.");
    } finally {
      setLoading(false);
    }
  };

  const changeMode = async (nextMode: DataMode) => {
    setMode(nextMode);
    if (nextMode === "LIVE") {
      setLoading(true);
      setLiveAvailable(false);
      setSourceError("");
      try {
        const liveData = await loadLiveData();
        setData(liveData);
        const newest = liveData.observations.at(-1);
        if (newest) {
          setSelectedDate(dateKey(newest.timestamp));
          setViewMonth(new Date(newest.timestamp));
        }
        setLiveAvailable(true);
      } catch (error) {
        setData({ observations: [], forecasts: [], events: [] });
        setLiveAvailable(false);
        setSourceError(error instanceof Error ? error.message : "Live data could not be loaded.");
      } finally {
        setLoading(false);
      }
    } else if (nextMode === "CSV") {
      setLiveAvailable(false);
      try {
        const observations = getImportedObservations();
        setData({ observations, forecasts: [], events: [] });
        setSourceError(observations.length ? "" : "Import a CSV file in Settings to load historical observations.");
      } catch (error) {
        setData({ observations: [], forecasts: [], events: [] });
        setSourceError(error instanceof Error ? error.message : "Could not load saved CSV observations.");
      }
    } else {
      setLiveAvailable(false);
      setData(createDemoData());
      setSourceError("");
    }
  };

  const observationsForDate = useMemo(() => {
    const forSelected = data.observations.filter((item) => dateKey(item.timestamp) === selectedDate);
    if (forSelected.length > 0) return forSelected;
    // In LIVE mode, if the selectedDate doesn't match (e.g. simulation date vs system date),
    // fall back to showing the latest date available so the screen is never blank!
    if (mode === "LIVE" && data.observations.length > 0) {
      const latestDate = dateKey(data.observations.at(-1)!.timestamp);
      return data.observations.filter((item) => dateKey(item.timestamp) === latestDate);
    }
    return [];
  }, [data.observations, selectedDate, mode]);

  const forecast = data.forecasts.find((item) => item.targetDate === selectedDate);
  const currentReading = mode === "LIVE" && data.observations.length > 0
    ? data.observations.at(-1)
    : observationsForDate.at(-1);
  const dataLabel = mode === "LIVE"
    ? loading ? "CONNECTING" : sourceError || !liveAvailable ? "OFFLINE" : "LIVE"
    : mode === "CSV" ? "LOCAL CSV" : mode;
  const modeBadgeClass = dataLabel === "OFFLINE" ? "mode-offline" : dataLabel === "CONNECTING" ? "mode-connecting" : `mode-${mode.toLowerCase()}`;
  const lastUpdated = data.observations.at(-1)?.timestamp;
  const displayedDate = mode === "LIVE" && lastUpdated ? new Date(lastUpdated) : now;

  const recordDemoAction = async (type: "LED reminder command" | "Voice prompt command") => {
    if (mode !== "DEMO" || actionBusy) return;
    setActionBusy(true);
    const event: ActivityEvent = {
      id: `${type}-${Date.now()}`,
      timestamp: new Date().toISOString(),
      type,
      description: type === "LED reminder command"
        ? "Demo command queued; no physical LED was controlled."
        : "Demo voice prompt queued; no audio was generated or played.",
      status: "Demo action",
      device: "Demo action interface",
      source: "DEMO",
    };
    const events = [event, ...data.events];
    setData((current) => ({ ...current, events }));
    try {
      saveEvents(events.filter((item) => item.status === "Demo action"));
      setNotice("Demo action recorded. No hardware command was sent.");
    } catch (error) {
      setSourceError(error instanceof Error ? error.message : "Could not save the demo action.");
    } finally {
      setActionBusy(false);
      window.setTimeout(() => setNotice(""), 4500);
    }
  };

  const importCsv = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      const parsed = parseObservationCsv(await file.text());
      saveImportedObservations(parsed.observations);
      setData({ observations: parsed.observations, forecasts: [], events: [] });
      setMode("CSV");
      setSelectedDate(dateKey(parsed.observations.at(-1)!.timestamp));
      setViewMonth(new Date(parsed.observations.at(-1)!.timestamp));
      setSourceError("");
      setNotice(`Imported ${parsed.observations.length} observations; ${parsed.rejectedRows} invalid and ${parsed.duplicateRows} duplicate rows skipped.`);
      setPage("Live Monitoring");
      window.setTimeout(() => setNotice(""), 6000);
    } catch (error) {
      setSourceError(error instanceof Error ? error.message : "CSV import failed.");
    }
  };

  const downloadTelemetryCsv = () => {
    if (data.observations.length === 0 || mode === "DEMO") return;

    const csv = serializeTelemetryCsv(data.observations);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    link.href = url;
    link.download = `routine-aware-telemetry-${timestamp}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);

    setNotice(mode === "LIVE"
      ? `Downloaded ${data.observations.length} live telemetry records. Live history currently provides up to 500 records.`
      : `Downloaded ${data.observations.length} imported CSV observations.`);
    window.setTimeout(() => setNotice(""), 6000);
  };

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileMenu ? "sidebar-open" : ""}`}>
        <div className="brand">
          <div className="brand-mark"><Leaf size={21} strokeWidth={2.4} /></div>
          <div><strong>routine<span>+</span></strong><small>COMPANION</small></div>
          <button className="icon-button sidebar-close" aria-label="Close navigation" onClick={() => setMobileMenu(false)}><X size={18} /></button>
        </div>
        <div className="nav-caption">WORKSPACE</div>
        <nav aria-label="Main navigation">
          {navigation.map(({ label, icon: Icon }) => (
            <button key={label} className={`nav-item ${page === label ? "active" : ""}`} onClick={() => { setPage(label); setMobileMenu(false); }}>
              <Icon size={18} strokeWidth={1.9} /><span>{label}</span>
              {label === "Predictions & Events" && data.events.length > 0 && <i className="nav-count">{data.events.length}</i>}
            </button>
          ))}
        </nav>
        <div className="sidebar-spacer" />
        <div className="privacy-card">
          <div className="privacy-icon"><ShieldCheck size={17} /></div>
          <strong>Privacy by design</strong>
          <span>Observations, predictions and actions stay clearly distinguished.</span>
        </div>
        <button className="profile-button">
          <div className="avatar">RC</div>
          <div><strong>Care team</strong><span>Local dashboard</span></div>
          <ChevronRight size={16} />
        </button>
      </aside>

      {mobileMenu && <button className="scrim" aria-label="Close menu" onClick={() => setMobileMenu(false)} />}

      <main className="main-area">
        <header className="topbar">
          <button className="icon-button mobile-menu-button" aria-label="Open navigation" onClick={() => setMobileMenu(true)}><Menu size={20} /></button>
          <div className="breadcrumb">Workspace <ChevronRight size={14} /> <strong>{page}</strong></div>
          <div className="topbar-actions">
            <button
              className={`button ${mode === "LIVE" ? "button-primary" : "button-secondary"}`}
              style={{ height: "30px", fontSize: "11px", padding: "0 10px", display: "inline-flex", alignItems: "center", gap: "6px" }}
              title="Click to toggle between Live Hardware Telemetry and Demo Mode"
              onClick={() => void changeMode(mode === "LIVE" ? "DEMO" : "LIVE")}
            >
              <span className={`connection-dot ${mode === "LIVE" && liveAvailable && !sourceError ? "dot-green" : "dot-blue"}`} style={{ display: "inline-block" }} />
              {mode === "LIVE" ? "● LIVE STREAMING" : "DEMO MODE (Click for Live)"}
            </button>
            <div className="connection-label">
              <span className={`connection-dot ${mode === "LIVE" && liveAvailable && !sourceError && !loading ? "dot-green" : mode === "DEMO" ? "dot-blue" : "dot-muted"}`} />
              {mode === "LIVE" ? (loading ? "Checking source" : sourceError || !liveAvailable ? "Unavailable" : lastUpdated ? `${data.observations.length} records · Live` : "Connected · waiting for readings") : mode === "CSV" ? "Local data" : "Demo mode"}
            </div>
            <span className={`mode-badge ${modeBadgeClass}`}>{dataLabel}</span>
            <span className="topbar-divider" />
            <span className="topbar-date">{mode === "LIVE" && lastUpdated ? "SIM · " : ""}{displayedDate.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })}</span>
            <button className="icon-button notification-button" aria-label="Notifications" onClick={() => setPage("Predictions & Events")}><Bell size={18} /><i /></button>
            <button className="icon-button mobile-menu-button" aria-label="Refresh data" onClick={() => void refresh()}><RefreshCw size={17} /></button>
          </div>
        </header>

        <div className="page-container">
          <div className="page-heading">
            <div><div className="eyebrow">ROUTINE-AWARE COMPANION <span className="eyebrow-line" /></div><h1>{page}</h1><p>{pageDescription(page)}</p></div>
            <div className="heading-actions">
              <button
                className="button button-secondary"
                onClick={downloadTelemetryCsv}
                disabled={mode === "DEMO" || data.observations.length === 0 || loading || (mode === "LIVE" && !liveAvailable)}
                title={mode === "LIVE" ? "Download the live telemetry records currently loaded in the dashboard (up to 500)." : "Download the imported telemetry observations as CSV."}
              ><Download size={15} />Download CSV</button>
              {page !== "Settings" && <button className="button button-secondary" onClick={() => void refresh()} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} />{loading ? "Syncing" : "Refresh"}</button>}
              {page !== "Settings" && <button className="date-chip" onClick={() => setPage("Predictive Calendar")}><CalendarDays size={16} />{formatDate(selectedDate)}<ChevronRight size={14} /></button>}
            </div>
          </div>

          {mode === "DEMO" && <div className="demo-banner"><span className="demo-banner-dot" /><strong>DEMO DATA</strong><span>Illustrative sample data only — not readings from a person or connected hardware.</span><button className="button button-primary" style={{ height: "24px", fontSize: "10px", marginLeft: "auto", padding: "0 8px" }} onClick={() => void changeMode("LIVE")}>Switch to Live Telemetry →</button></div>}
          {mode === "CSV" && <div className="csv-banner"><Upload size={15} /><strong>LOCAL CSV</strong><span>Imported observations saved in this browser. No live connection is active.</span><button onClick={() => setPage("Settings")}>Manage import <ArrowRight size={13} /></button></div>}
          {sourceError && <div className="error-banner" role="alert"><CircleAlert size={17} /><span>{sourceError}</span><button aria-label="Dismiss error" onClick={() => setSourceError("")}><X size={16} /></button></div>}
          {notice && <div className="notice-banner" role="status"><Check size={16} />{notice}</div>}

          {page === "Overview" && <Overview data={data} observations={observationsForDate} forecast={forecast} current={currentReading} selectedDate={selectedDate} mode={mode} onNavigate={setPage} />}
          {page === "Predictive Calendar" && <CalendarPage data={data} selectedDate={selectedDate} setSelectedDate={setSelectedDate} viewMonth={viewMonth} setViewMonth={setViewMonth} forecast={forecast} observations={observationsForDate} mode={mode} onNavigate={setPage} />}
          {page === "Live Monitoring" && <MonitoringPage observations={observationsForDate} range={chartRange} setRange={setChartRange} movementMetric={movementMetric} setMovementMetric={setMovementMetric} selectedDate={selectedDate} mode={mode} onImport={() => setPage("Settings")} />}
          {page === "Routine Analytics" && <AnalyticsPage data={data} mode={mode} />}
          {page === "Medication Monitoring" && <MedicationPage data={data} observations={observationsForDate} forecast={forecast} mode={mode} />}
          {page === "Predictions & Events" && <EventsPage events={data.events} forecasts={data.forecasts} selectedDate={selectedDate} mode={mode} onAction={recordDemoAction} actionBusy={actionBusy} />}
          {page === "Device Health" && <DeviceHealthPage mode={mode} lastUpdated={lastUpdated} loading={loading} liveAvailable={liveAvailable} />}
          {page === "Settings" && <SettingsPage mode={mode} onModeChange={changeMode} onImport={importCsv} onClear={() => {
            try {
              localStorage.removeItem("rac.csv-observations.v1");
              if (mode === "CSV") setData({ observations: [], forecasts: [], events: [] });
              setSourceError("");
              setNotice("Local CSV observations were cleared.");
              window.setTimeout(() => setNotice(""), 4500);
            }
            catch (error) { setSourceError(error instanceof Error ? error.message : "Could not clear local observations."); }
          }} />}
          <footer className="footer"><span>Routine-Aware Companion <b>·</b> {mode === "DEMO" ? "Demonstration workspace" : mode === "CSV" ? "Browser-local records" : "Live telemetry API"}</span><span>Observations are not diagnoses. Pillbox interaction does not confirm medication use.</span></footer>
        </div>
      </main>
    </div>
  );
}

function pageDescription(page: PageId) {
  const descriptions: Record<PageId, string> = {
    Overview: "A clear view of recent observations and upcoming routine forecasts.",
    "Predictive Calendar": "Explore saved forecasts and available observations by date.",
    "Live Monitoring": "Review sensor observations for the selected day.",
    "Routine Analytics": "Understand recorded activity patterns without overstating what sensors show.",
    "Medication Monitoring": "Track pillbox interactions and related forecast outcomes.",
    "Predictions & Events": "Review what was forecast, observed, and explicitly actioned.",
    "Device Health": "Check the reported status of each system component.",
    Settings: "Choose a data source and manage local observation imports.",
  };
  return descriptions[page];
}

function Panel({ title, subtitle, action, children, className = "" }: { title: string; subtitle?: string; action?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return <section className={`panel ${className}`}><div className="panel-heading"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>{action}</div>{children}</section>;
}

function MetricCard({ icon: Icon, label, value, unit, detail, trend, tone = "blue" }: {
  icon: typeof HeartPulse; label: string; value: string; unit?: string; detail: string; trend?: "up" | "down" | "flat"; tone?: string;
}) {
  return <article className="metric-card"><div className={`metric-icon tone-${tone}`}><Icon size={18} /></div><div className="metric-topline">{label}{trend && <span className={`trend ${trend === "up" ? "trend-up" : trend === "down" ? "trend-down" : ""}`}>{trend === "up" ? <ArrowUpRight size={13} /> : trend === "down" ? <ArrowDownRight size={13} /> : <ArrowRight size={13} />} {trend === "flat" ? "steady" : "trend"}</span>}</div><div className="metric-value">{value}{unit && <small>{unit}</small>}</div><div className="metric-detail">{detail}</div></article>;
}

function PredictionMeter({ forecast, mode }: { forecast?: Forecast; mode: DataMode }) {
  const score = forecast?.wakeConfidence;
  const scorePercent = score == null ? null : Math.round(score * 100);
  const sourceLabel = mode === "DEMO" ? "Illustrative demo score" : mode === "LIVE" ? "Model-provided score" : "No model score in CSV";
  const tickLabels = [0, 20, 40, 60, 80, 100];
  return <div className="prediction-meter">
    <div className="prediction-meter-heading"><strong>Prediction Score</strong><span>{mode === "DEMO" ? "DEMO" : mode === "LIVE" ? "MODEL" : "CSV"}</span></div>
    <svg
      className="prediction-gauge"
      viewBox="0 0 340 205"
      role="meter"
      aria-label="Machine-learning prediction confidence score"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={scorePercent ?? undefined}
      aria-valuetext={scorePercent === null ? "Score unavailable" : `${scorePercent}% ${sourceLabel.toLowerCase()}`}
    >
      <path className="prediction-gauge-track" d="M 24 166 A 146 146 0 0 1 316 166" pathLength="100" />
      {scorePercent !== null && <path
        className="prediction-gauge-value"
        d="M 24 166 A 146 146 0 0 1 316 166"
        pathLength="100"
        strokeDasharray="100 100"
        strokeDashoffset={100 - scorePercent}
      />}
      {tickLabels.map((tick) => {
        const angle = Math.PI * (1 - tick / 100);
        const x = 170 + 110 * Math.cos(angle);
        const y = 166 - 110 * Math.sin(angle);
        return <text className="prediction-gauge-tick" key={tick} x={x} y={y} textAnchor="middle" dominantBaseline="middle">{tick}%</text>;
      })}
      <text className="prediction-gauge-score" x="170" y="139" textAnchor="middle" dominantBaseline="middle">{scorePercent === null ? "—" : `${scorePercent}%`}</text>
      <text className="prediction-gauge-caption" x="170" y="160" textAnchor="middle" dominantBaseline="middle">ML TRUST SCORE</text>
    </svg>
    <p>{scorePercent === null ? "No confidence score supplied for this forecast." : sourceLabel} · This score reflects the forecast confidence provided by the model; it is not a guarantee of accuracy.</p>
  </div>;
}

function Overview({ data, observations, forecast, current, selectedDate, mode, onNavigate }: {
  data: DashboardData; observations: Observation[]; forecast?: Forecast; current?: Observation; selectedDate: string; mode: DataMode; onNavigate: (page: PageId) => void;
}) {
  const validHeart = [...observations].reverse().find((item) => item.heartRateValid && item.heartRate !== null);
  const lastEvent = data.events[0];
  const heart = validHeart ? String(validHeart.heartRate) : "—";
  const sensorTemp = current?.sensorTemperature;
  const movement = current?.movement;
  const pillState = current?.pillboxState ?? "Unknown";
  const dayEvents = data.events.filter((event) => dateKey(event.timestamp) === selectedDate);
  return <>
    <div className="overview-hero-grid">
      <div className="metric-grid overview-metric-grid">
        <MetricCard icon={HeartPulse} label="HEART RATE" value={heart} unit={validHeart ? "bpm" : undefined} detail={validHeart ? `Valid estimate · ${formatTime(validHeart.timestamp)}` : "No valid estimate for this date"} tone="rose" />
        <MetricCard icon={Thermometer} label="SENSOR TEMPERATURE" value={sensorTemp === null || sensorTemp === undefined ? "—" : sensorTemp.toFixed(1)} unit={sensorTemp === null || sensorTemp === undefined ? undefined : "°C"} detail={sensorTemp === null || sensorTemp === undefined ? "No valid sensor reading" : "Skin-site sensor reading"} tone="amber" />
        <MetricCard icon={Activity} label="MOVEMENT" value={movement === null || movement === undefined ? "—" : movement < 0.3 ? "Low" : movement < 0.65 ? "Moderate" : "Active"} detail={movement === null || movement === undefined ? "No movement data available" : `Intensity ${movement.toFixed(2)} · ${formatTime(current!.timestamp)}`} tone="blue" />
        <MetricCard icon={Watch} label="ROUTINE STATE" value={current?.routineState ?? "Unknown"} detail={current ? `Observed at ${formatTime(current.timestamp)} · inferred` : "No routine state available"} tone="green" />
        <MetricCard icon={Clock3} label="NEXT EVENT FORECAST" value={forecast?.wakeTime ?? "Unavailable"} unit={forecast?.wakeTime ? "expected wake" : undefined} detail={forecast ? `Demo forecast · generated ${formatTime(forecast.generatedAt)}` : "No saved forecast for this date"} tone="violet" />
        <MetricCard icon={Pill} label="PILLBOX INTERACTION" value={forecast?.missedInteractionProbability === null || forecast?.missedInteractionProbability === undefined ? "Unavailable" : `${Math.round(forecast.missedInteractionProbability * 100)}%`} detail={`Expected ${forecast?.medicationWindow ?? "window not configured"} · state ${pillState.toLowerCase()}`} tone="teal" />
      </div>
      <div className="prediction-score-column">
        <PredictionMeter forecast={forecast} mode={mode} />
      </div>
    </div>

    <div className="overview-grid overview-content-grid">
      <Panel title="Movement & heart rate" subtitle={`Observation trend · ${formatDate(selectedDate)}`} action={<button className="text-button" onClick={() => onNavigate("Live Monitoring")}>View details <ArrowRight size={14} /></button>}>
        {observations.length ? <LineChart observations={observations} primary="movement" secondary="heartRate" height={220} /> : <EmptyState title="No observations for this date" detail="Choose a date with available records or import a CSV file." />}
        <div className="chart-legend"><span><i className="legend-blue" />Movement intensity</span><span><i className="legend-orange" />Heart rate (bpm)</span><small>{mode === "DEMO" ? "DEMO illustration" : mode === "LIVE" ? "LIVE telemetry" : "Imported CSV"}</small></div>
      </Panel>
      <Panel title="Selected day" subtitle={formatDate(selectedDate)} action={<button className="text-button" onClick={() => onNavigate("Predictive Calendar")}>Open calendar <ArrowRight size={14} /></button>}>
        <div className="day-summary">
          <div className="day-summary-row"><span><i className="summary-dot dot-blue" />Saved observations</span><strong>{observations.length}</strong></div>
          <div className="day-summary-row"><span><i className="summary-dot dot-violet" />Saved forecast</span><strong>{forecast ? "Available" : "Not available"}</strong></div>
          <div className="day-summary-row"><span><i className="summary-dot dot-green" />Recorded events</span><strong>{dayEvents.length}</strong></div>
          <div className="summary-divider" />
          <div className="forecast-highlight"><div className="forecast-sun">☀</div><div><strong>{forecast ? `Expected wake ${forecast.wakeWindow ?? forecast.wakeTime}` : "No supported forecast"}</strong><span>{forecast ? `${forecast.source} · ${forecast.status}` : "The model has not provided a forecast for this date."}</span></div></div>
          {mode === "LIVE" && !forecast && <p className="muted-note">Model not available for this date. No forecast has been generated.</p>}
        </div>
      </Panel>
    </div>

    <div className="lower-grid overview-content-grid">
      <Panel title="Recent system activity" subtitle="Predictions, observations and actions are labelled separately" action={<button className="text-button" onClick={() => onNavigate("Predictions & Events")}>View all <ArrowRight size={14} /></button>}>
        {data.events.length ? <EventList events={data.events.slice(0, 4)} /> : <EmptyState title="No events recorded" detail="Events will appear when a source provides them." />}
      </Panel>
      <Panel title="System data source" subtitle="Current workspace status">
        <div className="source-status"><div className={`source-symbol ${mode === "DEMO" ? "symbol-demo" : mode === "CSV" ? "symbol-csv" : "symbol-live"}`}>{mode === "LIVE" ? <CloudOff size={19} /> : mode === "CSV" ? <Upload size={19} /> : <Activity size={19} />}</div><div><strong>{mode === "LIVE" ? "Live API source" : mode === "CSV" ? "Browser-local CSV" : "Demonstration dataset"}</strong><span>{mode === "LIVE" ? "No direct browser-to-serial connection" : mode === "CSV" ? "Local records only; no predictions included" : "Illustrative data, visibly labelled"}</span></div><span className={`status-pill ${mode === "LIVE" ? "status-neutral" : "status-blue"}`}>{mode}</span></div>
        <div className="source-meta"><span>Last observation</span><strong>{current ? formatTime(current.timestamp) : "Not available"}</strong></div>
        <div className="source-meta"><span>Prediction engine</span><strong>{forecast ? "Saved forecast" : "Not available"}</strong></div>
      </Panel>
    </div>
    {lastEvent && <p className="disclaimer"><ShieldCheck size={15} /> Sensor records are observations, not confirmed medical facts. A pillbox opening does not prove medication was taken.</p>}
  </>;
}

function CalendarPage({ data, selectedDate, setSelectedDate, viewMonth, setViewMonth, forecast, observations, mode, onNavigate }: {
  data: DashboardData; selectedDate: string; setSelectedDate: (date: string) => void; viewMonth: Date; setViewMonth: (date: Date) => void; forecast?: Forecast; observations: Observation[]; mode: DataMode; onNavigate: (page: PageId) => void;
}) {
  const year = viewMonth.getFullYear();
  const month = viewMonth.getMonth();
  const firstDay = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const today = dateKey(new Date());
  const forecastDays = new Set(data.forecasts.map((item) => item.targetDate));
  const observedDays = new Set(data.observations.map((item) => dateKey(item.timestamp)));
  const eventDays = new Set(data.events.map((item) => dateKey(item.timestamp)));
  const selectedEvents = data.events.filter((event) => dateKey(event.timestamp) === selectedDate);
  const selectedForecast = forecast;
  const nextMonth = (delta: number) => setViewMonth(new Date(year, month + delta, 1));
  return <div className="calendar-layout">
    <Panel title="Predictive calendar" subtitle="Choose a day to review its saved observations, forecasts and outcomes." className="calendar-panel">
      <div className="calendar-toolbar"><button className="icon-button" aria-label="Previous month" onClick={() => nextMonth(-1)}><ChevronLeft size={18} /></button><strong>{viewMonth.toLocaleDateString([], { month: "long", year: "numeric" })}</strong><button className="icon-button" aria-label="Next month" onClick={() => nextMonth(1)}><ChevronRight size={18} /></button></div>
      <div className="calendar-grid calendar-weekdays">{["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((day) => <span key={day}>{day}</span>)}</div>
      <div className="calendar-grid">
        {Array.from({ length: firstDay }, (_, index) => <span className="calendar-blank" key={`blank-${index}`} />)}
        {Array.from({ length: daysInMonth }, (_, index) => {
          const date = new Date(year, month, index + 1);
          const key = dateKey(date);
          const hasForecast = forecastDays.has(key);
          const hasObservation = observedDays.has(key);
          const hasEvents = eventDays.has(key);
          return <button key={key} className={`calendar-day ${selectedDate === key ? "selected" : ""} ${today === key ? "today" : ""}`} onClick={() => setSelectedDate(key)} aria-label={`${formatDate(key)}${hasForecast ? ", forecast available" : ""}${hasObservation ? ", observations available" : ""}`}>
            <span>{index + 1}</span><div>{hasObservation && <i className="day-dot dot-blue" />}{hasForecast && <i className="day-dot dot-violet" />}{hasEvents && <i className="day-dot dot-green" />}</div>
          </button>;
        })}
      </div>
      <div className="calendar-legend"><span><i className="legend-blue" />Observations</span><span><i className="legend-violet" />Saved forecast</span><span><i className="legend-green" />Recorded event</span></div>
      <div className="calendar-note"><CircleAlert size={16} /><span>Dates without a marker have no records available. Future sensor readings are never generated.</span></div>
    </Panel>
    <div className="calendar-detail-column">
      <Panel title="Daily prediction view" subtitle={formatDate(selectedDate)}>
        <div className="date-status-row"><span className={`status-pill ${observations.length ? "status-green" : "status-neutral"}`}>{observations.length ? `${observations.length} observations` : "No observations"}</span><span className={`status-pill ${selectedForecast ? "status-violet" : "status-neutral"}`}>{selectedForecast ? selectedForecast.status : "No saved forecast"}</span></div>
        {selectedForecast ? <div className="calendar-forecast">
          <div className="forecast-time"><span>HISTORICAL WAKE BASELINE</span><strong>{selectedForecast.typicalWakeTime ?? "Not provided"}</strong><small>Baseline supplied by the saved forecast</small></div>
          <div className="forecast-time"><span>EXPECTED WAKE WINDOW</span><strong>{selectedForecast.wakeWindow ?? selectedForecast.wakeTime ?? "Not provided"}</strong><small>Forecast generated {formatTime(selectedForecast.generatedAt)} · {selectedForecast.source}</small></div>
          <div className="forecast-probability"><span>EARLY-RISE PROBABILITY</span><strong>{selectedForecast.earlyRiseProbability === null ? "Not available" : `${Math.round(selectedForecast.earlyRiseProbability * 100)}%`}</strong><small>{selectedForecast.earlyRiseProbability === null ? "No supported score" : "Saved demonstration score"}</small></div>
          <div className="forecast-time"><span>EXPECTED PILLBOX WINDOW</span><strong>{selectedForecast.medicationWindow ?? "Not configured"}</strong><small>Interaction does not confirm medication use</small></div>
        </div> : <EmptyState title={selectedDate > today ? "No forecast for this future date" : "No saved forecast for this date"} detail={mode === "LIVE" ? "The prediction service has not provided a forecast. No values are estimated by the dashboard." : "Only dates with saved demonstration forecasts show forecast details."} />}
        <PredictionMeter forecast={selectedForecast} mode={mode} />
        {selectedDate > today && <div className="pending-box"><Clock3 size={16} /><span>Actual outcomes are pending for a future date.</span></div>}
        {selectedDate <= today && selectedForecast && <div className="compare-box"><div><strong>Predicted vs. observed</strong><span>Original forecast is retained; evaluation requires a corresponding observed event.</span></div><span className="status-pill status-neutral">{selectedEvents.length ? "Event available" : "Outcome pending"}</span></div>}
      </Panel>
      <Panel title="Hourly forecast" subtitle={selectedForecast ? `${selectedForecast.source} · generated ${formatTime(selectedForecast.generatedAt)}` : "Forecast windows are only shown when saved"}>
        {selectedForecast?.hourly?.length ? <ForecastTimeline forecast={selectedForecast} /> : <EmptyState title="No hourly forecast available" detail="The selected date has no saved hourly prediction windows." />}
      </Panel>
      <Panel title="Day overview" subtitle="Available records for the selected date">
        <div className="day-summary-row"><span><i className="summary-dot dot-blue" />Sensor observations</span><strong>{observations.length}</strong></div>
        <div className="day-summary-row"><span><i className="summary-dot dot-violet" />Forecast</span><strong>{selectedForecast ? "Saved" : "Unavailable"}</strong></div>
        <div className="day-summary-row"><span><i className="summary-dot dot-green" />Events</span><strong>{selectedEvents.length}</strong></div>
        <button className="button button-primary full-button" onClick={() => onNavigate(observations.length ? "Live Monitoring" : "Predictions & Events")}>{observations.length ? "Review observations" : "Review event history"}<ArrowRight size={15} /></button>
      </Panel>
    </div>
  </div>;
}

function ForecastTimeline({ forecast }: { forecast: Forecast }) {
  return <div className="forecast-timeline">{forecast.hourly?.map((hour) => <div className="forecast-hour" key={`${hour.window}-${hour.event}`}><div className="forecast-hour-time">{hour.window}</div><div className="forecast-hour-line"><i /></div><div className="forecast-hour-detail"><strong>{hour.routineState}</strong><span>{hour.event}</span><small>{hour.probability === null ? "Score unavailable" : `${Math.round(hour.probability * 100)}% probability`} · {hour.confidence === null ? "confidence unavailable" : `${Math.round(hour.confidence * 100)}% confidence`}</small></div><span className={`status-pill ${hour.status === "Outcome pending" ? "status-amber" : "status-violet"}`}>{hour.status}</span></div>)}</div>;
}

function LineChart({ observations, primary, secondary, height = 190 }: { observations: Observation[]; primary: "movement" | "heartRate"; secondary?: "heartRate"; height?: number }) {
  const width = 680;
  const margin = { top: 13, right: 14, bottom: 27, left: 26 };
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;
  const samples = observations.slice(-24);
  const values = samples.flatMap((item) => {
    const value = primary === "movement" ? item.movement : item.heartRateValid ? item.heartRate : null;
    return value === null ? [] : [value];
  });
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 1;
  const range = Math.max(max - min, 1);
  const points = samples.flatMap((item, index) => {
    const value = primary === "movement" ? item.movement : item.heartRateValid ? item.heartRate : null;
    if (value === null) return [];
    const x = margin.left + (index / Math.max(samples.length - 1, 1)) * innerWidth;
    const y = margin.top + innerHeight - ((value - min) / range) * innerHeight;
    return [{ x, y, value, label: formatTime(item.timestamp) }];
  });
  const segments: typeof points[] = [];
  let segment: typeof points = [];
  samples.forEach((item, index) => {
    const value = primary === "movement" ? item.movement : item.heartRateValid ? item.heartRate : null;
    if (value === null) {
      if (segment.length) segments.push(segment);
      segment = [];
      return;
    }
    const x = margin.left + (index / Math.max(samples.length - 1, 1)) * innerWidth;
    const y = margin.top + innerHeight - ((value - min) / range) * innerHeight;
    segment.push({ x, y, value, label: formatTime(item.timestamp) });
  });
  if (segment.length) segments.push(segment);
  const paths = segments.map((items) => items.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" "));
  return <div className="chart-wrap"><svg className="line-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${primary} observations chart`}>
    {[0, 1, 2, 3].map((line) => <g key={line}><line x1={margin.left} x2={width - margin.right} y1={margin.top + line * (innerHeight / 3)} y2={margin.top + line * (innerHeight / 3)} className="chart-gridline" /><text x={2} y={margin.top + line * (innerHeight / 3) + 4} className="chart-axis-label">{Math.round(max - (line / 3) * range)}</text></g>)}
    {paths.map((path, index) => <path key={index} d={path} className="chart-path chart-path-blue" />)}
    {points.map((point) => <circle key={`${point.label}-${point.x}`} cx={point.x} cy={point.y} r="3.4" className="chart-point"><title>{point.label} · {point.value}</title></circle>)}
    {samples.map((item, index) => index % Math.max(1, Math.floor(samples.length / 5)) === 0 ? <text key={item.timestamp} x={margin.left + (index / Math.max(samples.length - 1, 1)) * innerWidth} y={height - 5} textAnchor="middle" className="chart-axis-label">{formatTime(item.timestamp)}</text> : null)}
    {secondary && samples.map((item, index) => {
      if (item.heartRate === null || !item.heartRateValid) return null;
      const x = margin.left + (index / Math.max(samples.length - 1, 1)) * innerWidth;
      const y = margin.top + innerHeight - ((item.heartRate - 55) / 40) * innerHeight;
      return <circle key={`heart-${item.timestamp}`} cx={x} cy={Math.min(margin.top + innerHeight, Math.max(margin.top, y))} r="2.5" className="chart-point-orange"><title>{formatTime(item.timestamp)} · {item.heartRate} bpm</title></circle>;
    })}
  </svg>{!points.length && <div className="chart-overlay">No valid readings to plot</div>}</div>;
}

function MonitoringPage({ observations, range, setRange, movementMetric, setMovementMetric, selectedDate, mode, onImport }: {
  observations: Observation[]; range: string; setRange: (value: string) => void; movementMetric: "movement" | "heartRate"; setMovementMetric: (value: "movement" | "heartRate") => void; selectedDate: string; mode: DataMode; onImport: () => void;
}) {
  const rangeMinutes: Record<string, number> = { "5 minutes": 5, "15 minutes": 15, "1 hour": 60, "6 hours": 360, "24 hours": 1440 };
  const latestTimestamp = observations.at(-1)?.timestamp;
  const windowStart = latestTimestamp ? Date.parse(latestTimestamp) - rangeMinutes[range] * 60_000 : 0;
  const visibleObservations = observations.filter((item) => Date.parse(item.timestamp) >= windowStart);
  const availableRanges = Object.entries(rangeMinutes).filter(([, minutes]) => {
    if (!latestTimestamp) return false;
    const cutoff = Date.parse(latestTimestamp) - minutes * 60_000;
    return observations.some((item) => Date.parse(item.timestamp) >= cutoff);
  }).map(([label]) => label);
  const validObservations = observations.filter((item) => item.heartRateValid && item.heartRate !== null);
  const averageHeart = validObservations.length ? Math.round(validObservations.reduce((sum, item) => sum + item.heartRate!, 0) / validObservations.length) : null;
  const latest = observations.at(-1);
  return <>
    <div className="monitor-toolbar"><div><span className="live-indicator" />{mode === "LIVE" ? "SOURCE STATUS" : mode === "CSV" ? "IMPORTED OBSERVATIONS" : "DEMO OBSERVATIONS"}<strong>{mode === "LIVE" ? `${observations.length} API records · ${formatDate(selectedDate)}` : `${observations.length} records · ${formatDate(selectedDate)}`}</strong></div><div className="toolbar-controls"><select aria-label="Chart time range" value={range} onChange={(event) => setRange(event.target.value)}>{["5 minutes", "15 minutes", "1 hour", "6 hours", "24 hours"].map((value) => <option key={value} disabled={!availableRanges.includes(value)}>{value}</option>)}</select>{mode !== "DEMO" && <button className="button button-secondary" onClick={onImport}><Upload size={15} />Import CSV</button>}</div></div>
    <div className="metric-grid metric-grid-three"><MetricCard icon={HeartPulse} label="LATEST VALID HEART RATE" value={latest?.heartRateValid && latest.heartRate !== null ? String(latest.heartRate) : "—"} unit={latest?.heartRateValid && latest.heartRate !== null ? "bpm" : undefined} detail={latest ? `Latest record ${formatTime(latest.timestamp)}` : "No valid reading"} tone="rose" /><MetricCard icon={Activity} label="AVERAGE HEART RATE" value={averageHeart === null ? "—" : String(averageHeart)} unit={averageHeart === null ? undefined : "bpm"} detail={validObservations.length ? `${validObservations.length} valid records · selected date` : "No valid readings for selected date"} tone="blue" /><MetricCard icon={Thermometer} label="SENSOR TEMPERATURE" value={latest?.sensorTemperature === null || latest?.sensorTemperature === undefined ? "—" : latest.sensorTemperature.toFixed(1)} unit={latest?.sensorTemperature == null ? undefined : "°C"} detail="Skin-site sensor reading, when available" tone="amber" /></div>
    <div className="overview-grid">
      <Panel title="Heart-rate trend" subtitle="MAX30102 estimate · BPM" className="wide-panel">{visibleObservations.some((item) => item.heartRateValid && item.heartRate !== null) ? <><LineChart observations={visibleObservations} primary="heartRate" height={260} /><div className="chart-legend"><span><i className="legend-blue" />Valid estimates (bpm)</span><small>{visibleObservations.filter((item) => !item.heartRateValid || item.heartRate === null).length} missing or invalid samples omitted</small></div></> : <EmptyState title="No valid heart-rate estimates" detail="Signal quality or source data may be unavailable for this date and range." />}</Panel>
      <Panel title="Movement and activity" subtitle="MPU6050-derived movement intensity" action={<select className="compact-select" value={movementMetric} onChange={(event) => setMovementMetric(event.target.value as "movement" | "heartRate")}><option value="movement">Movement</option><option value="heartRate">Heart rate</option></select>}>{visibleObservations.length ? <LineChart observations={visibleObservations} primary={movementMetric} height={260} /> : <EmptyState title="No movement data" detail="No observations are available for this date and range." />}</Panel>
    </div>
    <div className="overview-grid">
      <Panel title="Temperature & environment" subtitle="Wearable and ambient sensors shown separately">
        {observations.some((item) => item.sensorTemperature !== null || item.ambientTemperature !== null) ? <TemperatureChart observations={observations} /> : <EmptyState title="No temperature readings" detail="No valid sensor or ambient readings are available for this date." />}
        <div className="sensor-row"><div className="sensor-row-icon tone-amber"><Thermometer size={17} /></div><div><strong>Sensor temperature</strong><span>DS18B20 · skin-site measurement</span></div><b>{latest?.sensorTemperature == null ? "—" : `${latest.sensorTemperature.toFixed(1)} °C`}</b></div>
        <div className="sensor-row"><div className="sensor-row-icon tone-blue"><Thermometer size={17} /></div><div><strong>Ambient temperature</strong><span>DHT11 · base station</span></div><b>{latest?.ambientTemperature == null ? "—" : `${latest.ambientTemperature.toFixed(1)} °C`}</b></div>
        <div className="sensor-row"><div className="sensor-row-icon tone-green"><Waves size={17} /></div><div><strong>Relative humidity</strong><span>DHT11 · base station</span></div><b>{latest?.humidity == null ? "—" : `${latest.humidity}%`}</b></div>
      </Panel>
      <Panel title="Pillbox state" subtitle="Interaction observation only">
        <div className="pillbox-state"><div className={`pillbox-icon ${latest?.pillboxState === "Open" ? "pillbox-open" : ""}`}><Pill size={24} /></div><div><span>Current reported state</span><strong>{latest?.pillboxState ?? "Unknown"}</strong><small>{latest ? `Observation at ${formatTime(latest.timestamp)}` : "No pillbox record for this date"}</small></div></div>
        <div className="disclaimer compact-disclaimer"><ShieldCheck size={15} />Opening the container does not confirm medication was taken.</div>
      </Panel>
    </div>
  </>;
}

function TemperatureChart({ observations }: { observations: Observation[] }) {
  const samples = observations.slice(-24);
  const values = samples.flatMap((item) => [item.sensorTemperature, item.ambientTemperature].filter((value): value is number => value !== null));
  const min = Math.floor(Math.min(...values) - 1);
  const max = Math.ceil(Math.max(...values) + 1);
  const width = 680;
  const height = 150;
  const margin = { top: 10, right: 12, bottom: 24, left: 26 };
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;
  const pathFor = (key: "sensorTemperature" | "ambientTemperature") => {
    const segments: { x: number; y: number; timestamp: string; value: number }[][] = [];
    let segment: { x: number; y: number; timestamp: string; value: number }[] = [];
    samples.forEach((item, index) => {
      const value = item[key];
      if (value === null) {
        if (segment.length) segments.push(segment);
        segment = [];
        return;
      }
      segment.push({
        x: margin.left + (index / Math.max(samples.length - 1, 1)) * innerWidth,
        y: margin.top + innerHeight - ((value - min) / Math.max(max - min, 1)) * innerHeight,
        timestamp: item.timestamp,
        value,
      });
    });
    if (segment.length) segments.push(segment);
    return segments;
  };
  const sensorSegments = pathFor("sensorTemperature");
  const ambientSegments = pathFor("ambientTemperature");
  return <div className="temperature-chart-wrap"><svg className="line-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Sensor and ambient temperature chart in degrees Celsius">
    {[0, 1, 2].map((line) => <g key={line}><line x1={margin.left} x2={width - margin.right} y1={margin.top + line * (innerHeight / 2)} y2={margin.top + line * (innerHeight / 2)} className="chart-gridline" /><text x={1} y={margin.top + line * (innerHeight / 2) + 3} className="chart-axis-label">{(max - line * (max - min) / 2).toFixed(0)}°</text></g>)}
    {[...sensorSegments.map((segment) => ({ segment, className: "chart-path-blue" })), ...ambientSegments.map((segment) => ({ segment, className: "chart-path-orange" }))].map(({ segment, className }, index) => <g key={index}><path d={segment.map((point, pointIndex) => `${pointIndex === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ")} className={`chart-path ${className}`} />{segment.map((point) => <circle key={`${point.timestamp}-${point.value}`} cx={point.x} cy={point.y} r="2.8" className={className === "chart-path-blue" ? "chart-point" : "chart-point-orange"}><title>{formatTime(point.timestamp)} · {point.value.toFixed(1)} °C</title></circle>)}</g>)}
  </svg><div className="chart-legend"><span><i className="legend-blue" />Skin-site sensor</span><span><i className="legend-orange" />Ambient sensor</span><small>°C</small></div></div>;
}

function AnalyticsPage({ data, mode }: { data: DashboardData; mode: DataMode }) {
  const states = new Map<string, number>();
  data.observations.forEach((item) => { if (item.routineState) states.set(item.routineState, (states.get(item.routineState) ?? 0) + 1); });
  return <div className="overview-grid">
    <Panel title="Recorded routine states" subtitle="Counts of labelled states in the current source">
      {states.size ? [...states.entries()].sort((a, b) => b[1] - a[1]).map(([state, count]) => <div className="analytics-row" key={state}><div><span className="state-marker" /><strong>{state}</strong></div><span>{count} records</span><div className="bar-track"><i style={{ width: `${Math.max(4, count / data.observations.length * 100)}%` }} /></div></div>) : <EmptyState title="No routine-state records" detail="The selected source does not contain classified routine states." />}
      <div className="chart-legend"><small>{mode} data · states are inferred labels, not clinical findings.</small></div>
    </Panel>
    <Panel title="Data coverage" subtitle="Availability in the current source">
      <div className="coverage-grid"><Coverage label="Sensor observations" value={data.observations.length} icon={Activity} /><Coverage label="Saved forecasts" value={data.forecasts.length} icon={Clock3} /><Coverage label="Recorded events" value={data.events.length} icon={CalendarDays} /></div>
      {mode === "LIVE" && <div className="calendar-note"><CircleAlert size={16} /><span>No analytics are inferred when the model does not provide them.</span></div>}
      <div className="compare-box"><div><strong>About this view</strong><span>This page summarizes records actually present in the selected data source. It does not calculate a medical or overall risk score.</span></div></div>
    </Panel>
  </div>;
}

function Coverage({ label, value, icon: Icon }: { label: string; value: number; icon: typeof Activity }) {
  return <div className="coverage-card"><div><Icon size={17} /><strong>{value}</strong></div><span>{label}</span></div>;
}

function MedicationPage({ data, observations, forecast, mode }: { data: DashboardData; observations: Observation[]; forecast?: Forecast; mode: DataMode }) {
  const interactions = data.events.filter((event) => event.type.toLowerCase().includes("pillbox") && dateKey(event.timestamp) === (observations[0] ? dateKey(observations[0].timestamp) : todayKey));
  const openCount = observations.filter((item) => item.pillboxState === "Open").length;
  return <div className="overview-grid">
    <Panel title="Pillbox interaction status" subtitle="Hall-effect sensor state is not proof of medication use">
      <div className="medication-summary"><div className="medication-symbol"><Pill size={25} /></div><div><span>Most recent reported state</span><strong>{observations.at(-1)?.pillboxState ?? "Unknown"}</strong><small>{observations.at(-1) ? `Recorded ${formatTime(observations.at(-1)!.timestamp)}` : "No record available for selected day"}</small></div><span className={`status-pill ${observations.at(-1)?.pillboxState === "Open" ? "status-amber" : "status-neutral"}`}>{observations.at(-1)?.pillboxState === "Open" ? "Interaction observed" : "Not confirmed"}</span></div>
      <div className="medication-facts"><div><span>Expected interaction window</span><strong>{forecast?.medicationWindow ?? "Not configured"}</strong></div><div><span>Observed open-state records</span><strong>{openCount}</strong></div><div><span>Interaction events</span><strong>{interactions.length}</strong></div></div>
      <div className="calendar-note"><ShieldCheck size={16} /><span>An open pillbox indicates an observed container interaction only. It cannot confirm that medication was taken.</span></div>
    </Panel>
    <Panel title="Medication-event forecast" subtitle="Displayed only when supplied by a saved forecast">
      {forecast?.missedInteractionProbability != null ? <div className="probability-block"><span>DEMO PROBABILITY OF NO INTERACTION</span><strong>{Math.round(forecast.missedInteractionProbability * 100)}<small>%</small></strong><p>Saved demonstration score · generated {formatTime(forecast.generatedAt)} · {forecast.status}</p></div> : <EmptyState title="No supported forecast" detail={mode === "LIVE" ? "Model not available. No medication-event probability is inferred in live mode." : "This date has no saved forecast for pillbox interaction."} />}
      <div className="compare-box"><div><strong>Outcome status</strong><span>{forecast?.targetDate && forecast.targetDate > todayKey ? "Future outcome pending." : "An outcome requires an observed event and adequate data."}</span></div><span className="status-pill status-neutral">{forecast?.targetDate && forecast.targetDate > todayKey ? "Pending" : "Unassessed"}</span></div>
    </Panel>
  </div>;
}

function EventsPage({ events, forecasts, selectedDate, mode, onAction, actionBusy }: { events: ActivityEvent[]; forecasts: Forecast[]; selectedDate: string; mode: DataMode; onAction: (type: "LED reminder command" | "Voice prompt command") => void; actionBusy: boolean }) {
  const selectedForecast = forecasts.find((item) => item.targetDate === selectedDate);
  return <>
    <Panel title="Prediction and action history" subtitle="Forecasts, observations and demo commands have distinct status labels" action={<span className="status-pill status-neutral">{events.length} events</span>}>
      {events.length ? <EventList events={events} /> : <EmptyState title="No events available" detail="The current source has not supplied a prediction or action history." />}
    </Panel>
    <div className="overview-grid page-section">
      <Panel title="Forecast comparison" subtitle={`Target date · ${formatDate(selectedDate)}`}>
        {selectedForecast ? <><div className="comparison-stat"><span>Predicted morning activity</span><strong>{selectedForecast.wakeTime ?? "Not supplied"}</strong><small>Forecast generated {formatTime(selectedForecast.generatedAt)} · {selectedForecast.source}</small></div><div className="comparison-stat"><span>Observed morning activity</span><strong>Not available</strong><small>{selectedForecast.targetDate > todayKey ? "Outcome still pending for future date." : "No corresponding observed event in the available data."}</small></div><div className="comparison-status"><span>Prediction error</span><strong>Not evaluated</strong></div></> : <EmptyState title="No saved forecast for this date" detail="The dashboard will not synthesize a prediction or an observed outcome." />}
      </Panel>
      <Panel title="Action interface" subtitle="Demonstration commands only — no hardware control is connected">
        <div className="action-disclaimer"><CircleAlert size={17} /><span>Actions require a supported execution path and acknowledgment. This demo records intent only; it cannot actuate devices.</span></div>
        <button className="action-command" disabled={mode !== "DEMO" || actionBusy} onClick={() => onAction("LED reminder command")}><span className="action-icon action-led"><Activity size={17} /></span><span><strong>Record LED reminder command</strong><small>Demo only · physical LED state is not verified</small></span><ArrowRight size={16} /></button>
        <button className="action-command" disabled={mode !== "DEMO" || actionBusy} onClick={() => onAction("Voice prompt command")}><span className="action-icon action-voice"><Mic2 size={17} /></span><span><strong>Record voice prompt command</strong><small>Demo only · no voice generation or playback</small></span><ArrowRight size={16} /></button>
        {mode !== "DEMO" && <p className="muted-note">Demo action controls are disabled outside DEMO mode.</p>}
      </Panel>
    </div>
  </>;
}

function EventList({ events }: { events: ActivityEvent[] }) {
  return <div className="event-list">{events.map((event) => <div className="event-item" key={event.id}><div className={`event-icon ${event.status === "Observed" ? "event-green" : event.status === "Predicted" ? "event-violet" : "event-blue"}`}>{event.status === "Observed" ? <Check size={15} /> : event.status === "Predicted" ? <Clock3 size={15} /> : <Activity size={15} />}</div><div className="event-content"><div><strong>{event.type}</strong><span className={`status-pill ${event.status === "Observed" ? "status-green" : event.status === "Predicted" ? "status-violet" : "status-blue"}`}>{event.status}</span></div><p>{event.description}</p><small>{formatDate(dateKey(event.timestamp))} · {formatTime(event.timestamp)} · {event.device} · {event.source}</small></div></div>)}</div>;
}

function DeviceHealthPage({ mode, lastUpdated, loading, liveAvailable }: { mode: DataMode; lastUpdated?: string; loading: boolean; liveAvailable: boolean }) {
  const sourceStatus = mode === "DEMO" ? "Demo source" : mode === "CSV" ? "Local data" : loading ? "Checking" : liveAvailable ? "API connected" : "Unavailable";
  const states = [
    { group: "WEARABLE", items: [["ESP32 telemetry", mode === "DEMO" ? "Demo data" : "Unavailable"], ["MAX30102 signal", mode === "DEMO" ? "Demo data" : "Unavailable"], ["DS18B20 sensor", mode === "DEMO" ? "Demo data" : "Unavailable"], ["MPU6050 movement", mode === "DEMO" ? "Demo data" : "Unavailable"]] },
    { group: "BASE STATION", items: [["ESP-NOW link", mode === "DEMO" ? "Demo data" : "Unavailable"], ["DHT11 environment", mode === "DEMO" ? "Demo data" : "Unavailable"], ["Pillbox sensor", mode === "DEMO" ? "Demo data" : "Unavailable"], ["LED / voice outputs", "Not connected"]] },
    { group: "LAPTOP & PREDICTION", items: [["Data source", sourceStatus], ["USB Serial", "Not accessed by browser"], ["CSV logging", mode === "CSV" ? "Browser-local import" : "Not connected"], ["Prediction model", "Model not available"]] },
  ];
  return <><div className="health-summary"><div className={`health-summary-icon ${mode === "LIVE" && !lastUpdated ? "health-offline" : "health-demo"}`}>{mode === "LIVE" && !lastUpdated ? <CloudOff size={23} /> : <ShieldCheck size={23} />}</div><div><span>SYSTEM SOURCE STATUS</span><strong>{sourceStatus}</strong><small>{lastUpdated ? `Latest record ${formatDate(dateKey(lastUpdated))} at ${formatTime(lastUpdated)}` : mode === "DEMO" ? "Green device connectivity is not claimed by this dashboard." : "No verified hardware status has been reported."}</small></div></div><div className="health-grid">{states.map((group) => <Panel key={group.group} title={group.group} subtitle="Status reported by the current frontend data source">{group.items.map(([label, value]) => <div className="health-row" key={label}><span>{label}</span><strong className={value === "Unavailable" || value === "Not connected" ? "health-value-muted" : ""}><i className={`health-dot ${value === "Demo data" ? "health-dot-demo" : value === "Unavailable" || value === "Not connected" || value === "Model not available" ? "health-dot-muted" : "health-dot-neutral"}`} />{value}</strong></div>)}</Panel>)}</div><div className="calendar-note"><CircleAlert size={16} /><span>The browser does not open the ESP32 serial port. Live status needs the laptop receiver and dashboard API to report verified values.</span></div></>;
}

function SettingsPage({ mode, onModeChange, onImport, onClear }: { mode: DataMode; onModeChange: (mode: DataMode) => void; onImport: (event: ChangeEvent<HTMLInputElement>) => void; onClear: () => void }) {
  const [confirmClear, setConfirmClear] = useState(false);
  return <div className="settings-grid">
    <Panel title="Data source" subtitle="Keep demo, imported and live data separate. No automatic fallback is used.">
      <div className="source-options">
        {([{ id: "DEMO" as const, title: "Demonstration data", text: "Illustrative records, clearly marked throughout the dashboard." }, { id: "CSV" as const, title: "Local CSV import", text: "Historical observations stored in this browser only." }, { id: "LIVE" as const, title: "Live dashboard API", text: "Connect to the laptop data service; no serial access from the browser." }]).map((option) => <button key={option.id} className={`source-option ${mode === option.id ? "source-option-active" : ""}`} onClick={() => onModeChange(option.id)}><span className="radio-indicator">{mode === option.id && <i />}</span><span><strong>{option.title}</strong><small>{option.text}</small></span><span className={`mode-badge mode-${option.id.toLowerCase()}`}>{option.id}</span></button>)}
      </div>
    </Panel>
    <Panel title="Import historical observations" subtitle="CSV is parsed locally; invalid timestamps are skipped and readings are range-checked.">
      <div className="import-drop"><div className="import-icon"><Upload size={20} /></div><strong>Select an observation CSV</strong><p>Required column: <code>timestamp</code>. The backend telemetry CSV is supported, including <code>pulse_rate_bpm</code>, <code>skin_temperature_c</code>, <code>room_temperature_c</code>, <code>room_humidity_percent</code>, acceleration axes, <code>pillbox_open</code>, and <code>sensor_validity</code>.</p><label className="button button-primary upload-label"><Upload size={15} />Choose CSV file<input type="file" accept=".csv,text/csv" onChange={(event) => void onImport(event)} /></label></div>
      <div className="schema-note"><strong>Also accepted</strong><span>heart_rate, heart_rate_valid, sensor_temperature, ambient_temperature, humidity, pillbox_state, routine_state, movement_intensity</span></div>
      <button className="text-button danger-text" onClick={() => setConfirmClear(true)}>Clear imported browser data</button>
    </Panel>
    <Panel title="Live API integration" subtitle="Live mode reads the validated version 1.0 telemetry history.">
      <div className="integration-code"><span>GET</span> {import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000"}/api/v1/telemetry/history?limit=500</div>
      <p className="integration-copy">Set <code>VITE_API_BASE_URL</code> in <code>frontend/.env.local</code> only when the API is not at the default local address. Live mode shows an error when the API is unavailable and never substitutes demo data. Forecasts and action events are left empty until those services are implemented.</p>
      <div className="calendar-note"><ShieldCheck size={16} /><span>Do not put API credentials in frontend environment variables. Keep secrets on a server-side service.</span></div>
    </Panel>
    {confirmClear && <div className="modal-backdrop" role="presentation"><div className="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="clear-heading"><h2 id="clear-heading">Clear local CSV data?</h2><p>This removes imported observations from this browser. Demo records and live service data are not affected.</p><div><button className="button button-secondary" onClick={() => setConfirmClear(false)}>Cancel</button><button className="button button-danger" onClick={() => { onClear(); setConfirmClear(false); }}>Clear data</button></div></div></div>}
  </div>;
}

function EmptyState({ title, detail }: { title: string; detail: string }) {
  return <div className="empty-state"><div><Activity size={19} /></div><strong>{title}</strong><span>{detail}</span></div>;
}

export default App;
