import { useCallback, useEffect, useMemo, useState } from "react";
import { api, imageUrl, post, violationUrl, type Violation, type Zone } from "../api";
import { detectorsOf, fmtTime, fmtWhen, gearLabel, isAi, itemsOf, itemText, money, shortId, statusOf, violationText } from "../format";
import { voiceText } from "../voice";
import { DetectorTag, Icon, PageTitle, Status } from "../ui";

const PAGE_SIZE = 10;
const RANGES: [string, number | null][] = [["Today", 0], ["Last 7 days", 7], ["Last 30 days", 30], ["All time", null]];

interface Report { summary?: string; risk?: string; fix?: string; prevention?: string; generated_by?: string }
const parseReport = (v: Violation): Report | null => {
  try { return v.report ? JSON.parse(v.report) : null; } catch { return null; }
};

export default function Incidents({ zones, dataVersion, onChanged }: { zones: Zone[]; dataVersion: number; onChanged: () => void }) {
  const [items, setItems] = useState<Violation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Violation | null>(null);
  const [status, setStatus] = useState("All statuses");
  const [zone, setZone] = useState("All zones");
  const [range, setRange] = useState("Last 7 days");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);

  const load = useCallback(async () => {
    try {
      setItems(await api<Violation[]>("/api/violations?limit=1000"));
      setError(null);
    } catch (e) {
      setError(`Could not load incidents: ${(e as Error).message}`);
    }
  }, []);
  useEffect(() => {
    load();
    const t = window.setInterval(load, 6000);
    return () => window.clearInterval(t);
  }, [load, dataVersion]);

  const zoneNames = useMemo(
    () => [...new Set([...zones.map((z) => z.name), ...(items ?? []).map((i) => i.zone_name)])].sort(),
    [zones, items],
  );

  const visible = useMemo(() => {
    const days = RANGES.find(([l]) => l === range)?.[1];
    const since = days == null ? 0 : days === 0 ? new Date(new Date().toDateString()).getTime() : Date.now() - days * 86400000;
    const q = query.trim().toLowerCase();
    return (items ?? []).filter((v) =>
      new Date(v.timestamp).getTime() >= since &&
      (status === "All statuses" || statusOf(v) === status) &&
      (zone === "All zones" || v.zone_name === zone) &&
      (!q || `${shortId(v)} ${v.zone_name} ${violationText(v)} ${itemsOf(v).map((i) => `${i.rule} ${i.detector}`).join(" ")}`.toLowerCase().includes(q)),
    );
  }, [items, status, zone, range, query]);

  useEffect(() => setPage(1), [status, zone, range, query]);
  const pages = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const shown = visible.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  const exportCsv = () => {
    const header = ["Incident", "Time (UTC)", "Zone", "Violation", "Detected by", "Rule", "Severity", "Status",
      "Notifications", "Potential fine (USD)", "Fine source", "Seconds to fix", "Sample"];
    const rows = visible.map((v) => {
      const its = itemsOf(v);
      return [shortId(v), v.timestamp, v.zone_name, violationText(v), detectorsOf(v).join(" + "),
        its.map((i) => i.rule).join("; "), [...new Set(its.map((i) => i.severity || ""))].join("; "), statusOf(v),
        v.emails_sent ?? 0, v.fine, its.map((i) => i.fine_source || "").join("; "), v.seconds_to_fix ?? "", v.sample ? "yes" : ""];
    });
    const csv = [header, ...rows].map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n");
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    link.download = `safesight-incidents-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  };

  return (
    <div>
      <PageTitle
        title="Incidents"
        subtitle="Review, resolve, and export safety events"
        action={
          <button className="primary" onClick={exportCsv} disabled={!visible.length}>
            <Icon name="download" size={16} /> Export CSV
          </button>
        }
      />
      {error && <div className="banner error">{error}</div>}
      <div className="filterbar">
        <div className="search">
          <Icon name="search" size={16} />
          <input placeholder="Search incidents…" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search incidents" />
        </div>
        <select value={zone} onChange={(e) => setZone(e.target.value)} aria-label="Zone">
          <option>All zones</option>
          {zoneNames.map((z) => <option key={z}>{z}</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
          <option>All statuses</option>
          <option>Open</option>
          <option>Notified</option>
          <option>Resolved</option>
          <option>Fixed late</option>
        </select>
        <select className="date-button" value={range} onChange={(e) => setRange(e.target.value)} aria-label="Date range">
          {RANGES.map(([l]) => <option key={l}>{l}</option>)}
        </select>
        <span className="result-count">{visible.length} incident{visible.length === 1 ? "" : "s"}</span>
      </div>
      <div className="table-card">
        <table>
          <thead>
            <tr>
              <th>INCIDENT</th>
              <th>TIME</th>
              <th>ZONE</th>
              <th>VIOLATION</th>
              <th>STATUS</th>
              <th>EMAILS</th>
              <th>POTENTIAL FINE</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {shown.map((v) => (
              <tr key={v.zone_id + v.timestamp} onClick={() => setSelected(v)} tabIndex={0}
                onKeyDown={(e) => e.key === "Enter" && setSelected(v)}>
                <td>
                  <div className="thumb">
                    <img src={imageUrl(v)} alt="" loading="lazy" onError={(e) => (e.currentTarget.style.visibility = "hidden")} />
                  </div>
                  <b>#{shortId(v)}</b>
                  {v.sample && <span className="sample-tag">sample</span>}
                </td>
                <td>{fmtWhen(v.timestamp)}</td>
                <td>
                  <span className="zone-dot" />
                  {v.zone_name}
                </td>
                <td>
                  <span className="violation-cell">{violationText(v)} {detectorsOf(v).map((d) => <DetectorTag key={d} detector={d} />)}</span>
                </td>
                <td><Status value={statusOf(v)} /></td>
                <td><span className="email-count">{v.emails_sent ?? 0}</span></td>
                <td><code>{money(v.fine)}</code></td>
                <td><Icon name="chevron" size={15} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        {items && !visible.length && (
          <div className="empty-state">
            <div><Icon name="check" size={30} /></div>
            <h3>{items.length ? "No incidents match" : "No incidents yet"}</h3>
            <p>{items.length ? "Try changing the date range, status or zone filters." : "Violations confirmed by the camera show up here."}</p>
          </div>
        )}
        {!items && !error && <div className="empty-state"><p>Loading incidents…</p></div>}
        <div className="table-footer">
          Showing {shown.length ? (page - 1) * PAGE_SIZE + 1 : 0}–{(page - 1) * PAGE_SIZE + shown.length} of {visible.length} incidents
          <div>
            <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1} aria-label="Previous page">‹</button>
            {Array.from({ length: pages }, (_, i) => i + 1)
              .filter((n) => n === 1 || n === pages || Math.abs(n - page) <= 1)
              .map((n) => (
                <button key={n} className={n === page ? "selected" : ""} onClick={() => setPage(n)}>{n}</button>
              ))}
            <button onClick={() => setPage((p) => Math.min(pages, p + 1))} disabled={page === pages} aria-label="Next page">›</button>
          </div>
        </div>
      </div>
      {selected && (
        <IncidentDrawer incident={selected} zone={zones.find((z) => z.zone_id === selected.zone_id)}
          onClose={() => setSelected(null)}
          onResolved={(v) => { setSelected(v); load(); onChanged(); }} />
      )}
    </div>
  );
}

function IncidentDrawer({ incident, zone, onClose, onResolved }: {
  incident: Violation; zone?: Zone; onClose: () => void; onResolved: (v: Violation) => void;
}) {
  const [v, setV] = useState(incident);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [imgOk, setImgOk] = useState(true);

  // fresh detail; keep polling while the Bedrock report is being written
  useEffect(() => {
    let alive = true;
    let t: number;
    const load = async () => {
      try {
        const d = await api<Violation>(violationUrl(incident));
        if (!alive) return;
        setV(d);
        if (d.report_status === "PENDING") t = window.setTimeout(load, 3000);
      } catch { /* keep list copy */ }
    };
    load();
    return () => { alive = false; window.clearTimeout(t); };
  }, [incident]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const report = parseReport(v);
  const st = statusOf(v);
  const items = itemsOf(v);
  const allAi = items.every((i) => isAi(i.gear));
  let dets: { box: { left: number; top: number; width: number; height: number }; status: string }[] = [];
  try { dets = JSON.parse(v.detections || "[]"); } catch { /* none */ }

  const resolve = async () => {
    setBusy(true);
    setErr(null);
    try {
      onResolved(await post<Violation>(`${violationUrl(v)}/resolve`));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const phrase = voiceText(v.zone_name, items.map((i) => i.gear), Object.fromEntries(items.map((i) => [i.gear, i.label])));
  const first = items[0];
  const timeline: { time: string; title: string; sub: string; tone: string }[] = [
    {
      time: fmtTime(v.timestamp),
      title: isAi(first.gear) ? "Violation confirmed by AI vision" : "Violation confirmed",
      sub: isAi(first.gear) ? (first.ai_reason ? `“${first.ai_reason}” · 2 consecutive checks` : "Flagged on 2 consecutive AI checks")
        : `${itemText(first)} continuously · ${v.people ?? 1} ${v.people == 1 ? "person" : "people"}`,
      tone: "red",
    },
    { time: fmtTime(v.timestamp), title: "Voice warning issued", sub: `“${phrase}”`, tone: "amber" },
  ];
  items.slice(1).forEach((i) => timeline.push({
    time: i.logged_at ? fmtTime(i.logged_at) : "—", title: `Also: ${itemText(i).replace(/^Missing /, "missing ")}`,
    sub: `${i.detector} · joined this incident · ${money(i.fine)}`, tone: "red",
  }));
  if (v.emailed_at) timeline.push({ time: fmtTime(v.emailed_at), title: "Supervisor notified", sub: zone?.alert_email || "SNS alert topic", tone: "blue" });
  items.forEach((i) => {
    if (i.status === "RESOLVED" && i.resolved_at && v.resolved_by !== "manager")
      timeline.push({ time: fmtTime(i.resolved_at), title: `${gearLabel(i.gear, i.label)} fixed`, sub: `After ${i.seconds_to_fix ?? "?"}s · ${money(i.fine)} avoided`, tone: "green" });
    if (i.status === "FIXED_LATE" && i.fixed_at)
      timeline.push({ time: fmtTime(i.fixed_at), title: `${gearLabel(i.gear, i.label)} fixed late`, sub: `After ${i.seconds_to_fix}s · exposure stands`, tone: "amber" });
  });
  if (v.resolved_by === "manager" && v.resolved_at)
    timeline.push({ time: fmtTime(v.resolved_at), title: "Marked resolved by manager", sub: "Resolved from the Incidents page", tone: "green" });
  else if (v.status === "RESOLVED") timeline.push({ time: v.resolved_at ? fmtTime(v.resolved_at) : "—", title: "Incident resolved", sub: "All items compliant · potential fine avoided", tone: "green" });
  else if (!items.some((i) => i.status === "OPEN")) { /* all fixed (some late) */ }
  else timeline.push({ time: "—", title: "Awaiting fix", sub: `${items.filter((i) => i.status === "OPEN").map((i) => i.label).join(", ")} · resolves after continuous compliance`, tone: "grey" });
  timeline.sort((a, b) => (a.time === "—" ? 1 : b.time === "—" ? -1 : 0));

  const downloadPdf = () => printReport(v, report, timeline, imgOk);

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={`Incident ${shortId(v)}`}>
        <div className="drawer-head">
          <div>
            <span>INCIDENT</span>
            <h2>Incident #{shortId(v)}</h2>
          </div>
          <button onClick={onClose} aria-label="Close"><Icon name="close" /></button>
        </div>
        <div className="drawer-body">
          <div className="incident-snapshot">
            {imgOk ? <img src={imageUrl(v)} alt="Incident snapshot" onError={() => setImgOk(false)} />
              : <div className="snapshot-missing">{v.sample ? "Sample record · no photo" : "Photo unavailable"}</div>}
            {imgOk && dets.map((d, i) => (
              <div key={i} className={`snapshot-box ${d.status}`}
                style={{ left: `${d.box.left * 100}%`, top: `${d.box.top * 100}%`, width: `${d.box.width * 100}%`, height: `${d.box.height * 100}%` }}>
                {d.status === "violation" && <span>{violationText(v)}</span>}
              </div>
            ))}
            <span className="snapshot-time">CAM-01 · {fmtTime(v.timestamp)}</span>
          </div>
          <div className="drawer-meta">
            <div><span>STATUS</span><Status value={st} /></div>
            <div><span>ZONE</span><b>{v.zone_name}</b></div>
            <div><span>POTENTIAL FINE</span><code>{money(v.fine)}</code></div>
          </div>
          <section className="drawer-section">
            <h3>Incident timeline</h3>
            <div className="timeline">
              {timeline.map((r, i) => (
                <div key={i} className={`timeline-row ${r.tone}`}>
                  <i />
                  <time>{r.time}</time>
                  <div><b>{r.title}</b><span>{r.sub}</span></div>
                </div>
              ))}
            </div>
          </section>
          <section className="report-box">
            <div className="ai-label">AI INCIDENT SUMMARY <span>{report?.generated_by?.includes("Bedrock") ? "Bedrock" : report ? "Template" : "Bedrock"}</span></div>
            {v.report_status === "PENDING" ? <p className="muted">Writing the incident report…</p>
              : report?.summary ? <p>{report.summary}</p> : <p className="muted">Report unavailable.</p>}
            {report?.risk && <p className="report-extra"><b>Risk.</b> {report.risk}</p>}
          </section>
          <section className="drawer-section">
            <h3>Violations in this incident</h3>
            <div className="item-list">
              {items.map((i) => (
                <div key={i.gear} className="incident-item">
                  <div>
                    <b>{itemText(i)} <DetectorTag detector={i.detector} /></b>
                    <small>{i.rule}{i.severity ? ` · ${i.severity}` : ""} · {i.fine_source === "policy" ? "from policy" : i.fine_source || "OSHA default"}</small>
                    {i.ai_reason && <small className="ai-reason">“{i.ai_reason}”</small>}
                  </div>
                  <code>{money(i.fine)}</code>
                  <Status value={i.status === "RESOLVED" ? "Resolved" : i.status === "FIXED_LATE" ? "Fixed late" : "Open"} />
                </div>
              ))}
            </div>
          </section>
          <section className="drawer-section details">
            <h3>Compliance details</h3>
            <div><span>Detected by</span><b>{allAi ? "AI vision (Amazon Bedrock)" : items.some((i) => isAi(i.gear)) ? "Amazon Rekognition PPE + AI vision" : "Amazon Rekognition PPE"}</b></div>
            {report?.fix && <div><span>Recommended fix</span><b>{report.fix}</b></div>}
            {report?.prevention && <div><span>Prevention</span><b>{report.prevention}</b></div>}
          </section>
          <p className="fine-note">Amounts are potential fine exposure based on policy or OSHA maximum penalties, not an assessed fine.</p>
          {err && <div className="banner error">{err}</div>}
        </div>
        <div className="drawer-actions">
          <button className="secondary" onClick={resolve} disabled={busy || v.status === "RESOLVED"}>
            {v.status === "RESOLVED" ? "Resolved" : busy ? "Resolving…" : "Mark resolved"}
          </button>
          <button className="primary" onClick={downloadPdf}>
            <Icon name="download" size={16} /> Download PDF report
          </button>
        </div>
      </aside>
    </div>
  );
}

/** Opens a print-ready report in a new window; the browser's "Save as PDF" makes the file. */
function printReport(v: Violation, report: Report | null, timeline: { time: string; title: string; sub: string }[], withImg: boolean) {
  const esc = (s: unknown) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
  const w = window.open("", "_blank");
  if (!w) return alert("Allow pop-ups for this site to download the report.");
  const rows = (pairs: [string, unknown][]) => pairs.filter(([, x]) => x).map(([k, x]) => `<tr><th>${esc(k)}</th><td>${esc(x)}</td></tr>`).join("");
  w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>SafeSight incident ${shortId(v)}</title>
<style>body{font:13px/1.5 Inter,system-ui,sans-serif;color:#111;margin:32px;max-width:760px}h1{font-size:20px;margin:0}
.sub{color:#555;margin:2px 0 18px}img{width:100%;border-radius:6px;margin:8px 0 14px}table{border-collapse:collapse;width:100%;margin-bottom:16px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #ddd;vertical-align:top}th{width:180px;color:#444;font-weight:600}
h2{font-size:14px;margin:18px 0 6px}.note{color:#666;font-size:11px;margin-top:24px}</style></head><body>
<h1>SafeSight incident #${shortId(v)}</h1><p class="sub">${esc(v.zone_name)} · ${esc(new Date(v.timestamp).toLocaleString())}</p>
${withImg ? `<img src="${location.origin}${imageUrl(v)}" alt="">` : ""}
<table>${rows([["Violation", violationText(v)], ["Detected by", detectorsOf(v).join(" + ")], ["Status", statusOf(v)],
    ["Potential fine exposure", money(v.fine)], ["Notifications sent", v.emails_sent ?? 0]])}</table>
<h2>Items</h2><table>${itemsOf(v).map((i) => `<tr><th>${esc(i.label)}</th><td>${esc(i.rule)} · ${esc(i.severity || "")} · ${esc(money(i.fine))} · ${esc(i.status.replace("_", " ").toLowerCase())}</td></tr>`).join("")}</table>
<h2>Timeline</h2><table>${timeline.map((r) => `<tr><th>${esc(r.time)}</th><td><b>${esc(r.title)}</b><br>${esc(r.sub)}</td></tr>`).join("")}</table>
${report ? `<h2>AI incident report</h2><table>${rows([["Summary", report.summary], ["Risk", report.risk], ["Immediate fix", report.fix], ["Prevention", report.prevention], ["Generated by", report.generated_by]])}</table>` : ""}
<p class="note">Amounts are potential fine exposure based on policy or OSHA maximum penalties, not an assessed fine.</p>
<script>window.onload=()=>setTimeout(()=>window.print(),300)<\/script></body></html>`);
  w.document.close();
}
