import { useCallback, useEffect, useRef, useState } from "react";
import { api, tzOffset, type DashboardData, type Health, type LiveEvent, type SettingsResponse, type Zone } from "./api";
import { ago, cap, isAi, lower, money } from "./format";
import { useMonitor } from "./monitor";
import { isMuted, setMuted } from "./voice";
import { Icon, Logo } from "./ui";
import LiveMonitor from "./pages/LiveMonitor";
import Incidents from "./pages/Incidents";
import Dashboard from "./pages/Dashboard";
import PolicySetup from "./pages/PolicySetup";
import SettingsPage from "./pages/Settings";

export type Page = "Live Monitor" | "Incidents" | "Dashboard" | "Policy Setup" | "Settings";
const PAGES: [Page, string, string][] = [
  ["Live Monitor", "monitor", "monitor"],
  ["Incidents", "incidents", "incidents"],
  ["Dashboard", "dashboard", "dashboard"],
  ["Policy Setup", "policy", "policy"],
  ["Settings", "settings", "settings"],
];
const pageFromHash = (): Page => PAGES.find(([, , h]) => `#/${h}` === location.hash)?.[0] ?? "Live Monitor";

export interface Toast {
  id: number;
  tone: "danger" | "success" | "warning" | "info";
  title: string;
  text: string;
  money?: string;
  moneyLabel?: string;
  meta: string;
  at: string;
}

let toastId = 0;
function toToast(ev: LiveEvent): Toast | null {
  const at = new Date().toISOString();
  const zone = ev.zone_name || "";
  const label = cap(ev.gear_label || "");
  const what = isAi(ev.gear) ? `AI vision: ${label}` : `Missing ${lower(label)}`;
  const base = { id: ++toastId, at };
  switch (ev.type) {
    case "violation":
      if (ev.joined)
        return { ...base, tone: "danger", title: `${zone} · also missing`, text: what, money: money(ev.incident_fine ?? ev.fine),
          moneyLabel: "Incident total", meta: "Added to the open incident · worker warned by voice" };
      if (ev.reattached)
        return { ...base, tone: "warning", title: `${zone} · still not fixed`, text: what, money: money(ev.fine),
          moneyLabel: "Potential fine exposure", meta: "Same incident · worker warned again" };
      return { ...base, tone: "danger", title: `${zone} · ${ev.detector === "AI vision" ? "Rule" : "PPE"} violation`,
        text: what, money: money(ev.fine), moneyLabel: "Potential fine exposure", meta: "Worker warned by voice" };
    case "notify":
      return ev.sent
        ? { ...base, tone: "info", title: `Supervisor notified · ${zone}`, text: `${label} not fixed after ${ev.age}s`,
            money: money(ev.fine), moneyLabel: "Potential fine exposure", meta: `Sent by ${ev.channel || "email"}` }
        : { ...base, tone: "danger", title: "Notification failed", text: ev.alert_error || "Unknown error", meta: zone };
    case "resolved":
      return { ...base, tone: "success", title: `Resolved · ${ev.notified ? "supervisor was notified" : "no email needed"}`,
        text: `${label} fixed in ${ev.seconds_to_fix}s`, money: money(ev.fine), moneyLabel: "potential fine avoided", meta: zone };
    case "fixed_late":
      return { ...base, tone: "warning", title: `Fixed late · ${zone}`, text: `${label} after ${ev.seconds_to_fix}s`,
        money: money(ev.fine), moneyLabel: "Exposure stands", meta: "Outside the 5-minute window" };
    case "expired":
      return { ...base, tone: "warning", title: `Still unresolved · ${zone}`, text: `${label}`,
        money: money(ev.fine), moneyLabel: "Exposure stays open", meta: "Tracking restarted" };
    case "error":
      return { ...base, tone: "danger", title: "Logging problem", text: ev.message || "", meta: zone };
    default:
      return null;
  }
}

export default function App() {
  const [page, setPageState] = useState<Page>(pageFromHash);
  const [health, setHealth] = useState<Health | null>(null);
  const [zones, setZones] = useState<Zone[]>([]);
  const [zoneId, setZoneId] = useState<string>(() => {
    try { return localStorage.getItem("safesight-zone") || ""; } catch { return ""; }
  });
  const [workspace, setWorkspace] = useState("SafeSight");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [feed, setFeed] = useState<Toast[]>([]); // bell history
  const [unread, setUnread] = useState(0);
  const [bellOpen, setBellOpen] = useState(false);
  const [muted, setMutedState] = useState(isMuted());
  const [summary, setSummary] = useState<DashboardData | null>(null);
  const [dataVersion, setDataVersion] = useState(0); // bump -> pages refetch
  const [zoneMenu, setZoneMenu] = useState(false);
  const zoneMenuRef = useRef<HTMLDivElement>(null);
  const bellRef = useRef<HTMLDivElement>(null);

  const setPage = (p: Page) => {
    const h = PAGES.find(([n]) => n === p)![2];
    history.replaceState(null, "", `#/${h}`);
    setPageState(p);
  };
  useEffect(() => {
    const onHash = () => setPageState(pageFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const loadZones = useCallback(async () => {
    try {
      const z = await api<Zone[]>("/api/zones");
      setZones(z);
      setZoneId((cur) => (z.some((x) => x.zone_id === cur) ? cur : z[0]?.zone_id || ""));
      setLoadError(null);
    } catch (e) {
      setLoadError(`Could not load zones: ${(e as Error).message}. Check AWS setup, or set MOCK_MODE=true in .env.`);
    }
  }, []);

  const loadHealth = useCallback(async () => {
    try { setHealth(await api<Health>("/api/health")); } catch { setHealth(null); }
  }, []);

  const loadSummary = useCallback(async () => {
    try { setSummary(await api<DashboardData>(`/api/dashboard?days=7&tz_offset=${tzOffset()}`)); } catch { /* keep last */ }
  }, []);

  useEffect(() => {
    loadZones();
    loadHealth();
    api<SettingsResponse>("/api/settings").then((s) => setWorkspace(s.settings.workspace_name)).catch(() => {});
    const t = window.setInterval(loadHealth, 15000);
    const onVis = () => !document.hidden && loadZones();
    document.addEventListener("visibilitychange", onVis);
    return () => { window.clearInterval(t); document.removeEventListener("visibilitychange", onVis); };
  }, [loadZones, loadHealth]);

  useEffect(() => {
    loadSummary();
    const t = window.setInterval(loadSummary, 8000);
    return () => window.clearInterval(t);
  }, [loadSummary, dataVersion]);

  useEffect(() => {
    try { localStorage.setItem("safesight-zone", zoneId); } catch { /* ignore */ }
  }, [zoneId]);

  // close popovers on outside click
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (zoneMenuRef.current && !zoneMenuRef.current.contains(e.target as Node)) setZoneMenu(false);
      if (bellRef.current && !bellRef.current.contains(e.target as Node)) setBellOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const onEvents = useCallback((evs: LiveEvent[]) => {
    // Items that went missing in the same frame are one incident -> one toast
    const merged: LiveEvent[] = [];
    for (const ev of evs) {
      const prev = merged.find((m) => m.type === "violation" && ev.type === "violation" && !m.reattached && !ev.reattached
        && m.zone_id === ev.zone_id && m.timestamp === ev.timestamp);
      if (prev) {
        const mixed = isAi(prev.gear) !== isAi(ev.gear);
        prev.gear_label = mixed ? `${prev.gear_label} + ${ev.gear_label}` : `${prev.gear_label} + ${lower(ev.gear_label || "")}`;
        prev.incident_fine = Math.max(prev.incident_fine ?? 0, ev.incident_fine ?? 0, (prev.fine ?? 0) + (ev.fine ?? 0));
        prev.fine = prev.incident_fine;
        prev.joined = false;
      } else if (ev.type === "resolved" || ev.type === "fixed_late") {
        const same = merged.find((m) => m.type === ev.type && m.zone_id === ev.zone_id && m.timestamp === ev.timestamp);
        if (same) {
          same.gear_label = `${same.gear_label} + ${lower(ev.gear_label || "")}`;
          same.fine = (same.fine ?? 0) + (ev.fine ?? 0);
          same.seconds_to_fix = Math.max(same.seconds_to_fix ?? 0, ev.seconds_to_fix ?? 0);
          same.notified = same.notified || ev.notified;
        } else merged.push({ ...ev });
      } else merged.push({ ...ev });
    }
    const ts = merged.map(toToast).filter(Boolean) as Toast[];
    if (!ts.length) return;
    setToasts((cur) => [...ts.reverse(), ...cur].slice(0, 4));
    setFeed((cur) => [...ts, ...cur].slice(0, 30));
    setUnread((n) => n + ts.length);
    ts.forEach((t) => window.setTimeout(() => setToasts((cur) => cur.filter((x) => x.id !== t.id)), 9000));
    setDataVersion((v) => v + 1);
  }, []);

  const monitor = useMonitor(zoneId, onEvents);
  const zone = zones.find((z) => z.zone_id === zoneId) || null;
  const openCount = summary?.current.open ?? 0;
  const systemsOk = !!health && health.ok && !loadError;

  return (
    <div className="app">
      <aside className="sidebar">
        <div>
          <Logo />
          <button className="company" onClick={() => setPage("Settings")} title="Workspace settings">
            <span className="company-mark">{workspace.charAt(0).toUpperCase()}</span>
            <span>
              <b>{workspace}</b>
              <small>{health?.mock_mode ? "Mock mode" : `AWS · ${health?.region || "…"}`}</small>
            </span>
            <span className="updown">⌃⌄</span>
          </button>
          <nav>
            {PAGES.map(([label, icon]) => (
              <button key={label} className={page === label ? "active" : ""} onClick={() => setPage(label)}>
                <Icon name={icon} />
                <span>{label}</span>
                {label === "Incidents" && openCount > 0 && <em>{openCount}</em>}
              </button>
            ))}
          </nav>
        </div>
        <div className="side-bottom">
          <div className={`system-ok ${systemsOk ? "" : "bad"}`}>
            <i /> {systemsOk ? (health?.mock_mode ? "Mock mode · demo data" : "All systems operational") : "Check AWS setup"}
          </div>
          <div className="aws">
            <b>Powered by AWS</b>
            <span>
              Rekognition · Bedrock · Polly
              <br />
              Textract · SNS · DynamoDB · S3
            </span>
          </div>
        </div>
      </aside>

      <div className="shell">
        <header className="topbar">
          <div className="zone-menu" ref={zoneMenuRef}>
            <button className="zone-select" onClick={() => setZoneMenu(!zoneMenu)} aria-haspopup="listbox" aria-expanded={zoneMenu}>
              <span className="muted">Viewing</span>
              <b>{zone?.name || (zones.length ? "Select zone" : "No zones")}</b>
              <span>⌄</span>
            </button>
            {zoneMenu && (
              <ul className="popover zone-list" role="listbox">
                {zones.map((z) => (
                  <li key={z.zone_id}>
                    <button role="option" aria-selected={z.zone_id === zoneId}
                      className={z.zone_id === zoneId ? "selected" : ""}
                      onClick={() => { setZoneId(z.zone_id); setZoneMenu(false); }}>
                      <b>{z.name}</b>
                      <small>{z.required.length} Rekognition · {z.ai_rules.length} AI vision</small>
                    </button>
                  </li>
                ))}
                <li><button className="link" onClick={() => { setZoneMenu(false); setPage("Policy Setup"); }}>+ Add zones from a policy</button></li>
              </ul>
            )}
          </div>
          <div className="top-actions">
            {health?.mock_mode && <span className="demo-badge mock" title="MOCK_MODE=true: fake detections, no AWS calls">MOCK</span>}
            {health?.demo_mode && <span className="demo-badge" title="Demo timers (Settings → Alert timings)">DEMO TIMERS</span>}
            <button className={`voice ${muted ? "" : "on"}`} onClick={() => { setMuted(!muted); setMutedState(!muted); }}
              aria-pressed={!muted} title="Voice warnings (Amazon Polly)">
              <Icon name={muted ? "mute" : "sound"} size={16} /> Voice {muted ? "off" : "on"}
            </button>
            <button className={`camera-pill ${monitor.stream ? "live" : "offline"}`}
              onClick={() => (monitor.stream ? monitor.stop() : monitor.start())} disabled={monitor.starting || !zone}
              title={monitor.stream ? "Stop camera" : "Start camera"}>
              <i />
              {monitor.starting ? "Starting…" : monitor.stream ? "Live · Camera 01" : "Start camera"}
            </button>
            <div className="bell" ref={bellRef}>
              <button className="icon-button" aria-label={`Notifications${unread ? ` (${unread} new)` : ""}`}
                onClick={() => { setBellOpen(!bellOpen); setUnread(0); }}>
                <Icon name="bell" />
                {unread > 0 && <span className="bell-dot">{unread > 9 ? "9+" : unread}</span>}
              </button>
              {bellOpen && (
                <div className="popover bell-list">
                  <div className="popover-head">Activity</div>
                  {feed.length ? feed.map((t) => (
                    <div key={t.id} className={`bell-item ${t.tone}`}>
                      <i />
                      <div><b>{t.title}</b><span>{t.text}{t.money ? ` · ${t.money}` : ""}</span></div>
                      <time>{ago(t.at)}</time>
                    </div>
                  )) : <p className="popover-empty">No activity yet. Start the camera to begin monitoring.</p>}
                </div>
              )}
            </div>
            <button className="avatar" onClick={() => setPage("Settings")} title="Settings">
              {workspace.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase()}
              <span className="online" />
            </button>
          </div>
        </header>
        <main>
          {loadError && <div className="banner error app-banner">{loadError}</div>}
          {page === "Live Monitor" && (
            <LiveMonitor zone={zone} monitor={monitor} summary={summary} health={health} setPage={setPage} />
          )}
          {page === "Incidents" && <Incidents zones={zones} dataVersion={dataVersion} onChanged={() => setDataVersion((v) => v + 1)} />}
          {page === "Dashboard" && <Dashboard workspace={workspace} dataVersion={dataVersion} />}
          {page === "Policy Setup" && (
            <PolicySetup zones={zones} reloadZones={loadZones} onWatch={(id) => { setZoneId(id); setPage("Live Monitor"); }} />
          )}
          {page === "Settings" && (
            <SettingsPage zones={zones} reloadZones={loadZones} onSaved={(s) => { setWorkspace(s.workspace_name); loadHealth(); }}
              muted={muted} setMuted={(v) => { setMuted(v); setMutedState(v); }} />
          )}
        </main>
        {toasts.length > 0 && (
          <div className="toasts" aria-live="polite">
            {toasts.map((t) => (
              <div key={t.id} className={`toast ${t.tone}`}>
                <span className="toast-icon">{t.tone === "success" ? <Icon name="check" size={14} /> : t.tone === "info" ? "✉" : "!"}</span>
                <div>
                  <b>{t.title}</b>
                  <p>{t.text}</p>
                  {t.money && <strong>{t.moneyLabel} <code>{t.money}</code></strong>}
                  <small>{t.meta} · {ago(t.at)}</small>
                </div>
                <button aria-label="Dismiss" onClick={() => setToasts((cur) => cur.filter((x) => x.id !== t.id))}>×</button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
