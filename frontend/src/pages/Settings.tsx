import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, post, type Settings, type SettingsResponse, type Zone } from "../api";
import { Icon, PageTitle } from "../ui";
import { unlockAudio } from "../voice";

const SECTIONS = ["Alert timings", "Supervisors", "Voice & audio", "Detection", "Workspace"];

export default function SettingsPage({ zones, reloadZones, onSaved, muted, setMuted }: {
  zones: Zone[]; reloadZones: () => Promise<void>; onSaved: (s: Settings) => void; muted: boolean; setMuted: (v: boolean) => void;
}) {
  const [meta, setMeta] = useState<SettingsResponse | null>(null);
  const [s, setS] = useState<Settings | null>(null);
  const [saved, setSaved] = useState<Settings | null>(null);
  const [msg, setMsg] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [active, setActive] = useState(SECTIONS[0]);
  const refs = useRef<Record<string, HTMLElement | null>>({});

  useEffect(() => {
    api<SettingsResponse>("/api/settings")
      .then((r) => { setMeta(r); setS(r.settings); setSaved(r.settings); })
      .catch((e) => setMsg({ tone: "error", text: `Could not load settings: ${e.message}` }));
  }, []);

  const dirty = !!s && !!saved && JSON.stringify(s) !== JSON.stringify(saved);
  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setS((cur) => (cur ? { ...cur, [k]: v } : cur));

  const save = async () => {
    if (!s) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await post<{ settings: Settings }>("/api/settings", s, "PUT");
      setS(r.settings);
      setSaved(r.settings);
      onSaved(r.settings);
      setMsg({ tone: "ok", text: "Settings saved. New timings apply to the live monitor immediately." });
    } catch (e) {
      setMsg({ tone: "error", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const go = (name: string) => {
    setActive(name);
    refs.current[name]?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  if (!s || !meta) return <div><PageTitle title="Settings" subtitle="Loading…" />{msg && <div className={`banner ${msg.tone}`}>{msg.text}</div>}</div>;

  const applyMode = (mode: "demo" | "production") => setS({ ...s, mode, ...(meta.presets[mode] as Partial<Settings>) });

  return (
    <div className="settings-page">
      <PageTitle
        title="Settings"
        subtitle="Configure alerts, supervisors, and detection behavior"
        action={
          <button className="primary" onClick={save} disabled={busy || !dirty}>
            {busy ? "Saving…" : dirty ? "Save changes" : "Saved"}
          </button>
        }
      />
      {msg && <div className={`banner ${msg.tone}`}>{msg.text}</div>}
      <div className="settings-layout">
        <div className="settings-nav">
          {SECTIONS.map((n) => <button key={n} className={active === n ? "active" : ""} onClick={() => go(n)}>{n}</button>)}
        </div>
        <div className="settings-content">
          <SettingsSection refEl={(el) => (refs.current["Alert timings"] = el)} title="Alert timings"
            subtitle="How quickly SafeSight responds to gear that stays missing."
            right={
              <div className="mode-toggle">
                <button className={s.mode === "demo" ? "active" : ""} onClick={() => applyMode("demo")}>Demo</button>
                <button className={s.mode === "production" ? "active" : ""} onClick={() => applyMode("production")}>Production</button>
              </div>
            }>
            <div className="timing-grid three">
              <Timing label="Voice warning" value={s.voice_after_seconds} onChange={(v) => set("voice_after_seconds", v)}
                desc="Gear missing continuously this long → incident logged + Polly warning" />
              <Timing label="Supervisor notification" value={s.notify_after_seconds} onChange={(v) => set("notify_after_seconds", v)}
                desc="Still missing → ONE message per zone (email now, SMS later)" />
              <Timing label="Resolve window" value={s.resolve_seconds} onChange={(v) => set("resolve_seconds", v)}
                desc="Continuous compliance needed to resolve" />
            </div>
            <div className="demo-note">
              <span>⌁</span>
              <p>
                <b>One notification per incident.</b> AI-vision rules confirm on 2 consecutive checks (10 s apart) instead of the
                voice timer. Fixes within 5 minutes of the warning count as a fine avoided.
              </p>
            </div>
          </SettingsSection>

          <SettingsSection refEl={(el) => (refs.current["Supervisors"] = el)} title="Supervisor emails"
            subtitle={`Each alert names the zone's supervisor. Delivery: Amazon SNS topic${meta.alert_email ? ` (subscribed: ${meta.alert_email})` : ""}.`}>
            <div className="supervisor-list">
              {zones.map((z) => <SupervisorRow key={z.zone_id} zone={z} onSaved={reloadZones} />)}
              {!zones.length && <p className="muted">No zones yet.</p>}
            </div>
          </SettingsSection>

          <SettingsSection refEl={(el) => (refs.current["Voice & audio"] = el)} title="Voice & audio"
            subtitle="Spoken warnings are generated with Amazon Polly (neural) and cached per phrase.">
            <div className="field-row">
              <label>
                <span>Amazon Polly voice</span>
                <select value={s.polly_voice} onChange={(e) => set("polly_voice", e.target.value)}>
                  {Object.entries(meta.polly_voices).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
                </select>
              </label>
              <label className="toggle-row">
                <span>
                  <b>Mute voice warnings</b>
                  <small>Visual and email alerts continue · this browser only</small>
                </span>
                <button type="button" className={`switch ${muted ? "on" : ""}`} onClick={() => setMuted(!muted)} aria-pressed={muted}><i /></button>
              </label>
            </div>
            <TestVoice zones={zones} dirty={dirty && s.polly_voice !== saved?.polly_voice} />
          </SettingsSection>

          <SettingsSection refEl={(el) => (refs.current["Detection"] = el)} title="Detection threshold"
            subtitle="Ignore people far in the background (shown as grey dashed boxes, never logged).">
            <div className="slider-label">
              <span>
                <b>Minimum person size</b>
                <small>{s.min_person_area === 0 ? "Checking everyone in frame" : `Ignore people smaller than ${Math.round(s.min_person_area * 100)}% of frame`}</small>
              </span>
              <code>{Math.round(s.min_person_area * 100)}%</code>
            </div>
            <input className="range" type="range" min={0} max={20} value={Math.round(s.min_person_area * 100)}
              onChange={(e) => set("min_person_area", +e.target.value / 100)}
              style={{ "--value": `${(Math.round(s.min_person_area * 100) / 20) * 100}%` } as React.CSSProperties} aria-label="Minimum person size" />
            <div className="range-values"><span>0% · Everyone</span><span>20% · Nearby only</span></div>
          </SettingsSection>

          <SettingsSection refEl={(el) => (refs.current["Workspace"] = el)} title="Workspace" subtitle="Shown in the sidebar and reports.">
            <div className="field-row">
              <label>
                <span>Workspace name</span>
                <input className="text-input" value={s.workspace_name} maxLength={60} onChange={(e) => set("workspace_name", e.target.value)} />
              </label>
              <div className="info-grid">
                <span>Notification channel</span><b>{meta.notify_channel}{meta.sns_topic ? " · SNS topic configured" : " · SNS topic missing"}</b>
              </div>
            </div>
          </SettingsSection>
        </div>
      </div>
    </div>
  );
}

function SettingsSection({ title, subtitle, right, children, refEl }: {
  title: string; subtitle: string; right?: ReactNode; children: ReactNode; refEl: (el: HTMLElement | null) => void;
}) {
  return (
    <section className="settings-section" ref={refEl}>
      <div className="settings-heading">
        <div>
          <h2>{title}</h2>
          <p>{subtitle}</p>
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

function Timing({ label, value, desc, onChange }: { label: string; value: number; desc: string; onChange: (v: number) => void }) {
  return (
    <label className="timing">
      <span>{label}</span>
      <div>
        <input type="number" min={1} value={value} onChange={(e) => onChange(Math.max(0, Math.round(+e.target.value)))} />
        <em>seconds</em>
      </div>
      <small>{desc}</small>
    </label>
  );
}

function SupervisorRow({ zone, onSaved }: { zone: Zone; onSaved: () => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  const [email, setEmail] = useState(zone.alert_email || "");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => setEmail(zone.alert_email || ""), [zone.alert_email]);
  const save = async () => {
    setErr(null);
    try {
      await post(`/api/zones/${encodeURIComponent(zone.zone_id)}`, { alert_email: email }, "PUT");
      setEditing(false);
      await onSaved();
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  return (
    <div>
      <span className="zone-avatar">{zone.name.charAt(0)}</span>
      <div className="grow">
        <b>{zone.name}</b>
        {editing ? (
          <input className="text-input" type="email" value={email} autoFocus onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(false); }} aria-label={`Supervisor email for ${zone.name}`} />
        ) : (
          <small>{zone.alert_email || "No supervisor email set"}</small>
        )}
        {err && <small className="error-text">{err}</small>}
      </div>
      {editing ? (
        <button onClick={save} title="Save" aria-label="Save email"><Icon name="check" size={15} /></button>
      ) : (
        <button onClick={() => setEditing(true)} title="Edit" aria-label={`Edit ${zone.name} supervisor`}><Icon name="edit" size={15} /></button>
      )}
    </div>
  );
}

function TestVoice({ zones, dirty }: { zones: Zone[]; dirty: boolean }) {
  const [state, setState] = useState<string | null>(null);
  const z = zones.find((x) => x.required.length || x.ai_rules.length);
  const play = () => {
    if (!z) return;
    unlockAudio();
    const gear = z.required[0] || z.ai_rules[0].key;
    const a = new Audio(`/api/voice/${encodeURIComponent(z.zone_id)}/${encodeURIComponent(gear)}?t=${Date.now()}`);
    setState("Playing…");
    a.onended = () => setState(null);
    a.onerror = () => setState("Polly unavailable (mock mode or no permission) — the browser voice is used instead.");
    a.play().catch(() => setState("Click again to allow audio."));
  };
  return (
    <div className="test-voice">
      <button className="secondary" onClick={play} disabled={!z}><Icon name="sound" size={15} /> Test voice warning</button>
      <small>{dirty ? "Save first to hear the new voice." : state || (z ? `Plays the ${z.name} warning` : "Add a zone first")}</small>
    </div>
  );
}
