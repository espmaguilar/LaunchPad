import { useEffect, useState, type ReactNode } from "react";
import { api, post, tzOffset, type DashboardData, type WeeklyReport } from "../api";
import { ago, fmtDate, money, pctChange } from "../format";
import { Icon, PageTitle } from "../ui";

const DONUT_COLORS = ["#ffb020", "#ef4444", "#60a5fa", "#5f6670"]; // design slots d1-d4

export default function Dashboard({ workspace, dataVersion }: { workspace: string; dataVersion: number }) {
  const [days, setDays] = useState(7);
  const [d, setD] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<WeeklyReport | null>(null);
  const [reportBusy, setReportBusy] = useState(false);
  const [reportErr, setReportErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api<DashboardData>(`/api/dashboard?days=${days}&tz_offset=${tzOffset()}`)
        .then((x) => alive && (setD(x), setError(null)))
        .catch((e) => alive && setError(`Could not load dashboard: ${e.message}`));
    load();
    const t = window.setInterval(load, 10000);
    return () => { alive = false; window.clearInterval(t); };
  }, [days, dataVersion]);

  const generate = async () => {
    setReportBusy(true);
    setReportErr(null);
    try {
      setReport(await post<WeeklyReport>(`/api/weekly-report?tz_offset=${tzOffset()}`));
    } catch (e) {
      setReportErr((e as Error).message);
    } finally {
      setReportBusy(false);
    }
  };

  const c = d?.current;
  const p = d?.previous;
  const vs = `vs. previous ${days} days`;
  const trend = (cur: number | null | undefined, prev: number | null | undefined, goodWhenDown: boolean, unit = "%") => {
    const ch = pctChange(cur ?? null, prev ?? null);
    if (ch == null) return { note: prev === 0 && (cur ?? 0) > 0 ? "New" : "—", tone: "neutral" };
    const down = ch < 0;
    return { note: `${down ? "↓" : "↑"} ${Math.abs(ch)}${unit}`, tone: down === goodWhenDown ? "green" : "red" };
  };
  const te = trend(c?.fine_exposure, p?.fine_exposure, true);
  const ta = trend(c?.fines_avoided, p?.fines_avoided, false);
  const fixDelta = c?.avg_seconds_to_fix != null && p?.avg_seconds_to_fix != null ? c.avg_seconds_to_fix - p.avg_seconds_to_fix : null;
  const rateDelta = c?.resolution_rate != null && p?.resolution_rate != null ? Math.round((c.resolution_rate - p.resolution_rate) * 10) / 10 : null;

  return (
    <div>
      <PageTitle
        title="Safety dashboard"
        subtitle={<>{workspace} · All zones{d ? ` · ${fmtDate(d.start)} – ${fmtDate(d.end)}` : ""}{d?.includes_sample && <span className="sample-tag">includes sample data</span>}</>}
        action={
          <div className="segmented" role="group" aria-label="Date range">
            {[7, 30, 90].map((n) => (
              <button key={n} className={days === n ? "active" : ""} onClick={() => setDays(n)}>{n} days</button>
            ))}
          </div>
        }
      />
      {error && <div className="banner error">{error}</div>}
      <div className="dashboard-kpis">
        <Metric label="FINE EXPOSURE" value={money(c?.fine_exposure ?? 0)} note={te.note} tone={te.tone} sub={`${c?.open ?? 0} open · ${vs}`} />
        <Metric label="FINES AVOIDED" value={money(c?.fines_avoided ?? 0)} note={ta.note} tone={ta.tone} sub={`${c?.resolved ?? 0} resolved · ${vs}`} />
        <Metric label="RESOLVED IN TIME" value={c?.resolution_rate != null ? `${c.resolution_rate}%` : "—"}
          note={rateDelta == null ? "—" : `${rateDelta >= 0 ? "↑" : "↓"} ${Math.abs(rateDelta)} pts`}
          tone={rateDelta == null ? "neutral" : rateDelta >= 0 ? "green" : "red"} sub={`${c?.violations ?? 0} incidents`} />
        <Metric label="AVG. TIME TO FIX" value={c?.avg_seconds_to_fix != null ? `${c.avg_seconds_to_fix}s` : "—"}
          note={fixDelta == null ? "—" : `${fixDelta <= 0 ? "↓" : "↑"} ${Math.abs(fixDelta)}s`}
          tone={fixDelta == null ? "neutral" : fixDelta <= 0 ? "green" : "red"} sub="After the voice warning" />
      </div>
      <div className="chart-grid">
        <ChartCard title="Violations by hour" subtitle={`${c?.violations ?? 0} violations · last ${days} days`} large>
          <HourChart data={d?.by_hour ?? Array(24).fill(0)} />
        </ChartCard>
        <ChartCard title="Violations by zone" subtitle={`Last ${days} days`}>
          <ZoneBars rows={d?.by_zone ?? []} />
        </ChartCard>
        <ChartCard title="Fine exposure vs. avoided" subtitle={`USD · per day · last ${days} days`} large>
          <LineChart daily={d?.daily ?? []} />
        </ChartCard>
        <ChartCard title="Most common violations" subtitle={`${c?.violations ?? 0} total detections`}>
          <Donut rows={d?.by_issue ?? []} total={c?.violations ?? 0} />
        </ChartCard>
      </div>
      <div className="ai-report">
        <div className="ai-stars">✦</div>
        <div>
          <span>AI WEEKLY SAFETY REPORT · LAST 7 DAYS</span>
          {report ? (
            <>
              <h3>{report.summary.split(/(?<=\.)\s/)[0]}</h3>
              <p>{report.summary.split(/(?<=\.)\s/).slice(1).join(" ")}</p>
              {report.highlights?.length > 0 && <ul className="report-list">{report.highlights.map((h, i) => <li key={i}>{h}</li>)}</ul>}
              {report.recommendations?.length > 0 && (
                <ol className="report-list recs">{report.recommendations.map((h, i) => <li key={i}>{h}</li>)}</ol>
              )}
              <div className="report-tags">
                <span>{report.highlights?.length ?? 0} insights</span>
                <span>{report.recommendations?.length ?? 0} recommendations</span>
                <span>{report.generated_by} · {ago(report.generated_at)}</span>
              </div>
            </>
          ) : (
            <>
              <h3>{reportBusy ? "Writing this week's briefing…" : "Weekly briefing for site managers"}</h3>
              <p>
                {reportErr ? `Could not generate: ${reportErr}` :
                  "Amazon Bedrock turns the last 7 days of incidents into a summary, highlights and 3 recommendations. All numbers come from your records."}
              </p>
            </>
          )}
        </div>
        <button className="primary" onClick={generate} disabled={reportBusy}>
          {reportBusy ? "Generating…" : report ? "Regenerate" : "Generate weekly report"} <Icon name="arrow" size={16} />
        </button>
      </div>
    </div>
  );
}

function Metric({ label, value, note, sub, tone }: { label: string; value: string; note: string; sub: string; tone: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
      <div>
        <em className={tone}>{note}</em>
        <small>{sub}</small>
      </div>
    </div>
  );
}

function ChartCard({ title, subtitle, children, large }: { title: string; subtitle: string; children: ReactNode; large?: boolean }) {
  return (
    <section className={`chart-card ${large ? "large" : ""}`}>
      <div className="chart-head">
        <div>
          <h3>{title}</h3>
          <span>{subtitle}</span>
        </div>
      </div>
      {children}
    </section>
  );
}

const niceMax = (v: number) => {
  if (v <= 4) return 4;
  const step = Math.pow(10, Math.floor(Math.log10(v)));
  const m = [1, 2, 2.5, 5, 10].find((k) => k * step >= v)!;
  return m * step;
};
const hourName = (h: number) => (h % 12 || 12) + (h < 12 ? " AM" : " PM");

function HourChart({ data }: { data: number[] }) {
  const max = niceMax(Math.max(...data));
  const top = Math.max(...data);
  // busiest 2-hour window
  let best = -1, bestSum = 0;
  for (let h = 0; h < 23; h++) if (data[h] + data[h + 1] > bestSum) { bestSum = data[h] + data[h + 1]; best = h; }
  return (
    <div className="hour-chart">
      <div className="y-labels">{[1, 0.75, 0.5, 0.25, 0].map((f) => <span key={f}>{Math.round(max * f)}</span>)}</div>
      <div className="bars">
        {data.map((v, i) => (
          <i key={i} className={top > 0 && bestSum > 0 && (i === best || i === best + 1) && v > 0 ? "hot" : ""}
            style={{ height: v ? `${Math.max(3, (v / max) * 100)}%` : "2%" }} title={`${hourName(i)}: ${v} violation${v === 1 ? "" : "s"}`} />
        ))}
        {bestSum > 1 && (
          <div className="spike-label" style={{ left: `${((best + 1) / 24) * 100}%`, right: "auto", transform: "translateX(-50%)" }}>
            {hourName(best).replace(" ", "")}–{hourName(best + 2).replace(" ", "")} peak
          </div>
        )}
      </div>
      <div className="x-labels"><span>12 AM</span><span>6 AM</span><span>12 PM</span><span>6 PM</span><span>12 AM</span></div>
    </div>
  );
}

function ZoneBars({ rows }: { rows: { zone: string; violations: number }[] }) {
  const max = Math.max(1, ...rows.map((r) => r.violations));
  if (!rows.length) return <p className="chart-empty">No violations in this range.</p>;
  return (
    <div className="zone-bars">
      {rows.slice(0, 5).map((r) => (
        <div key={r.zone}>
          <span><b>{r.zone}</b><em>{r.violations}</em></span>
          <i><b style={{ width: `${(100 * r.violations) / max}%` }} /></i>
        </div>
      ))}
    </div>
  );
}

function LineChart({ daily }: { daily: { date: string; exposure: number; avoided: number }[] }) {
  const W = 620, H = 185, top = 20, bottom = 165;
  const max = niceMax(Math.max(1, ...daily.flatMap((d) => [d.exposure, d.avoided])));
  const x = (i: number) => (daily.length <= 1 ? W / 2 : (i / (daily.length - 1)) * W);
  const y = (v: number) => bottom - (v / max) * (bottom - top);
  const path = (key: "exposure" | "avoided") => daily.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(d[key]).toFixed(1)}`).join(" ");
  const labelEvery = Math.ceil(daily.length / 7);
  return (
    <div className="line-chart">
      <div className="legend">
        <span><i className="exposure" />Exposure</span>
        <span><i className="avoided" />Avoided</span>
        <span className="legend-max">max {money(max)}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="Fine exposure vs avoided per day">
        <defs>
          <linearGradient id="greenfill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor="#22c55e" stopOpacity=".2" />
            <stop offset="1" stopColor="#22c55e" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path className="gridline" d="M0 30H620M0 75H620M0 120H620M0 165H620" />
        {daily.length > 0 && (
          <>
            <path className="fill" d={`${path("avoided")} L${x(daily.length - 1)} ${H} L${x(0)} ${H}Z`} />
            <path className="avoided-line" d={path("avoided")} vectorEffect="non-scaling-stroke" />
            <path className="exposure-line" d={path("exposure")} vectorEffect="non-scaling-stroke" />
          </>
        )}
      </svg>
      <div className="days">
        {daily.filter((_, i) => i % labelEvery === 0 || i === daily.length - 1).map((d) => (
          <span key={d.date}>{daily.length <= 7 ? new Date(d.date + "T00:00:00").toLocaleDateString([], { weekday: "short" }) : fmtDate(d.date)}</span>
        ))}
      </div>
    </div>
  );
}

function Donut({ rows, total }: { rows: { label: string; detector: string; violations: number }[]; total: number }) {
  const top = rows.slice(0, 3);
  const other = rows.slice(3).reduce((s, r) => s + r.violations, 0);
  const parts = [...top.map((r) => ({ label: r.label, n: r.violations, ai: r.detector === "AI vision" })), ...(other ? [{ label: "Other", n: other, ai: false }] : [])];
  let acc = 0;
  const stops = parts.map((p, i) => {
    const from = (acc / Math.max(1, total)) * 100;
    acc += p.n;
    return `${DONUT_COLORS[i]} ${from}% ${(acc / Math.max(1, total)) * 100}%`;
  });
  return (
    <div className="donut-wrap">
      <div className="donut" style={{ background: total ? `conic-gradient(${stops.join(", ")})` : "#262b33" }}>
        <div><strong>{total}</strong><span>TOTAL</span></div>
      </div>
      <div className="donut-legend">
        {parts.length ? parts.map((p, i) => (
          <span key={p.label}>
            <i style={{ background: DONUT_COLORS[i] }} />
            {p.label.charAt(0).toUpperCase() + p.label.slice(1)}{p.ai ? " (AI)" : ""} <b>{Math.round((100 * p.n) / Math.max(1, total))}%</b>
          </span>
        )) : <span className="chart-empty">No violations yet.</span>}
      </div>
    </div>
  );
}
