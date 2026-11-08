// Camera + analysis loop, owned by <App> so it keeps running (voice, notifications,
// incident logging) while the user is on other pages.
//   webcam -> 640px JPEG every 2 s -> POST /api/analyze -> boxes, tracking, events
// While the camera is off, /api/tracking is polled so timers/notifications still show.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, post, type AnalyzeResult, type AiVision, type LiveEvent, type Track } from "./api";
import { queueVoice, unlockAudio } from "./voice";

const SNAPSHOT_MS = 2000;
const SNAPSHOT_MAX_WIDTH = 640; // smaller payload, faster Rekognition

export interface MonitorState {
  stream: MediaStream | null;
  result: AnalyzeResult | null;
  tracking: Record<string, Track> | null;
  aiVision: AiVision | null;
  latency: number | null;
  lastFrameAt: Date | null;
  error: string | null;
  starting: boolean;
}

export function useMonitor(zoneId: string, onEvents: (evs: LiveEvent[]) => void) {
  const video = useRef<HTMLVideoElement | null>(null);
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const inFlight = useRef(false);
  const zoneRef = useRef(zoneId);
  const onEventsRef = useRef(onEvents);
  onEventsRef.current = onEvents;
  const [state, setState] = useState<MonitorState>({
    stream: null, result: null, tracking: null, aiVision: null, latency: null, lastFrameAt: null, error: null, starting: false,
  });

  const handleEvents = useCallback((evs: LiveEvent[]) => {
    if (!evs?.length) return;
    evs.filter((e) => e.type === "violation").forEach(queueVoice);
    onEventsRef.current(evs);
  }, []);

  // zone change: drop the old frame's boxes
  useEffect(() => {
    zoneRef.current = zoneId;
    setState((s) => ({ ...s, result: null, tracking: null, aiVision: null }));
  }, [zoneId]);

  const snapshot = () => {
    const v = video.current!;
    const c = (canvas.current ||= document.createElement("canvas"));
    const scale = Math.min(1, SNAPSHOT_MAX_WIDTH / v.videoWidth);
    c.width = Math.round(v.videoWidth * scale);
    c.height = Math.round(v.videoHeight * scale);
    c.getContext("2d")!.drawImage(v, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", 0.85);
  };

  const analyze = useCallback(async () => {
    const v = video.current;
    const zone = zoneRef.current;
    if (inFlight.current || !v || !v.videoWidth || !zone) return; // skip if previous call still running
    inFlight.current = true;
    const t0 = performance.now();
    try {
      const r = await post<AnalyzeResult>("/api/analyze", { image: snapshot(), zone_id: zone });
      if (r.zone_id !== zoneRef.current) return; // zone changed mid-request
      setState((s) => ({
        ...s, result: r, tracking: r.tracking, aiVision: r.ai_vision,
        latency: Math.round(performance.now() - t0), lastFrameAt: new Date(), error: null,
      }));
      handleEvents(r.events);
    } catch (e) {
      setState((s) => ({ ...s, error: `Analysis failed: ${(e as Error).message}` }));
    } finally {
      inFlight.current = false;
    }
  }, [handleEvents]);

  // analysis loop while streaming
  useEffect(() => {
    if (!state.stream) return;
    const t = window.setInterval(analyze, SNAPSHOT_MS);
    return () => window.clearInterval(t);
  }, [state.stream, analyze]);

  // camera off: poll tracking so timers + background notifications still surface
  useEffect(() => {
    if (state.stream || !zoneId) return;
    let alive = true;
    const poll = async () => {
      try {
        const r = await api<{ tracking: Record<string, Track>; events: LiveEvent[]; ai_vision: AiVision | null }>(
          `/api/tracking/${encodeURIComponent(zoneId)}`,
        );
        if (!alive || zoneRef.current !== zoneId) return;
        setState((s) => ({ ...s, tracking: r.tracking, aiVision: r.ai_vision }));
        handleEvents(r.events);
      } catch {
        /* server restarting: try again next tick */
      }
    };
    poll();
    const t = window.setInterval(poll, 2000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [state.stream, zoneId, handleEvents]);

  const start = useCallback(async () => {
    unlockAudio(); // inside the click: lets later voice warnings play (Safari)
    setState((s) => ({ ...s, starting: true, error: null }));
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 1280, height: 720 }, audio: false });
      if (!video.current) video.current = document.createElement("video");
      const v = video.current;
      v.muted = true;
      v.playsInline = true;
      v.srcObject = stream;
      await v.play();
      setState((s) => ({ ...s, stream, starting: false }));
    } catch (e) {
      setState((s) => ({
        ...s, starting: false,
        error: `Camera unavailable: ${(e as Error).message}. Allow camera access and use http://localhost.`,
      }));
    }
  }, []);

  const stop = useCallback(() => {
    setState((s) => {
      s.stream?.getTracks().forEach((t) => t.stop());
      if (video.current) video.current.srcObject = null;
      return { ...s, stream: null, result: null, latency: null };
    });
  }, []);

  useEffect(() => () => state.stream?.getTracks().forEach((t) => t.stop()), []); // eslint-disable-line react-hooks/exhaustive-deps

  return { ...state, start, stop, analyzeNow: analyze };
}
