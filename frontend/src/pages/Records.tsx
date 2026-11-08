import { useEffect, useState } from "react";
import { api, type LabSession } from "../api";
import { fmtWhen, mmss } from "../format";
import { Icon, Lightbox, Notice, PageHeading, type Slide } from "../ui";

const MISTAKE_LABEL: Record<string, string> = {
  wrong_order: "Out-of-order action", wrong_material: "Wrong material", wrong_technique: "Technique",
  incomplete: "Step not finished", safety: "Safety",
};

export const durationOf = (s: LabSession) =>
  ((s.ended_at ? new Date(s.ended_at).getTime() : Date.now()) - new Date(s.started_at).getTime()) / 1000;

export default function Records({ selected, setSelected }: { selected: string | null; setSelected: (id: string | null) => void }) {
  const [list, setList] = useState<LabSession[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viewer, setViewer] = useState<number | null>(null);
  useEffect(() => {
    api<LabSession[]>("/api/sessions").then(setList).catch((e) => setError(e.message));
  }, []);
  const rec = list?.find((s) => s.session_id === selected) ?? list?.find((s) => s.ended_at) ?? list?.[0] ?? null;

  if (error) return <div className="page-container"><Notice tone="error">{error}</Notice></div>;
  if (!list) return <div className="page-container"><p className="panel-empty">Loading…</p></div>;
  if (!rec) {
    return (
      <div className="page-container record-page">
        <PageHeading kicker="LAB RECORDS" title="No sessions yet" sub="Run a lab session and its record appears here." />
      </div>
    );
  }

  const steps = rec.procedure.steps;
  const doneCount = rec.steps_state.filter((x) => x.status === "done").length;
  const mistakes = rec.feedback.filter((f) => f.kind === "mistake");
  const corrected = mistakes.filter((f) => f.corrected).length;
  const skips = rec.feedback.filter((f) => f.kind === "possible_skip");
  const result = rec.complete ? `Completed · ${mistakes.length ? `${corrected} of ${mistakes.length} mistake${mistakes.length === 1 ? "" : "s"} corrected` : "no mistakes"}`
    : `${doneCount} of ${steps.length} steps${rec.ended_at ? "" : " · in progress"}`;

  // every snapshot in this record, in timeline order (steps, then feedback)
  const snap = (id: string) => `/api/sessions/${rec.session_id}/snapshots/${id}`;
  const slides: (Slide & { key: string })[] = [
    ...steps.flatMap((step, i) => {
      const st = rec.steps_state[i];
      return st?.status === "done" && st.snapshot_id
        ? [{ key: `s${step.number}`, src: snap(st.snapshot_id), title: `Step ${step.number} · ${mmss(st.done_elapsed ?? 0)}`,
             caption: `${step.instruction} — ${st.method === "manual" ? "marked done by the student" : `seen: ${st.evidence}`}` }]
        : [];
    }),
    ...rec.feedback.filter((f) => f.snapshot_id).map((f) => ({ key: `f${f.id}`, src: snap(f.snapshot_id!),
      title: `${f.kind === "possible_skip" ? "Possible skipped step" : MISTAKE_LABEL[f.mistake_type] || "Mistake"} · step ${f.step} · ${mmss(f.elapsed)}`,
      caption: f.what_went_wrong })),
  ];
  const openSlide = (key: string) => setViewer(Math.max(0, slides.findIndex((x) => x.key === key)));

  return (
    <div className="page-container record-page">
      {viewer != null && <Lightbox slides={slides} index={viewer} onClose={() => setViewer(null)} />}
      {list.length > 1 && (
        <div className="record-picker">
          <span>Session</span>
          <select value={rec.session_id} onChange={(e) => setSelected(e.target.value)} aria-label="Choose session">
            {list.map((s) => <option key={s.session_id} value={s.session_id}>{s.procedure.title} · {fmtWhen(s.started_at)}{s.ended_at ? "" : " (live)"}</option>)}
          </select>
        </div>
      )}
      <div className="record-hero card">
        <div className="record-icon"><Icon name="records" size={25} /></div>
        <div className="record-title">
          <span>LAB RECORD · {rec.session_id.toUpperCase()}</span>
          <h1>{rec.procedure.title}</h1>
          <p>{new Date(rec.started_at).toLocaleString([], { dateStyle: "long", timeStyle: "short" })}</p>
        </div>
        <div className="record-result">
          <span className={rec.complete ? "" : "partial"}><Icon name={rec.complete ? "check" : "clock"} size={15} /> {result}</span>
          <div><small>DURATION</small><strong>{mmss(durationOf(rec))}</strong></div>
        </div>
        <div className="record-actions">
          <button onClick={() => window.print()}><Icon name="download" size={16} /> Download PDF</button>
        </div>
      </div>
      <div className="record-grid">
        <main className="card report-section">
          <div className="section-title">
            <div><span>SESSION EVIDENCE</span><h2>Experiment timeline</h2></div>
            <small>{doneCount} of {steps.length} steps complete</small>
          </div>
          <div className="report-timeline">
            {steps.map((step, i) => {
              const st = rec.steps_state[i];
              const done = st?.status === "done";
              return (
                <div key={step.number} className={done ? "" : "not-done"}>
                  <div className="timeline-dot">
                    <span>{done ? <Icon name="check" size={13} /> : step.number}</span>
                    {i < steps.length - 1 && <i />}
                  </div>
                  <time>{done ? mmss(st.done_elapsed ?? 0) : "—"}</time>
                  <div className="timeline-text">
                    <strong>{step.number}. {step.instruction}</strong>
                    <p>{done ? (st.method === "manual" ? "Marked done by the student" : `Seen: ${st.evidence}`) : "Not completed"}</p>
                  </div>
                  <div className="evidence-shot">
                    {done && st.snapshot_id ? (
                      <button className="shot-btn" onClick={() => openSlide(`s${step.number}`)} aria-label={`View step ${step.number} snapshot`}>
                        <img src={snap(st.snapshot_id)} alt={`Step ${step.number} evidence`} />
                        <span>View</span>
                      </button>
                    ) : <div className="shot-empty"><Icon name="camera" size={14} /></div>}
                  </div>
                </div>
              );
            })}
          </div>
        </main>
        <aside className="record-side">
          <section className="card deviations">
            <div className="section-title">
              <div><span>REVIEW</span><h2>Deviations</h2></div>
              <b>{rec.feedback.length}</b>
            </div>
            {rec.feedback.map((f) => (
              <div className="deviation-row" key={f.id}>
                {f.snapshot_id ? (
                  <button className="dev-shot" onClick={() => openSlide(`f${f.id}`)} aria-label="View snapshot"><img src={snap(f.snapshot_id)} alt="" /></button>
                ) : <span><Icon name="alert" size={16} /></span>}
                <div>
                  <strong>{f.kind === "possible_skip" ? "Possible skipped step" : MISTAKE_LABEL[f.mistake_type] || "Mistake"}</strong>
                  <p>{f.what_went_wrong}</p>
                  <small>{mmss(f.elapsed)} · step {f.step} · {f.corrected ? "corrected" : f.corrected === false ? "not corrected" : "open"}</small>
                </div>
              </div>
            ))}
            {!skips.length && (
              <div className="no-deviation"><Icon name="shield" size={16} /> No skipped steps{rec.feedback.length ? "" : " or mistakes"}</div>
            )}
          </section>
          <section className="card tips">
            <div className="section-title"><div><span>PERSONALIZED</span><h2>Mistakes & tips</h2></div></div>
            {mistakes.length ? (
              <div className="corrected"><Icon name="check" size={14} /> {corrected} of {mistakes.length} mistake{mistakes.length === 1 ? "" : "s"} corrected during the session</div>
            ) : <div className="corrected"><Icon name="check" size={14} /> No mistakes flagged</div>}
            {mistakes.length > 0 && (
              <ul>
                {mistakes.map((f, i) => (
                  <li key={f.id}><span>{i + 1}</span><p><b>{f.what_went_wrong}</b> {f.how_to_fix}</p></li>
                ))}
              </ul>
            )}
            <p className="tips-note"><Icon name="spark" size={13} /> Personalized tips for next time are written by Amazon Bedrock when the lab record is generated — coming in the next phase.</p>
          </section>
        </aside>
      </div>
    </div>
  );
}
