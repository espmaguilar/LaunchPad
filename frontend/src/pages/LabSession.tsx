import { useEffect, useRef, useState } from "react";
import { api, type FeedbackItem, type Health, type LabSession as Session, type ProcedureSummary } from "../api";
import { hhmmss, mmss, pad2 } from "../format";
import type { useLab } from "../lab";
import { Icon, Notice } from "../ui";

type Lab = ReturnType<typeof useLab>;

const STATUS_LINE: Record<string, string> = {
  done: "Looks done — confirming…", in_progress: "In progress…", not_started: "Waiting for this step…",
  next_step_detected: "Next step spotted", wrong_action: "Something looks off",
};
const MISTAKE_LABEL: Record<string, [string, string]> = {
  wrong_order: ["ORDER CHECK", "Wrong order"], wrong_material: ["MATERIAL CHECK", "Wrong material"],
  wrong_technique: ["TECHNIQUE", "Technique tip"], incomplete: ["COMPLETENESS", "Step not finished"],
  safety: ["SAFETY", "Safety first"],
};

export default function LabSession({ lab, health, openRecord, goProcedures }: {
  lab: Lab; health: Health | null; openRecord: (sessionId: string) => void; goProcedures: () => void;
}) {
  const [procs, setProcs] = useState<ProcedureSummary[] | null>(null);
  const [pick, setPick] = useState("");
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    api<ProcedureSummary[]>("/api/procedures")
      .then((p) => { setProcs(p); setPick((cur) => cur || p[0]?.procedure_id || ""); })
      .catch((e) => setLoadErr(e.message));
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  const sess = lab.session;
  const running = !!sess && !sess.ended_at;
  const mock = !!health?.mock_mode;
  const coachFresh = !!lab.coach && now - lab.coach.at < 7000;
  const toast = coachFresh && lab.coach && (lab.coach.type === "step_done" || lab.coach.type === "complete") ? lab.coach : null;

  return (
    <div className="live-page">
      <StepsPanel sess={sess} />
      <main className="live-center">
        {(lab.error || loadErr) && <Notice tone="error">{lab.error || loadErr}</Notice>}
        {running ? <CurrentCard sess={sess!} /> : (
          <section className="current-card card start-card">
            <div className="current-number"><Icon name="flask" size={22} /></div>
            <div className="current-copy">
              <span>{sess ? "SESSION ENDED" : "START A SESSION"}</span>
              <h1>{sess ? `${sess.procedure.title} — ${sess.complete ? "all steps done" : `${sess.steps_state.filter((x) => x.status === "done").length} of ${sess.procedure.steps.length} steps`}` : "Pick a procedure to begin"}</h1>
              <div className="start-row">
                {procs?.length ? (
                  <select value={pick} onChange={(e) => setPick(e.target.value)} aria-label="Procedure">
                    {procs.map((p) => <option key={p.procedure_id} value={p.procedure_id}>{p.title} · {p.step_count} steps</option>)}
                  </select>
                ) : <button className="secondary" onClick={goProcedures}>Upload a lab manual first</button>}
                <button className="primary" disabled={!pick} onClick={() => lab.startSession(pick)}>
                  <Icon name="play" size={14} /> {sess ? "Start new session" : "Start session"}
                </button>
                {sess && <button className="secondary" onClick={() => openRecord(sess.session_id)}><Icon name="records" size={15} /> View lab record</button>}
              </div>
            </div>
          </section>
        )}
        <div className="feed-wrap">
          <CameraFeed lab={lab} running={running} mock={mock} now={now} />
        </div>
        {running && (
          <div className="camera-controls">
            <button className="primary" onClick={lab.markDone} disabled={sess!.complete}><Icon name="check" size={17} /> Mark step done</button>
            <button className="secondary" onClick={lab.undo} disabled={sess!.current === 0}><Icon name="undo" size={16} /> Undo</button>
            <span>Use manual override only if the AI misses a step</span>
            <button className="secondary end-btn" onClick={lab.endSession}>End session</button>
          </div>
        )}
        {toast && (
          <div className="success-toast">
            <span><Icon name="check" size={15} /></span>
            <div>
              <strong>{toast.type === "complete" ? "All steps complete" : toast.text.split(". Next:")[0]}</strong>
              <small>{toast.type === "complete" ? "End the session to see your lab record" : `Next: ${toast.text.split(". Next: ")[1] ?? ""}`}</small>
            </div>
          </div>
        )}
      </main>
      <aside className="right-panels">
        <ScribePanel lab={lab} running={running} />
        <CoachPanel lab={lab} sess={sess} speaking={coachFresh ? lab.coach!.text : null} />
      </aside>
    </div>
  );
}

function StepsPanel({ sess }: { sess: Session | null }) {
  const steps = sess?.procedure.steps ?? [];
  const total = steps.length;
  const done = sess?.steps_state.filter((x) => x.status === "done").length ?? 0;
  const cur = sess ? Math.min(sess.current + 1, total) : 0;
  return (
    <section className="steps-panel card">
      <div className="panel-heading">
        <div>
          <span>PROCEDURE</span>
          <h2>Experiment steps</h2>
        </div>
      </div>
      <div className="progress-label">
        <span>{sess ? (sess.complete ? "All steps done" : `Step ${cur} of ${total}`) : "No session"}</span>
        <strong>{total ? Math.round((done / total) * 100) : 0}%</strong>
      </div>
      <div className="progress-track"><i style={{ width: `${total ? (done / total) * 100 : 0}%` }} /></div>
      <div className="step-list">
        {!sess && <p className="panel-empty">Start a session to see the steps here.</p>}
        {steps.map((step, index) => {
          const st = sess!.steps_state[index];
          const isDone = st?.status === "done";
          const isCur = st?.status === "current";
          return (
            <div className={`live-step ${isDone ? "done" : ""} ${isCur ? "current" : ""}`} key={step.number}>
              <div className="step-rail">
                <span>{isDone ? <Icon name="check" size={14} stroke={2.5} /> : isCur ? <i className="pulse" /> : step.number}</span>
                {index < steps.length - 1 && <b />}
              </div>
              <div className="step-copy">
                <div className="step-kicker">
                  {isDone ? <><Icon name="clock" size={12} />{mmss(st.done_elapsed ?? 0)}</> : isCur ? "IN PROGRESS" : `STEP ${step.number}`}
                  {step.safety_critical && <span className="kicker-safety"><Icon name="shield" size={11} /></span>}
                </div>
                <strong>{step.instruction}</strong>
                {isDone && <small>{st.method === "manual" ? "Marked done by you" : st.evidence}</small>}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function CurrentCard({ sess }: { sess: Session }) {
  const step = sess.procedure.steps[sess.current];
  if (sess.complete || !step) {
    return (
      <section className="current-card card">
        <div className="current-number done"><Icon name="check" size={24} stroke={2.4} /></div>
        <div className="current-copy">
          <span>ALL DONE</span>
          <h1>Every step is complete — nice work!</h1>
          <div className="status-line"><i /> End the session to generate your lab record</div>
        </div>
      </section>
    );
  }
  const chk = sess.last_check && sess.last_check.step === step.number ? sess.last_check : null;
  const conf = chk ? Math.round(chk.confidence * 100) : null;
  return (
    <section className={`current-card card ${step.safety_critical ? "safety" : ""}`}>
      <div className="current-number">{pad2(step.number)}</div>
      <div className="current-copy">
        <span>CURRENT STEP{step.safety_critical ? " · SAFETY-CRITICAL" : ""}</span>
        <h1>{step.instruction}</h1>
        <div className={`status-line ${chk?.mistake ? "bad" : ""}`}>
          <i /> {chk ? STATUS_LINE[chk.status] || chk.status : "Watching for this step…"}
          {chk?.evidence && <em>“{chk.evidence}”</em>}
        </div>
        <div className="looks-for">Camera looks for: <b>{step.expected_action || "—"}</b>
          {step.required_ppe.length > 0 && <span className="ppe-mini"><Icon name="shield" size={11} /> {step.required_ppe.join(", ")}</span>}
        </div>
      </div>
      <div className="confidence">
        <div>
          <span>AI confidence</span>
          <strong>{conf == null ? "—" : `${conf}%`}</strong>
        </div>
        <div className="confidence-track"><i style={{ width: `${conf ?? 0}%` }} /></div>
        <small>{conf == null ? "No check yet" : conf >= 70 ? "High confidence" : conf >= 50 ? "Medium confidence" : "Low confidence"}</small>
      </div>
    </section>
  );
}

function CameraFeed({ lab, running, mock, now }: { lab: Lab; running: boolean; mock: boolean; now: number }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    if (ref.current) {
      ref.current.srcObject = lab.stream;
      if (lab.stream) ref.current.play().catch(() => {});
    }
  }, [lab.stream]);
  const state = lab.starting ? "loading" : lab.stream ? "live" : "error";
  const last = lab.lines[lab.lines.length - 1];
  const elapsed = lab.session ? (now - new Date(lab.session.started_at).getTime()) / 1000 : 0;
  return (
    <div className={`camera-feed ${state}`}>
      {state === "live" && (
        <>
          <video ref={ref} muted playsInline aria-label="Live bench camera" />
          <div className="camera-vignette" />
          <div className="camera-label"><i /><span>{running ? "AI WATCHING" : "CAMERA ON"}</span></div>
          {running && <div className="scan-line" />}
          {running && last && (
            <div className="detected">
              <Icon name="spark" size={13} />
              <span>{last.action}</span>
              {last.ms > 0 && <strong>{(last.ms / 1000).toFixed(1)}s</strong>}
            </div>
          )}
          <div className="camera-time">CAM 01&nbsp;&nbsp; {running ? hhmmss(elapsed) : "STANDBY"}</div>
        </>
      )}
      {state === "loading" && (
        <div className="camera-skeleton">
          <div className="skeleton-icon"><Icon name="camera" size={28} /></div>
          <span /><span />
          <p>Connecting to camera…</p>
        </div>
      )}
      {state === "error" && (
        <div className="camera-error">
          <div><Icon name="camera" size={30} /></div>
          <h3>Camera is off</h3>
          <p>Point it at your bench so LabScribe can see your hands and materials.{mock ? " Mock mode runs without a camera." : ""}</p>
          <button onClick={lab.startCamera}>Start camera</button>
        </div>
      )}
    </div>
  );
}

function ScribePanel({ lab, running }: { lab: Lab; running: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  useEffect(() => {
    if (box.current && stick.current) box.current.scrollTop = box.current.scrollHeight;
  }, [lab.lines]);
  const exportTxt = () => {
    const sess = lab.session;
    const text = [`LabScribe transcript - ${sess?.procedure.title ?? ""} - ${sess?.started_at ?? ""}`, "",
      ...lab.lines.map((l) => `[${mmss(l.elapsed)}] ${l.action}${l.repeat > 1 ? ` (x${l.repeat})` : ""}`)].join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    a.download = "labscribe-transcript.txt";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
  return (
    <section className="scribe card">
      <div className="panel-heading">
        <div>
          <span>LIVE TRANSCRIPT</span>
          <h2>Scribe</h2>
        </div>
        <div className={`listening ${running ? "" : "idle"}`}><i /> {running ? "Listening" : "Idle"}</div>
      </div>
      <div className="transcript" ref={box}
        onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 30; }}>
        {!lab.lines.length && <p className="panel-empty">{running ? "Watching… the first line appears within a few seconds." : "The transcript appears here during a session."}</p>}
        {lab.lines.map((l, i) => (
          <div className={`${i === lab.lines.length - 1 && running ? "new" : ""} ${l.action.toLowerCase().startsWith("no visible action") ? "idle" : ""}`} key={`${l.time}-${i}`}>
            <time>{mmss(l.elapsed)}</time>
            <p>{l.action}{l.repeat > 1 && <em> ×{l.repeat}</em>}</p>
          </div>
        ))}
      </div>
      <div className="scribe-footer">
        <span>{lab.scribeError ? `Last frame failed: ${lab.scribeError}` : lab.skipped ? `${lab.skipped} frames skipped` : "Auto-scrolling"}</span>
        <button onClick={exportTxt} disabled={!lab.lines.length}><Icon name="download" size={14} /> Export</button>
      </div>
    </section>
  );
}

function CoachPanel({ lab, sess, speaking }: { lab: Lab; sess: Session | null; speaking: string | null }) {
  const open = (sess?.feedback ?? []).filter((f) => !f.dismissed).slice().reverse();
  const flagged = (sess?.feedback ?? []).length;
  return (
    <section className="coach card">
      <div className="panel-heading">
        <div>
          <span>GUIDANCE</span>
          <h2>Coach feedback</h2>
        </div>
        <div className="coach-online"><Icon name="spark" size={13} /> {sess && !sess.ended_at ? "Active" : "Standby"}</div>
      </div>
      {open.length > 0 && (
        <div className="mistake-stack">
          {open.map((f) => <MistakeCard key={f.id} f={f} sid={sess!.session_id} onDismiss={lab.dismiss} />)}
        </div>
      )}
      {sess && !sess.ended_at && !sess.complete ? (
        <div className="tip-suggestion">
          <div className="tip-head">
            <span><Icon name="spark" size={14} /></span>
            <small>COACH SUGGESTION · STEP {sess.procedure.steps[sess.current]?.number}</small>
          </div>
          <p>{sess.coach_tip?.text || `Start with: ${sess.procedure.steps[sess.current]?.instruction}`}</p>
          {sess.last_check && (
            <em>Last check: {STATUS_LINE[sess.last_check.status]?.replace("…", "") || sess.last_check.status} · {Math.round(sess.last_check.confidence * 100)}%</em>
          )}
        </div>
      ) : !open.length && (
        <div className="all-clear">
          <span><Icon name="check" /></span>
          <strong>{sess?.complete ? "Every step done — great work" : flagged ? "You’re back on track" : sess ? "Session ended" : "Ready when you are"}</strong>
          <p>{sess ? "Open the lab record to review your run." : "Tips and corrections show up here during a session."}</p>
        </div>
      )}
      {speaking && (
        <div className="voice-speaking">
          <div className="wave"><i /><i /><i /><i /></div>
          <span title={speaking}>{speaking}</span>
        </div>
      )}
    </section>
  );
}

function MistakeCard({ f, sid, onDismiss }: { f: FeedbackItem; sid: string; onDismiss: (id: string) => void }) {
  const [imgOk, setImgOk] = useState(true);
  const [kicker, title] = f.kind === "possible_skip" ? ["STEP CHECK", "Possible skipped step"] : MISTAKE_LABEL[f.mistake_type] ?? ["CHECK", "Mistake"];
  return (
    <div className={`mistake-card ${f.kind}`}>
      <div className="mistake-title">
        <span><Icon name="alert" size={16} /></span>
        <div>
          <small>{kicker}</small>
          <strong>{title}</strong>
        </div>
        <time>{mmss(f.elapsed)}</time>
      </div>
      <div className="feedback-row">
        <b>What happened</b>
        <p>{f.what_went_wrong}</p>
      </div>
      <div className="feedback-row fix">
        <b>How to fix</b>
        <p>{f.how_to_fix}</p>
      </div>
      <div className="mistake-footer">
        <div className="snapshot">
          {f.snapshot_id && imgOk ? <img src={`/api/sessions/${sid}/snapshots/${f.snapshot_id}`} alt="Snapshot when it happened" onError={() => setImgOk(false)} />
            : <div className="snap-empty"><Icon name="camera" size={14} /></div>}
        </div>
        {f.corrected && <em className="fixed-tag"><Icon name="check" size={12} /> fixed</em>}
        <button onClick={() => onDismiss(f.id)}><Icon name="check" size={15} /> Got it</button>
      </div>
    </div>
  );
}
