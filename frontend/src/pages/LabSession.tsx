import { useEffect, useRef, useState } from "react";
import { api, type Health, type ProcedureSummary } from "../api";
import type { useLab } from "../lab";
import { Icon, PageTitle } from "../ui";

type Lab = ReturnType<typeof useLab>;
const mmss = (s: number) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

export default function LabSession({ lab, health, goProcedures }: { lab: Lab; health: Health | null; goProcedures: () => void }) {
  const [procs, setProcs] = useState<ProcedureSummary[] | null>(null);
  const [pick, setPick] = useState("");
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    api<ProcedureSummary[]>("/api/procedures")
      .then((p) => { setProcs(p); setPick((cur) => cur || p[0]?.procedure_id || ""); })
      .catch((e) => setLoadErr(e.message));
  }, []);
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  const sess = lab.session;
  const running = !!sess && !sess.ended_at;
  const elapsed = sess ? ((sess.ended_at ? new Date(sess.ended_at).getTime() : now) - new Date(sess.started_at).getTime()) / 1000 : 0;
  const mock = !!health?.mock_mode;

  return (
    <div className="live-page">
      <PageTitle
        title="Lab session"
        subtitle={sess ? `${sess.procedure.title} · ${running ? "in progress" : "ended"} · ${mmss(elapsed)}` : "Pick a procedure, start the camera, and LabScribe writes down what happens"}
        action={
          <div className="session-actions">
            {!running && (
              <select value={pick} onChange={(e) => setPick(e.target.value)} aria-label="Procedure" disabled={!procs?.length}>
                {procs?.length ? procs.map((p) => <option key={p.procedure_id} value={p.procedure_id}>{p.title} ({p.step_count} steps)</option>)
                  : <option>No procedures yet</option>}
              </select>
            )}
            {running ? (
              <button className="secondary" onClick={lab.endSession}>End session</button>
            ) : (
              <button className="primary" disabled={!pick} onClick={() => lab.startSession(pick)}>
                <Icon name="flask" size={15} /> {sess ? "Start new session" : "Start session"}
              </button>
            )}
          </div>
        }
      />
      {(lab.error || loadErr) && <div className="banner error">{lab.error || loadErr}</div>}
      {procs && !procs.length && (
        <div className="banner info">No procedures yet. <button className="link-button" onClick={goProcedures}>Upload a lab manual first →</button></div>
      )}
      {running && !lab.stream && !mock && (
        <div className="banner info">Start the camera so the Scribe can see the bench.</div>
      )}

      <div className="monitor-grid">
        <Feed lab={lab} mock={mock} running={running} />
        <ScribePanel lab={lab} running={running} />
      </div>

      {sess && (
        <section className="steps-preview">
          <div className="review-head">
            <div>
              <h2>Procedure · {sess.procedure.steps.length} steps</h2>
              <p>Step tracking and feedback arrive in the next phase — for now, compare the Scribe's log with the steps.</p>
            </div>
          </div>
          <ol className="step-checklist">
            {sess.procedure.steps.map((s) => (
              <li key={s.number} className={s.safety_critical ? "safety" : ""}>
                <span className="step-n">{s.number}</span>
                <div>
                  <b>{s.instruction}</b>
                  <small>Camera looks for: {s.expected_action || "—"}{s.required_ppe.length ? ` · PPE: ${s.required_ppe.join(", ")}` : ""}</small>
                </div>
                {s.safety_critical && <span className="safety-tag"><Icon name="shield" size={11} /> safety</span>}
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}

function Feed({ lab, mock, running }: { lab: Lab; mock: boolean; running: boolean }) {
  const ref = useRef<HTMLVideoElement>(null);
  const [dims, setDims] = useState({ w: 16, h: 9 });
  useEffect(() => {
    if (ref.current) {
      ref.current.srcObject = lab.stream;
      if (lab.stream) ref.current.play().catch(() => {});
    }
  }, [lab.stream]);
  return (
    <section className="feed-card">
      <div className="feed-top">
        <div>
          <span className={`live-dot ${lab.stream ? "" : "off"}`} />
          {lab.stream ? "LIVE" : "OFFLINE"} <b>BENCH CAM</b>
          <span>·</span>
          <span>{running ? "Scribe recording" : "Scribe idle"}</span>
        </div>
        <div>
          {lab.stream && <button title="Stop camera" aria-label="Stop camera" onClick={lab.stopCamera}>■</button>}
        </div>
      </div>
      <div className={`feed ${!lab.stream ? "feed-offline" : ""}`} style={lab.stream ? { aspectRatio: `${dims.w} / ${dims.h}` } : undefined}>
        {lab.stream ? (
          <>
            <video ref={ref} className="feed-video" muted playsInline
              onLoadedMetadata={(e) => setDims({ w: e.currentTarget.videoWidth, h: e.currentTarget.videoHeight })} />
            <div className="camera-shade" />
            <div className="source-tags"><span>Bedrock vision</span><span>every 3 s</span>{mock && <span>Mock scribe</span>}</div>
            {lab.lines.length > 0 && running && (
              <div className="scribe-caption">{lab.lines[lab.lines.length - 1].action}</div>
            )}
          </>
        ) : (
          <div className="offline-state">
            <div className="offline-icon"><Icon name="camera" size={30} /></div>
            <h3>Camera is off</h3>
            <p>Point the camera at your bench so it can see your hands, the cup and the bottles.{mock ? " (Mock mode works without a camera.)" : ""}</p>
            <button className="primary" onClick={lab.startCamera} disabled={lab.starting}>
              <Icon name="camera" size={16} /> {lab.starting ? "Starting…" : "Start camera"}
            </button>
          </div>
        )}
      </div>
      <div className="feed-foot">
        <div>
          <span className="quality"><i className={lab.stream ? "" : "off"} /> {lab.stream && dims.h > 9 ? `${dims.h}p` : "—"}</span>
          <span>2 frames / 3 s</span>
          <span>Scribe {lab.lastMs != null ? `${(lab.lastMs / 1000).toFixed(1)} s` : "—"}</span>
        </div>
        <span>{lab.model || "—"}</span>
      </div>
    </section>
  );
}

function ScribePanel({ lab, running }: { lab: Lab; running: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  useEffect(() => {
    if (box.current && stick.current) box.current.scrollTop = box.current.scrollHeight;
  }, [lab.lines]);
  return (
    <section className="detection-panel scribe-panel">
      <div className="panel-heading">
        <div>
          <span>LIVE TRANSCRIPT</span>
          <h2>Scribe</h2>
        </div>
        <span className={`rec ${running ? "on" : ""}`}><i /> {running ? "REC" : "IDLE"}</span>
      </div>
      <div className="scribe-log" ref={box}
        onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40; }}>
        {!lab.lines.length && (
          <p className="panel-empty">{running ? "Listening… the first line appears within a few seconds." : "Start a session to begin the transcript."}</p>
        )}
        {lab.lines.map((l, i) => (
          <div key={`${l.time}-${i}`} className={`scribe-line ${l.action.toLowerCase().startsWith("no visible action") ? "idle" : ""}`}>
            <time title={new Date(l.time).toLocaleTimeString()}>{mmss(l.elapsed)}</time>
            <div>
              <p>{l.action}{l.repeat > 1 && <em> ×{l.repeat}</em>}</p>
              {l.objects.length > 0 && <div className="obj-chips">{l.objects.map((o) => <span key={o}>{o}</span>)}</div>}
            </div>
          </div>
        ))}
      </div>
      <div className="scribe-foot">
        {lab.scribeError ? <span className="bad">Last frame failed: {lab.scribeError}</span>
          : <span>{lab.lines.length} lines{lab.skipped ? ` · ${lab.skipped} frames skipped (previous call still running)` : ""}</span>}
      </div>
    </section>
  );
}
