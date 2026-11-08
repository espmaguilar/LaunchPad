import { useEffect, useState } from "react";
import { api, type LabSession } from "../api";
import { mmss } from "../format";
import { Icon, Notice, PageHeading, type IconName } from "../ui";
import { durationOf } from "./Records";

const COLORS = ["#d8efeb", "#f9e4d5", "#e4e3f7", "#f6dddd", "#e5edd9", "#dce9f5"];

export default function Instructor({ openRecord }: { openRecord: (id: string) => void }) {
  const [list, setList] = useState<LabSession[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const load = () => api<LabSession[]>("/api/sessions").then(setList).catch((e) => setError(e.message));
    load();
    const t = window.setInterval(load, 5000);
    return () => window.clearInterval(t);
  }, []);
  if (error) return <div className="page-container"><Notice tone="error">{error}</Notice></div>;
  const all = list ?? [];
  const today = all.filter((s) => new Date(s.started_at).toDateString() === new Date().toDateString());
  const live = all.filter((s) => s.active);
  const completed = all.filter((s) => s.complete && s.completed_at);
  const avg = completed.length
    ? completed.reduce((t, s) => t + (new Date(s.completed_at!).getTime() - new Date(s.started_at).getTime()) / 1000, 0) / completed.length : null;
  const mistakes = all.flatMap((s) => s.feedback.filter((f) => f.kind === "mistake").map((f) => ({ ...f, s })));
  const safety = mistakes.filter((m) => m.mistake_type === "safety").length;
  const corrected = mistakes.filter((m) => m.corrected).length;

  // most common mistakes by step (across sessions, grouped by step instruction)
  const byStep = new Map<string, number>();
  for (const m of mistakes) {
    const step = m.s.procedure.steps.find((x) => x.number === m.step);
    const label = step ? `${step.number}. ${step.instruction}` : `Step ${m.step}`;
    byStep.set(label, (byStep.get(label) ?? 0) + 1);
  }
  const bars = [...byStep.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6);
  const maxBar = Math.max(1, ...bars.map((b) => b[1]));
  const top = bars[0];

  const kpis: [IconName, string, string, string][] = [
    ["live", "Sessions today", String(today.length), `${today.filter((s) => s.complete).length} complete · ${live.length} live`],
    ["clock", "Avg. completion", avg == null ? "—" : mmss(avg), `${completed.length} completed session${completed.length === 1 ? "" : "s"}`],
    ["shield", "Safety flags", String(safety), safety ? "Review in lab records" : "None so far"],
    ["check", "Mistakes corrected", mistakes.length ? `${Math.round((100 * corrected) / mistakes.length)}%` : "—", `${corrected} of ${mistakes.length} corrected`],
  ];

  return (
    <div className="page-container instructor-page">
      <PageHeading kicker="LIVE CLASSROOM" title="Instructor overview"
        sub={`${live.length} session${live.length === 1 ? "" : "s"} running now · ${all.length} since the server started`}
        right={<div className="updated"><i /> Live · refreshes every 5 s</div>} />
      <div className="kpi-grid">
        {kpis.map((k, i) => (
          <div className="kpi card" key={k[1]}>
            <div className={`kpi-icon k${i}`}><Icon name={k[0]} /></div>
            <div><span>{k[1]}</span><strong>{k[2]}</strong><small>{k[3]}</small></div>
          </div>
        ))}
      </div>
      <div className="instructor-grid">
        <section className="card class-card">
          <div className="section-title"><div><span>SESSIONS</span><h2>Student sessions</h2></div><small>{all.length}</small></div>
          {!all.length && <p className="panel-empty">No sessions yet. They appear here as soon as one starts.</p>}
          <div className="student-grid">
            {all.slice(0, 12).map((s, i) => {
              const total = s.procedure.steps.length;
              const done = s.steps_state.filter((x) => x.status === "done").length;
              const m = s.feedback.filter((f) => f.kind === "mistake").length;
              const open = s.feedback.some((f) => !f.dismissed && f.corrected !== true);
              const status = s.complete ? "Completed" : s.active ? (open ? "Needs attention" : "On track") : s.ended_at ? "Ended early" : "Idle";
              return (
                <div className="student-card" key={s.session_id}>
                  <div className="student-top">
                    <span className="student-avatar" style={{ background: COLORS[i % COLORS.length] }}>{s.session_id.slice(0, 2).toUpperCase()}</span>
                    <div>
                      <strong>{s.procedure.title}</strong>
                      <small><i className={status === "Needs attention" || status === "Idle" ? "orange" : status === "Ended early" ? "red" : ""} /> {status} · {mmss(durationOf(s))}</small>
                    </div>
                  </div>
                  <div className="student-progress">
                    <div><span>{s.complete ? "All steps" : `Step ${Math.min(s.current + 1, total)} of ${total}`}</span><b>{Math.round((100 * done) / Math.max(1, total))}%</b></div>
                    <div><i style={{ width: `${(100 * done) / Math.max(1, total)}%` }} /></div>
                  </div>
                  <div className="student-bottom">
                    <span>{m} {m === 1 ? "mistake" : "mistakes"}</span>
                    <button onClick={() => openRecord(s.session_id)}>Open record <Icon name="chevron" size={12} /></button>
                  </div>
                </div>
              );
            })}
          </div>
        </section>
        <section className="card chart-card">
          <div className="section-title"><div><span>CLASS INSIGHTS</span><h2>Most common mistakes by step</h2></div></div>
          {bars.length ? (
            <div className="bar-chart">
              {bars.map(([label, n], i) => (
                <div key={label}>
                  <span>{i + 1}</span>
                  <p title={label}>{label}</p>
                  <div><i style={{ width: `${(100 * n) / maxBar}%` }} /></div>
                  <b>{n}</b>
                </div>
              ))}
            </div>
          ) : <p className="panel-empty">No mistakes recorded yet.</p>}
          {top && (
            <div className="chart-note">
              <Icon name="spark" size={15} />
              <p><strong>Teaching insight</strong> Step {top[0].split(".")[0]} (“{top[0].split(". ").slice(1).join(". ").replace(/\.$/, "")}”) accounts for {Math.round((100 * top[1]) / mistakes.length)}% of corrections. Consider demonstrating it before the next run.</p>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
