// Camera + lab session loop, owned by <App> so the scribe keeps running on every page.
//   every FRAME_GAP s : snapshot the webcam into a 2-frame ring (downscaled JPEG)
//   every INTERVAL s  : POST the ring (oldest first) to /api/sessions/{id}/scribe
// No backlog: if the previous scribe call hasn't returned, the tick is skipped.
// MOCK_MODE works without a camera (the server scripts the transcript).
import { useCallback, useEffect, useRef, useState } from "react";
import { api, post, type Health, type LabSession, type ScribeLine, type ScribeResult, type SessionEvent } from "./api";
import { motionSummary, setFocusTerms, setScribeObjects, startHands, stopHands } from "./hands";
import { isSpeaking, onSpeaking, speak, unlockAudio } from "./voice";

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
  coach: { text: string; type: SessionEvent["type"]; at: number } | null; // latest spoken line
  chat: ChatMessage[];
  asking: boolean;
  confusedOffer: number | null; // step number the "Feeling confused?" pop-up is asking about
  speaking: boolean;            // LabScribe is talking: frames are sent "speaking" (mistakes only, no advancing)
}

export interface ChatMessage {
  id: number;
  from: "ai" | "user";
  text: string;
  kind?: "tip" | "step" | "mistake" | "answer" | "info";
  feedbackId?: string;
}
let chatId = 0;

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
  const streamRef = useRef<MediaStream | null>(null);
  const [s, setS] = useState<LabState>({
    stream: null, session: null, lines: [], lastMs: null, model: null, skipped: 0, scribeError: null, error: null, starting: false,
    coach: null, chat: [], asking: false, confusedOffer: null, speaking: false,
  });
  // while LabScribe speaks, frames still go to the server marked speaking: it only watches for wrong moves then
  useEffect(() => onSpeaking((on) => setS((x) => ({ ...x, speaking: on }))), []);
  const pushChat = useCallback((msgs: Omit<ChatMessage, "id">[]) => {
    if (!msgs.length) return;
    setS((x) => ({ ...x, chat: [...x.chat, ...msgs.map((m) => ({ ...m, id: ++chatId }))].slice(-60) }));
  }, []);

  // Apply a server state (progress, feedback) and speak its events (step done, corrections).
  const applyState = useCallback((st?: LabSession) => {
    if (!st) return;
    const { transcript: _t, events, ...rest } = st;
    void _t;
    sessionRef.current = { ...(sessionRef.current as LabSession), ...rest, transcript: [] };
    if (st.focus_terms) setFocusTerms(st.focus_terms.action, st.focus_terms.objects);
    const evs = events ?? [];
    if (evs.length) speak(evs.map((e) => e.voice).join(" "));
    // chat log (chatbot only): corrections are shown as cards; no tips, no step chatter
    const msgs: Omit<ChatMessage, "id">[] = [];
    for (const e of evs) {
      if (e.type === "mistake" || e.type === "possible_skip") msgs.push({ from: "ai", text: e.voice, kind: "mistake", feedbackId: e.feedback_id });
    }
    const offer = evs.find((e) => e.type === "confused_offer");
    if (offer) setTimeout(() => setS((x) => ({ ...x, confusedOffer: offer.step ?? null })), 0);
    if (msgs.length) setTimeout(() => pushChat(msgs), 0);
    setS((x) => ({
      ...x,
      session: x.session ? { ...x.session, ...rest } : x.session,
      coach: evs.length ? { text: evs[evs.length - 1].voice, type: evs[evs.length - 1].type, at: Date.now() } : x.coach,
    }));
  }, []);

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
      const r = await post<ScribeResult>(`/api/sessions/${sess.session_id}/scribe`, { frames, motion: motionSummary(), speaking: isSpeaking() });
      if (sessionRef.current?.session_id !== sess.session_id) return;
      applyState(r.state);
      if (r.line) {
        const line = r.line;
        setScribeObjects(line.boxes ?? [], line.action, line.objects ?? []); // the grid labels its boxes with the scribe's names
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
  }, [mock, applyState]);

  useEffect(() => {
    if (!s.session || s.session.ended_at) return;
    const t = window.setInterval(tick, interval);
    return () => window.clearInterval(t);
  }, [s.session, interval, tick]);

  /** Ask for camera permission and start the feed. Resolves true when the camera is live. */
  const startCamera = useCallback(async (): Promise<boolean> => {
    unlockAudio();
    if (streamRef.current) return true;
    setS((x) => ({ ...x, starting: true, error: null }));
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 1280, height: 720 }, audio: false });
      const v = (video.current ||= document.createElement("video"));
      v.muted = true;
      v.playsInline = true;
      v.srcObject = stream;
      await v.play();
      ring.current = [];
      startHands(v); // MediaPipe hand tracking (optional: no-op if it can't load)
      streamRef.current = stream;
      setS((x) => ({ ...x, stream, starting: false }));
      return true;
    } catch (e) {
      setS((x) => ({ ...x, starting: false,
        error: `Camera unavailable: ${(e as Error).message}. Allow camera access and use http://localhost.` }));
      return false;
    }
  }, []);

  const stopCamera = useCallback(() => {
    setS((x) => {
      x.stream?.getTracks().forEach((t) => t.stop());
      if (video.current) video.current.srcObject = null;
      ring.current = [];
      stopHands();
      streamRef.current = null;
      return { ...x, stream: null };
    });
  }, []);

  const startSession = useCallback(async (procedureId: string) => {
    unlockAudio();
    setS((x) => ({ ...x, error: null }));
    // camera permission FIRST: the session clock and the spoken intro only start once the camera prompt is answered
    // (if it's denied the session still starts - mock mode and Mark done work without a camera)
    if (!streamRef.current) await startCamera();
    try {
      const sess = await post<LabSession>("/api/sessions", { procedure_id: procedureId });
      sessionRef.current = sess;
      if (sess.focus_terms) setFocusTerms(sess.focus_terms.action, sess.focus_terms.objects);
      const first = sess.procedure.steps[0];
      const hello = first ? `Let's start ${sess.procedure.title}. Step 1: ${first.instruction}` : "Let's start.";
      setS((x) => ({ ...x, session: sess, lines: [], skipped: 0, scribeError: null, lastMs: null, coach: null,
        chat: [{ id: ++chatId, from: "ai", text: hello, kind: "info" }] }));
      speak(hello);
    } catch (e) {
      setS((x) => ({ ...x, error: `Could not start the session: ${(e as Error).message}` }));
    }
  }, [startCamera]);

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

  const act = useCallback(async (path: string, body?: unknown) => {
    const sess = sessionRef.current;
    if (!sess) return;
    try {
      applyState(await post<LabSession>(`/api/sessions/${sess.session_id}/${path}`, body));
    } catch (e) {
      setS((x) => ({ ...x, error: (e as Error).message }));
    }
  }, [applyState]);
  const ask = useCallback(async (question: string) => {
    const sess = sessionRef.current;
    const q = question.trim();
    if (!sess || !q) return;
    pushChat([{ from: "user", text: q }]);
    setS((x) => ({ ...x, asking: true }));
    try {
      const r = await post<{ answer: string }>(`/api/sessions/${sess.session_id}/ask`, { question: q });
      pushChat([{ from: "ai", text: r.answer, kind: "answer" }]);
      speak(r.answer);
    } catch (e) {
      pushChat([{ from: "ai", text: `Sorry, I couldn't answer that: ${(e as Error).message}`, kind: "info" }]);
    } finally {
      setS((x) => ({ ...x, asking: false }));
    }
  }, [pushChat]);
  const answerConfused = useCallback(async (yes: boolean) => {
    setS((x) => ({ ...x, confusedOffer: null }));
    const sess = sessionRef.current;
    if (!sess) return;
    try {
      applyState(await post<LabSession>(`/api/sessions/${sess.session_id}/confused`, { yes }));
    } catch (e) {
      setS((x) => ({ ...x, error: `Couldn't break the step down: ${(e as Error).message}` }));
    }
  }, [applyState]);
  // manual clicks carry the step number on screen: if a camera tick advanced meanwhile, the server ignores them
  const seenStep = () => {
    const sess = sessionRef.current;
    return { step: sess ? sess.procedure.steps[sess.current]?.number ?? 0 : 0 };
  };
  const substepDone = useCallback(() => act("substeps/done", seenStep()), [act]);
  const exitBreakdown = useCallback(() => act("substeps/exit"), [act]);
  const markDone = useCallback(() => act("steps/done", seenStep()), [act]);
  const undo = useCallback(() => act("steps/undo", seenStep()), [act]);
  const restartStep = useCallback(() => act("steps/restart", seenStep()), [act]);
  const dismiss = useCallback((id: string) => act(`feedback/${id}/dismiss`), [act]);

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

  return { ...s, startCamera, stopCamera, startSession, endSession, closeSession, resync, markDone, undo, restartStep, dismiss, ask, answerConfused, substepDone, exitBreakdown };
}
