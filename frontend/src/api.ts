// Typed client for the LabScribe FastAPI backend. Same origin in production (served
// by FastAPI); in `npm run dev` Vite proxies /api to :8000.

export interface Health {
  ok: boolean;
  app: string;
  mock_mode: boolean;
  region: string;
  model: string;
  vision_model: string;
  missing_settings: string[];
}
export interface Step {
  number: number;
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
