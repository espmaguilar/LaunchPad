import { useCallback, useEffect, useState } from "react";
import { api, type Health, type SettingsResponse } from "./api";
import { isMuted, setMuted } from "./voice";
import { Icon, Logo } from "./ui";
import { useLab } from "./lab";
import LabSession from "./pages/LabSession";
import Procedures from "./pages/Procedures";
import SettingsPage from "./pages/Settings";

export type Page = "Lab session" | "Procedures" | "Settings";
const PAGES: [Page, string, string][] = [
  ["Lab session", "monitor", "session"],
  ["Procedures", "flask", "procedures"],
  ["Settings", "settings", "settings"],
];
const pageFromHash = (): Page => PAGES.find(([, , h]) => location.hash.startsWith(`#/${h}`))?.[0] ?? "Lab session";

export default function App() {
  const [page, setPageState] = useState<Page>(pageFromHash);
  const [health, setHealth] = useState<Health | null>(null);
  const [workspace, setWorkspace] = useState("LabScribe");
  const [muted, setMutedState] = useState(isMuted());

  const setPage = (p: Page) => {
    history.replaceState(null, "", `#/${PAGES.find(([n]) => n === p)![2]}`);
    setPageState(p);
  };
  useEffect(() => {
    const onHash = () => setPageState(pageFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const loadHealth = useCallback(async () => {
    try { setHealth(await api<Health>("/api/health")); } catch { setHealth(null); }
  }, []);
  useEffect(() => {
    loadHealth();
    api<SettingsResponse>("/api/settings").then((s) => setWorkspace(s.settings.workspace_name)).catch(() => {});
    const t = window.setInterval(loadHealth, 15000);
    return () => window.clearInterval(t);
  }, [loadHealth]);

  const lab = useLab(health);
  const recording = !!lab.session && !lab.session.ended_at;
  const ok = !!health?.ok;
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
                {label === "Lab session" && recording && <em className="rec-dot" title="Session recording">REC</em>}
              </button>
            ))}
          </nav>
        </div>
        <div className="side-bottom">
          <div className={`system-ok ${ok ? "" : "bad"}`}>
            <i /> {ok ? (health?.mock_mode ? "Mock mode · no AWS calls" : "All systems operational") : "Check AWS setup"}
          </div>
          <div className="aws">
            <b>Powered by AWS</b>
            <span>
              Bedrock · Textract · Polly
              <br />
              Rekognition · DynamoDB · S3
            </span>
          </div>
        </div>
      </aside>

      <div className="shell">
        <header className="topbar">
          <div className="topbar-title">
            <span className="muted">AI lab partner</span>
          </div>
          <div className="top-actions">
            {health?.mock_mode && <span className="demo-badge mock" title="MOCK_MODE=true: no AWS calls">MOCK</span>}
            <button className={`camera-pill ${lab.stream ? "live" : "offline"}`} disabled={lab.starting}
              onClick={() => (lab.stream ? lab.stopCamera() : lab.startCamera())} title={lab.stream ? "Stop camera" : "Start camera"}>
              <i />
              {lab.starting ? "Starting…" : lab.stream ? "Camera on" : "Start camera"}
            </button>
            <button className={`voice ${muted ? "" : "on"}`} onClick={() => { setMuted(!muted); setMutedState(!muted); }}
              aria-pressed={!muted} title="Voice guidance (Amazon Polly)">
              <Icon name={muted ? "mute" : "sound"} size={16} /> Voice {muted ? "off" : "on"}
            </button>
            <button className="avatar" onClick={() => setPage("Settings")} title="Settings">
              {workspace.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase()}
              <span className="online" />
            </button>
          </div>
        </header>
        <main>
          {health && !health.ok && (
            <div className="banner error app-banner">
              Missing settings in .env: {health.missing_settings.join(", ")}. Or set MOCK_MODE=true to try LabScribe offline.
            </div>
          )}
          {page === "Lab session" && <LabSession lab={lab} health={health} goProcedures={() => setPage("Procedures")} />}
          {page === "Procedures" && <Procedures health={health} />}
          {page === "Settings" && (
            <SettingsPage onSaved={(s) => setWorkspace(s.workspace_name)} muted={muted}
              setMuted={(v) => { setMuted(v); setMutedState(v); }} />
          )}
        </main>
      </div>
    </div>
  );
}
