import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, post, type Settings, type SettingsResponse } from "../api";
import { Icon, PageTitle } from "../ui";
import { speak, unlockAudio } from "../voice";

const SECTIONS = ["Voice & audio", "Camera", "Workspace"];

export default function SettingsPage({ onSaved, muted, setMuted }: {
  onSaved: (s: Settings) => void; muted: boolean; setMuted: (v: boolean) => void;
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
      setMsg({ tone: "ok", text: "Settings saved." });
    } catch (e) {
      setMsg({ tone: "error", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  if (!s || !meta) return <div><PageTitle title="Settings" subtitle="Loading…" />{msg && <div className={`banner ${msg.tone}`}>{msg.text}</div>}</div>;
  const pct = Math.round(s.min_person_area * 100);

  return (
    <div className="settings-page">
      <PageTitle title="Settings" subtitle="Voice guidance, camera and workspace"
        action={<button className="primary" onClick={save} disabled={busy || !dirty}>{busy ? "Saving…" : dirty ? "Save changes" : "Saved"}</button>} />
      {msg && <div className={`banner ${msg.tone}`}>{msg.text}</div>}
      <div className="settings-layout">
        <div className="settings-nav">
          {SECTIONS.map((n) => (
            <button key={n} className={active === n ? "active" : ""}
              onClick={() => { setActive(n); refs.current[n]?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>{n}</button>
          ))}
        </div>
        <div className="settings-content">
          <Section refEl={(el) => (refs.current["Voice & audio"] = el)} title="Voice & audio"
            subtitle="Step guidance and corrections are spoken with Amazon Polly (neural), cached per phrase.">
            <div className="field-row">
              <label>
                <span>Amazon Polly voice</span>
                <select value={s.polly_voice} onChange={(e) => set("polly_voice", e.target.value)}>
                  {Object.entries(meta.polly_voices).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
                </select>
              </label>
              <label className="toggle-row">
                <span>
                  <b>Mute voice guidance</b>
                  <small>On-screen guidance continues · this browser only</small>
                </span>
                <button type="button" className={`switch ${muted ? "on" : ""}`} onClick={() => setMuted(!muted)} aria-pressed={muted}><i /></button>
              </label>
            </div>
            <div className="test-voice">
              <button className="secondary" onClick={() => { unlockAudio(); speak("Step 1 complete. Next: fill the clear cup halfway with water.", true); }}>
                <Icon name="sound" size={15} /> Test voice
              </button>
              <small>{dirty && s.polly_voice !== saved?.polly_voice ? "Save first to hear the new voice." : "Plays a sample step announcement"}</small>
            </div>
          </Section>

          <Section refEl={(el) => (refs.current["Camera"] = el)} title="Background filter"
            subtitle="Ignore people far in the background so only the student at the bench is tracked.">
            <div className="slider-label">
              <span>
                <b>Minimum person size</b>
                <small>{pct === 0 ? "Track everyone in frame" : `Ignore people smaller than ${pct}% of the frame`}</small>
              </span>
              <code>{pct}%</code>
            </div>
            <input className="range" type="range" min={0} max={20} value={pct} onChange={(e) => set("min_person_area", +e.target.value / 100)}
              style={{ "--value": `${(pct / 20) * 100}%` } as React.CSSProperties} aria-label="Minimum person size" />
            <div className="range-values"><span>0% · Everyone</span><span>20% · Nearby only</span></div>
          </Section>

          <Section refEl={(el) => (refs.current["Workspace"] = el)} title="Workspace" subtitle="Shown in the sidebar and lab records.">
            <div className="field-row">
              <label>
                <span>Workspace name</span>
                <input className="text-input" value={s.workspace_name} maxLength={60} onChange={(e) => set("workspace_name", e.target.value)} />
              </label>
            </div>
          </Section>
        </div>
      </div>
    </div>
  );
}

function Section({ title, subtitle, children, refEl }: { title: string; subtitle: string; children: ReactNode; refEl: (el: HTMLElement | null) => void }) {
  return (
    <section className="settings-section" ref={refEl}>
      <div className="settings-heading"><div><h2>{title}</h2><p>{subtitle}</p></div></div>
      {children}
    </section>
  );
}
