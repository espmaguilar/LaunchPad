import { useCallback, useEffect, useRef, useState } from "react";
import { api, post, type Health, type ManualDraft, type Procedure, type ProcedureSummary, type Step } from "../api";
import { fmtWhen } from "../format";
import { Icon, PageTitle } from "../ui";

const MAX_MB = 10;
const COMMON_PPE = ["safety goggles", "gloves", "lab coat", "face mask"];

type View = "list" | "upload" | "processing" | "review";
interface Editing {
  procedure_id?: string;
  source_key: string;
  title: string;
  steps: Step[];
  meta?: Pick<ManualDraft, "filename" | "extraction" | "generated_by" | "text_chars" | "warnings" | "text_preview">;
}

const readAsDataURL = (blob: Blob) =>
  new Promise<string>((ok, fail) => {
    const r = new FileReader();
    r.onload = () => ok(r.result as string);
    r.onerror = () => fail(r.error);
    r.readAsDataURL(blob);
  });
const blankStep = (n: number): Step => ({ number: n, instruction: "", expected_action: "", visual_cues: [], safety_critical: false, required_ppe: [] });
const renumber = (steps: Step[]) => steps.map((s, i) => ({ ...s, number: i + 1 }));
const splitList = (v: string) => v.split(",").map((x) => x.trim()).filter(Boolean);

export default function Procedures({ health }: { health: Health | null }) {
  const [items, setItems] = useState<ProcedureSummary[] | null>(null);
  const [view, setView] = useState<View>("list");
  const [editing, setEditing] = useState<Editing | null>(null);
  const [filename, setFilename] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<Procedure | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const list = await api<ProcedureSummary[]>("/api/procedures");
      setItems(list);
      setView((v) => (v === "list" && list.length === 0 ? "upload" : v));
    } catch (e) {
      setItems([]);
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const extract = async (blob: Blob, name: string) => {
    setError(null);
    setSaved(null);
    if (blob.size > MAX_MB * 1024 * 1024) return setError(`That PDF is larger than ${MAX_MB} MB.`);
    setFilename(name);
    setView("processing");
    try {
      const d = await post<ManualDraft>("/api/procedures/upload", { filename: name, data: await readAsDataURL(blob) });
      setEditing({ source_key: d.source_key, title: d.title, steps: d.steps.length ? d.steps : [blankStep(1)],
        meta: { filename: d.filename, extraction: d.extraction, generated_by: d.generated_by, text_chars: d.text_chars,
          warnings: d.warnings, text_preview: d.text_preview } });
      setView("review");
    } catch (e) {
      setView("upload");
      setError(`Manual extraction failed: ${(e as Error).message}`);
    }
  };
  const onFile = (f?: File | null) => {
    if (!f) return;
    if (!/\.pdf$/i.test(f.name) && f.type !== "application/pdf") return setError("Please choose a PDF file.");
    extract(f, f.name);
  };
  const useSample = async () => {
    try {
      const res = await fetch("/api/procedures/sample");
      if (!res.ok) throw new Error("demo manual not found - run python demo/make_lab_manual.py");
      await extract(await res.blob(), "color_change_lab.pdf");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const openExisting = async (id: string) => {
    setError(null);
    setSaved(null);
    try {
      const p = await api<Procedure>(`/api/procedures/${encodeURIComponent(id)}`);
      setEditing({ procedure_id: p.procedure_id, source_key: p.source_key, title: p.title, steps: p.steps });
      setView("review");
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const remove = async (p: ProcedureSummary) => {
    if (!confirm(`Delete "${p.title}"? This can't be undone.`)) return;
    try {
      await api(`/api/procedures/${encodeURIComponent(p.procedure_id)}`, { method: "DELETE" });
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const approve = async () => {
    if (!editing) return;
    setBusy(true);
    setError(null);
    try {
      const p = await post<Procedure>("/api/procedures/approve", {
        procedure_id: editing.procedure_id, source_key: editing.source_key, title: editing.title, steps: editing.steps,
      });
      setSaved(p);
      setEditing(null);
      setView("list");
      await load();
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      setError(`Not saved: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const step = view === "upload" ? 1 : view === "processing" ? 2 : view === "review" ? 3 : 0;
  return (
    <div>
      <PageTitle
        title="Procedures"
        subtitle="Upload a lab manual — AI turns it into steps the camera can follow"
        action={view === "list" ? (
          <button className="primary" onClick={() => { setSaved(null); setError(null); setView("upload"); }}>
            <Icon name="plus" size={16} /> New procedure
          </button>
        ) : view !== "processing" && (items?.length ?? 0) > 0 ? (
          <button className="secondary" onClick={() => { setEditing(null); setView("list"); }}>← All procedures</button>
        ) : undefined}
      />
      {step > 0 && (
        <div className="stepper">
          {[["1", "Upload manual"], ["2", "AI processing"], ["3", "Review & approve"]].map(([n, l], i) => (
            <div key={n} className={`${step >= +n ? "active" : ""} ${step > +n ? "complete" : ""}`}>
              <span>{step > +n ? <Icon name="check" size={14} /> : n}</span>
              <b>{l}</b>
              {i < 2 && <i />}
            </div>
          ))}
        </div>
      )}
      {error && <div className="banner error">{error}</div>}
      {saved && (
        <div className="banner ok">
          Saved “{saved.title}” · {saved.step_count} steps ({saved.safety_step_count} safety-critical). It's ready for a lab session
          (camera tracking arrives in the next phase).
        </div>
      )}

      {view === "list" && (
        <div className="table-card">
          <table>
            <thead><tr><th>PROCEDURE</th><th>STEPS</th><th>SAFETY STEPS</th><th>APPROVED</th><th></th></tr></thead>
            <tbody>
              {(items ?? []).map((p) => (
                <tr key={p.procedure_id} onClick={() => openExisting(p.procedure_id)} tabIndex={0}
                  onKeyDown={(e) => e.key === "Enter" && openExisting(p.procedure_id)}>
                  <td><b>{p.title}</b></td>
                  <td>{p.step_count}</td>
                  <td>{p.safety_step_count ? <span className="safety-tag"><Icon name="shield" size={11} /> {p.safety_step_count}</span> : "—"}</td>
                  <td>{p.approved_at ? fmtWhen(p.approved_at) : "—"}</td>
                  <td className="row-actions">
                    <button title="Edit" aria-label={`Edit ${p.title}`} onClick={(e) => { e.stopPropagation(); openExisting(p.procedure_id); }}><Icon name="edit" size={15} /></button>
                    <button title="Delete" aria-label={`Delete ${p.title}`} onClick={(e) => { e.stopPropagation(); remove(p); }}><Icon name="trash" size={15} /></button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {items && !items.length && <div className="empty-state"><h3>No procedures yet</h3><p>Upload a lab manual to get started.</p></div>}
          {!items && <div className="empty-state"><p>Loading…</p></div>}
        </div>
      )}

      {view === "upload" && (
        <div className={`upload-card ${dragOver ? "drag" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); onFile(e.dataTransfer.files[0]); }}>
          <div className="upload-icon"><Icon name="upload" size={26} /></div>
          <h2>Upload a lab manual</h2>
          <p>Drop your PDF here, or click to browse</p>
          <span>PDF up to {MAX_MB} MB · stored in Amazon S3</span>
          <input ref={fileRef} type="file" aria-label="Upload lab manual" accept="application/pdf,.pdf"
            onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = ""; }} />
          <button className="primary" onClick={() => fileRef.current?.click()}>Choose PDF file</button>
          <div className="divider"><span>OR</span></div>
          <div className="sample-buttons">
            <button className="secondary" onClick={useSample}>
              <Icon name="flask" size={16} /> Use demo manual: Color-Change Reaction
            </button>
          </div>
          <small>{health?.mock_mode ? "Mock mode: steps are read from the numbered list (no Bedrock)" : "Takes about 10 seconds"}</small>
        </div>
      )}

      {view === "processing" && <Processing filename={filename} />}

      {view === "review" && editing && (
        <Review editing={editing} setEditing={setEditing} onApprove={approve} busy={busy}
          onCancel={() => { setEditing(null); setView((items?.length ?? 0) ? "list" : "upload"); }} />
      )}
    </div>
  );
}

function Processing({ filename }: { filename: string }) {
  const [t, setT] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setT((x) => x + 1), 1000);
    return () => window.clearInterval(id);
  }, []);
  // The request is a single call, so these stages are honest estimates by elapsed time.
  const stage = t < 2 ? 0 : t < 5 ? 1 : 2;
  const s = (i: number) => (stage > i ? "done" : stage === i ? "working" : "");
  return (
    <div className="processing-card">
      <div className="processing-orb"><div>✦</div></div>
      <h2>Reading your lab manual</h2>
      <p>Turning the procedure into steps, expected actions and safety checks. {t}s elapsed.</p>
      <div className="processing-progress"><i /></div>
      <span>Usually about 10 seconds</span>
      <div className="stages">
        <div className={s(0)}>
          <span>{stage > 0 ? <Icon name="check" size={14} /> : "✦"}</span>
          <div><b>Storing in Amazon S3</b><small>{filename}</small></div>
        </div>
        <Icon name="arrow" />
        <div className={s(1)}>
          <span>{stage > 1 ? <Icon name="check" size={14} /> : "✦"}</span>
          <div><b>Reading with Amazon Textract</b><small>pypdf fallback if unavailable</small></div>
        </div>
        <Icon name="arrow" />
        <div className={s(2)}>
          <span>✦</span>
          <div><b>Structuring steps with Bedrock</b><small>Expected actions, visual cues, PPE…</small></div>
        </div>
      </div>
    </div>
  );
}

function Review({ editing, setEditing, onApprove, onCancel, busy }: {
  editing: Editing; setEditing: (e: Editing) => void; onApprove: () => void; onCancel: () => void; busy: boolean;
}) {
  const steps = editing.steps;
  const set = (steps: Step[]) => setEditing({ ...editing, steps: renumber(steps) });
  const patch = (i: number, p: Partial<Step>) => set(steps.map((s, j) => (j === i ? { ...s, ...p } : s)));
  const move = (i: number, d: number) => {
    const j = i + d;
    if (j < 0 || j >= steps.length) return;
    const next = [...steps];
    [next[i], next[j]] = [next[j], next[i]];
    set(next);
  };
  const m = editing.meta;
  const needsCues = steps.some((s) => !s.expected_action.trim());

  return (
    <div className="review">
      <div className="info-banner">
        <span>i</span>
        <div>
          <b>Review before saving</b>
          <p>AI can misread manuals — check each step's expected action: it's what the camera will look for.</p>
        </div>
      </div>
      {m?.warnings?.length ? <ul className="warn-list">{m.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul> : null}
      <div className="review-head">
        <div className="title-edit">
          <label>Procedure name
            <input value={editing.title} onChange={(e) => setEditing({ ...editing, title: e.target.value })} aria-label="Procedure name" />
          </label>
          <p>
            {steps.length} steps · {steps.filter((s) => s.safety_critical).length} safety-critical
            {m ? ` · ${m.filename} · ${m.text_chars.toLocaleString()} characters via ${m.extraction}${m.generated_by ? ` · structured by ${m.generated_by}` : ""}` : " · editing saved procedure"}
          </p>
        </div>
      </div>

      <ol className="step-list">
        {steps.map((s, i) => (
          <li key={i} className={`step-edit ${s.safety_critical ? "safety" : ""}`}>
            <div className="step-edit-head">
              <span className="step-num">STEP {s.number}</span>
              <button type="button" className={`safety-toggle ${s.safety_critical ? "on" : ""}`}
                onClick={() => patch(i, { safety_critical: !s.safety_critical })} aria-pressed={s.safety_critical}
                title="Safety-critical steps check the required PPE before the student can continue">
                <Icon name="shield" size={12} /> {s.safety_critical ? "Safety-critical" : "Mark safety-critical"}
              </button>
              <div className="step-tools">
                <button onClick={() => move(i, -1)} disabled={i === 0} title="Move up" aria-label={`Move step ${s.number} up`}><Icon name="up" size={15} /></button>
                <button onClick={() => move(i, 1)} disabled={i === steps.length - 1} title="Move down" aria-label={`Move step ${s.number} down`}><Icon name="down" size={15} /></button>
                <button onClick={() => set(steps.filter((_, j) => j !== i))} title="Delete step" aria-label={`Delete step ${s.number}`}><Icon name="trash" size={15} /></button>
              </div>
            </div>
            <label className="field">Instruction
              <textarea rows={2} value={s.instruction} onChange={(e) => patch(i, { instruction: e.target.value })}
                placeholder="What the student does, as the manual says it" />
            </label>
            <div className="field-grid">
              <label className="field">What the camera should see
                <input value={s.expected_action} onChange={(e) => patch(i, { expected_action: e.target.value })}
                  placeholder="e.g. blue food coloring dripped into the cup" className={!s.expected_action.trim() ? "needs" : ""} />
              </label>
              <label className="field">Visual cues <small>comma-separated</small>
                <CommaInput value={s.visual_cues} onChange={(v) => patch(i, { visual_cues: v })} placeholder="clear cup, blue liquid" />
              </label>
            </div>
            <div className="ppe-row">
              <span>Required PPE</span>
              {COMMON_PPE.map((p) => {
                const on = s.required_ppe.includes(p);
                return (
                  <button key={p} type="button" className={`ppe-chip ${on ? "on" : ""}`} aria-pressed={on}
                    onClick={() => patch(i, { required_ppe: on ? s.required_ppe.filter((x) => x !== p) : [...s.required_ppe, p] })}>
                    {on && <Icon name="check" size={11} />} {p}
                  </button>
                );
              })}
              {s.required_ppe.filter((p) => !COMMON_PPE.includes(p)).map((p) => (
                <button key={p} type="button" className="ppe-chip on" onClick={() => patch(i, { required_ppe: s.required_ppe.filter((x) => x !== p) })}>
                  <Icon name="check" size={11} /> {p}
                </button>
              ))}
            </div>
          </li>
        ))}
      </ol>
      <button className="add-step" onClick={() => set([...steps, blankStep(steps.length + 1)])}>
        <Icon name="plus" size={15} /> Add step
      </button>

      <div className="publish-row">
        <div className="muted small">{needsCues ? "Some steps have no expected action yet — the camera needs it to know when a step is done." : "Every step has an expected action."}</div>
        <div>
          <button className="secondary" onClick={onCancel}>Cancel</button>
          <button className="primary" onClick={onApprove} disabled={busy || !steps.some((s) => s.instruction.trim())}>
            {busy ? "Saving…" : "Approve & save"} <Icon name="arrow" size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}

/** Comma-separated list input that keeps the user's text while typing. */
function CommaInput({ value, onChange, placeholder }: { value: string[]; onChange: (v: string[]) => void; placeholder: string }) {
  const [text, setText] = useState(value.join(", "));
  useEffect(() => {
    if (splitList(text).join("|") !== value.join("|")) setText(value.join(", "));
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps
  return <input value={text} placeholder={placeholder} onChange={(e) => { setText(e.target.value); onChange(splitList(e.target.value)); }} />;
}
