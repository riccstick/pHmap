"use strict";

const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="phmap-token"]').content;
const terminal = new Set(["completed", "failed", "cancelled", "interrupted"]);
const stageNames = { pdb2pqr: "Preparing protonation", apbs: "Calculating potential", pymol: "Rendering surface" };
let proteins = [];
let selected = null;
let stream = null;
let toolsReady = false;
let busy = false;
let refreshTimer = null;
let lastLogPaths = "";
let selectedStatus = null;

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

async function api(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.method && options.method !== "GET") headers["X-Phmap-Token"] = token;
  const response = await fetch(url, { ...options, headers });
  const data = await response.json();
  if (!response.ok) {
    const message = typeof data.detail === "string" ? data.detail : "The request could not be completed.";
    throw new Error(message);
  }
  return data;
}

function message(text, error = false) {
  const element = $("form-message");
  element.textContent = text;
  element.className = error ? "error" : "success";
  element.hidden = !text;
}

function updateButtons() {
  $("submit").disabled = busy || !proteins.length || !toolsReady;
  $("validate").disabled = busy || !proteins.length;
  $("submit").textContent = busy ? "Submitting…" : "Start calculation →";
}

function setProteins(files) {
  proteins = Array.from(files).map((file) => ({ file, label: file.name.replace(/\.pdb$/i, "") }));
  renderProteins();
  message("");
}

function renderProteins() {
  const list = $("protein-list");
  list.replaceChildren();
  proteins.forEach((protein, index) => {
    const row = node("div", undefined, "protein-item");
    const filename = node("span", `${index + 1}. ${protein.file.name}`, "filename");
    filename.title = protein.file.name;
    const label = node("input");
    label.type = "text";
    label.value = protein.label;
    label.maxLength = 128;
    label.setAttribute("aria-label", `Row label for ${protein.file.name}`);
    label.addEventListener("input", () => { protein.label = label.value; });
    const controls = node("div", undefined, "protein-controls");
    const up = node("button", "↑");
    up.type = "button";
    up.title = "Move protein up";
    up.setAttribute("aria-label", `Move ${protein.file.name} up`);
    up.disabled = index === 0;
    up.onclick = () => {
      [proteins[index - 1], proteins[index]] = [proteins[index], proteins[index - 1]];
      renderProteins();
    };
    const down = node("button", "↓");
    down.type = "button";
    down.title = "Move protein down";
    down.setAttribute("aria-label", `Move ${protein.file.name} down`);
    down.disabled = index === proteins.length - 1;
    down.onclick = () => {
      [proteins[index + 1], proteins[index]] = [proteins[index], proteins[index + 1]];
      renderProteins();
    };
    const remove = node("button", "×");
    remove.type = "button";
    remove.title = "Remove protein";
    remove.setAttribute("aria-label", `Remove ${protein.file.name}`);
    remove.onclick = () => { proteins.splice(index, 1); renderProteins(); };
    controls.append(up, down, remove);
    row.append(filename, label, controls);
    list.append(row);
  });
  $("order-hint").hidden = !proteins.length;
  updateCount();
  updateButtons();
}

function phMode() {
  return document.querySelector('input[name="ph-mode"]:checked').value;
}

function updateCount() {
  let count = 0;
  if (phMode() === "range") {
    const start = Number($("ph-start").value);
    const stop = Number($("ph-stop").value);
    const step = Number($("ph-step").value);
    if (step > 0 && stop >= start && start >= 0 && stop <= 14) {
      count = Math.floor((stop - start) / step + 1e-9) + 1;
    }
  } else {
    count = $("ph-values").value.trim().split(/[\s,]+/).filter(Boolean).length;
  }
  const cells = proteins.length * count;
  $("calculation-count").textContent = cells
    ? `${proteins.length} protein${proteins.length === 1 ? "" : "s"} × ${count} pH value${count === 1 ? "" : "s"} = ${cells} calculations`
    : "Choose proteins and valid pH values to preview your calculation.";
}

function formData() {
  const background = $("background").value;
  const settings = {
    name: $("run-name").value,
    ph_mode: phMode(), ph_start: $("ph-start").value, ph_stop: $("ph-stop").value,
    ph_step: $("ph-step").value, ph_values: $("ph-values").value,
    labels: proteins.map((p) => p.label), size: Number($("size").value),
    dpi: Number($("dpi").value), level: Number($("level").value),
    background: background === "transparent" ? null : background,
    foreground: background === "black" ? "white" : "black",
    ramp_colors: $("ramp").value.split(","), force_field: $("force-field").value,
    ray_trace: $("ray").checked,
  };
  const data = new FormData();
  data.append("settings", JSON.stringify(settings));
  proteins.forEach((p) => data.append("proteins", p.file, p.file.name));
  if ($("view").files.length) data.append("view", $("view").files[0]);
  if ($("ligand").files.length) data.append("ligand", $("ligand").files[0]);
  return data;
}

async function send(validateOnly) {
  if (!proteins.length || busy) return;
  if (proteins.some((p) => !/\.pdb$/i.test(p.file.name))) {
    message("All protein structures must be .pdb files.", true);
    return;
  }
  if (!validateOnly && !toolsReady) {
    message("Resolve the missing calculation tools before starting a run.", true);
    return;
  }
  busy = true;
  updateButtons();
  message("");
  try {
    const result = await api(validateOnly ? "/api/validate" : "/api/runs", {
      method: "POST", body: formData(),
    });
    if (validateOnly) {
      message(`Inputs are valid. ${result.total} calculations at pH ${result.ph_values.join(", ")}.`);
    } else {
      message("Calculation queued. You can prepare another while this one runs.");
      await refreshHistory();
      await selectRun(result.run_id);
    }
  } catch (error) {
    message(error.message, true);
  } finally {
    busy = false;
    updateButtons();
  }
}

async function checkTools() {
  $("recheck-tools").disabled = true;
  $("toolchain-title").textContent = "Checking calculation tools…";
  $("toolchain-dot").className = "status-dot loading";
  try {
    const data = await api("/api/doctor");
    toolsReady = data.ready;
    $("toolchain-title").textContent = data.ready ? "Calculation tools are ready" : "Some calculation tools need attention";
    $("toolchain-dot").className = data.ready ? "status-dot" : "status-dot failed";
    const chips = data.checks.map((check) => {
      const chip = node("span", `${check.ok ? "✓" : "!"} ${check.name}`, `tool-chip${check.ok ? "" : " failed"}`);
      chip.title = [check.version, check.message, check.path].filter(Boolean).join(" · ");
      if (!check.ok) chip.textContent += `: ${check.message}`;
      return chip;
    });
    $("toolchain-checks").replaceChildren(...chips);
  } catch (error) {
    toolsReady = false;
    $("toolchain-title").textContent = error.message;
    $("toolchain-dot").className = "status-dot failed";
  } finally {
    $("recheck-tools").disabled = false;
    updateButtons();
  }
}

function formatDate(value) {
  if (!value) return "";
  return new Date(value).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

async function refreshHistory() {
  try {
    const history = await api("/api/runs");
    const buttons = history.map((run) => {
      const button = node("button", undefined, `history-item${run.run_id === selected ? " selected" : ""}`);
      button.type = "button";
      button.dataset.runId = run.run_id;
      button.append(node("strong", run.name || run.run_id));
      button.append(node("small", `${formatDate(run.created_at)} · ${run.status}`));
      button.onclick = () => selectRun(run.run_id).catch((error) => message(error.message, true));
      return button;
    });
    $("history").replaceChildren(...(buttons.length ? buttons : [node("p", "Your calculations will appear here.", "sidebar-muted")]));
  } catch (error) {
    $("history").replaceChildren(node("p", error.message, "sidebar-muted"));
  }
}

function artifactUrl(runId, path, download = false) {
  return `/api/runs/${encodeURIComponent(runId)}/files/${path.split("/").map(encodeURIComponent).join("/")}${download ? "?download=true" : ""}`;
}

function renderRun(run) {
  if (selected !== run.run_id) return;
  selectedStatus = run.status;
  $("empty-state").hidden = true;
  $("run-detail").hidden = false;
  $("run-title").textContent = run.name || run.run_id;
  $("run-origin").textContent = run.source === "cli" ? "COMMAND-LINE CALCULATION" : "LOCAL CALCULATION";
  $("run-time").textContent = `${formatDate(run.created_at)} · ${run.run_id}`;
  $("run-status").textContent = run.status;
  $("run-status").className = `status-badge ${run.status}`;
  $("run-progress").value = run.progress;
  $("progress-label").textContent = `${run.completed} / ${run.total} calculations complete`;
  $("cancel").hidden = !run.can_cancel;
  $("cancel").disabled = run.status === "cancelling";
  const active = run.cells.find((cell) => cell.status === "running");
  $("active-stage").textContent = active
    ? `${active.protein} · pH ${active.ph} · ${stageNames[active.stage] || "Starting calculation"}`
    : run.status === "queued" ? "Waiting for the previous calculation to finish."
    : run.status === "running" ? "Checking tools or composing the final figure…"
    : run.status === "completed" ? "All surfaces rendered. Your map is ready."
    : run.status === "cancelling" ? "Stopping the calculation and its scientific tools…" : "";
  $("run-error").hidden = !run.error;
  $("run-error").textContent = run.error || "";
  $("figure-panel").hidden = !run.figure;
  if (run.figure) {
    const url = artifactUrl(run.run_id, run.figure);
    if ($("figure").getAttribute("src") !== url) $("figure").src = url;
    $("download-figure").href = artifactUrl(run.run_id, run.figure, true);
    $("full-figure").href = url;
  } else {
    $("figure").removeAttribute("src");
  }
  const rows = run.cells.map((cell) => {
    const row = node("tr");
    const charge = cell.stages.find((stage) => stage.metrics?.pqr)?.metrics.pqr.total_charge;
    const state = cell.status === "running" ? stageNames[cell.stage] || "Starting" : cell.status;
    row.append(node("td", cell.protein), node("td", cell.ph), node("td", state), node("td", charge === undefined ? "—" : charge.toFixed(2)));
    return row;
  });
  $("cells").replaceChildren(...rows);
  $("tiles-details").hidden = !run.tiles.length;
  const tileKey = run.tiles.join("|");
  if ($("tiles").dataset.key !== `${run.run_id}|${tileKey}`) {
    $("tiles").dataset.key = `${run.run_id}|${tileKey}`;
    $("tiles").replaceChildren(...run.tiles.map((path) => {
      const link = node("a", undefined, "tile");
      link.href = artifactUrl(run.run_id, path);
      link.target = "_blank";
      link.rel = "noopener";
      const caption = path.replace(/^renders\//, "").replace(/\.png$/, "").replace("/ph-", " · pH ");
      const image = node("img");
      image.src = link.href;
      image.alt = caption;
      image.loading = "lazy";
      link.append(image, node("span", caption));
      return link;
    }));
  }
  const logKey = run.logs.join("|");
  if (logKey !== lastLogPaths) {
    const current = $("log-select").value;
    const placeholder = node("option", "Select a log…");
    placeholder.value = "";
    $("log-select").replaceChildren(placeholder, ...run.logs.map((path) => {
      const option = node("option", path.replace(/^logs\//, ""));
      option.value = path;
      return option;
    }));
    if (run.logs.includes(current)) $("log-select").value = current;
    lastLogPaths = logKey;
  }
  $("worker-log").hidden = !run.error || !run.worker_log;
  $("worker-log").textContent = run.worker_log || "";
  $("run-directory").textContent = run.directory;
  $("download-manifest").hidden = !run.manifest;
  if (run.manifest) $("download-manifest").href = artifactUrl(run.run_id, run.manifest, true);
}

async function selectRun(runId) {
  if (stream) stream.close();
  if (refreshTimer) clearTimeout(refreshTimer);
  selected = runId;
  selectedStatus = null;
  lastLogPaths = "";
  $("log-select").value = "";
  $("log-content").textContent = "Select a stage log to inspect its latest output.";
  document.querySelectorAll(".history-item").forEach((button) => {
    button.classList.toggle("selected", button.dataset.runId === runId);
  });
  const run = await api(`/api/runs/${encodeURIComponent(runId)}`);
  renderRun(run);
  if (terminal.has(run.status)) return;
  stream = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
  stream.addEventListener("progress", (event) => {
    const data = JSON.parse(event.data);
    renderRun(data);
    refreshHistory();
    if ($("logs-details").open && $("log-select").value) loadLog();
    if (terminal.has(data.status)) { stream.close(); stream = null; }
  });
  stream.onerror = () => {
    if (selected !== runId) return;
    // EventSource reconnects itself. A one-shot fetch also reconciles a terminal
    // state if the connection closes between the last write and the final event.
    refreshTimer = setTimeout(async () => {
      try {
        const current = await api(`/api/runs/${encodeURIComponent(runId)}`);
        renderRun(current);
        if (terminal.has(current.status) && stream) { stream.close(); stream = null; }
      } catch (error) { $("active-stage").textContent = "Connection lost. Keep the local GUI server running, then refresh."; }
    }, 1500);
  };
}

async function loadLog() {
  const path = $("log-select").value;
  const runId = selected;
  if (!path || !runId) return;
  try {
    const data = await api(`/api/runs/${encodeURIComponent(runId)}/log/${path.split("/").map(encodeURIComponent).join("/")}`);
    if (selected === runId && $("log-select").value === path) {
      $("log-content").textContent = data.text || "This log is currently empty.";
    }
  } catch (error) { $("log-content").textContent = error.message; }
}

$("proteins").addEventListener("change", (event) => setProteins(event.target.files));
$("file-drop").addEventListener("dragover", (event) => { event.preventDefault(); $("file-drop").classList.add("dragging"); });
$("file-drop").addEventListener("dragleave", () => $("file-drop").classList.remove("dragging"));
$("file-drop").addEventListener("drop", (event) => { event.preventDefault(); $("file-drop").classList.remove("dragging"); setProteins(event.dataTransfer.files); });
document.querySelectorAll('input[name="ph-mode"]').forEach((input) => input.addEventListener("change", () => {
  const range = phMode() === "range";
  $("range-fields").hidden = !range;
  $("explicit-fields").hidden = range;
  ["ph-start", "ph-stop", "ph-step"].forEach((id) => { $(id).required = range; });
  $("ph-values").required = !range;
  updateCount();
}));
["ph-start", "ph-stop", "ph-step", "ph-values"].forEach((id) => $(id).addEventListener("input", updateCount));
$("run-form").addEventListener("submit", (event) => { event.preventDefault(); send(false); });
$("validate").onclick = () => send(true);
$("refresh-history").onclick = refreshHistory;
$("recheck-tools").onclick = checkTools;
$("log-select").onchange = loadLog;
$("logs-details").addEventListener("toggle", () => { if ($("logs-details").open) loadLog(); });
$("cancel").onclick = async () => {
  if (!selected) return;
  $("cancel").disabled = true;
  try {
    const data = await api(`/api/runs/${encodeURIComponent(selected)}/cancel`, { method: "POST" });
    renderRun(data);
    refreshHistory();
  } catch (error) { message(error.message, true); $("cancel").disabled = false; }
};
$("new-run").onclick = () => {
  if (stream) { stream.close(); stream = null; }
  if (refreshTimer) clearTimeout(refreshTimer);
  selected = null;
  selectedStatus = null;
  $("run-detail").hidden = true;
  $("empty-state").hidden = false;
  message("");
  refreshHistory();
  $("run-name").focus();
};
const logTimer = setInterval(() => {
  if (selected && !terminal.has(selectedStatus) && $("logs-details").open) loadLog();
}, 2000);
window.addEventListener("pagehide", () => {
  if (stream) stream.close();
  clearInterval(logTimer);
});
checkTools();
refreshHistory();
updateCount();
