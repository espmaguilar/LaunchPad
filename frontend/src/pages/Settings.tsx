import { useEffect, useState } from "react";
import { api, post, type Settings, type SettingsResponse } from "../api";
import type { useLab } from "../lab";
import { Icon, Notice, PageHeading } from "../ui";
import { speak, unlockAudio } from "../voice";

type Lab = ReturnType<typeof useLab>;

export default function SettingsPage({ lab, muted, toggleVoice, onSaved }: {
  lab: Lab; muted: boolean; toggleVoice: () => void; onSaved: (s: Settings) => void;
}) {
  const [meta, setMeta] = useState<SettingsResponse | null>(null);
  const [s, setS] = useState<Settings | null>(null);
  const [saved, setSaved] = useState<Settings | null>(null);
  const [msg, setMsg] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<SettingsResponse>("/api/settings")
      .then((r) => { setMeta(r); setS(r.settings); setSaved(r.settings); })
      .catch((e) => setMsg({ tone: "error", text: `Could not load settings: ${e.message}` }));
  }, []);
  const dirty = !!s && !!saved && JSON.stringify(s) !== JSON.stringify(saved);
  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setS((c) => (c ? { ...c, [k]: v } : c));
  const save = async () => {
    if (!s) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await post<{ settings: Settings }>("/api/settings", s, "PUT");
      setS(r.settings); setSaved(r.settings); onSaved(r.settings);
      setMsg({ tone: "ok", text: "Settings saved." });
    } catch (e) {
      setMsg({ tone: "error", text: (e as Error).message });
    } finally { setBusy(false); }
  };
  const pct = s ? Math.round(s.min_person_area * 100) : 0;

  return (
    <div className="page-container settings-page">
      <PageHeading kicker="PREFERENCES" title="Session settings" sub="Control how LabScribe watches, speaks, and records your sessions."
        right={<button className="primary" onClick={save} disabled={busy || !dirty}>{busy ? "Saving…" : dirty ? "Save changes" : "Saved"}</button>} />
      {msg && <Notice tone={msg.tone} onClose={() => setMsg(null)}>{msg.text}</Notice>}
      <section className="settings-card card">
        <h2>Live session</h2>
        <div className="setting-row">
          <div className="setting-icon"><Icon name="camera" /></div>
          <div><strong>Camera monitoring</strong><p>Confirm experiment steps using your live camera.</p></div>
          <button className={`toggle ${lab.stream ? "on" : ""}`} aria-pressed={!!lab.stream} aria-label="Camera monitoring"
            onClick={() => (lab.stream ? lab.stopCamera() : lab.startCamera())}><i /></button>
        </div>
        <div className="setting-row">
          <div className="setting-icon"><Icon name="volume" /></div>
          <div><strong>Coach voice</strong><p>Read corrections and next steps aloud with Amazon Polly.</p></div>
          <button className={`toggle ${muted ? "" : "on"}`} aria-pressed={!muted} aria-label="Coach voice" onClick={toggleVoice}><i /></button>
        </div>
        <div className="setting-row disabled">
          <div className="setting-icon"><Icon name="shield" /></div>
          <div><strong>Block on safety steps</strong><p>Pause the procedure until required PPE is confirmed by the camera. Arrives with the PPE check (next phase).</p></div>
          <button className="toggle on" disabled aria-label="Block on safety steps (coming soon)"><i /></button>
        </div>
      </section>

      {s && meta && (
        <section className="settings-card card">
          <h2>Voice, camera & workspace</h2>
          <div className="setting-row">
            <div className="setting-icon"><Icon name="mic" /></div>
            <div><strong>Amazon Polly voice</strong><p>Neural voice used for step guidance and corrections (cached per phrase).</p></div>
            <div className="setting-control">
              <select value={s.polly_voice} onChange={(e) => set("polly_voice", e.target.value)} aria-label="Polly voice">
                {Object.entries(meta.polly_voices).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
              </select>
              <button className="secondary small" onClick={() => { unlockAudio(); speak("Step 1 complete. Next: fill the clear cup halfway with water.", true); }}
                title={dirty ? "Save first to hear a new voice" : "Play a sample"}><Icon name="play" size={12} /> Test</button>
            </div>
          </div>
          <div className="setting-row">
            <div className="setting-icon"><Icon name="users" /></div>
            <div><strong>Background filter</strong><p>{pct === 0 ? "Track everyone in frame." : `Ignore people smaller than ${pct}% of the frame (classmates in the background).`}</p></div>
            <div className="setting-control">
              <input type="range" min={0} max={20} value={pct} onChange={(e) => set("min_person_area", +e.target.value / 100)} aria-label="Minimum person size" />
              <code>{pct}%</code>
            </div>
          </div>
          <div className="setting-row">
            <div className="setting-icon"><Icon name="file" /></div>
            <div><strong>Workspace name</strong><p>Shown in the sidebar and on lab records.</p></div>
            <div className="setting-control">
              <input className="text-input" value={s.workspace_name} maxLength={60} onChange={(e) => set("workspace_name", e.target.value)} aria-label="Workspace name" />
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
