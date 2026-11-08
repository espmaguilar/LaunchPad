// Voice warnings: ONE generalized announcement per zone, never one per item or person.
// Violations arriving within BATCH_MS are merged into one phrase ("face cover and gloves");
// a zone speaks at most once per COOLDOWN_MS - later items are announced together after it.
// Audio is Amazon Polly (/api/voice, cached server-side); falls back to browser speech.
import { GEAR_LABELS, GEAR_ORDER, isAi } from "./format";
import type { LiveEvent } from "./api";

const BATCH_MS = 2500;
const COOLDOWN_MS = 15000;

const audio = typeof Audio !== "undefined" ? new Audio() : null;
let unlocked = false;
let muted = false;
try {
  muted = localStorage.getItem("safesight-muted") === "1";
} catch {
  /* storage blocked */
}

type Q = { zoneName: string; gears: Set<string>; labels: Record<string, string>; timer: number | null; lastPlayed: number };
const queue: Record<string, Q> = {};

export const isMuted = () => muted;
export function setMuted(v: boolean) {
  muted = v;
  try {
    localStorage.setItem("safesight-muted", v ? "1" : "0");
  } catch {
    /* ignore */
  }
  if (v) {
    audio?.pause();
    window.speechSynthesis?.cancel();
  }
}

function silentWav() {
  const n = 400;
  const buf = new DataView(new ArrayBuffer(44 + n));
  const str = (o: number, t: string) => [...t].forEach((c, i) => buf.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF");
  buf.setUint32(4, 36 + n, true);
  str(8, "WAVE");
  str(12, "fmt ");
  buf.setUint32(16, 16, true);
  buf.setUint16(20, 1, true);
  buf.setUint16(22, 1, true);
  buf.setUint32(24, 8000, true);
  buf.setUint32(28, 8000, true);
  buf.setUint16(32, 1, true);
  buf.setUint16(34, 8, true);
  str(36, "data");
  buf.setUint32(40, n, true);
  for (let i = 0; i < n; i++) buf.setUint8(44 + i, 128);
  return URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
}

/** Browsers (esp. Safari) only allow audio after a user gesture: call from a click. */
export function unlockAudio() {
  if (unlocked || !audio) return;
  unlocked = true;
  audio.src = silentWav();
  audio.play().catch(() => {});
  try {
    window.speechSynthesis?.speak(new SpeechSynthesisUtterance(""));
  } catch {
    /* ignore */
  }
}

function joinLabels(gears: string[]) {
  const labels = GEAR_ORDER.filter((g) => gears.includes(g)).map((g) => GEAR_LABELS[g].toLowerCase());
  return labels.length === 1 ? labels[0] : `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}`;
}

/** Same text as voice.phrase() on the server. */
export function voiceText(zoneName: string, gears: string[], labels: Record<string, string>) {
  const ppe = gears.filter((g) => !isAi(g));
  const ai = gears.filter(isAi).map((g) => (labels[g] || g).replace(/\.$/, ""));
  const parts = ppe.length ? [`Attention: ${joinLabels(ppe)} required in the ${zoneName}.`] : [];
  if (ai.length)
    parts.push(`${ppe.length ? "" : `Attention, ${zoneName}. `}Safety rule${ai.length > 1 ? "s" : ""}: ${ai.join("; ")}.`);
  return parts.join(" ");
}

function speakFallback(text: string) {
  if (!window.speechSynthesis) return;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}

function speakZone(zoneId: string) {
  const q = queue[zoneId];
  const gears = [...q.gears];
  q.gears.clear();
  q.timer = null;
  q.lastPlayed = Date.now();
  if (muted || !gears.length || !audio) return;
  const ordered = GEAR_ORDER.filter((g) => gears.includes(g)).concat(gears.filter(isAi).sort());
  const text = voiceText(q.zoneName, ordered, q.labels);
  audio.onerror = () => speakFallback(text); // Polly failed / mock mode -> browser speech
  audio.src = `/api/voice/${encodeURIComponent(zoneId)}/${ordered.map(encodeURIComponent).join(",")}`;
  audio.play().catch(() => speakFallback(text));
}

export function queueVoice(ev: LiveEvent) {
  if (!ev.zone_id || !ev.gear) return;
  const q = (queue[ev.zone_id] ||= { zoneName: "", gears: new Set(), labels: {}, timer: null, lastPlayed: 0 });
  q.zoneName = ev.zone_name || q.zoneName;
  q.gears.add(ev.gear);
  q.labels[ev.gear] = ev.gear_label || ev.gear;
  if (q.timer) return; // already scheduled: joins the same announcement
  const delay = Math.max(BATCH_MS, q.lastPlayed + COOLDOWN_MS - Date.now());
  q.timer = window.setTimeout(() => speakZone(ev.zone_id!), delay);
}
