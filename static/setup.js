// SafeSight policy setup: upload PDF -> review/edit extracted zones -> approve.

const $ = (id) => document.getElementById(id);
const TYPES = [
  ["HAND_COVER", "Gloves (camera)"],
  ["FACE_COVER", "Face cover (camera)"],
  ["HEAD_COVER", "Head protection (camera)"],
  ["UNMONITORED", "Not camera-monitored"],
];
const SEVERITIES = ["", "Serious", "Willful", "Repeat", "Other-than-serious"];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "zone";

let draft = { policy_key: "", zones: [] };

function showError(msg) { $("errorBanner").textContent = msg; $("errorBanner").classList.remove("hidden"); }
function clearBanners() { $("errorBanner").classList.add("hidden"); $("okBanner").classList.add("hidden"); }

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json();
}

// ---------- upload ----------
$("pdfInput").addEventListener("change", () => {
  const f = $("pdfInput").files[0];
  $("fileLabel").textContent = f ? f.name : "Choose PDF…";
  $("extractBtn").disabled = !f;
});

const readAsDataURL = (blob) => new Promise((ok, fail) => {
  const r = new FileReader();
  r.onload = () => ok(r.result);
  r.onerror = () => fail(r.error);
  r.readAsDataURL(blob);
});

async function extract(blob, filename) {
  clearBanners();
  $("extractBtn").disabled = $("sampleBtn").disabled = true;
  $("progress").textContent = "Uploading to S3 and extracting text… then asking Bedrock to structure it (≈10–30 s).";
  try {
    const r = await api("/api/policy/upload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename, data: await readAsDataURL(blob) }),
    });
    draft = { policy_key: r.policy_key, zones: r.zones };
    $("extractMeta").textContent =
      `${r.filename} · ${r.text_chars.toLocaleString()} characters via ${r.extraction}` +
      (r.generated_by ? ` · structured by ${r.generated_by}` : "") + ` · stored at ${r.policy_key}`;
    $("warnings").innerHTML = r.warnings.map((w) => `<li>${esc(w)}</li>`).join("");
    $("textPreview").textContent = r.text_preview;
    $("reviewCard").classList.remove("hidden");
    $("approveCard").classList.remove("hidden");
    $("progress").textContent = r.zones.length
      ? `Found ${r.zones.length} zone${r.zones.length === 1 ? "" : "s"}. Review below - nothing is live yet.`
      : "No zones extracted. Add them manually below.";
    renderZones();
    $("reviewCard").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    $("progress").textContent = "";
    showError(`Policy extraction failed: ${e.message}`);
  } finally {
    $("extractBtn").disabled = !$("pdfInput").files[0];
    $("sampleBtn").disabled = false;
  }
}

$("extractBtn").addEventListener("click", () => {
  const f = $("pdfInput").files[0];
  if (f) extract(f, f.name);
});
$("sampleBtn").addEventListener("click", async () => {
  try {
    const res = await fetch("/api/policy/sample");
    if (!res.ok) throw new Error("sample PDF not found - run python demo/make_sample_policy.py");
    extract(await res.blob(), "sample_safety_policy.pdf");
  } catch (e) { showError(e.message); }
});

// ---------- review table ----------
function gearRow(zi, gi, g) {
  const off = g.gear_type === "UNMONITORED";
  const src = g.fine_source || "";
  return `<tr class="${off ? "off" : ""}" data-z="${zi}" data-g="${gi}">
    <td><input type="text" data-f="item" value="${esc(g.item)}" aria-label="PPE item"></td>
    <td><select data-f="gear_type" aria-label="Camera check">${TYPES.map(([v, l]) =>
      `<option value="${v}" ${v === g.gear_type ? "selected" : ""}>${l}</option>`).join("")}</select></td>
    <td><input type="text" data-f="rule" value="${esc(g.rule)}" placeholder="Rule reference" aria-label="Rule reference"></td>
    <td><select data-f="severity" aria-label="Severity">${SEVERITIES.map((s) =>
      `<option value="${s}" ${s === (g.severity || "") ? "selected" : ""}>${s || "—"}</option>`).join("")}</select></td>
    <td><input type="number" min="0" step="1" data-f="fine" value="${g.fine ?? ""}" placeholder="${off ? "—" : "OSHA default"}" aria-label="Potential fine (USD)"></td>
    <td><span class="src ${src === "policy" ? "policy" : ""}">${esc(src === "policy" ? "from policy" : src)}</span></td>
    <td><button class="icon-btn" data-act="del-gear" aria-label="Remove item">×</button></td>
  </tr>`;
}

function renderZones() {
  $("zones").innerHTML = draft.zones.map((z, zi) => `
    <div class="zone-card" data-z="${zi}">
      <div class="zone-head">
        <label class="field">Zone name
          <input type="text" data-zf="name" value="${esc(z.name)}"></label>
        <label class="field">Supervisor email (alerts)
          <input type="email" data-zf="alert_email" value="${esc(z.alert_email)}"></label>
        <button class="icon-btn" data-act="del-zone" aria-label="Remove zone" title="Remove zone">🗑</button>
      </div>
      <div class="zone-id">zone id: ${esc(slug(z.name || ""))}</div>
      <div class="table-wrap"><table class="gear-table">
        <thead><tr><th class="c-item">Required PPE</th><th class="c-type">Camera check</th><th class="c-rule">Rule reference</th>
          <th class="c-sev">Severity</th><th class="c-fine">Potential fine ($)</th><th class="c-src">Source</th><th></th></tr></thead>
        <tbody>${z.gear.map((g, gi) => gearRow(zi, gi, g)).join("")}</tbody>
      </table></div>
      <button class="link-btn" data-act="add-gear">+ Add PPE item</button>
    </div>`).join("");
}

// One delegated listener keeps `draft` in sync with every edit.
$("zones").addEventListener("input", (e) => {
  const el = e.target;
  const card = el.closest(".zone-card");
  if (!card) return;
  const z = draft.zones[+card.dataset.z];
  if (el.dataset.zf) {
    z[el.dataset.zf] = el.value;
    if (el.dataset.zf === "name") card.querySelector(".zone-id").textContent = `zone id: ${slug(el.value)}`;
    return;
  }
  const row = el.closest("tr");
  if (!row || !el.dataset.f) return;
  const g = z.gear[+row.dataset.g];
  if (el.dataset.f === "fine") {
    g.fine = el.value === "" ? null : Math.round(+el.value);
    g.fine_source = el.value === "" ? (g.gear_type === "UNMONITORED" ? "" : "OSHA default") : "edited";
    row.querySelector(".src").textContent = g.fine_source;
    row.querySelector(".src").classList.remove("policy");
  } else {
    g[el.dataset.f] = el.value;
    if (el.dataset.f === "gear_type") row.classList.toggle("off", el.value === "UNMONITORED");
  }
});

$("zones").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-act]");
  if (!btn) return;
  const zi = +btn.closest(".zone-card").dataset.z;
  if (btn.dataset.act === "del-zone") draft.zones.splice(zi, 1);
  if (btn.dataset.act === "add-gear") draft.zones[zi].gear.push(
    { item: "", gear_type: "HAND_COVER", rule: "", severity: "Serious", fine: null, fine_source: "OSHA default" });
  if (btn.dataset.act === "del-gear") draft.zones[zi].gear.splice(+btn.closest("tr").dataset.g, 1);
  renderZones();
});

$("addZoneBtn").addEventListener("click", () => {
  draft.zones.push({ name: "", alert_email: "", gear: [] });
  renderZones();
});

// ---------- approve ----------
$("approveBtn").addEventListener("click", async () => {
  clearBanners();
  $("approveBtn").disabled = true;
  try {
    const r = await api("/api/policy/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ policy_key: draft.policy_key, zones: draft.zones, replace_existing: $("replaceChk").checked }),
    });
    const names = r.zones.map((z) => `${z.name} (${z.required.length} camera-checked)`).join(", ");
    $("okBanner").innerHTML = `Published ${r.zones.length} zone${r.zones.length === 1 ? "" : "s"}: ${esc(names)}. ` +
      `<a class="nav-link" href="/">Open live monitor →</a>`;
    $("okBanner").classList.remove("hidden");
    try { new BroadcastChannel("safesight").postMessage("zones-updated"); } catch (_) {}
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (e) {
    showError(`Not published: ${e.message}`);
  } finally {
    $("approveBtn").disabled = false;
  }
});
