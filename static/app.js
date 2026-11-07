// SafeSight frontend: webcam -> snapshot every 2s -> /api/analyze -> draw boxes.

const SNAPSHOT_MS = 2000;
const SNAPSHOT_MAX_WIDTH = 640; // downscale before upload: smaller payload, faster Rekognition

const GEAR_LABELS = { FACE_COVER: "face cover", HAND_COVER: "gloves", HEAD_COVER: "head protection" };
const COLORS = { ok: "#22c55e", violation: "#ef4444", unknown: "#f59e0b" };
const DEBOUNCE_FRAMES = 3; // keep in sync with config.py (display only)
const money = (n) => "$" + Number(n).toLocaleString();

const $ = (id) => document.getElementById(id);
const video = $("video");
const overlay = $("overlay");
const ctx = overlay.getContext("2d");
const snapCanvas = document.createElement("canvas");

let zones = [];
let stream = null;
let timer = null;
let inFlight = false;
let lastResult = null;

// ---------- helpers ----------
function escHtml(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const gearList = (gears) => gears.map((g) => GEAR_LABELS[g] || g).join(", ");

function showError(msg) {
  $("errorBanner").textContent = msg;
  $("errorBanner").classList.remove("hidden");
}
function clearError() {
  $("errorBanner").classList.add("hidden");
}

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

function currentZone() {
  return zones.find((z) => z.zone_id === $("zoneSelect").value);
}

// ---------- zones ----------
async function loadZones() {
  try {
    const health = await api("/api/health");
    $("mockBadge").classList.toggle("hidden", !health.mock_mode);
    $("demoBadge").classList.toggle("hidden", !health.demo_mode);
    const prev = $("zoneSelect").value;
    zones = await api("/api/zones");
    $("zoneSelect").innerHTML = zones
      .map((z) => `<option value="${esc(z.zone_id)}">${esc(z.name)}</option>`)
      .join("");
    if (zones.some((z) => z.zone_id === prev)) $("zoneSelect").value = prev; // keep selection across refreshes
    renderRequired();
    clearError();
  } catch (e) {
    showError(`Could not load zones: ${e.message}. Check AWS setup, or set MOCK_MODE=true in .env.`);
  }
}

function renderRequired() {
  const z = currentZone();
  if (!z) { $("requiredGear").innerHTML = ""; return; }
  const cam = z.required.map((g) => {
    const r = (z.rules || {})[g] || {};
    const tip = [r.item, r.rule, r.fine != null ? money(r.fine) : null].filter(Boolean).join(" · ");
    return `<span class="chip" title="${esc(tip)}">${esc(GEAR_LABELS[g] || g)}</span>`;
  });
  const off = (z.unmonitored || []).map((u) =>
    `<span class="chip off" title="Required by policy, not camera-monitored">${esc(u.item)}</span>`);
  $("requiredGear").innerHTML = cam.concat(off).join("") || `<span class="muted">No camera-checked gear</span>`;
}

// ---------- camera ----------
async function startCamera() {
  unlockAudio();
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { width: 1280, height: 720 }, audio: false });
  } catch (e) {
    showError(`Camera unavailable: ${e.message}. Allow camera access and use http://localhost.`);
    return;
  }
  video.srcObject = stream;
  await video.play();
  $("placeholder").classList.add("hidden");
  $("startBtn").textContent = "Stop camera";
  $("startBtn").classList.add("stop");
  resizeOverlay();
  timer = setInterval(captureAndAnalyze, SNAPSHOT_MS);
  captureAndAnalyze();
}

function stopCamera() {
  clearInterval(timer);
  timer = null;
  stream?.getTracks().forEach((t) => t.stop());
  stream = null;
  video.srcObject = null;
  lastResult = null;
  ctx.clearRect(0, 0, overlay.width, overlay.height);
  $("placeholder").classList.remove("hidden");
  $("startBtn").textContent = "Start camera";
  $("startBtn").classList.remove("stop");
  $("zoneStatus").className = "zone-status idle";
  $("zoneStatus").textContent = "Waiting…";
  $("personList").innerHTML = "";
}

function snapshot() {
  const scale = Math.min(1, SNAPSHOT_MAX_WIDTH / video.videoWidth);
  snapCanvas.width = Math.round(video.videoWidth * scale);
  snapCanvas.height = Math.round(video.videoHeight * scale);
  snapCanvas.getContext("2d").drawImage(video, 0, 0, snapCanvas.width, snapCanvas.height);
  return snapCanvas.toDataURL("image/jpeg", 0.85);
}

async function captureAndAnalyze() {
  if (inFlight || !stream || !video.videoWidth) return; // skip if previous call still running
  const zone = currentZone();
  if (!zone) return;
  inFlight = true;
  const t0 = performance.now();
  try {
    const result = await api("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image: snapshot(), zone_id: zone.zone_id }),
    });
    if (result.zone_id !== $("zoneSelect").value) return; // zone changed mid-request
    lastResult = result;
    clearError();
    drawBoxes();
    renderStatus();
    renderTracking(result.tracking);
    (result.events || []).forEach(showEvent);
    if (result.events?.length) { loadIncidents(); loadStats(); }
    $("frameInfo").textContent =
      `Last frame ${new Date().toLocaleTimeString()} · ${Math.round(performance.now() - t0)} ms` +
      (result.mock ? " · mock data" : "");
  } catch (e) {
    showError(`Analysis failed: ${e.message}`);
  } finally {
    inFlight = false;
  }
}

// ---------- drawing ----------
function resizeOverlay() {
  overlay.width = video.clientWidth;
  overlay.height = video.clientHeight;
  drawBoxes();
}

function drawBoxes() {
  ctx.clearRect(0, 0, overlay.width, overlay.height);
  if (!lastResult) return;
  const W = overlay.width, H = overlay.height;
  ctx.font = "600 14px system-ui, sans-serif";
  ctx.textBaseline = "top";

  // Background people (below MIN_PERSON_AREA): faint dashed box, not checked
  (lastResult.ignored || []).forEach((p) => {
    const x = p.box.left * W, y = p.box.top * H, w = p.box.width * W, h = p.box.height * H;
    ctx.save();
    ctx.setLineDash([6, 5]);
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = "rgba(139,147,167,.7)";
    ctx.strokeRect(x, y, w, h);
    ctx.font = "500 12px system-ui, sans-serif";
    ctx.fillStyle = "rgba(139,147,167,.9)";
    ctx.fillText("background (ignored)", x + 4, y + 4);
    ctx.restore();
  });

  lastResult.persons.forEach((p, i) => {
    // Rekognition boxes are 0-1 ratios of the image; scale to the canvas.
    const x = p.box.left * W, y = p.box.top * H, w = p.box.width * W, h = p.box.height * H;
    const color = COLORS[p.status];
    ctx.lineWidth = 3;
    ctx.strokeStyle = color;
    ctx.strokeRect(x, y, w, h);

    const label =
      p.status === "violation" ? `Person ${i + 1}: missing ${gearList(p.missing)}`
      : p.status === "unknown" ? `Person ${i + 1}: ${gearList(p.unknown)} not visible`
      : `Person ${i + 1}: OK`;
    const pad = 5, tw = ctx.measureText(label).width, th = 18;
    const ly = y - th - 2 * pad >= 0 ? y - th - 2 * pad : y; // above box, or inside if no room
    ctx.fillStyle = color;
    ctx.fillRect(x - 1.5, ly, tw + 2 * pad, th + 2 * pad);
    ctx.fillStyle = p.status === "unknown" ? "#1a1200" : "#fff";
    ctx.fillText(label, x + pad - 1.5, ly + pad + 1);
  });
}

function renderStatus() {
  const r = lastResult;
  const el = $("zoneStatus");
  if (r.persons.length === 0) {
    el.className = "zone-status idle";
    el.textContent = r.ignored?.length ? "Only background people (ignored)" : "No people detected";
  } else if (r.missing_gear.length) {
    el.className = "zone-status violation";
    el.textContent = `Missing: ${gearList(r.missing_gear)}`;
  } else if (r.persons.some((p) => p.status === "unknown")) {
    el.className = "zone-status unknown";
    el.textContent = "Some gear not visible";
  } else {
    el.className = "zone-status ok";
    el.textContent = "All compliant";
  }

  $("personList").innerHTML = r.persons
    .map((p, i) => {
      const sub =
        p.status === "violation" ? `Missing: ${gearList(p.missing)}`
        : p.status === "unknown" ? `Not visible: ${gearList(p.unknown)}`
        : `Wearing: ${gearList(p.worn)}`;
      return `<li class="${p.status}"><div>Person ${i + 1}</div><div class="sub">${sub}</div></li>`;
    })
    .join("");
}

// ---------- smart logic display ----------
function dots(n, total) {
  const k = Math.min(n, total);
  return "●".repeat(k) + "○".repeat(total - k);
}

function renderTracking(tracking) {
  if (!tracking) return;
  const row = (cls, left, right = "") => `<div class="sub tier ${cls}"><span>${left}</span><span>${right}</span></div>`;
  $("trackingList").innerHTML = Object.entries(tracking)
    .map(([gear, t]) => {
      const pill = t.open ? `<span class="pill open">OPEN</span>` : `<span class="pill clear">CLEAR</span>`;
      let body;
      if (!t.open) {
        body = row("next", `Missing <span class="dots">${dots(t.miss_streak, DEBOUNCE_FRAMES)}</span>`,
                   `voice warning at ${DEBOUNCE_FRAMES}`);
      } else {
        const lines = [row("done", `🔊 Warned${t.voice_count > 1 ? ` ×${t.voice_count}` : ""}`)];
        if (t.paused) lines.push(row("paused", "⏸ Camera offline", "timers paused"));
        lines.push(t.emailed ? row("done", "📧 Supervisor emailed")
          : row("next", t.resolving != null ? "📧 Email held" : `📧 Email in ${t.email_in}s`,
                t.resolving != null ? "resolving" : ""));
        lines.push(t.escalated ? row("done", "🚨 Escalated")
          : row("next", `🚨 Escalation in ${t.escalation_in}s`));
        if (t.resolving != null) {
          const done = Math.min(t.resolving, t.resolve_seconds);
          lines.push(row("done", `Resolving ${Math.floor(done)}/${t.resolve_seconds}s`) +
            `<div class="progress"><i style="width:${(100 * done) / t.resolve_seconds}%"></i></div>`);
        }
        body = lines.join("");
      }
      return `<li><div class="gear">${GEAR_LABELS[gear] || gear} ${pill}</div>${body}</li>`;
    })
    .join("");
}

// ---------- voice (tier 1) ----------
let muted = false;
try { muted = localStorage.getItem("safesight-muted") === "1"; } catch (_) {}
const voiceAudio = new Audio();
let audioUnlocked = false;

function renderMute() {
  $("muteBtn").textContent = muted ? "🔇 Muted" : "🔊 Voice on";
  $("muteBtn").setAttribute("aria-pressed", String(muted));
}
$("muteBtn").addEventListener("click", () => {
  muted = !muted;
  try { localStorage.setItem("safesight-muted", muted ? "1" : "0"); } catch (_) {}
  if (muted) { voiceAudio.pause(); window.speechSynthesis?.cancel(); }
  renderMute();
});
renderMute();

// Browsers (esp. Safari) only allow audio after a user gesture: prime both
// players inside the "Start camera" click so later warnings can play.
function silentWav() {
  // 0.05 s of 8 kHz mono silence: a valid tiny WAV built in-page.
  const n = 400, buf = new DataView(new ArrayBuffer(44 + n));
  const str = (o, t) => [...t].forEach((c, i) => buf.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF"); buf.setUint32(4, 36 + n, true); str(8, "WAVE"); str(12, "fmt ");
  buf.setUint32(16, 16, true); buf.setUint16(20, 1, true); buf.setUint16(22, 1, true);
  buf.setUint32(24, 8000, true); buf.setUint32(28, 8000, true); buf.setUint16(32, 1, true);
  buf.setUint16(34, 8, true); str(36, "data"); buf.setUint32(40, n, true);
  for (let i = 0; i < n; i++) buf.setUint8(44 + i, 128);
  return URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
}

function unlockAudio() {
  if (audioUnlocked) return;
  audioUnlocked = true;
  voiceAudio.src = silentWav();
  voiceAudio.play().catch(() => {});
  try { window.speechSynthesis?.speak(new SpeechSynthesisUtterance("")); } catch (_) {}
}

function speakFallback(text) {
  if (!window.speechSynthesis) return;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}

function playVoice(ev) {
  if (muted) return;
  const text = `Attention: ${ev.gear_label} required in the ${ev.zone_name}.`; // same phrase as config.VOICE_TEXT
  voiceAudio.onerror = () => speakFallback(text); // Polly failed / mock mode -> browser speech
  voiceAudio.src = `/api/voice/${encodeURIComponent(ev.zone_id)}/${encodeURIComponent(ev.gear)}`;
  voiceAudio.play().catch(() => speakFallback(text));
}

function toast(kind, title, sub) {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = `<div class="title"></div><div class="sub"></div>`;
  el.querySelector(".title").textContent = title;
  el.querySelector(".sub").textContent = sub;
  $("toasts").prepend(el);
  setTimeout(() => el.remove(), 8000);
  while ($("toasts").children.length > 4) $("toasts").lastChild.remove();
}

function showEvent(ev) {
  if (ev.type === "violation") {
    playVoice(ev);
    toast("violation", `${ev.zone_name}: missing ${ev.gear_label}`,
          `🔊 Voice warning · potential fine exposure ${money(ev.fine)}`);
  } else if (ev.type === "voice_repeat") {
    playVoice(ev);
  } else if (ev.type === "email") {
    toast("email", `📧 Supervisor emailed: ${ev.zone_name}`,
          ev.sent ? `Missing ${ev.gear_label} not fixed after ${ev.age}s` : `Email failed: ${ev.alert_error}`);
  } else if (ev.type === "escalation") {
    toast("escalation", `🚨 Escalated: ${ev.zone_name}`,
          ev.sent ? `Missing ${ev.gear_label} unresolved for ${ev.age}s` : `Email failed: ${ev.alert_error}`);
  } else if (ev.type === "resolved") {
    toast("resolved", `Resolved: ${ev.gear_label} in ${ev.zone_name}`,
          `Fixed in ${ev.seconds_to_fix}s${ev.emails_sent ? "" : " · no email needed"} · ` +
          `${money(ev.fine)} potential fine avoided`);
  } else if (ev.type === "fixed_late") {
    toast("expired", `Fixed late: ${ev.gear_label} in ${ev.zone_name}`,
          `After ${ev.seconds_to_fix}s · ${money(ev.fine)} exposure stands`);
  } else if (ev.type === "expired") {
    toast("expired", `Unresolved: ${ev.gear_label}`, `${ev.zone_name} · ${money(ev.fine)} exposure stays open`);
  } else if (ev.type === "error") {
    toast("error", "Logging problem", ev.message);
  }
}

// ---------- incident log ----------
const INCIDENT_POLL_MS = 4000;
const seenIncidents = new Set();
let firstIncidentLoad = true;

const esc = escHtml;
const fmtTime = (iso) => new Date(iso).toLocaleString([], {
  month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
const incidentUrl = (it) =>
  `/api/violations/${encodeURIComponent(it.zone_id)}/${encodeURIComponent(it.timestamp)}`;

async function loadIncidents() {
  let items;
  try {
    items = await api("/api/violations?limit=25");
  } catch (e) {
    return; // the analyze loop already surfaces AWS problems; don't double-banner
  }
  const tbody = $("incidentRows");
  if (!items.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty">No incidents yet.</td></tr>`;
    return;
  }
  tbody.innerHTML = items.map((it) => {
    const key = `${it.zone_id}|${it.timestamp}`;
    const isNew = !firstIncidentLoad && !seenIncidents.has(key);
    seenIncidents.add(key);
    const reportState = { READY: "View report", PENDING: "Generating…", FAILED: "Failed" }[it.report_status] || "—";
    return `<tr class="${isNew ? "flash" : ""}" data-zone="${esc(it.zone_id)}" data-ts="${esc(it.timestamp)}">
      <td>${esc(fmtTime(it.timestamp))}</td>
      <td>${esc(it.zone_name)}</td>
      <td>${esc(GEAR_LABELS[it.gear_type] || it.gear_type)}</td>
      <td>${esc(it.osha_rule)}</td>
      <td class="num">${money(it.fine)}</td>
      <td><span class="status ${esc(it.status)}">${esc(it.status)}</span></td>
      <td><span class="report-state ${esc(it.report_status)}">${reportState}</span></td>
    </tr>`;
  }).join("");
  firstIncidentLoad = false;
}

$("incidentRows").addEventListener("click", (e) => {
  const row = e.target.closest("tr[data-zone]");
  if (row) openIncident(row.dataset.zone, row.dataset.ts);
});

// ---------- report modal ----------
let modalTimer = null;

async function openIncident(zoneId, ts) {
  clearTimeout(modalTimer);
  let it;
  try {
    it = await api(incidentUrl({ zone_id: zoneId, timestamp: ts }));
  } catch (e) {
    showError(`Could not open incident: ${e.message}`);
    return;
  }
  const rule = it.osha_rule || "";
  $("modalTitle").textContent = `${it.zone_name}: missing ${GEAR_LABELS[it.gear_type] || it.gear_type}`;

  const facts = [
    ["When", fmtTime(it.timestamp)],
    ["Where", it.zone_name],
    ["What", `${it.people} ${it.people == 1 ? "person" : "people"} without ${GEAR_LABELS[it.gear_type] || it.gear_type}`],
    ["Rule", rule],
    ["Severity", it.severity],
    ["Potential fine exposure", money(it.fine)],
    ["Status", it.status === "RESOLVED" ? `Resolved in ${it.seconds_to_fix}s (fine avoided)`
      : it.fixed_at ? `Fixed late, after ${it.seconds_to_fix}s (exposure stands)` : "Open"],
    ["Escalation", [
      it.voice_warnings ? `voice warning${it.voice_warnings > 1 ? " ×" + it.voice_warnings : ""}` : null,
      it.emails_sent >= 1 ? "supervisor emailed" : null,
      it.emails_sent >= 2 ? "escalated" : null,
    ].filter(Boolean).join(" → ") || "—"],
  ];
  $("modalFacts").innerHTML = facts.map(([k, v]) => {
    const cls = k === "Potential fine exposure" ? `money ${it.status === "RESOLVED" ? "avoided" : ""}` : "";
    return `<dt>${esc(k)}</dt><dd class="${cls}">${esc(v)}</dd>`;
  }).join("") + (it.sample ? `<dt>Data</dt><dd>Sample (demo seed)</dd>` : "");

  renderNarrative(it);

  const img = $("modalPhoto");
  img.onload = () => drawModalBoxes(it);
  img.onerror = () => { img.alt = "Snapshot unavailable"; };
  img.src = incidentUrl(it) + "/image";

  $("modal").classList.remove("hidden");

  if (it.report_status === "PENDING") {
    modalTimer = setTimeout(() => { if (!$("modal").classList.contains("hidden")) openIncident(zoneId, ts); }, 1500);
  }
}

function renderNarrative(it) {
  const el = $("modalNarrative");
  if (it.report_status === "PENDING") {
    el.innerHTML = `<p class="source">Generating AI incident report…</p>`;
    return;
  }
  let r = null;
  try { r = it.report ? JSON.parse(it.report) : null; } catch (_) {}
  if (!r) {
    el.innerHTML = `<p class="source">Report unavailable.</p>`;
    return;
  }
  const sections = [["Summary", r.summary], ["Risk", r.risk], ["Immediate fix", r.fix], ["Prevention", r.prevention]];
  el.innerHTML = sections.filter(([, v]) => v)
    .map(([h, v]) => `<div><h4>${esc(h)}</h4><p>${esc(v)}</p></div>`).join("") +
    `<p class="source">Generated by ${esc(r.generated_by)}</p>`;
}

function drawModalBoxes(it) {
  const img = $("modalPhoto"), c = $("modalOverlay"), g = c.getContext("2d");
  c.width = img.clientWidth;
  c.height = img.clientHeight;
  g.clearRect(0, 0, c.width, c.height);
  let dets = [];
  try { dets = JSON.parse(it.detections || "[]"); } catch (_) {}
  g.lineWidth = 3;
  dets.forEach((d) => {
    g.strokeStyle = COLORS[d.status] || COLORS.unknown;
    g.strokeRect(d.box.left * c.width, d.box.top * c.height, d.box.width * c.width, d.box.height * c.height);
  });
}

function closeModal() {
  clearTimeout(modalTimer);
  $("modal").classList.add("hidden");
}
$("modalClose").addEventListener("click", closeModal);
$("modal").addEventListener("click", (e) => { if (e.target === $("modal")) closeModal(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeModal(); $("weeklyModal").classList.add("hidden"); }
});

// ---------- dashboard ----------
// Gear colors: validated categorical palette (blue, orange, aqua) for the dark surface.
// Red/green stay reserved for violation/compliant status.
const GEAR_COLORS = { HAND_COVER: "#3987e5", FACE_COVER: "#d95926", HEAD_COVER: "#199e70" };
const SURFACE = "#0b0d11";
const INK = "#c3c6d0", GRID = "#262b36";
const tz = () => new Date().getTimezoneOffset();
let hourChart = null, zoneChart = null;

if (window.Chart) {
  Chart.defaults.color = INK;
  Chart.defaults.font.family = "system-ui, -apple-system, 'Segoe UI', sans-serif";
  Chart.defaults.borderColor = GRID;
}

function baseOptions(stacked) {
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: { legend: { display: stacked, position: "bottom", labels: { boxWidth: 12, boxHeight: 12 } } },
    scales: {
      x: { stacked, grid: { display: false } },
      y: { stacked, beginAtZero: true, ticks: { precision: 0 }, grid: { color: GRID } },
    },
  };
}

function renderCharts(st) {
  if (!window.Chart) return; // vendor script failed to load; KPIs still work
  const hours = [...Array(24).keys()].map((h) => String(h).padStart(2, "0"));
  if (!hourChart) {
    const opts = baseOptions(false);
    opts.plugins.tooltip = { callbacks: {
      title: (i) => `${i[0].label}:00 – ${i[0].label}:59`,
      label: (i) => `${i.raw} violation${i.raw === 1 ? "" : "s"}` } };
    hourChart = new Chart($("hourChart"), {
      type: "bar",
      data: { labels: hours, datasets: [{ label: "Violations", data: st.by_hour,
        backgroundColor: GEAR_COLORS.HAND_COVER, borderRadius: 4, maxBarThickness: 18 }] },
      options: opts,
    });
  } else {
    hourChart.data.datasets[0].data = st.by_hour;
    hourChart.update();
  }

  const zones = Object.keys(st.by_zone);
  const datasets = Object.keys(GEAR_COLORS).map((g) => ({
    label: GEAR_LABELS[g],
    data: zones.map((z) => st.by_zone[z][g] || 0),
    backgroundColor: GEAR_COLORS[g],
    borderColor: SURFACE, borderWidth: { top: 2 }, borderSkipped: "bottom", // 2px gap between stacked segments
    borderRadius: 4, maxBarThickness: 56,
  }));
  if (!zoneChart) {
    zoneChart = new Chart($("zoneChart"), { type: "bar", data: { labels: zones, datasets }, options: baseOptions(true) });
  } else {
    zoneChart.data.labels = zones;
    zoneChart.data.datasets.forEach((d, i) => { d.data = datasets[i].data; });
    zoneChart.update();
  }
}

async function loadTracking() {
  if (stream) return; // while the camera runs, /api/analyze already returns tracking + events
  const zone = $("zoneSelect").value;
  if (!zone) return;
  try {
    const r = await api(`/api/tracking/${encodeURIComponent(zone)}`);
    renderTracking(r.tracking);
    r.events.forEach(showEvent);
  } catch (_) {}
}

async function loadStats() {
  let st;
  try { st = await api(`/api/stats?tz_offset=${tz()}`); } catch (_) { return; }
  const t = st.today;
  $("kpiExposure").textContent = money(t.fine_exposure);
  $("kpiExposureSub").textContent = `${t.open} open violation${t.open === 1 ? "" : "s"}`;
  $("kpiAvoided").textContent = money(t.fines_avoided);
  $("kpiAvoidedSub").textContent = `${t.resolved} fixed within 5 min`;
  $("kpiCount").textContent = t.violations;
  $("kpiCountSub").textContent = t.violations
    ? `${Math.round((100 * t.resolved) / t.violations)}% fixed quickly` : "\u00a0";
  const rz = st.riskiest_zone;
  $("kpiZone").textContent = rz ? rz.zone : "—";
  $("kpiZoneSub").textContent = rz
    ? `${rz.violations} violation${rz.violations === 1 ? "" : "s"} · ${money(rz.open_exposure)} open`
    : "No violations yet";
  $("sampleBadge").classList.toggle("hidden", !st.includes_sample);
  renderCharts(st);
}

// ---------- weekly report ----------
async function generateWeekly() {
  const btn = $("weeklyBtn");
  btn.disabled = true;
  btn.textContent = "Generating…";
  try {
    const r = await api(`/api/weekly-report?tz_offset=${tz()}`, { method: "POST" });
    const w = r.stats.week;
    const list = (arr, tag) => arr.length
      ? `<${tag}>${arr.map((x) => `<li>${esc(x)}</li>`).join("")}</${tag}>` : "<p>—</p>";
    $("weeklyBody").innerHTML = `
      <div class="weekly-stats">
        <div><b>${w.violations}</b><span>violations</span></div>
        <div><b>${w.violations ? Math.round((100 * w.resolved) / w.violations) : 0}%</b><span>fixed within 5 min</span></div>
        <div><b class="kpi-value bad" style="font-size:20px">${money(w.fine_exposure)}</b><span>potential fine exposure (open)</span></div>
        <div><b class="kpi-value good" style="font-size:20px">${money(w.fines_avoided)}</b><span>potential fines avoided</span></div>
      </div>
      <h4>Summary</h4><p>${esc(r.summary)}</p>
      <h4>Highlights</h4>${list(r.highlights, "ul")}
      <h4>Recommendations for next week</h4>${list(r.recommendations, "ol")}
      <p class="source" style="font-size:12px;color:var(--muted);margin-top:14px">
        Last 7 days · generated by ${esc(r.generated_by)}${r.stats.includes_sample ? " · includes sample data" : ""}</p>`;
    $("weeklyModal").classList.remove("hidden");
  } catch (e) {
    showError(`Weekly report failed: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Generate weekly report";
  }
}
$("weeklyBtn").addEventListener("click", generateWeekly);
$("weeklyClose").addEventListener("click", () => $("weeklyModal").classList.add("hidden"));
$("weeklyModal").addEventListener("click", (e) => {
  if (e.target === $("weeklyModal")) $("weeklyModal").classList.add("hidden");
});

// ---------- wiring ----------
$("startBtn").addEventListener("click", () => (stream ? stopCamera() : startCamera()));
$("zoneSelect").addEventListener("change", () => {
  lastResult = null;
  loadTracking();
  drawBoxes();
  renderRequired();
});
new ResizeObserver(resizeOverlay).observe(video);

loadZones();
// Zones published from /setup: refresh the dropdown live (other tab) or when this tab regains focus.
try { new BroadcastChannel("safesight").onmessage = (e) => { if (e.data === "zones-updated") loadZones(); }; } catch (_) {}
document.addEventListener("visibilitychange", () => { if (!document.hidden) loadZones(); });
loadIncidents();
loadStats();
setInterval(() => { loadIncidents(); loadStats(); }, INCIDENT_POLL_MS);
setInterval(loadTracking, 2000);
