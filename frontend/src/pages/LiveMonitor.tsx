import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import type { DashboardData, Health, Person, Track, Zone } from "../api";
import { GEAR_LABELS, gearLabel, isAi, lower, money } from "../format";
import type { Page } from "../App";
import type { useMonitor } from "../monitor";
import { Icon, PageTitle } from "../ui";

type Monitor = ReturnType<typeof useMonitor>;
const PART: Record<string, string> = { HAND_COVER: "hands", FACE_COVER: "face", HEAD_COVER: "head" };

function useNow(ms = 1000) {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), ms);
    return () => window.clearInterval(t);
  }, [ms]);
  return now;
}

export default function LiveMonitor({ zone, monitor, summary, health, setPage }: {
  zone: Zone | null; monitor: Monitor; summary: DashboardData | null; health: Health | null; setPage: (p: Page) => void;
}) {
  const now = useNow();
  const lastAgo = monitor.lastFrameAt ? Math.round((now.getTime() - monitor.lastFrameAt.getTime()) / 1000) : null;
  const today = summary?.today;
  return (
    <div className="live-page">
      <PageTitle
        title="Live monitor"
        subtitle={`Real-time PPE compliance · ${zone?.name || "no zone selected"}`}
        action={
          <div className={`timestamp ${monitor.stream ? "" : "idle"}`}>
            <i /> Last detection <b>{lastAgo == null ? "—" : lastAgo < 3 ? "just now" : `${lastAgo}s ago`}</b>
          </div>
        }
      />
      {monitor.error && <div className="banner error">{monitor.error}</div>}
      <div className="monitor-grid">
        <Feed zone={zone} monitor={monitor} now={now} />
        <DetectionPanel zone={zone} monitor={monitor} setPage={setPage} health={health} />
      </div>
      <div className="kpi-strip">
        <Kpi label="Fine exposure today" value={money(today?.fine_exposure ?? 0)} tone="red"
          trend={`${today?.open ?? 0} open`} note={`Across ${today?.violations ?? 0} incident${today?.violations === 1 ? "" : "s"} today`} />
        <Kpi label="Fines avoided" value={money(today?.fines_avoided ?? 0)} tone="green"
          trend={`↑ ${today?.resolved ?? 0} today`} note="Fixed within 5 minutes of the warning" />
        <Kpi label="Open violations" value={String(summary?.current.open ?? 0)} tone="amber" trend="View all →"
          note={`Last 7 days · ${money(summary?.current.fine_exposure ?? 0)} exposure`} onClick={() => setPage("Incidents")} />
      </div>
    </div>
  );
}

function Feed({ zone, monitor, now }: { zone: Zone | null; monitor: Monitor; now: Date }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const feedRef = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState<{ w: number; h: number }>({ w: 16, h: 9 });

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    v.srcObject = monitor.stream;
    if (monitor.stream) v.play().catch(() => {});
  }, [monitor.stream]);

  const r = monitor.result;
  const persons = r?.persons ?? [];
  const ignored = r?.ignored ?? [];
  const hasAi = (zone?.ai_rules.length ?? 0) > 0;

  return (
    <section className="feed-card">
      <div className="feed-top">
        <div>
          <span className={`live-dot ${monitor.stream ? "" : "off"}`} />
          {monitor.stream ? "LIVE" : "OFFLINE"} <b>CAM-01</b>
          <span>·</span>
          <span>{zone?.name || "—"}</span>
        </div>
        <div>
          <button title="Analyze a frame now" aria-label="Analyze now" onClick={() => monitor.analyzeNow()} disabled={!monitor.stream}>↻</button>
          <button title="Fullscreen" aria-label="Fullscreen" onClick={() => feedRef.current?.requestFullscreen?.()}>⛶</button>
        </div>
      </div>
      <div ref={feedRef} className={`feed ${!monitor.stream ? "feed-offline" : ""}`}
        style={monitor.stream ? { aspectRatio: `${dims.w} / ${dims.h}` } : undefined}>
        {monitor.starting ? (
          <CameraSkeleton />
        ) : monitor.stream ? (
          <>
            <video ref={videoRef} className="feed-video" muted playsInline
              onLoadedMetadata={(e) => setDims({ w: e.currentTarget.videoWidth, h: e.currentTarget.videoHeight })} />
            <div className="camera-shade" />
            <div className="feed-time">
              {now.toLocaleTimeString([], { hour12: false })} &nbsp;·&nbsp;{" "}
              {now.toLocaleDateString([], { day: "2-digit", month: "short", year: "numeric" }).toUpperCase()}
            </div>
            <div className="source-tags">
              <span>Rekognition</span>
              {hasAi && <span>AI Vision</span>}
              {r?.mock && <span>Mock data</span>}
            </div>
            {ignored.map((p, i) => (
              <DetectionBox key={`ig${i}`} type="grey" person={p} label="Background (ignored)" />
            ))}
            {persons.map((p, i) => (
              <DetectionBox key={i} type={p.status === "ok" ? "green" : p.status === "violation" ? "red" : "amber"} person={p}
                label={
                  p.status === "ok" ? `Person ${i + 1} · Compliant`
                    : p.status === "violation" ? `Missing: ${p.missing.map((g) => lower(GEAR_LABELS[g] || g)).join(", ")}`
                      : `Unknown · ${[...new Set(p.unknown.map((g) => PART[g] || g))].join(", ")} not visible`
                } />
            ))}
          </>
        ) : (
          <div className="offline-state">
            <div className="offline-icon">
              <Icon name="camera" size={30} />
            </div>
            <h3>Camera is off</h3>
            <p>
              {zone ? `Start the camera to monitor ${zone.name}. Notification timers pause while the feed is off.`
                : "Pick a zone first (top left)."}
            </p>
            <button className="primary" onClick={monitor.start} disabled={!zone}>
              <Icon name="camera" size={16} /> Start camera
            </button>
          </div>
        )}
      </div>
      <div className="feed-foot">
        <div>
          <span className="quality">
            <i className={monitor.stream ? "" : "off"} /> {monitor.stream && dims.h > 9 ? `${dims.h}p` : "—"}
          </span>
          <span>Analyzing every 2 s</span>
          <span>Latency {monitor.latency != null ? `${monitor.latency}ms` : "—"}</span>
        </div>
        <span>
          {persons.length} {persons.length === 1 ? "person" : "people"} checked
          {ignored.length ? ` · ${ignored.length} ignored` : ""}
        </span>
      </div>
    </section>
  );
}

function CameraSkeleton() {
  return (
    <div className="skeleton-camera">
      <div className="skeleton-icon">
        <Icon name="camera" size={28} />
      </div>
      <div className="skeleton-line" />
      <div className="skeleton-line short" />
      <span>Connecting to camera…</span>
    </div>
  );
}

function DetectionBox({ type, person, label }: { type: string; person: Person; label: string }) {
  const b = person.box; // Rekognition ratios (0-1) -> percentages of the frame
  const style: CSSProperties = { left: `${b.left * 100}%`, top: `${b.top * 100}%`, width: `${b.width * 100}%`, height: `${b.height * 100}%` };
  return (
    <div className={`detect-box ${type} ${b.top < 0.06 ? "label-inside" : ""}`} style={style}>
      <span>{label}</span>
      <i className="corner tl" />
      <i className="corner tr" />
      <i className="corner bl" />
      <i className="corner br" />
    </div>
  );
}

function DetectionPanel({ zone, monitor, setPage, health }: { zone: Zone | null; monitor: Monitor; setPage: (p: Page) => void; health: Health | null }) {
  const tracking = monitor.tracking || {};
  const entries = Object.entries(tracking);
  const active = entries.filter(([, t]) => t.open).length;
  const ai = monitor.aiVision;
  const reason = (key: string) => ai?.results.find((r) => r.key === key)?.reason;
  return (
    <section className="detection-panel">
      <div className="panel-heading">
        <div>
          <span>ZONE</span>
          <h2>{zone?.name || "—"}</h2>
        </div>
        <button title="Edit zone rules" aria-label="Edit zone rules" onClick={() => setPage("Policy Setup")}>
          <Icon name="more" />
        </button>
      </div>
      <div className="rule-section">
        <label>REQUIRED GEAR & RULES</label>
        <div className="chips">
          {zone?.required.map((g) => {
            const r = zone.rules?.[g];
            return (
              <span key={g} className="chip-rek" title={[r?.rule, r?.fine != null ? money(r.fine) : null].filter(Boolean).join(" · ") || "Amazon Rekognition"}>
                {GEAR_LABELS[g] || g} <small>Rekognition</small>
              </span>
            );
          })}
          {zone?.ai_rules.map((r) => (
            <span key={r.key} className="chip-ai" title={[r.rule, r.fine != null ? money(r.fine) : "no fine set"].filter(Boolean).join(" · ")}>
              {r.item} <small>AI vision</small>
            </span>
          ))}
          {zone && !zone.required.length && !zone.ai_rules.length && <span className="text-rule">No rules yet</span>}
        </div>
      </div>
      <div className="logic-title">
        <label>DETECTION LOGIC</label>
        <span className={active ? "" : "calm"}>{active} active</span>
      </div>
      {!entries.length && <p className="panel-empty">{zone ? "Waiting for the first frame…" : "Pick a zone to see its rules."}</p>}
      {entries.map(([key, t]) => (
        <LogicEntry key={key} gear={key} t={t} reason={isAi(key) ? reason(key) : undefined} />
      ))}
      {(zone?.ai_rules.length ?? 0) > 0 && (
        <div className={`ai-status ${ai?.error ? "bad" : ""}`}>
          <b>AI vision</b>
          {ai ? (ai.error ? <span>Check failed: {ai.error}</span> : <span>{ai.model} · checked {ai.age}s ago · every 10 s</span>)
            : <span>{monitor.stream ? "First check within 10 s" : "Runs while the camera is on"}</span>}
        </div>
      )}
      <div className="flow-note">
        Missing {health?.timers.voice_after ?? 20}s → 🔊 voice · {health?.timers.notify_after ?? 30}s → 📧 supervisor ·
        compliant {health?.timers.resolve ?? 10}s → resolved
      </div>
    </section>
  );
}

function LogicEntry({ gear, t, reason }: { gear: string; t: Track; reason?: string }) {
  const title = gearLabel(gear, t.label);
  const ai = isAi(gear);
  const tag = <span className={`det-tag ${ai ? "ai" : "rek"}`}>{ai ? "AI vision" : "Rekognition"}</span>;

  if (t.open && t.resolving != null) {
    const rs = t.resolve_seconds ?? 10;
    const done = Math.min(t.resolving, rs);
    return (
      <LogicCard tone="resolving" title={title} tag={tag} subtitle={`Resolving ${Math.floor(done)}/${rs}s`} icon="✓">
        <div className="progress"><i style={{ width: `${(100 * done) / rs}%` }} /></div>
        <small>Keep compliant for {Math.max(0, Math.ceil(rs - done))} more seconds</small>
      </LogicCard>
    );
  }
  if (t.open) {
    const total = Math.max(1, t.notify_after - (ai ? 0 : t.voice_after));
    const left = t.notify_in ?? 0;
    return (
      <LogicCard tone="danger" title={title} tag={tag} icon="●●●"
        subtitle={`${ai ? "Rule broken" : "Missing"} · ${t.missing_for}s${t.paused ? " · camera offline" : ""}`}>
        <div className="logic-flow">
          <span className="done">🔊 Warned</span>
          {t.paused ? (
            <span className="paused">⏸ Timers paused</span>
          ) : t.notified ? (
            <span className="done">📧 Supervisor notified</span>
          ) : (
            <span className="countdown">
              <i style={{ "--p": `${Math.min(100, (100 * (total - left)) / total)}%` } as CSSProperties}><em>{left}</em></i>
              <b>Supervisor in {left}s</b>
            </span>
          )}
        </div>
        {reason && <small className="ai-reason">“{reason}”</small>}
      </LogicCard>
    );
  }
  if (ai && (t.checks ?? 0) > 0) {
    const need = t.checks_needed ?? 2;
    return (
      <LogicCard tone="warning" title={title} tag={tag} subtitle="Confirming violation" icon={"●".repeat(t.checks!) + "○".repeat(Math.max(0, need - t.checks!))}>
        <div className="confirm-bars">
          {Array.from({ length: need }, (_, i) => <i key={i} className={i < t.checks! ? "" : "empty"} />)}
          <span>{t.checks} of {need} checks</span>
        </div>
        {reason && <small className="ai-reason">“{reason}”</small>}
      </LogicCard>
    );
  }
  if (!ai && t.missing_for > 0) {
    const steps = 5;
    const filled = Math.min(steps, Math.ceil((t.missing_for / t.voice_after) * steps));
    return (
      <LogicCard tone="warning" title={title} tag={tag} subtitle="Confirming missing" icon={`${t.missing_for}s`}>
        <div className="confirm-bars">
          {Array.from({ length: steps }, (_, i) => <i key={i} className={i < filled ? "" : "empty"} />)}
          <span>{t.missing_for} of {t.voice_after}s · voice at {t.voice_after}s</span>
        </div>
      </LogicCard>
    );
  }
  return (
    <div className="logic-ok">
      <span><Icon name="check" size={14} /></span>
      <div>
        <b>{title} {tag}</b>
        <small>{ai ? (reason ? `“${reason}”` : "No violation seen") : "No violation detected"}</small>
      </div>
      <em>Compliant</em>
    </div>
  );
}

function LogicCard({ tone, title, tag, subtitle, icon, children }: {
  tone: string; title: string; tag: ReactNode; subtitle: string; icon: string; children: ReactNode;
}) {
  return (
    <div className={`logic-card ${tone}`}>
      <div className="logic-head">
        <div>
          <i />
          <span>
            <b>{title} {tag}</b>
            <small>{subtitle}</small>
          </span>
        </div>
        <code>{icon}</code>
      </div>
      {children}
    </div>
  );
}

function Kpi({ label, value, note, tone, trend, onClick }: {
  label: string; value: string; note: string; tone: string; trend: string; onClick?: () => void;
}) {
  return (
    <button className={`kpi ${tone}`} onClick={onClick} disabled={!onClick} type="button">
      <span>{label}</span>
      <div>
        <strong>{value}</strong>
        <em>{trend}</em>
      </div>
      <small>{note}</small>
    </button>
  );
}
