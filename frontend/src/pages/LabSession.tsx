import { useEffect, useRef, useState } from "react";
import { api, type FeedbackItem, type Health, type LabSession as Session, type ProcedureSummary } from "../api";
import { hhmmss, mmss, pad2 } from "../format";
import type { useLab } from "../lab";
import { Icon } from "../ui";
import { isMuted } from "../voice";

type Lab = ReturnType<typeof useLab>;

const STATUS_LINE: Record<string, string> = {
  done: "Looks done", in_progress: "In progress", not_started: "Watching", next_step_detected: "Next step spotted",
  wrong_action: "Something looks off",
};
const MISTAKE_LABEL: Record<string, string> = {
  wrong_order: "Wrong order", wrong_material: "Wrong material", wrong_technique: "Technique tip", incomplete: "Step not finished", safety: "Safety first",
};

export default function LabSession({ lab, health, openRecord, goProcedures, muted, toggleVoice }: {
  lab: Lab; health: Health | null; openRecord: (sessionId: string) => void; goProcedures: () => void;
  muted: boolean; toggleVoice: () => void;
}) {
  const [procs, setProcs] = useState<ProcedureSummary[] | null>(null);
  const [pick, setPick] = useState("");
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());
  const [stepsOpen, setStepsOpen] = useState(false);

  useEffect(() => {
    api<ProcedureSummary[]>("/api/procedures")
      .then((p) => { setProcs(p); setPick((cur) => cur || lab.session?.procedure_id || p[0]?.procedure_id || ""); })
      .catch((e) => setLoadErr(e.message));
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const sess = lab.session;
  const running = !!sess && !sess.ended_at;
  const mock = !!health?.mock_mode;
  useEffect(() => { if (sess?.procedure_id) setPick(sess.procedure_id); }, [sess?.procedure_id]);

  const choose = async (id: string) => {
    setPick(id);
    if (running && id !== sess!.procedure_id) {
      const title = procs?.find((p) => p.procedure_id === id)?.title ?? "this procedure";
      if (!confirm(`End the current session and start "${title}"?`)) { setPick(sess!.procedure_id); return; }
      await lab.endSession();
      await lab.startSession(id);
    }
  };

  const steps = sess?.procedure.steps ?? [];
  const step = sess && !sess.complete ? steps[sess.current] : undefined;
  const chk = sess?.last_check && step && sess.last_check.step === step.number ? sess.last_check : null;
  const conf = chk ? Math.round(chk.confidence * 100) : null;
  const elapsed = sess ? ((sess.ended_at ? new Date(sess.ended_at).getTime() : now) - new Date(sess.started_at).getTime()) / 1000 : 0;
  const toast = lab.coach && now - lab.coach.at < 6000 && (lab.coach.type === "step_done" || lab.coach.type === "complete") ? lab.coach : null;

  return (
    <div className="live-immersive">
      <header className="instruction-header">
        <div className="instruction-step">{step ? pad2(step.number) : sess?.complete ? <Icon name="check" size={20} stroke={2.4} /> : <Icon name="flask" size={20} />}</div>
        <div className="instruction-copy">
          <span>
            {running && step ? `CURRENT INSTRUCTION · STEP ${step.number} OF ${steps.length}${step.section ? ` · ${step.section.toUpperCase()}` : ""}`
              : sess?.complete ? "ALL STEPS COMPLETE" : sess ? "SESSION ENDED" : "CHOOSE A PROCEDURE"}
          </span>
          <h1>
            {running && step ? step.instruction
              : sess?.complete ? "Every step is done — nice work!"
                : sess ? `${sess.procedure.title}: ${sess.steps_state.filter((x) => x.status === "done").length} of ${steps.length} steps done`
                  : "Pick a procedure from your library and start the session"}
          </h1>
          {running && step && step.camera_checkable === false && <em className="paper-flag"><Icon name="edit" size={11} /> Paper step — tap Mark step done when finished</em>}
        </div>
        {running && step && step.camera_checkable !== false && (
          <div className="instruction-status">
            <div>
              <span><i /> {chk ? STATUS_LINE[chk.status] || chk.status : "AI watching"}</span>
              <strong>{conf == null ? "—" : `${conf}%`}</strong>
            </div>
            <div className="instruction-confidence"><i style={{ width: `${conf ?? 0}%` }} /></div>
          </div>
        )}
        <div className="procedure-picker">
          <select value={pick} onChange={(e) => choose(e.target.value)} aria-label="Procedure" disabled={!procs?.length}>
            {procs?.length ? procs.map((p) => <option key={p.procedure_id} value={p.procedure_id}>{p.title} · {p.step_count} steps</option>)
              : <option value="">No procedures yet</option>}
          </select>
          {!procs?.length ? (
            <button className="picker-btn" onClick={goProcedures}><Icon name="upload" size={13} /> Add one</button>
          ) : running ? (
            <button className="picker-btn ghost" onClick={lab.endSession}>End</button>
          ) : (
            <button className="picker-btn" disabled={!pick} onClick={() => lab.startSession(pick)}><Icon name="play" size={12} /> {sess ? "Restart" : "Start"}</button>
          )}
        </div>
        {sess && (
          <div className="instruction-timer"><Icon name="clock" size={15} /><span>{hhmmss(elapsed)}</span></div>
        )}
        {sess && (
          <button className={`instruction-more ${stepsOpen ? "open" : ""}`} onClick={() => setStepsOpen(!stepsOpen)}>
            All steps <Icon name="chevron" size={13} />
          </button>
        )}
        {sess && !running && (
          <button className="instruction-more" onClick={() => openRecord(sess.session_id)}>Lab record <Icon name="chevron" size={13} /></button>
        )}
      </header>

      <main className="immersive-feed">
        {(lab.error || loadErr) && <div className="feed-alert">{lab.error || loadErr}</div>}
        <CameraFeed lab={lab} running={running} mock={mock} elapsed={elapsed} confidence={conf} />
        {stepsOpen && sess && <StepsDrawer sess={sess} onClose={() => setStepsOpen(false)} />}
        <ScribeOverlay lab={lab} running={running} />

        <div className="immersive-controls">
          <button className="control-undo" onClick={lab.undo} disabled={!running || sess!.current === 0} title="Undo previous step" aria-label="Undo previous step">
            <Icon name="undo" size={17} />
          </button>
          <button className="control-done" onClick={lab.markDone} disabled={!running || !!sess?.complete}>
            <Icon name="check" size={17} /> Mark step done
          </button>
          <button className={`control-safety ${lab.stream ? "on" : ""}`} onClick={() => (lab.stream ? lab.stopCamera() : lab.startCamera())}
            title={lab.stream ? "Stop camera" : "Start camera"} aria-label={lab.stream ? "Stop camera" : "Start camera"}>
            <Icon name="camera" size={17} />
          </button>
        </div>

        <CoachChat lab={lab} sess={sess} muted={muted} toggleVoice={toggleVoice} />

        {toast && (
          <div className="success-toast immersive-toast">
            <span><Icon name="check" size={15} /></span>
            <div>
              <strong>{toast.type === "complete" ? "All steps complete" : toast.text.split(". Next:")[0]}</strong>
              <small>{toast.type === "complete" ? "End the session to see your lab record" : `Next: ${(toast.text.split(". Next: ")[1] ?? "").split(" This one's")[0]}`}</small>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

function CameraFeed({ lab, running, mock, elapsed, confidence }: { lab: Lab; running: boolean; mock: boolean; elapsed: number; confidence: number | null }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    if (ref.current) {
      ref.current.srcObject = lab.stream;
      if (lab.stream) ref.current.play().catch(() => {});
    }
  }, [lab.stream]);
  const state = lab.starting ? "loading" : lab.stream ? "live" : mock && running ? "mock" : "error";
  const last = lab.lines[lab.lines.length - 1];
  const showCallout = running && last && !last.action.toLowerCase().startsWith("no visible action");
  return (
    <div className={`camera-feed ${state === "mock" ? "error" : state}`}>
      {state === "live" && (
        <>
          <video ref={ref} muted playsInline aria-label="Live bench camera" />
          <div className="camera-vignette" />
          <div className="camera-label"><i /><span>{running ? "AI WATCHING" : "CAMERA ON"}</span></div>
          {showCallout && (
            <div className="detection-lock">
              <div className="detection-rings"><i /><i /><span><Icon name="spark" size={15} stroke={2.2} /></span></div>
              <div className="detection-callout">
                <span>SCRIBE · {mmss(last.elapsed)}</span>
                <strong>{last.objects[0] ? last.objects[0].charAt(0).toUpperCase() + last.objects[0].slice(1) : "Action"}</strong>
                <small>{last.action}{confidence != null ? ` · ${confidence}%` : ""}</small>
              </div>
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
      {(state === "error" || state === "mock") && (
        <div className="camera-error">
          <div><Icon name="camera" size={30} /></div>
          <h3>{state === "mock" ? "Mock mode: scripted demo running" : "Camera is off"}</h3>
          <p>{state === "mock" ? "No camera needed — the scribe plays a recorded run." : "Point it at your bench so LabScribe can see your hands and materials."}</p>
          <button onClick={lab.startCamera}>Start camera</button>
        </div>
      )}
    </div>
  );
}

function StepsDrawer({ sess, onClose }: { sess: Session; onClose: () => void }) {
  const steps = sess.procedure.steps;
  const done = sess.steps_state.filter((x) => x.status === "done").length;
  let lastSection = "";
  return (
    <section className="steps-panel card steps-drawer">
      <div className="panel-heading">
        <div><span>PROCEDURE</span><h2>{sess.procedure.title}</h2></div>
        <button onClick={onClose} aria-label="Close steps"><Icon name="close" size={14} /></button>
      </div>
      <div className="progress-label"><span>{done} of {steps.length} done</span><strong>{Math.round((100 * done) / Math.max(1, steps.length))}%</strong></div>
      <div className="progress-track"><i style={{ width: `${(100 * done) / Math.max(1, steps.length)}%` }} /></div>
      <div className="step-list">
        {steps.map((step, i) => {
          const st = sess.steps_state[i];
          const isDone = st?.status === "done";
          const isCur = st?.status === "current";
          const header = step.section && step.section !== lastSection ? step.section : null;
          if (step.section) lastSection = step.section;
          return (
            <div key={step.number}>
              {header && <div className="drawer-section">{header}</div>}
              <div className={`live-step ${isDone ? "done" : ""} ${isCur ? "current" : ""}`}>
                <div className="step-rail">
                  <span>{isDone ? <Icon name="check" size={14} stroke={2.5} /> : isCur ? <i className="pulse" /> : step.number}</span>
                  {i < steps.length - 1 && <b />}
                </div>
                <div className="step-copy">
                  <div className="step-kicker">
                    {isDone ? <><Icon name="clock" size={12} />{mmss(st.done_elapsed ?? 0)}</> : isCur ? "IN PROGRESS" : `STEP ${step.number}`}
                    {step.camera_checkable === false && <span className="kicker-paper">paper</span>}
                    {step.safety_critical && <span className="kicker-safety"><Icon name="shield" size={11} /></span>}
                  </div>
                  <strong>{step.instruction}</strong>
                  {isDone && <small>{st.method === "manual" ? "Marked done by you" : st.evidence}</small>}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function ScribeOverlay({ lab, running }: { lab: Lab; running: boolean }) {
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
    <section className="scribe-overlay">
      <header>
        <div>
          <span className={running ? "" : "idle"}><i /> LIVE TRANSCRIPT</span>
          <h2>Scribe</h2>
        </div>
      </header>
      <div className="scribe-scroll" ref={box}
        onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 30; }}>
        {!lab.lines.length && <p className="overlay-empty">{running ? "Watching… the first line appears within a few seconds." : "Start a session to see what LabScribe sees."}</p>}
        {lab.lines.map((l, i) => (
          <div className={`${i === lab.lines.length - 1 ? "latest" : ""} ${l.action.toLowerCase().startsWith("no visible action") ? "idle" : ""}`} key={`${l.time}-${i}`}>
            <time>{mmss(l.elapsed)}</time>
            <p>{l.action}{l.repeat > 1 && <em> ×{l.repeat}</em>}</p>
          </div>
        ))}
      </div>
      <footer>
        <span>{lab.scribeError ? `Frame failed: ${lab.scribeError.slice(0, 40)}` : lab.skipped ? `${lab.skipped} frames skipped` : "Auto-scroll on"}</span>
        <button onClick={exportTxt} disabled={!lab.lines.length}><Icon name="download" size={13} /> Export</button>
      </footer>
    </section>
  );
}

function CoachChat({ lab, sess, muted, toggleVoice }: { lab: Lab; sess: Session | null; muted: boolean; toggleVoice: () => void }) {
  const [open, setOpen] = useState(true);
  const [draft, setDraft] = useState("");
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { if (box.current) box.current.scrollTop = box.current.scrollHeight; }, [lab.chat, open, lab.asking]);
  const fbById = new Map((sess?.feedback ?? []).map((f) => [f.id, f]));
  const unread = lab.chat.filter((m) => m.kind === "mistake").length;

  if (!open) {
    return (
      <button className="chat-launcher" onClick={() => setOpen(true)}>
        <span><Icon name="spark" size={18} /></span>
        Ask LabScribe
        {unread > 0 && <i />}
      </button>
    );
  }
  const send = () => {
    if (!draft.trim() || !sess) return;
    lab.ask(draft);
    setDraft("");
  };
  return (
    <section className="coach-chat">
      <header>
        <div className="chat-avatar"><Icon name="spark" size={15} /></div>
        <div>
          <strong>LabScribe coach</strong>
          <span><i /> {sess && !sess.ended_at ? "Watching with you" : "Standby"}</span>
        </div>
        <button onClick={() => setOpen(false)} title="Minimize chat" aria-label="Minimize chat"><span /></button>
      </header>
      <div className="chat-messages" ref={box}>
        {!lab.chat.length && <div className="ai">Pick a procedure and press Start — I'll guide you step by step and you can ask me anything.</div>}
        {lab.chat.map((m) => {
          const fb = m.feedbackId ? fbById.get(m.feedbackId) : undefined;
          if (m.kind === "mistake" && fb) return <MistakeBubble key={m.id} f={fb} sid={sess!.session_id} onDismiss={lab.dismiss} />;
          return <div className={`${m.from} ${m.kind ?? ""}`} key={m.id}>{m.kind === "tip" && <b className="bubble-tag">Tip</b>}{m.text}</div>;
        })}
        {lab.asking && <div className="ai typing">Thinking…</div>}
      </div>
      <button type="button" className="chat-suggestion" onClick={toggleVoice} aria-pressed={!muted}>
        <Icon name={muted ? "volume" : "mic"} size={13} /> Coach voice is {muted || isMuted() ? "off — tap to turn on" : "on"}
      </button>
      <form onSubmit={(e) => { e.preventDefault(); send(); }}>
        <input value={draft} onChange={(e) => setDraft(e.target.value)} disabled={!sess}
          placeholder={sess ? "Ask about this step…" : "Start a session to ask questions"} aria-label="Ask LabScribe" />
        <button type="submit" aria-label="Send message" disabled={!sess || !draft.trim() || lab.asking}><Icon name="arrow" size={15} /></button>
      </form>
    </section>
  );
}

function MistakeBubble({ f, sid, onDismiss }: { f: FeedbackItem; sid: string; onDismiss: (id: string) => void }) {
  const [imgOk, setImgOk] = useState(true);
  return (
    <div className={`ai mistake-bubble ${f.dismissed ? "dismissed" : ""}`}>
      <div className="mb-head">
        <Icon name="alert" size={14} />
        <strong>{f.kind === "possible_skip" ? "Possible skipped step" : MISTAKE_LABEL[f.mistake_type] ?? "Mistake"}</strong>
        <time>{mmss(f.elapsed)}</time>
      </div>
      <p><b>What happened</b> {f.what_went_wrong}</p>
      <p className="fix"><b>How to fix</b> {f.how_to_fix}</p>
      <div className="mb-foot">
        {f.snapshot_id && imgOk && <img src={`/api/sessions/${sid}/snapshots/${f.snapshot_id}`} alt="Snapshot when it happened" onError={() => setImgOk(false)} />}
        {f.corrected && <em className="fixed-tag"><Icon name="check" size={12} /> fixed</em>}
        {!f.dismissed ? <button onClick={() => onDismiss(f.id)}><Icon name="check" size={13} /> Got it</button> : <span className="muted-note">Dismissed</span>}
      </div>
    </div>
  );
}
