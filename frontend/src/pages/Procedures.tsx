import { useCallback, useEffect, useRef, useState } from "react";
import { api, post, type Health, type ManualDraft, type Procedure, type ProcedureSummary, type Step } from "../api";
import { fmtWhen } from "../format";
import { Icon, Notice, PageHeading } from "../ui";

const MAX_MB = 10;
const COMMON_PPE = ["safety goggles", "gloves", "lab coat", "face mask"];
type View = "upload" | "processing" | "review";
interface Editing {
  procedure_id?: string;
  source_key: string;
  title: string;
  steps: Step[];
  meta?: Pick<ManualDraft, "filename" | "extraction" | "generated_by" | "text_chars" | "warnings">;
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

export default function Procedures({ health, startSession }: { health: Health | null; startSession: (procedureId: string) => Promise<void> }) {
  const [items, setItems] = useState<ProcedureSummary[] | null>(null);
  const [view, setView] = useState<View>("upload");
  const [editing, setEditing] = useState<Editing | null>(null);
  const [filename, setFilename] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try { setItems(await api<ProcedureSummary[]>("/api/procedures")); } catch (e) { setItems([]); setError((e as Error).message); }
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
        meta: { filename: d.filename, extraction: d.extraction, generated_by: d.generated_by, text_chars: d.text_chars, warnings: d.warnings } });
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
    } catch (e) { setError((e as Error).message); }
  };
  const openExisting = async (id: string) => {
    setError(null);
    setSaved(null);
    try {
      const p = await api<Procedure>(`/api/procedures/${encodeURIComponent(id)}`);
      setEditing({ procedure_id: p.procedure_id, source_key: p.source_key, title: p.title, steps: p.steps });
      setView("review");
    } catch (e) { setError((e as Error).message); }
  };
  const remove = async (p: ProcedureSummary) => {
    if (!confirm(`Delete "${p.title}"? This can't be undone.`)) return;
    try { await api(`/api/procedures/${encodeURIComponent(p.procedure_id)}`, { method: "DELETE" }); load(); } catch (e) { setError((e as Error).message); }
  };
  const approve = async (andStart: boolean) => {
    if (!editing) return;
    setBusy(true);
    setError(null);
    try {
      const p = await post<Procedure>("/api/procedures/approve", {
        procedure_id: editing.procedure_id, source_key: editing.source_key, title: editing.title, steps: editing.steps });
      await load();
      if (andStart) return await startSession(p.procedure_id);
      setSaved(`Saved “${p.title}” · ${p.step_count} steps (${p.safety_step_count} safety-critical).`);
      setEditing(null);
      setView("upload");
    } catch (e) {
      setError(`Not saved: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const stepNo = view === "upload" ? 1 : view === "processing" ? 2 : 3;
  return (
    <div className="page-container setup-page">
      <PageHeading kicker="PROCEDURE BUILDER" title="Prepare a new lab session" sub="Turn any lab manual into a guided, camera-ready procedure."
        right={view === "review" ? <div className="draft-pill">{editing?.procedure_id ? "Editing saved procedure" : "Draft — not saved yet"}</div> : undefined} />
      <div className="setup-stepper">
        {["Upload manual", "AI processing", "Review & publish"].map((label, i) => (
          <button key={label} type="button" className={`${stepNo === i + 1 ? "active" : ""} ${stepNo > i + 1 ? "done" : ""}`} tabIndex={-1}>
            <span>{stepNo > i + 1 ? <Icon name="check" size={14} /> : i + 1}</span>
            <strong>{label}</strong>
            {i < 2 && <i />}
          </button>
        ))}
      </div>
      {error && <Notice tone="error" onClose={() => setError(null)}>{error}</Notice>}
      {saved && <Notice tone="ok" onClose={() => setSaved(null)}>{saved}</Notice>}

      {view === "upload" && (
        <>
          <section className={`upload-card card ${dragOver ? "drag" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }} onDragLeave={() => setDragOver(false)}
            onDrop={(e) => { e.preventDefault(); setDragOver(false); onFile(e.dataTransfer.files[0]); }}>
            <div className="upload-icon"><Icon name="upload" size={28} /></div>
            <h2>Upload your lab manual</h2>
            <p>Drag and drop a PDF here, or choose a file from your computer.</p>
            <input ref={fileRef} type="file" hidden accept="application/pdf,.pdf" onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = ""; }} />
            <button className="primary" onClick={() => fileRef.current?.click()}>Choose PDF</button>
            <div className="divider"><span>or</span></div>
            <button className="sample-button" onClick={useSample}>
              <Icon name="flask" /> Use sample: Color-Change Reaction <Icon name="arrow" />
            </button>
            <small>{health?.mock_mode ? "Mock mode: steps are read from the numbered list (no Bedrock)." : "Ready in about 10 seconds — no training needed."}</small>
          </section>
          {(items?.length ?? 0) > 0 && (
            <section className="card saved-list">
              <div className="section-title"><div><span>LIBRARY</span><h2>Saved procedures</h2></div><small>{items!.length}</small></div>
              {items!.map((p) => (
                <div className="saved-row" key={p.procedure_id}>
                  <div className="saved-icon"><Icon name="file" size={16} /></div>
                  <div className="saved-copy">
                    <strong>{p.title}</strong>
                    <small>{p.step_count} steps · {p.safety_step_count} safety-critical · approved {p.approved_at ? fmtWhen(p.approved_at) : "—"}</small>
                  </div>
                  <button className="primary small" onClick={() => startSession(p.procedure_id)}><Icon name="play" size={12} /> Start session</button>
                  <button className="icon-btn" title="Edit" aria-label={`Edit ${p.title}`} onClick={() => openExisting(p.procedure_id)}><Icon name="edit" size={16} /></button>
                  <button className="icon-btn" title="Delete" aria-label={`Delete ${p.title}`} onClick={() => remove(p)}><Icon name="trash" size={16} /></button>
                </div>
              ))}
            </section>
          )}
        </>
      )}

      {view === "processing" && <Processing filename={filename} />}
      {view === "review" && editing && (
        <Review editing={editing} setEditing={setEditing} busy={busy} onApprove={approve}
          onCancel={() => { setEditing(null); setView("upload"); }} />
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
  const stage = t < 2 ? 0 : t < 5 ? 1 : 2; // one request: stages are honest estimates by elapsed time
  const row = (i: number, title: string, sub: string) => (
    <div className={stage > i ? "complete" : stage === i ? "active" : "pending"}>
      <span>{stage > i ? <Icon name="check" size={15} /> : <i />}</span>
      <div><strong>{title}</strong><small>{sub}</small></div>
    </div>
  );
  return (
    <section className="processing-card card">
      <div className="processing-orbit">
        <div><Icon name="spark" size={28} /></div>
        <i /><i /><i />
      </div>
      <h2>Building your procedure</h2>
      <p>LabScribe is finding the steps, materials, and safety checks. {t}s</p>
      <div className="processing-list">
        {row(0, "Stored securely in Amazon S3", filename)}
        {row(1, "Reading with Amazon Textract", "pypdf fallback if unavailable")}
        {row(2, "Structuring steps with Amazon Bedrock", "Identifying camera cues and safety requirements…")}
      </div>
    </section>
  );
}

function Review({ editing, setEditing, busy, onApprove, onCancel }: {
  editing: Editing; setEditing: (e: Editing) => void; busy: boolean; onApprove: (andStart: boolean) => void; onCancel: () => void;
}) {
  const [open, setOpen] = useState<number | null>(null);
  const [editTitle, setEditTitle] = useState(false);
  const steps = editing.steps;
  const set = (s: Step[]) => setEditing({ ...editing, steps: renumber(s) });
  const patch = (i: number, p: Partial<Step>) => set(steps.map((s, j) => (j === i ? { ...s, ...p } : s)));
  const move = (i: number, d: number) => {
    const j = i + d;
    if (j < 0 || j >= steps.length) return;
    const next = [...steps];
    [next[i], next[j]] = [next[j], next[i]];
    set(next);
    setOpen((o) => (o === i ? j : o === j ? i : o));
  };
  const safety = steps.filter((s) => s.safety_critical).length;
  const visual = steps.filter((s) => s.expected_action.trim()).length;
  const m = editing.meta;

  return (
    <div className="review-layout">
      <main>
        <div className="info-banner">
          <Icon name="spark" size={18} />
          <div>
            <strong>Give the AI a quick review</strong>
            <span>AI can misread documents — check each step’s camera cue before publishing.</span>
          </div>
        </div>
        {m?.warnings?.length ? <Notice tone="info">{m.warnings.join(" ")}</Notice> : null}
        <div className="review-header">
          <div>
            {editTitle ? (
              <input className="title-input" value={editing.title} autoFocus onBlur={() => setEditTitle(false)}
                onKeyDown={(e) => e.key === "Enter" && setEditTitle(false)} onChange={(e) => setEditing({ ...editing, title: e.target.value })} aria-label="Procedure name" />
            ) : <h2>{editing.title || "Untitled procedure"}</h2>}
            <p>{steps.length} steps{m ? ` · ${m.filename} · ${m.text_chars.toLocaleString()} chars via ${m.extraction}${m.generated_by ? ` · ${m.generated_by}` : ""}` : ""}</p>
          </div>
          <button onClick={() => setEditTitle(true)}><Icon name="edit" size={15} /> Edit name</button>
        </div>
        <div className="review-list">
          {steps.map((item, index) => (
            <div className={`review-step card ${open === index ? "editing" : ""}`} key={index}>
              <div className="drag reorder">
                <button onClick={() => move(index, -1)} disabled={index === 0} aria-label={`Move step ${index + 1} up`} title="Move up"><Icon name="chevron" size={13} /></button>
                <button onClick={() => move(index, 1)} disabled={index === steps.length - 1} aria-label={`Move step ${index + 1} down`} title="Move down"><Icon name="chevron" size={13} /></button>
              </div>
              <span className="review-num">{index + 1}</span>
              <div className="review-copy">
                {open === index ? (
                  <StepEditor step={item} onChange={(p) => patch(index, p)} />
                ) : (
                  <>
                    <div className="review-title">
                      <h3>{item.instruction || <em className="placeholder">New step — click edit</em>}</h3>
                      {item.safety_critical && <span className="critical"><Icon name="shield" size={12} /> Safety-critical</span>}
                    </div>
                    <p className={item.expected_action ? "" : "missing"}><b>What the camera looks for</b>{item.expected_action || "Not set — the camera needs this to know when the step is done"}</p>
                    <div className="chips">
                      {item.visual_cues.map((c) => <span key={c}>{c}</span>)}
                      {item.required_ppe.map((p) => <span className="ppe" key={p}><Icon name="shield" size={11} /> PPE: {p}</span>)}
                    </div>
                  </>
                )}
              </div>
              <div className="row-actions">
                <button title={open === index ? "Done editing" : "Edit"} aria-label={open === index ? "Done editing" : `Edit step ${index + 1}`}
                  onClick={() => setOpen(open === index ? null : index)}><Icon name={open === index ? "check" : "edit"} size={16} /></button>
                <button title="Delete" aria-label={`Delete step ${index + 1}`} onClick={() => { set(steps.filter((_, i) => i !== index)); setOpen(null); }}><Icon name="trash" size={16} /></button>
              </div>
            </div>
          ))}
        </div>
        <button className="add-step" onClick={() => { set([...steps, blankStep(steps.length + 1)]); setOpen(steps.length); }}>
          <Icon name="plus" size={16} /> Add step
        </button>
      </main>
      <aside className="publish-card card">
        <span>READY TO PUBLISH</span>
        <h3>Looks good?</h3>
        <p>Students will be guided through all {steps.length} steps with real-time camera confirmation.</p>
        <div>
          <Icon name="camera" />
          <span><strong>{visual} visual check{visual === 1 ? "" : "s"}</strong><small>{visual < steps.length ? `${steps.length - visual} step(s) still need a camera cue` : "Camera-detectable cues"}</small></span>
        </div>
        <div>
          <Icon name="shield" />
          <span><strong>{safety} safety checkpoint{safety === 1 ? "" : "s"}</strong><small>PPE confirmed before continuing</small></span>
        </div>
        <button className="primary" onClick={() => onApprove(true)} disabled={busy || !steps.some((s) => s.instruction.trim())}>
          {busy ? "Saving…" : "Approve & start session"} <Icon name="arrow" />
        </button>
        <button className="secondary wide" onClick={() => onApprove(false)} disabled={busy || !steps.some((s) => s.instruction.trim())}>Approve & save only</button>
        <button className="text-button" onClick={onCancel}>Cancel</button>
      </aside>
    </div>
  );
}

function StepEditor({ step, onChange }: { step: Step; onChange: (p: Partial<Step>) => void }) {
  const [cues, setCues] = useState(step.visual_cues.join(", "));
  return (
    <div className="step-editor">
      <label>Instruction<textarea rows={2} value={step.instruction} autoFocus onChange={(e) => onChange({ instruction: e.target.value })} /></label>
      <label>What the camera looks for<input value={step.expected_action} placeholder="e.g. blue food coloring dripped into the cup"
        onChange={(e) => onChange({ expected_action: e.target.value })} /></label>
      <label>Visual cues <small>comma-separated</small><input value={cues} placeholder="clear cup, blue liquid"
        onChange={(e) => { setCues(e.target.value); onChange({ visual_cues: splitList(e.target.value) }); }} /></label>
      <div className="editor-row">
        <button type="button" className={`safety-toggle ${step.safety_critical ? "on" : ""}`} aria-pressed={step.safety_critical}
          onClick={() => onChange({ safety_critical: !step.safety_critical })}><Icon name="shield" size={12} /> Safety-critical</button>
        {COMMON_PPE.map((p) => {
          const on = step.required_ppe.includes(p);
          return (
            <button key={p} type="button" className={`ppe-chip ${on ? "on" : ""}`} aria-pressed={on}
              onClick={() => onChange({ required_ppe: on ? step.required_ppe.filter((x) => x !== p) : [...step.required_ppe, p] })}>{p}</button>
          );
        })}
      </div>
    </div>
  );
}
