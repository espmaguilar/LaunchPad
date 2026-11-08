import { useEffect, useRef, useState } from "react";
import { api, post, type DraftGear, type DraftZone, type PolicyDraft, type Zone } from "../api";
import { GEAR_LABELS, money, severityClass } from "../format";
import { Icon, PageTitle } from "../ui";

const SEVERITIES = ["Serious", "Willful", "Repeat", "Other-than-serious"];
const DETECTION_OPTIONS: [string, string][] = [
  ["HAND_COVER", "Rekognition · Gloves"],
  ["FACE_COVER", "Rekognition · Face cover"],
  ["HEAD_COVER", "Rekognition · Head protection"],
  ["UNMONITORED", "AI vision (Bedrock)"],
];
const DRAFT_KEY = "safesight-policy-draft";
const MAX_MB = 10;

const readAsDataURL = (blob: Blob) =>
  new Promise<string>((ok, fail) => {
    const r = new FileReader();
    r.onload = () => ok(r.result as string);
    r.onerror = () => fail(r.error);
    r.readAsDataURL(blob);
  });

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "zone";

export default function PolicySetup({ zones, reloadZones, onWatch }: {
  zones: Zone[]; reloadZones: () => Promise<void>; onWatch: (zoneId: string) => void;
}) {
  const [step, setStep] = useState(1);
  const [draft, setDraft] = useState<PolicyDraft | null>(null);
  const [filename, setFilename] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [published, setPublished] = useState<{ zone_id: string; name: string; text: string }[] | null>(null);
  const [replace, setReplace] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(() => {
    try { return JSON.parse(localStorage.getItem(DRAFT_KEY) || "null")?.saved_at ?? null; } catch { return null; }
  });
  const fileRef = useRef<HTMLInputElement>(null);

  const extract = async (blob: Blob, name: string) => {
    setError(null);
    setPublished(null);
    if (blob.size > MAX_MB * 1024 * 1024) return setError(`That PDF is larger than ${MAX_MB} MB.`);
    setFilename(name);
    setStep(2);
    try {
      const r = await post<PolicyDraft>("/api/policy/upload", { filename: name, data: await readAsDataURL(blob) });
      setDraft(r);
      setStep(3);
    } catch (e) {
      setStep(1);
      setError(`Policy extraction failed: ${(e as Error).message}`);
    }
  };

  const useSample = async (name: "brightwater" | "new_rules", file: string) => {
    try {
      const res = await fetch(`/api/policy/sample?name=${name}`);
      if (!res.ok) throw new Error(`sample PDF not found - ${(await res.json().catch(() => ({}))).detail || "run the demo/ script"}`);
      await extract(await res.blob(), file);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const onFile = (f?: File | null) => {
    if (!f) return;
    if (!/\.pdf$/i.test(f.name) && f.type !== "application/pdf") return setError("Please choose a PDF file.");
    extract(f, f.name);
  };

  const zonesList = draft?.zones ?? [];
  const updateZones = (fn: (z: DraftZone[]) => DraftZone[]) => setDraft((d) => (d ? { ...d, zones: fn(d.zones.map((z) => ({ ...z, gear: [...z.gear] }))) } : d));
  const ruleCount = zonesList.reduce((n, z) => n + z.gear.length, 0);

  const approve = async () => {
    if (!draft) return;
    setBusy(true);
    setError(null);
    try {
      const r = await post<{ zones: { zone_id: string; name: string; required: string[]; unmonitored: unknown[] }[] }>("/api/policy/approve", {
        policy_key: draft.policy_key, zones: draft.zones, replace_existing: replace,
      });
      setPublished(r.zones.map((z) => ({ zone_id: z.zone_id, name: z.name,
        text: `${z.required.length} Rekognition · ${z.unmonitored.length} AI vision` })));
      try { localStorage.removeItem(DRAFT_KEY); } catch { /* ignore */ }
      setSavedAt(null);
      await reloadZones();
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      setError(`Not published: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const saveDraft = () => {
    if (!draft) return;
    const at = new Date().toISOString();
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify({ draft, filename, saved_at: at }));
      setSavedAt(at);
    } catch {
      setError("Could not save the draft in this browser.");
    }
  };
  const restoreDraft = () => {
    try {
      const s = JSON.parse(localStorage.getItem(DRAFT_KEY) || "null");
      if (s?.draft) { setDraft(s.draft); setFilename(s.filename || "saved draft"); setStep(3); setPublished(null); }
    } catch { setError("Saved draft could not be read."); }
  };

  return (
    <div>
      <PageTitle title="Policy setup" subtitle="Turn your safety policy into live detection rules" />
      <div className="stepper">
        {[["1", "Upload policy"], ["2", "AI processing"], ["3", "Review & approve"]].map(([n, l], i) => (
          <div key={n} className={`${step >= +n ? "active" : ""} ${step > +n ? "complete" : ""}`}>
            <span>{step > +n ? <Icon name="check" size={14} /> : n}</span>
            <b>{l}</b>
            {i < 2 && <i />}
          </div>
        ))}
      </div>
      {error && <div className="banner error">{error}</div>}
      {published && (
        <div className="banner ok published">
          <div>
            <b>Published.</b> Rules are live for these zones — the camera checks them when that zone is selected:
            <div className="published-zones">
              {published.map((z) => (
                <button key={z.zone_id} className="primary" onClick={() => onWatch(z.zone_id)}>
                  Watch {z.name} live <small>{z.text}</small> <Icon name="arrow" size={14} />
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {step === 1 && (
        <>
          <div className={`upload-card ${dragOver ? "drag" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => { e.preventDefault(); setDragOver(false); onFile(e.dataTransfer.files[0]); }}>
            <div className="upload-icon"><Icon name="upload" size={26} /></div>
            <h2>Upload your safety policy</h2>
            <p>Drop your PDF here, or click to browse</p>
            <span>PDF up to {MAX_MB} MB · stored in Amazon S3</span>
            <input ref={fileRef} type="file" aria-label="Upload policy" accept="application/pdf,.pdf"
              onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = ""; }} />
            <button className="primary" onClick={() => fileRef.current?.click()}>Choose PDF file</button>
            <div className="divider"><span>OR</span></div>
            <div className="sample-buttons">
              <button className="secondary" onClick={() => useSample("brightwater", "sample_safety_policy.pdf")}>
                <Icon name="file" size={16} /> Use sample policy
              </button>
              <button className="secondary" onClick={() => useSample("new_rules", "new_rules.pdf")}
                title="6 gesture & conduct rules (two fingers, phones, headphones…) checked by AI vision">
                <Icon name="file" size={16} /> Use “New Rules” test policy
              </button>
            </div>
            {savedAt && <button className="link-button" onClick={restoreDraft}>Resume saved draft ({new Date(savedAt).toLocaleString()})</button>}
            <small>Setup takes about 30 seconds</small>
          </div>
          <LiveRules zones={zones} reloadZones={reloadZones} />
        </>
      )}

      {step === 2 && <Processing filename={filename} />}

      {step === 3 && draft && (
        <div className="review">
          <div className="info-banner">
            <span>i</span>
            <div>
              <b>Review before publishing</b>
              <p>AI can misread documents — nothing goes live until you approve. Click ✎ to edit a row.</p>
            </div>
          </div>
          {draft.warnings.length > 0 && <ul className="warn-list">{draft.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
          <div className="review-head">
            <div>
              <h2>Detected safety rules</h2>
              <p>
                {ruleCount} rules across {zonesList.length} zone{zonesList.length === 1 ? "" : "s"} · Extracted from {filename} ·{" "}
                {draft.text_chars.toLocaleString()} characters via {draft.extraction}
                {draft.generated_by ? ` · structured by ${draft.generated_by}` : ""}
              </p>
            </div>
            <button className="secondary" onClick={() => { setStep(1); setDraft(null); }}>Replace file</button>
          </div>
          <div className="policy-table">
            <table>
              <thead>
                <tr>
                  <th>ZONE / REQUIREMENT</th>
                  <th>DETECTION</th>
                  <th>POLICY REFERENCE</th>
                  <th>SEVERITY</th>
                  <th>FINE</th>
                  <th>SOURCE</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {zonesList.map((z, zi) => (
                  <ZoneRows key={zi} zone={z} liveZones={zones}
                    onZone={(patch) => updateZones((zs) => { zs[zi] = { ...zs[zi], ...patch }; return zs; })}
                    onRemoveZone={() => updateZones((zs) => zs.filter((_, i) => i !== zi))}
                    onGear={(gi, g) => updateZones((zs) => { zs[zi].gear[gi] = g; return zs; })}
                    onRemoveGear={(gi) => updateZones((zs) => { zs[zi].gear = zs[zi].gear.filter((_, i) => i !== gi); return zs; })} />
                ))}
                {!zonesList.length && (
                  <tr><td colSpan={7} className="table-empty">No zones extracted. Add a zone below, then add its rules.</td></tr>
                )}
              </tbody>
            </table>
            <AddRuleRow zoneNames={zonesList.map((z) => z.name)}
              onAdd={(zoneIdx, g) => updateZones((zs) => { zs[zoneIdx].gear.push(g); return zs; })}
              onAddZone={(name) => updateZones((zs) => [...zs, { name, alert_email: "", gear: [] }])} />
          </div>
          <div className="publish-row">
            <label className="checkbox">
              <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />
              <i />
              <span>
                <b>Replace existing zones</b>
                <small>Remove zones that aren't in this policy (past incidents are kept)</small>
              </span>
            </label>
            <div>
              <button className="secondary" onClick={saveDraft}>{savedAt ? "Draft saved ✓" : "Save draft"}</button>
              <button className="primary" onClick={approve} disabled={busy || !zonesList.length}>
                {busy ? "Publishing…" : "Approve & publish"} <Icon name="arrow" size={16} />
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ZoneRows({ zone, liveZones, onZone, onRemoveZone, onGear, onRemoveGear }: {
  zone: DraftZone; liveZones: Zone[]; onZone: (p: Partial<DraftZone>) => void; onRemoveZone: () => void;
  onGear: (i: number, g: DraftGear) => void; onRemoveGear: (i: number) => void;
}) {
  return (
    <>
      <tr className="zone-row">
        <td colSpan={7}>
          <div className="zone-row-fields">
            <label>Zone<input value={zone.name} onChange={(e) => onZone({ name: e.target.value })} aria-label="Zone name" /></label>
            <label>Supervisor email<input type="email" value={zone.alert_email} onChange={(e) => onZone({ alert_email: e.target.value })}
              placeholder="supervisor@company.com" aria-label="Supervisor email" disabled={!!zone.target_zone_id} /></label>
            <label>Publish to
              <select value={zone.target_zone_id || ""} onChange={(e) => onZone({ target_zone_id: e.target.value || undefined })} aria-label="Publish to">
                <option value="">{liveZones.some((z) => z.zone_id === slug(zone.name)) ? `Replace live zone “${zone.name}”` : `New zone “${zone.name || "unnamed"}”`}</option>
                {liveZones.map((z) => <option key={z.zone_id} value={z.zone_id}>Add rules to {z.name}</option>)}
              </select>
            </label>
            <button className="icon-only" onClick={onRemoveZone} title="Remove zone" aria-label="Remove zone"><Icon name="trash" size={15} /></button>
          </div>
        </td>
      </tr>
      {zone.gear.map((g, gi) => <GearRow key={gi} g={g} onChange={(ng) => onGear(gi, ng)} onRemove={() => onRemoveGear(gi)} />)}
      {!zone.gear.length && <tr><td colSpan={7} className="table-empty small">No rules in this zone yet.</td></tr>}
    </>
  );
}

function GearRow({ g, onChange, onRemove }: { g: DraftGear; onChange: (g: DraftGear) => void; onRemove: () => void }) {
  const [editing, setEditing] = useState(!g.item);
  const ai = g.gear_type === "UNMONITORED";
  const source = g.fine_source === "policy" ? "From policy" : g.fine_source === "edited" ? "Edited" : g.fine_source || (ai ? "No fine set" : "OSHA default");
  if (editing) {
    return (
      <tr className="editing">
        <td><input value={g.item} onChange={(e) => onChange({ ...g, item: e.target.value })} placeholder="Requirement" aria-label="Requirement" autoFocus /></td>
        <td>
          <select value={g.gear_type} onChange={(e) => onChange({ ...g, gear_type: e.target.value })} aria-label="Detection">
            {DETECTION_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </td>
        <td><input value={g.rule} onChange={(e) => onChange({ ...g, rule: e.target.value })} placeholder="Rule reference" aria-label="Policy reference" /></td>
        <td>
          <select value={g.severity || ""} onChange={(e) => onChange({ ...g, severity: e.target.value || null })} aria-label="Severity">
            <option value="">—</option>
            {SEVERITIES.map((s) => <option key={s}>{s}</option>)}
          </select>
        </td>
        <td>
          <div className="money-input">$ <input type="number" min={0} value={g.fine ?? ""} aria-label="Fine"
            onChange={(e) => onChange({ ...g, fine: e.target.value === "" ? null : Math.round(+e.target.value), fine_source: e.target.value === "" ? "" : "edited" })} /></div>
        </td>
        <td><span className="source">{source}</span></td>
        <td>
          <button onClick={() => setEditing(false)} title="Done" aria-label="Done editing"><Icon name="check" size={15} /></button>
          <button onClick={onRemove} title="Delete" aria-label="Delete rule"><Icon name="trash" size={15} /></button>
        </td>
      </tr>
    );
  }
  return (
    <tr>
      <td><b>{g.item || "—"}</b></td>
      <td>
        <span className={`detection-type ${ai ? "ai" : ""}`}>
          <i />{ai ? "AI vision" : `Rekognition · ${GEAR_LABELS[g.gear_type]?.toLowerCase()}`}
        </span>
      </td>
      <td>{g.rule || "—"}</td>
      <td>{g.severity ? <span className={`severity ${severityClass(g.severity)}`}>{g.severity}</span> : "—"}</td>
      <td><code>{g.fine != null ? money(g.fine) : ai ? "—" : "OSHA"}</code></td>
      <td><span className={`source ${source === "OSHA default" ? "osha" : ""}`}>{source}</span></td>
      <td>
        <button onClick={() => setEditing(true)} title="Edit" aria-label="Edit rule"><Icon name="edit" size={15} /></button>
        <button onClick={onRemove} title="Delete" aria-label="Delete rule"><Icon name="trash" size={15} /></button>
      </td>
    </tr>
  );
}

function AddRuleRow({ zoneNames, onAdd, onAddZone }: {
  zoneNames: string[]; onAdd: (zoneIdx: number, g: DraftGear) => void; onAddZone: (name: string) => void;
}) {
  const [text, setText] = useState("");
  const [zi, setZi] = useState(0);
  const [fine, setFine] = useState("");
  const add = () => {
    const item = text.trim();
    if (!item) return;
    if (!zoneNames.length) { onAddZone(item); setText(""); return; } // first entry creates a zone
    onAdd(Math.min(zi, zoneNames.length - 1), {
      item, gear_type: "UNMONITORED", rule: "Custom rule", severity: "Other-than-serious",
      fine: fine === "" ? null : Math.round(+fine), fine_source: fine === "" ? "" : "edited",
    });
    setText("");
    setFine("");
  };
  return (
    <div className="add-rule">
      <Icon name="plus" size={17} />
      <input value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()}
        placeholder={zoneNames.length ? "Add a rule, e.g. “No phones in use” (checked by AI vision)" : "Add a zone name first, e.g. “Clean Room”"}
        aria-label="New rule" />
      {zoneNames.length > 0 && (
        <select value={zi} onChange={(e) => setZi(+e.target.value)} aria-label="Zone for new rule">
          {zoneNames.map((n, i) => <option key={i} value={i}>{n || `Zone ${i + 1}`}</option>)}
        </select>
      )}
      {zoneNames.length > 0 && <div className="money-input">$ <input value={fine} onChange={(e) => setFine(e.target.value.replace(/[^0-9]/g, ""))} placeholder="500" aria-label="Fine" /></div>}
      <button onClick={add}>{zoneNames.length ? "Add rule" : "Add zone"}</button>
      {zoneNames.length > 0 && <button className="link-button" onClick={() => { const n = prompt("New zone name"); if (n?.trim()) onAddZone(n.trim()); }}>+ Zone</button>}
    </div>
  );
}

function Processing({ filename }: { filename: string }) {
  const [t, setT] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setT((x) => x + 1), 1000);
    return () => window.clearInterval(id);
  }, []);
  // Honest staging by elapsed time: the request is a single call, so these are estimates.
  const stage = t < 2 ? 0 : t < 6 ? 1 : 2;
  const s = (i: number) => (stage > i ? "done" : stage === i ? "working" : "");
  return (
    <div className="processing-card">
      <div className="processing-orb"><div>✦</div></div>
      <h2>Reading your safety policy</h2>
      <p>Extracting zones, requirements, citations and fines. {t}s elapsed.</p>
      <div className="processing-progress"><i /></div>
      <span>Usually takes less than 30 seconds</span>
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
          <div><b>Structuring rules with Bedrock</b><small>Identifying compliance logic…</small></div>
        </div>
      </div>
    </div>
  );
}

/** Live zones: add free-text AI-vision rules without a PDF ("No phones", $500). */
function LiveRules({ zones, reloadZones }: { zones: Zone[]; reloadZones: () => Promise<void> }) {
  const [zoneId, setZoneId] = useState("");
  const [text, setText] = useState("");
  const [fine, setFine] = useState("");
  const [severity, setSeverity] = useState("Other-than-serious");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (!zones.some((z) => z.zone_id === zoneId)) setZoneId(zones[0]?.zone_id || ""); }, [zones, zoneId]);

  const add = async () => {
    const item = text.trim();
    if (!item || !zoneId) return;
    setBusy(true);
    setErr(null);
    try {
      await post(`/api/zones/${encodeURIComponent(zoneId)}/rules`, { item, fine: fine === "" ? null : +fine, severity });
      setText("");
      setFine("");
      await reloadZones();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const remove = async (zid: string, key: string) => {
    setErr(null);
    try {
      await api(`/api/zones/${encodeURIComponent(zid)}/rules/${encodeURIComponent(key)}`, { method: "DELETE" });
      await reloadZones();
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  return (
    <section className="live-rules">
      <div className="review-head">
        <div>
          <h2>Live zones & quick rules</h2>
          <p>Add a free-text rule to a live zone without a PDF. AI vision (Amazon Bedrock) checks it every 10 s.</p>
        </div>
      </div>
      {err && <div className="banner error">{err}</div>}
      <div className="policy-table">
        <table>
          <thead>
            <tr><th>ZONE / REQUIREMENT</th><th>DETECTION</th><th>POLICY REFERENCE</th><th>SEVERITY</th><th>FINE</th><th>SOURCE</th><th></th></tr>
          </thead>
          <tbody>
            {zones.map((z) => (
              <ZoneLive key={z.zone_id} z={z} onRemove={(key) => remove(z.zone_id, key)} />
            ))}
            {!zones.length && <tr><td colSpan={7} className="table-empty">No zones yet. Upload a policy above.</td></tr>}
          </tbody>
        </table>
        <div className="add-rule">
          <Icon name="plus" size={17} />
          <input value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()}
            placeholder="Add a rule, e.g. “No phones in use”" aria-label="New live rule" />
          <select value={zoneId} onChange={(e) => setZoneId(e.target.value)} aria-label="Zone">
            {zones.map((z) => <option key={z.zone_id} value={z.zone_id}>{z.name}</option>)}
          </select>
          <select value={severity} onChange={(e) => setSeverity(e.target.value)} aria-label="Severity">
            {SEVERITIES.map((s) => <option key={s}>{s}</option>)}
          </select>
          <div className="money-input">$ <input value={fine} onChange={(e) => setFine(e.target.value.replace(/[^0-9]/g, ""))} placeholder="500" aria-label="Fine" /></div>
          <button onClick={add} disabled={busy || !text.trim() || !zoneId}>{busy ? "Adding…" : "Add rule"}</button>
        </div>
      </div>
    </section>
  );
}

function ZoneLive({ z, onRemove }: { z: Zone; onRemove: (key: string) => void }) {
  const rows = [
    ...z.required.map((g) => ({ key: g, item: z.rules?.[g]?.item || GEAR_LABELS[g], ai: false, rule: z.rules?.[g]?.rule || "OSHA default",
      severity: z.rules?.[g]?.severity || "Serious", fine: z.rules?.[g]?.fine ?? null, source: z.rules?.[g]?.source || "OSHA default" })),
    ...z.ai_rules.map((r) => ({ key: r.key, item: r.item, ai: true, rule: r.rule || "—", severity: r.severity || null,
      fine: r.fine ?? null, source: r.source || "no fine set" })),
  ];
  return (
    <>
      <tr className="zone-row"><td colSpan={7}><label className="zone-label">{z.name}</label>{z.alert_email && <span className="zone-email">{z.alert_email}</span>}</td></tr>
      {rows.map((r) => (
        <tr key={r.key}>
          <td><b>{r.item}</b></td>
          <td><span className={`detection-type ${r.ai ? "ai" : ""}`}><i />{r.ai ? "AI vision" : "Rekognition"}</span></td>
          <td>{r.rule}</td>
          <td>{r.severity ? <span className={`severity ${severityClass(r.severity)}`}>{r.severity}</span> : "—"}</td>
          <td><code>{r.fine != null ? money(r.fine) : r.ai ? "—" : "OSHA"}</code></td>
          <td><span className={`source ${r.source === "OSHA default" ? "osha" : ""}`}>{r.source === "policy" ? "From policy" : r.source === "custom" ? "Custom rule" : r.source}</span></td>
          <td>{r.ai && <button onClick={() => onRemove(r.key)} title="Remove rule" aria-label={`Remove ${r.item}`}><Icon name="trash" size={15} /></button>}</td>
        </tr>
      ))}
    </>
  );
}
