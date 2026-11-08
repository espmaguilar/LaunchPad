// Camera + lab session loop, owned by <App> so the scribe keeps running on every page.
//   every FRAME_GAP s : snapshot the webcam into a 2-frame ring (downscaled JPEG)
//   every INTERVAL s  : POST the ring (oldest first) to /api/sessions/{id}/scribe
// No backlog: if the previous scribe call hasn't returned, the tick is skipped.
// MOCK_MODE works without a camera (the server scripts the transcript).
import { useCallback, useEffect, useRef, useState } from "react";
import { api, post, type Health, type LabSession, type ScribeLine, type ScribeResult } from "./api";
import { unlockAudio } from "./voice";

export interface LabState {
  stream: MediaStream | null;
  session: LabSession | null;
  lines: ScribeLine[];
  lastMs: number | null;
  model: string | null;
  skipped: number;
  scribeError: string | null;
  error: string | null;
  starting: boolean;
}

export function useLab(health: Health | null) {
  const interval = (health?.scribe.interval ?? 3) * 1000;
  const gap = (health?.scribe.frame_gap ?? 1) * 1000;
  const maxWidth = health?.scribe.max_width ?? 640;
  const mock = !!health?.mock_mode;

  const video = useRef<HTMLVideoElement | null>(null);
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const ring = useRef<string[]>([]);
  const inFlight = useRef(false);
  const sessionRef = useRef<LabSession | null>(null);
  const [s, setS] = useState<LabState>({
    stream: null, session: null, lines: [], lastMs: null, model: null, skipped: 0, scribeError: null, error: null, starting: false,
  });

  const snapshot = useCallback(() => {
    const v = video.current;
    if (!v || !v.videoWidth) return null;
    const c = (canvas.current ||= document.createElement("canvas"));
    const scale = Math.min(1, maxWidth / v.videoWidth);
    c.width = Math.round(v.videoWidth * scale);
    c.height = Math.round(v.videoHeight * scale);
    c.getContext("2d")!.drawImage(v, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", 0.8);
  }, [maxWidth]);

  // frame ring (motion context)
  useEffect(() => {
    if (!s.stream) return;
    const t = window.setInterval(() => {
      const f = snapshot();
      if (f) ring.current = [...ring.current, f].slice(-2);
    }, gap);
    return () => window.clearInterval(t);
  }, [s.stream, gap, snapshot]);

  const tick = useCallback(async () => {
    const sess = sessionRef.current;
    if (!sess || sess.ended_at) return;
    if (inFlight.current) { setS((x) => ({ ...x, skipped: x.skipped + 1 })); return; } // no backlog
    const frames = ring.current.slice();
    if (!frames.length && !mock) return; // camera not ready yet
    inFlight.current = true;
    try {
      const r = await post<ScribeResult>(`/api/sessions/${sess.session_id}/scribe`, { frames });
      if (sessionRef.current?.session_id !== sess.session_id) return;
      if (r.line) {
        const line = r.line;
        setS((x) => ({
          ...x,
          lines: r.new_line ? [...x.lines, line].slice(-300) : [...x.lines.slice(0, -1), line],
          lastMs: line.ms, model: line.model, scribeError: null,
        }));
      } else if (r.error) {
        setS((x) => ({ ...x, scribeError: r.error! }));
      } else if (r.skipped) {
        setS((x) => ({ ...x, skipped: x.skipped + 1 }));
      }
    } catch (e) {
      setS((x) => ({ ...x, scribeError: (e as Error).message }));
    } finally {
      inFlight.current = false;
    }
  }, [mock]);

  useEffect(() => {
    if (!s.session || s.session.ended_at) return;
    const t = window.setInterval(tick, interval);
    return () => window.clearInterval(t);
  }, [s.session, interval, tick]);

  const startCamera = useCallback(async () => {
    unlockAudio();
    setS((x) => ({ ...x, starting: true, error: null }));
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 1280, height: 720 }, audio: false });
      const v = (video.current ||= document.createElement("video"));
      v.muted = true;
      v.playsInline = true;
      v.srcObject = stream;
      await v.play();
      ring.current = [];
      setS((x) => ({ ...x, stream, starting: false }));
    } catch (e) {
      setS((x) => ({ ...x, starting: false,
        error: `Camera unavailable: ${(e as Error).message}. Allow camera access and use http://localhost.` }));
    }
  }, []);

  const stopCamera = useCallback(() => {
    setS((x) => {
      x.stream?.getTracks().forEach((t) => t.stop());
      if (video.current) video.current.srcObject = null;
      ring.current = [];
      return { ...x, stream: null };
    });
  }, []);

  const startSession = useCallback(async (procedureId: string) => {
    unlockAudio();
    setS((x) => ({ ...x, error: null }));
    try {
      const sess = await post<LabSession>("/api/sessions", { procedure_id: procedureId });
      sessionRef.current = sess;
      setS((x) => ({ ...x, session: sess, lines: [], skipped: 0, scribeError: null, lastMs: null }));
    } catch (e) {
      setS((x) => ({ ...x, error: `Could not start the session: ${(e as Error).message}` }));
    }
  }, []);

  const endSession = useCallback(async () => {
    const sess = sessionRef.current;
    if (!sess) return;
    try {
      const ended = await post<LabSession>(`/api/sessions/${sess.session_id}/end`);
      sessionRef.current = ended;
      setS((x) => ({ ...x, session: ended }));
    } catch (e) {
      setS((x) => ({ ...x, error: (e as Error).message }));
    }
  }, []);

  const closeSession = useCallback(() => {
    sessionRef.current = null;
    setS((x) => ({ ...x, session: null, lines: [] }));
  }, []);

  // resync after a tab sleep or reload of state (server is the source of truth)
  const resync = useCallback(async () => {
    const sess = sessionRef.current;
    if (!sess) return;
    try {
      const full = await api<LabSession>(`/api/sessions/${sess.session_id}`);
      setS((x) => ({ ...x, lines: full.transcript }));
    } catch { /* server restarted: the session is gone */ }
  }, []);

  useEffect(() => () => s.stream?.getTracks().forEach((t) => t.stop()), []); // eslint-disable-line react-hooks/exhaustive-deps

  return { ...s, startCamera, stopCamera, startSession, endSession, closeSession, resync };
}
