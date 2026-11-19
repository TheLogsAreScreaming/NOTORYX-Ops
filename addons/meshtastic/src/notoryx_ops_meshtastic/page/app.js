// SPDX-License-Identifier: GPL-3.0-only
// Meshtastic add-on page for NOTORYX Ops. Plain JavaScript, no build step.
"use strict";

const API = "/api/addons/meshtastic";
const $ = (id) => document.getElementById(id);
let status = null;
let nodes = [];
let current = null; // { num, name }
let lastRead = null; // { section, values }
const picked = new Set();

function token() {
  try { return localStorage.getItem("notoryx-ops-token") || ""; } catch { return ""; }
}

async function api(path, opts = {}) {
  const headers = {};
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.method && opts.method !== "GET" && token()) headers.Authorization = `Bearer ${token()}`;
  const r = await fetch(API + path, { method: opts.method || "GET", headers, body: opts.body === undefined ? undefined : JSON.stringify(opts.body) });
  if (r.status === 401) { location.href = "/"; throw new Error("Sign in first."); }
  let data = null;
  try { data = await r.json(); } catch { /* empty */ }
  if (!r.ok) throw new Error((data && (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail))) || `${r.status}`);
  return data;
}

function text(el, s, cls) { el.textContent = s; el.className = cls || el.className; }
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function ago(t) {
  if (!t) return "—";
  const s = Date.now() / 1000 - t;
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} d`;
}
function say(el, msg, ok = true) { el.textContent = msg; el.className = `small ${ok ? "okmsg" : "err"}`; }

// ---- status -----------------------------------------------------------------------
async function loadStatus() {
  status = await api("/status");
  const r = status.radio;
  const st = $("state");
  st.textContent = { connected: "Connected", connecting: "Connecting…", disconnected: "Disconnected", off: "Off" }[r.state] || r.state;
  st.className = `pill ${r.state === "connected" ? "ok" : r.state === "off" ? "" : "warn"}`;
  const me = r.me;
  $("me").innerHTML = me
    ? `<strong>${esc(me.long_name || me.id)}</strong> <span class="faint">${esc(me.id)} · ${esc(me.hw_model || "")}${me.firmware ? " · fw " + esc(me.firmware) : ""}</span>
       <div style="margin-top:6px">Public key (add this under Security → Admin keys on nodes you manage):</div>
       <div class="row"><span class="mono">${esc(me.public_key || "not available")}</span>${me.public_key ? '<button id="copy-key">Copy</button>' : ""}</div>`
    : (r.error ? `<span class="err">${esc(r.error)}</span>` : "Not connected.");
  if ($("copy-key")) $("copy-key").onclick = () => navigator.clipboard.writeText(me.public_key);
  const s = status.settings;
  if (!$("transport").dataset.loaded) {
    $("transport").value = s.transport || "none";
    $("serial_port").value = s.serial_port || "";
    $("tcp_host").value = s.tcp_host || "";
    $("ble_address").value = s.ble_address || "";
    $("bridge_enabled").checked = !!s.bridge_enabled;
    $("bridge_per_minute").value = s.bridge_per_minute || 6;
    $("bridge_pairs").value = (s.bridge_pairs || []).join("\n");
    $("transport").dataset.loaded = "1";
    showTransport();
    $("m-section").innerHTML = status.sections.map((x) => `<option>${esc(x)}</option>`).join("");
  }
  $("bridge-hint").textContent = `MeshCore channels on this station: ${status.meshcore_channels.join(", ") || "none"}.`;
  const b = status.bridge.stats;
  $("bridge-stats").textContent = `Sent to MeshCore ${b.to_meshcore} · to Meshtastic ${b.to_meshtastic} · held back by the limit ${b.dropped_rate} · failed ${b.dropped_error}` +
    (status.bridge.last_error ? ` · last error: ${status.bridge.last_error}` : "");
}

function showTransport() {
  const t = $("transport").value;
  $("f-serial").hidden = t !== "serial";
  $("f-tcp").hidden = t !== "tcp";
  $("f-ble").hidden = t !== "ble";
}
$("transport").onchange = showTransport;

$("save-radio").onclick = async () => {
  try {
    await api("/settings", { method: "PUT", body: { transport: $("transport").value, serial_port: $("serial_port").value, tcp_host: $("tcp_host").value, ble_address: $("ble_address").value } });
    say($("radio-msg"), "Saved. Connecting…");
    setTimeout(loadStatus, 2500);
  } catch (e) { say($("radio-msg"), e.message, false); }
};

$("save-bridge").onclick = async () => {
  try {
    const pairs = $("bridge_pairs").value.split("\n").map((x) => x.trim()).filter(Boolean);
    await api("/settings", { method: "PUT", body: { bridge_enabled: $("bridge_enabled").checked, bridge_pairs: pairs, bridge_per_minute: Number($("bridge_per_minute").value) || 6 } });
    say($("bridge-msg"), $("bridge_enabled").checked ? "Saved. The bridge is on." : "Saved. The bridge is off.");
    loadStatus();
  } catch (e) { say($("bridge-msg"), e.message, false); }
};

// ---- nodes -----------------------------------------------------------------------
async function loadNodes() {
  nodes = await api("/nodes");
  $("node-count").textContent = `${nodes.length} heard`;
  $("nodes").innerHTML = nodes.map((n) => `
    <tr class="${current && current.num === n.num ? "sel" : ""}">
      <td><label class="row"><input type="checkbox" data-pick="${n.num}" ${picked.has(n.num) ? "checked" : ""}> ${esc(n.long_name || n.short_name || n.id)}</label></td>
      <td class="mono">${esc(n.id)}</td><td>${esc(n.hw_model || "")}</td><td>${esc(n.role || "")}</td>
      <td>${ago(n.last_heard)}</td><td>${n.snr ?? ""}</td><td>${n.hops ?? ""}</td>
      <td>${n.has_key ? '<span class="pill ok">PKI</span>' : '<span class="pill faint">none</span>'}</td>
      <td><button data-manage="${n.num}">Manage</button></td>
    </tr>`).join("") || '<tr><td colspan="9" class="faint">No nodes yet. Connect the station radio above.</td></tr>';
  $("m-fav-target").innerHTML = nodes.map((n) => `<option value="${n.num}">${esc(n.long_name || n.id)}</option>`).join("");
}

$("nodes").onclick = (e) => {
  const m = e.target.closest("[data-manage]");
  if (m) {
    const n = nodes.find((x) => x.num === Number(m.dataset.manage));
    current = { num: n.num, name: n.long_name || n.id };
    $("manage").hidden = false;
    $("m-name").textContent = `· ${current.name} (${n.id})`;
    $("m-long").value = n.long_name || "";
    $("m-short").value = n.short_name || "";
    $("m-fields").innerHTML = "";
    $("m-actions").hidden = true;
    $("m-diff").textContent = "";
    $("m-meta-out").textContent = "";
    $("manage").scrollIntoView({ behavior: "smooth" });
    loadNodes();
  }
  const p = e.target.closest("[data-pick]");
  if (p) {
    const num = Number(p.dataset.pick);
    if (p.checked) picked.add(num); else picked.delete(num);
    $("picked").textContent = picked.size;
  }
};

// ---- manage one node -------------------------------------------------------------------
async function busy(label, f) {
  $("m-busy").textContent = label;
  try { return await f(); } finally { $("m-busy").textContent = ""; }
}

$("m-meta").onclick = async () => {
  try {
    const m = await busy("Asking the node…", () => api(`/nodes/${current.num}/metadata`));
    say($("m-meta-out"), `Firmware ${m.firmware_version || "?"} · ${m.hw_model || ""} · ${m.remote_admin_ok ? "remote admin supported" : "too old for remote admin (needs 2.5+)"}`, m.remote_admin_ok);
  } catch (e) { say($("m-meta-out"), e.message, false); }
};

$("m-read").onclick = async () => {
  const section = $("m-section").value;
  try {
    const values = await busy("Reading over the air…", () => api(`/nodes/${current.num}/config/${section}`));
    lastRead = { section, values };
    $("m-fields").innerHTML = Object.entries(values).map(([k, v]) => {
      const complex = typeof v === "object" && v !== null;
      return `<span class="small">${esc(k)}</span>` + (complex
        ? `<span class="small faint mono">${esc(JSON.stringify(v))}</span>`
        : `<input data-field="${esc(k)}" data-orig="${esc(v)}" value="${esc(v)}" ${v === "(hidden)" ? "disabled" : ""}>`);
    }).join("");
    $("m-actions").hidden = false;
    $("m-apply").disabled = true;
    $("m-diff").textContent = "";
  } catch (e) { say($("m-msg"), e.message, false); }
};

function edits() {
  const out = {};
  document.querySelectorAll("#m-fields input[data-field]").forEach((i) => {
    if (i.value !== i.dataset.orig) out[i.dataset.field] = i.value;
  });
  return out;
}

$("m-preview").onclick = async () => {
  const changes = edits();
  if (!Object.keys(changes).length) return say($("m-diff"), "Nothing changed yet.", false);
  try {
    const r = await busy("Checking against the node…", () => api(`/nodes/${current.num}/config/${lastRead.section}`, { method: "POST", body: { changes } }));
    $("m-diff").innerHTML = r.changes.length
      ? "Will change: " + r.changes.map((d) => `<span class="mono">${esc(d.field)}</span> ${esc(d.before)} → <strong>${esc(d.after)}</strong>`).join(" · ")
      : "The node already has these values.";
    $("m-apply").disabled = !r.changes.length;
  } catch (e) { say($("m-diff"), e.message, false); }
};

$("m-apply").onclick = async () => {
  if (!confirm(`Change ${lastRead.section} settings on ${current.name}? Some changes restart the node.`)) return;
  try {
    const r = await busy("Writing over the air…", () => api(`/nodes/${current.num}/config/${lastRead.section}`, { method: "POST", body: { changes: edits(), confirm: true } }));
    say($("m-diff"), r.applied ? `Done: ${r.changes.length} setting(s) changed.` : "Nothing to change.");
    $("m-apply").disabled = true;
  } catch (e) { say($("m-diff"), e.message, false); }
};

$("m-template").onclick = async () => {
  const changes = edits();
  if (!Object.keys(changes).length) return say($("m-diff"), "Change some fields first; those become the template.", false);
  const name = prompt("Template name");
  if (!name) return;
  try {
    await api("/templates", { method: "POST", body: { name, section: lastRead.section, fields: changes } });
    say($("m-diff"), `Template ${name} saved.`);
    loadTemplates();
  } catch (e) { say($("m-diff"), e.message, false); }
};

$("m-owner").onclick = async () => {
  if (!confirm(`Rename ${current.name}?`)) return;
  try {
    await busy("Renaming…", () => api(`/nodes/${current.num}/owner`, { method: "POST", body: { long_name: $("m-long").value || null, short_name: $("m-short").value || null, confirm: true } }));
    say($("m-msg"), "Renamed. The new name spreads with the node's next info broadcast.");
  } catch (e) { say($("m-msg"), e.message, false); }
};

async function fav(on) {
  const target = Number($("m-fav-target").value);
  try {
    await busy("Sending…", () => api(`/nodes/${current.num}/favorite`, { method: "POST", body: { target, on } }));
    say($("m-msg"), on ? "Favourite set." : "Favourite removed.");
  } catch (e) { say($("m-msg"), e.message, false); }
}
$("m-fav").onclick = () => fav(true);
$("m-unfav").onclick = () => fav(false);

$("m-reboot").onclick = async () => {
  if (!confirm(`Reboot ${current.name}? It will be off the mesh for a minute or so.`)) return;
  try {
    await busy("Sending…", () => api(`/nodes/${current.num}/reboot`, { method: "POST", body: { seconds: 10, confirm: true } }));
    say($("m-msg"), "Reboot sent. It restarts in about 10 seconds.");
  } catch (e) { say($("m-msg"), e.message, false); }
};

// ---- templates ---------------------------------------------------------------------------
async function loadTemplates() {
  const t = await api("/templates");
  $("templates").innerHTML = t.map((x) => `
    <tr><td>${esc(x.name)}</td><td>${esc(x.section)}</td>
      <td class="mono small">${esc(Object.entries(x.fields).map(([k, v]) => `${k}=${v}`).join(", "))}</td>
      <td class="row"><button data-check="${x.id}">Check picked nodes</button><button data-apply="${x.id}">Apply to picked nodes</button><button class="danger" data-del="${x.id}">Delete</button></td></tr>`).join("")
    || '<tr><td colspan="4" class="faint">No templates yet. Read a node\'s settings, change some fields and save them as a template.</td></tr>';
}

async function watch(job) {
  const out = $("job");
  for (;;) {
    const j = await api(`/jobs/${job.id}`);
    const lines = j.results.map((r) => {
      const n = nodes.find((x) => x.num === r.node);
      const name = esc(n ? n.long_name || n.id : r.node);
      if (!r.ok) return `<div>${name}: <span class="err">${esc(r.error)}</span></div>`;
      if (j.kind === "check") return `<div>${name}: ${r.compliant ? '<span class="pill ok">matches</span>' : '<span class="pill warn">differs</span> ' + r.differences.map((d) => `${esc(d.field)} ${esc(d.before)} → ${esc(d.after)}`).join(", ")}</div>`;
      return `<div>${name}: <span class="pill ok">${r.changed.length} changed</span></div>`;
    });
    out.innerHTML = `<div>${j.kind === "check" ? "Checking" : "Applying"}: ${j.done}/${j.total} · ${esc(j.state)}${j.error ? " · " + esc(j.error) : ""}</div>` + lines.join("");
    if (j.state === "done" || j.state === "failed") return;
    await new Promise((r) => setTimeout(r, 2000));
  }
}

$("templates").onclick = async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  const list = [...picked];
  try {
    if (b.dataset.del) {
      await api(`/templates/${b.dataset.del}`, { method: "DELETE" });
      return loadTemplates();
    }
    if (!list.length) return say($("job"), "Tick some nodes in the Nodes table first.", false);
    if (b.dataset.check) return watch(await api(`/templates/${b.dataset.check}/check`, { method: "POST", body: { nodes: list } }));
    if (b.dataset.apply) {
      if (!confirm(`Apply this template to ${list.length} node(s), one at a time? Some changes restart nodes.`)) return;
      return watch(await api(`/templates/${b.dataset.apply}/apply`, { method: "POST", body: { nodes: list, confirm: true } }));
    }
  } catch (err) { say($("job"), err.message, false); }
};

// ---- start -----------------------------------------------------------------------------
async function refresh() {
  try { await loadStatus(); await loadNodes(); } catch (e) { $("me").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
}
refresh();
loadTemplates().catch(() => {});
setInterval(refresh, 15000);
