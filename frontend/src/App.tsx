import { useCallback, useEffect, useState } from "react";
import { api, type Health, type SettingsResponse } from "./api";
import { hhmmss } from "./format";
import { useLab } from "./lab";
import { Icon, Logo, type IconName } from "./ui";
import { isMuted, setMuted } from "./voice";
import Instructor from "./pages/Instructor";
import LabSession from "./pages/LabSession";
import Procedures from "./pages/Procedures";
import Records from "./pages/Records";
import SettingsPage from "./pages/Settings";

export type Page = "live" | "procedures" | "records" | "instructor" | "settings";
const NAV: { page: Page; label: string; icon: IconName }[] = [
  { page: "live", label: "Live Session", icon: "live" },
  { page: "procedures", label: "Procedures", icon: "file" },
  { page: "records", label: "Lab Records", icon: "records" },
  { page: "instructor", label: "Instructor View", icon: "users" },
  { page: "settings", label: "Settings", icon: "settings" },
];
const pageFromHash = (): Page => NAV.find((n) => location.hash === `#/${n.page}`)?.page ?? "live";

export default function App() {
  const [page, setPageState] = useState<Page>(pageFromHash);
  const [health, setHealth] = useState<Health | null>(null);
  const [workspace, setWorkspace] = useState("LabScribe");
  const [muted, setMutedState] = useState(isMuted());
  const [recordId, setRecordId] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem("labscribe-sidebar") !== "open"; } catch { return true; }
  });
  const toggleSidebar = () => {
    setCollapsed((c) => { try { localStorage.setItem("labscribe-sidebar", c ? "open" : "closed"); } catch { /* ignore */ } return !c; });
  };
  const lab = useLab(health);

  const setPage = useCallback((p: Page) => {
    history.replaceState(null, "", `#/${p}`);
    setPageState(p);
  }, []);
  useEffect(() => {
    const onHash = () => setPageState(pageFromHash());
    window.addEventListener("hashchange", onHash);
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => { window.removeEventListener("hashchange", onHash); window.clearInterval(t); };
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

  const toggleVoice = () => { setMuted(!muted); setMutedState(!muted); };
  const sess = lab.session;
  const recording = !!sess && !sess.ended_at;
  const elapsed = sess ? ((sess.ended_at ? new Date(sess.ended_at).getTime() : now) - new Date(sess.started_at).getTime()) / 1000 : 0;
  const titles: Record<Page, string> = {
    live: sess?.procedure.title || "Lab session",
    procedures: "Procedure setup",
    records: "Lab record",
    instructor: "Instructor overview",
    settings: "Settings",
  };
  const initials = workspace.split(/[\s·]+/).filter(Boolean).map((w) => w[0]).join("").slice(0, 3).toUpperCase();

  return (
    <div className="app-shell">
      <aside className={`sidebar ${collapsed ? "collapsed" : ""}`}>
        <Logo />
        <button className="sidebar-collapse" onClick={toggleSidebar} title={collapsed ? "Open sidebar" : "Collapse sidebar"}
          aria-label={collapsed ? "Open sidebar" : "Collapse sidebar"}>
          <Icon name="chevron" size={15} />
        </button>
        <button className="workspace" onClick={() => setPage("settings")} title="Workspace settings">
          <div className="workspace-icon">{initials}</div>
          <div>
            <span>Workspace</span>
            <strong>{workspace}</strong>
          </div>
          <Icon name="chevron" size={15} />
        </button>
        <nav>
          {NAV.map((item) => (
            <button key={item.page} className={page === item.page ? "active" : ""} onClick={() => setPage(item.page)} title={item.label}>
              <Icon name={item.icon} />
              <span>{item.label}</span>
              {item.page === "live" && recording && <i title="Session recording" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <button className="manual-upload" onClick={() => setPage("procedures")} title="Feed in a lab manual">
            <span><Icon name="upload" size={17} /></span>
            <div>
              <strong>Feed in a lab manual</strong>
              <small>Upload PDF or use a sample</small>
            </div>
            <Icon name="chevron" size={13} />
          </button>
          <div className="tip-card">
            <div className="tip-icon"><Icon name="spark" size={16} /></div>
            <strong>{health?.mock_mode ? "Mock mode" : "LabScribe tip"}</strong>
            <p>{health?.mock_mode ? "No AWS calls: the scribe plays a scripted demo run."
              : health && !health.ok ? `Missing in .env: ${health.missing_settings.join(", ")}`
                : "Keep your workspace in frame for the best step detection."}</p>
          </div>
          <div className="aws-badge">
            <span>Powered by AWS</span>
            <small>Bedrock · Textract · Polly<br />S3 · DynamoDB</small>
          </div>
        </div>
      </aside>

      <div className="workspace-area">
        {page !== "live" && <header className="topbar">
          <div className="top-title">
            <span>{workspace}</span>
            <strong>{titles[page]}</strong>
          </div>
          <div className="top-actions">
            {recording && (
              <div className="timer" title="Session running"><Icon name="clock" size={16} /><span>{hhmmss(elapsed)}</span></div>
            )}
            <button className={`live-pill ${lab.stream ? "" : "off"}`} disabled={lab.starting}
              onClick={() => (lab.stream ? lab.stopCamera() : lab.startCamera())} title={lab.stream ? "Stop camera" : "Start camera"}>
              <i /> <span>{lab.starting ? "Starting…" : lab.stream ? "Camera live" : "Camera off"}</span>
            </button>
            <button className={`voice-pill ${muted ? "" : "on"}`} onClick={toggleVoice} aria-pressed={!muted}>
              <Icon name="volume" size={16} />
              <span>Coach voice {muted ? "off" : "on"}</span>
            </button>
            <button className="avatar" onClick={() => setPage("settings")} title={workspace}>{initials.slice(0, 2)}</button>
          </div>
        </header>}
        <div className="page-scroll">
          {page === "live" && (
            <LabSession lab={lab} health={health} muted={muted} toggleVoice={toggleVoice}
              openRecord={(id) => { setRecordId(id); setPage("records"); }} goProcedures={() => setPage("procedures")} />
          )}
          {page === "procedures" && (
            <Procedures health={health} startSession={async (id) => { await lab.startSession(id); setPage("live"); }} />
          )}
          {page === "records" && <Records selected={recordId} setSelected={setRecordId} />}
          {page === "instructor" && <Instructor openRecord={(id) => { setRecordId(id); setPage("records"); }} />}
          {page === "settings" && (
            <SettingsPage lab={lab} muted={muted} toggleVoice={toggleVoice} onSaved={(s) => setWorkspace(s.workspace_name)} />
          )}
        </div>
      </div>
    </div>
  );
}
