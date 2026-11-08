// Voice guidance: Amazon Polly (/api/voice, cached server-side) with a browser-speech
// fallback, a mute toggle, and the Safari audio unlock. Step announcements use speak().
const audio = typeof Audio !== "undefined" ? new Audio() : null;
let unlocked = false;
let muted = false;
try {
  muted = localStorage.getItem("labscribe-muted") === "1";
} catch {
  /* storage blocked */
}

export const isMuted = () => muted;

// ---- speaking state: detection pauses while LabScribe is talking (user: "detection only works after speech") ----
let speaking = false;
let speakTimer = 0;
const speechListeners = new Set<(on: boolean) => void>();
export const isSpeaking = () => speaking;
export function onSpeaking(fn: (on: boolean) => void) {
  speechListeners.add(fn);
  return () => { speechListeners.delete(fn); };
}
function setSpeaking(on: boolean, text = "") {
  window.clearTimeout(speakTimer);
  // safety net: never stay "speaking" forever if an audio event is lost (~ 70 ms per character + 4 s)
  if (on) speakTimer = window.setTimeout(() => setSpeaking(false), 4000 + text.length * 70);
  if (on === speaking) return;
  speaking = on;
  speechListeners.forEach((fn) => fn(on));
}
if (audio) {
  audio.addEventListener("ended", () => setSpeaking(false));
}
export function setMuted(v: boolean) {
  muted = v;
  try {
    localStorage.setItem("labscribe-muted", v ? "1" : "0");
  } catch {
    /* ignore */
  }
  if (v) {
    audio?.pause();
    window.speechSynthesis?.cancel();
    setSpeaking(false);
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

function speakFallback(text: string) {
  if (!window.speechSynthesis) { setSpeaking(false); return; }
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.onend = () => setSpeaking(false);
  u.onerror = () => setSpeaking(false);
  setSpeaking(true, text);
  window.speechSynthesis.speak(u);
}

/** Speak a guidance phrase (Polly, cached; browser voice if Polly is unavailable). */
export function speak(text: string, force = false) {
  if ((muted && !force) || !audio || !text) return;
  // Polly can fail two ways at once (load error + rejected play()): fall back only once.
  let fellBack = false;
  const fallback = () => { if (!fellBack) { fellBack = true; speakFallback(text); } };
  audio.onerror = fallback;
  setSpeaking(true, text);   // from the moment we ask (includes loading the audio) until it ends
  audio.src = `/api/voice?text=${encodeURIComponent(text)}`;
  audio.play().catch(fallback);
}
