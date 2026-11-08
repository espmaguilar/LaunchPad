import type { Gear, IncidentItem, Violation } from "./api";

export const GEAR_LABELS: Record<string, string> = {
  FACE_COVER: "Face cover",
  HAND_COVER: "Gloves",
  HEAD_COVER: "Head protection",
};
export const GEAR_ORDER = ["FACE_COVER", "HAND_COVER", "HEAD_COVER"];

export const isAi = (g?: Gear) => String(g ?? "").startsWith("AI:");
export const cap = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
export const gearLabel = (g: Gear, label?: string) => cap(label || GEAR_LABELS[g] || g.replace(/^AI:/, "").replace(/-/g, " "));
export const lower = (s: string) => (s ? s.charAt(0).toLowerCase() + s.slice(1) : s);
export const money = (n?: number | null) => (n == null ? "—" : `$${Math.round(Number(n)).toLocaleString()}`);

export const itemLabel = (v: Violation) => gearLabel(v.gear_type, v.gear_label);

/** Items in an incident (records written before grouping are read as one item). */
export function itemsOf(v: Violation): IncidentItem[] {
  if (v.items?.length) return v.items;
  return [{
    gear: v.gear_type, label: gearLabel(v.gear_type, v.gear_label), detector: v.detector || "Rekognition", rule: v.osha_rule,
    severity: v.severity, fine: Number(v.fine), fine_source: v.fine_source,
    status: v.status === "RESOLVED" ? "RESOLVED" : v.fixed_at ? "FIXED_LATE" : "OPEN",
    seconds_to_fix: v.seconds_to_fix, ai_reason: v.ai_reason, resolved_at: v.resolved_at, fixed_at: v.fixed_at,
  }];
}
export const itemText = (i: { gear: Gear; label?: string }) =>
  isAi(i.gear) ? gearLabel(i.gear, i.label) : `Missing ${lower(gearLabel(i.gear, i.label))}`;
/** "Missing gloves + face cover", "No phones in use", "Missing gloves + No phones in use" */
export function violationText(v: Violation) {
  const items = itemsOf(v);
  const ppe = items.filter((i) => !isAi(i.gear)).map((i) => lower(gearLabel(i.gear, i.label)));
  const ai = items.filter((i) => isAi(i.gear)).map((i) => gearLabel(i.gear, i.label));
  return [ppe.length ? `Missing ${ppe.join(" + ")}` : "", ...ai].filter(Boolean).join(" + ");
}
export const detectorsOf = (v: Violation) => [...new Set(itemsOf(v).map((i) => i.detector || "Rekognition"))];

/** Status shown in the UI. RESOLVED = fixed in the 5-min window (fine avoided);
 *  fixed_at on an OPEN record = fixed late (exposure stands). */
export function statusOf(v: Violation): "Open" | "Resolved" | "Fixed late" | "Notified" {
  if (v.status === "RESOLVED") return "Resolved";
  if (v.fixed_at) return "Fixed late";
  if ((v.emails_sent ?? 0) > 0) return "Notified";
  return "Open";
}

export const shortId = (v: Violation) => {
  // Stable 4-digit id from the timestamp (records have no numeric id)
  let h = 0;
  for (const c of v.zone_id + v.timestamp) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return String(h % 10000).padStart(4, "0");
};

export function fmtWhen(iso: string) {
  const d = new Date(iso);
  const today = new Date();
  const yest = new Date(Date.now() - 86400000);
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (d.toDateString() === today.toDateString()) return `Today, ${time}`;
  if (d.toDateString() === yest.toDateString()) return `Yesterday, ${time}`;
  return `${d.toLocaleDateString([], { month: "short", day: "numeric" })}, ${time}`;
}
export const fmtTime = (iso: string | Date) =>
  new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
export const fmtDate = (iso: string) =>
  new Date(iso + (iso.length === 10 ? "T00:00:00" : "")).toLocaleDateString([], { month: "short", day: "numeric" });

export function ago(iso: string) {
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 10) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

/** Severity -> design pill class (critical / high / medium) */
export const severityClass = (s?: string | null) =>
  s === "Willful" || s === "Repeat" ? "critical" : s === "Serious" ? "high" : "medium";

export function pctChange(cur: number | null, prev: number | null) {
  if (cur == null || prev == null || prev === 0) return null;
  return Math.round(((cur - prev) / prev) * 1000) / 10;
}
