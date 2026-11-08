// Typed client for the LabScribe FastAPI backend. Same origin in production (served
// by FastAPI); in `npm run dev` Vite proxies /api to :8000.

export interface Health {
  ok: boolean;
  app: string;
  mock_mode: boolean;
  region: string;
  model: string;
  vision_model: string;
  scribe: { interval: number; frame_gap: number; max_width: number };
  missing_settings: string[];
}
export interface Step {
  number: number;
  section?: string;
  camera_checkable?: boolean;
  instruction: string;
  expected_action: string;
  visual_cues: string[];
  safety_critical: boolean;
  required_ppe: string[];
}
export interface ProcedureSummary {
  procedure_id: string;
  title: string;
  step_count: number;
  safety_step_count: number;
  approved_at: string;
  created_at: string;
}
export interface Procedure extends ProcedureSummary {
  steps: Step[];
  source_key: string;
}
export interface ManualDraft {
  source_key: string;
  filename: string;
  extraction: string;
  text_chars: number;
  text_preview: string;
  generated_by: string | null;
  warnings: string[];
  title: string;
  steps: Step[];
}
export interface ScribeLine {
  time: string;
  elapsed: number;
  action: string;
  objects: string[];
  model: string;
  ms: number;
  repeat: number;
  last_time?: string;
  last_elapsed?: number;
}
export interface StepState {
  number: number;
  status: "done" | "current" | "upcoming";
  started_elapsed: number | null;
  done_at: string | null;
  done_elapsed: number | null;
  evidence: string;
  method: "auto" | "manual" | null;
  snapshot_id: string | null;
}
export interface FeedbackItem {
  id: string;
  kind: "mistake" | "possible_skip";
  step: number;
  time: string;
  elapsed: number;
  mistake_type: string;
  what_went_wrong: string;
  how_to_fix: string;
  evidence: string;
  snapshot_id: string | null;
  dismissed: boolean;
  corrected: boolean | null;
}
export interface MatchCheck {
  status: string;
  confidence: number;
  evidence: string;
  mistake: boolean;
  step: number;
  time: string;
}
export interface SessionEvent {
  type: "step_done" | "complete" | "mistake" | "possible_skip" | "undo" | "tip";
  voice: string;
  step?: number;
  feedback_id?: string;
}
export interface ScribeResult {
  line?: ScribeLine;
  new_line?: boolean;
  skipped?: boolean;
  error?: string;
  state?: LabSession;
}
export interface LabSession {
  session_id: string;
  procedure_id: string;
  procedure: { title: string; steps: Step[] };
  started_at: string;
  ended_at: string | null;
  transcript: ScribeLine[];
  line_count: number;
  elapsed: number;
  ticks: number;
  skipped: number;
  errors: number;
  last_error: string | null;
  current: number;
  steps_state: StepState[];
  feedback: FeedbackItem[];
  last_check: (MatchCheck & { confirmed?: boolean }) | null;
  completed_at: string | null;
  complete: boolean;
  active: boolean;
  coach_tip: { step: number; text: string; time: string } | null;
  checks: { elapsed: number; step: number; status: string; confidence: number; mistake: boolean; evidence: string; tip: string }[];
  streaks: { done: number; skip: number; mistake: number };
  events?: SessionEvent[];
}
export interface Settings {
  workspace_name: string;
  polly_voice: string;
  min_person_area: number;
}
export interface SettingsResponse {
  settings: Settings;
  polly_voices: Record<string, string>;
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
