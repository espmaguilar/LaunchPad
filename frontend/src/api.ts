// Typed client for the SafeSight FastAPI backend. Same origin in production (served
// by FastAPI); in `npm run dev` Vite proxies /api to :8000.

export type Gear = string; // "HAND_COVER" | "FACE_COVER" | "HEAD_COVER" | "AI:<slug>"

export interface Rule {
  item?: string;
  rule?: string;
  severity?: string | null;
  fine?: number | null;
  source?: string | null;
}
export interface AiRule extends Rule {
  key: string;
  item: string;
}
export interface Zone {
  zone_id: string;
  name: string;
  required: Gear[];
  rules?: Record<string, Rule>;
  unmonitored?: Rule[];
  ai_rules: AiRule[];
  alert_email?: string;
}
export interface Health {
  ok: boolean;
  mock_mode: boolean;
  demo_mode: boolean;
  min_person_area: number;
  timers: { voice_after: number; notify_after: number; resolve: number };
  notify_channel: string;
  region: string;
  missing_settings: string[];
}
export interface Box {
  left: number;
  top: number;
  width: number;
  height: number;
}
export interface Person {
  id: number;
  box: Box;
  status: "ok" | "violation" | "unknown";
  worn: Gear[];
  missing: Gear[];
  unknown: Gear[];
}
export interface Track {
  open: boolean;
  detector: "Rekognition" | "AI vision";
  label: string;
  missing_for: number;
  voice_after: number;
  notify_after: number;
  checks?: number;
  checks_needed?: number;
  paused?: boolean;
  notified?: boolean;
  notify_in?: number;
  resolving?: number | null;
  resolve_seconds?: number;
}
export interface AiVision {
  model: string;
  age: number;
  error: string | null;
  results: { key: string; item: string; violated: boolean | null; confidence: number; reason: string }[];
}
export interface LiveEvent {
  type: "violation" | "resolved" | "fixed_late" | "notify" | "expired" | "error";
  gear?: Gear;
  gears?: Gear[];
  gear_label?: string;
  detector?: string;
  zone_id?: string;
  zone_name?: string;
  timestamp?: string;
  fine?: number;
  seconds_to_fix?: number;
  notified?: boolean;
  sent?: boolean;
  channel?: string;
  alert_error?: string;
  age?: number;
  message?: string;
  missing_for?: number;
  reattached?: boolean;
  joined?: boolean;
  incident_fine?: number;
}
export interface AnalyzeResult {
  zone_id: string;
  zone_name: string;
  required: Gear[];
  mock: boolean;
  persons: Person[];
  ignored: Person[];
  missing_gear: Gear[];
  events: LiveEvent[];
  tracking: Record<string, Track>;
  ai_vision: AiVision | null;
}
export interface IncidentItem {
  gear: Gear;
  label: string;
  detector: string;
  rule: string;
  severity?: string | null;
  fine: number;
  fine_source?: string | null;
  status: "OPEN" | "RESOLVED" | "FIXED_LATE";
  logged_at?: string;
  resolved_at?: string;
  fixed_at?: string;
  seconds_to_fix?: number | null;
  ai_reason?: string;
  notified?: boolean;
}
export interface Violation {
  items?: IncidentItem[];
  zone_id: string;
  timestamp: string;
  zone_name: string;
  gear_type: Gear;
  gear_label?: string;
  detector?: string;
  osha_rule: string;
  severity?: string;
  fine: number;
  fine_source?: string;
  status: "OPEN" | "RESOLVED";
  report?: string;
  report_status?: "PENDING" | "READY" | "FAILED";
  people?: number;
  detections?: string;
  voice_warnings?: number;
  emails_sent?: number;
  emailed_at?: string;
  resolved_at?: string;
  resolved_by?: string;
  fixed_at?: string;
  seconds_to_fix?: number;
  ai_reason?: string;
  sample?: boolean;
}
export interface Summary {
  violations: number;
  open: number;
  resolved: number;
  fine_exposure: number;
  fines_avoided: number;
  resolution_rate: number | null;
  avg_seconds_to_fix: number | null;
}
export interface DashboardData {
  days: number;
  start: string;
  end: string;
  current: Summary;
  previous: Summary;
  by_hour: number[];
  by_zone: { zone: string; violations: number }[];
  by_issue: { label: string; detector: string; violations: number }[];
  daily: { date: string; violations: number; exposure: number; avoided: number }[];
  includes_sample: boolean;
  today: { violations: number; open: number; resolved: number; fine_exposure: number; fines_avoided: number };
}
export interface WeeklyReport {
  summary: string;
  highlights: string[];
  recommendations: string[];
  generated_by: string;
  generated_at: string;
}
export interface DraftGear {
  item: string;
  gear_type: string; // Rekognition type or "UNMONITORED" (AI vision)
  rule: string;
  severity: string | null;
  fine: number | null;
  fine_source: string;
}
export interface DraftZone {
  name: string;
  alert_email: string;
  gear: DraftGear[];
  target_zone_id?: string; // set -> add these rules to that existing zone instead of a new one
}
export interface PolicyDraft {
  policy_key: string;
  filename: string;
  text_chars: number;
  extraction: string;
  generated_by: string | null;
  warnings: string[];
  text_preview: string;
  zones: DraftZone[];
}
export interface Settings {
  workspace_name: string;
  mode: "demo" | "production";
  voice_after_seconds: number;
  notify_after_seconds: number;
  resolve_seconds: number;
  polly_voice: string;
  min_person_area: number;
}
export interface SettingsResponse {
  settings: Settings;
  presets: Record<string, Partial<Settings>>;
  polly_voices: Record<string, string>;
  notify_channel: string;
  sns_topic: boolean;
  alert_email: string;
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const post = <T>(path: string, body?: unknown, method = "POST") =>
  api<T>(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export const tzOffset = () => new Date().getTimezoneOffset();
export const imageUrl = (v: { zone_id: string; timestamp: string }) =>
  `/api/violations/${encodeURIComponent(v.zone_id)}/${encodeURIComponent(v.timestamp)}/image`;
export const violationUrl = (v: { zone_id: string; timestamp: string }) =>
  `/api/violations/${encodeURIComponent(v.zone_id)}/${encodeURIComponent(v.timestamp)}`;
