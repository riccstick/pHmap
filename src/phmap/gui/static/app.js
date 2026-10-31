"use strict";

const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="phmap-token"]').content;
const terminal = new Set(["completed", "failed", "cancelled", "interrupted"]);
const stageNames = { pdb2pqr: "Preparing protonation", apbs: "Calculating potential", pymol: "Rendering surface" };
const proteinSelection = new ProteinSelection();
const proteins = proteinSelection.rows;
let selected = null;
let toolsReady = false;
let pymolReady = false;
let busy = false;
let fetching = false;
let lastLogPaths = "";
let selectedStatus = null;
let selectedCanTemplate = false;
let templateSource = null;
let reuseView = false;
let reuseLigand = false;
let templateAbort = null;
let pdbAbort = null;
let operation = "full";
let sourceManifest = null;
let selectedCanRework = false;
let updateInPlace = false;
let editingRunning = false;
let preferences = JSON.parse($("preferences-data").textContent);
let preferencesBusy = false;
let preferencesLoading = false;
let firstLaunchSetup = false;
const wizard = new CalculationWizard(renderWizard, validateWizardStep);
const selection = new RunSelection({
  load: (runId, options) => api(`/api/runs/${encodeURIComponent(runId)}`, options),
  terminal,
  onUpdate: (run, progress) => {
    renderRun(run);
    if (progress) {
      refreshHistory();
      if ($("logs-details").open && $("log-select").value) loadLog();
    }
  },
  onError: () => {
    $("active-stage").textContent = "Connection lost. Keep the local GUI server running, then refresh.";
  },
});
// Keep the calculation being edited live even while another history item is selected.
const editingSelection = new RunSelection({
  load: (runId, options) => api(`/api/runs/${encodeURIComponent(runId)}`, options),
  terminal,
  onUpdate: (run) => { reconcileEditingSession(run); updateButtons(); refreshHistory(); },
  onError: () => message("Could not refresh the editing calculation. Re-select it in history to reconnect.", true),
});

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
  const loading = templateAbort !== null;
  const failed = typeof document !== "undefined" && document.documentElement?.dataset.phmapFailed === "true";
  $("submit").disabled = failed || busy || fetching || loading || editingRunning || !proteins.length || !requiredToolsReady();
  $("validate").disabled = failed || busy || fetching || loading || !proteins.length;
  $("fetch-pdb").disabled = busy || fetching || loading || operation !== "full";
  $("fetch-pdb").textContent = fetching ? "Fetching…" : "Add PDB entry";
  $("proteins").disabled = busy || loading || operation !== "full";
  $("use-template").disabled = busy || fetching || loading || !selectedCanTemplate;
  $("use-template").textContent = loading ? "Loading setup…" : "Duplicate calculation";
  $("edit-calculation").disabled = busy || fetching || loading || !selectedCanTemplate;
  $("run-form").inert = failed || loading;
  $("edit-figure").disabled = busy || fetching || loading || !selectedCanRework;
  $("rerender").disabled = $("edit-figure").disabled;
  $("new-run").disabled = failed || busy;
  $("done-editing").disabled = busy || loading || editingRunning;
  $("submit").textContent = busy ? "Submitting…" : editingRunning ? "Updating calculation…"
    : operation === "compose" ? "Update figure →"
    : operation === "render" ? "Update surface renders →"
    : updateInPlace ? "Rerun this calculation →" : "Start calculation →";
  if (typeof wizard !== "undefined") renderWizard(wizard.step);
}

function requiredToolsReady() {
  return operation === "compose" || (operation === "render" ? pymolReady : toolsReady);
}

function setProteins(files) {
  if (templateAbort || operation !== "full") return;
  try {
    proteinSelection.addFiles(files);
    $("proteins").value = "";
    renderProteins();
    message("");
  } catch (error) {
    message(error.message, true);
  }
}

async function addPdb() {
  if (busy || fetching || templateAbort || operation !== "full") return;
  const pdbId = $("pdb-code").value.trim();
  if (!pdbId) { message("Enter a PDB ID, for example 3SQR.", true); return; }
  if (proteins.length >= 32) { message("Use at most 32 protein structures per comparison.", true); return; }
  fetching = true;
  const controller = new AbortController();
  pdbAbort = controller;
  updateButtons();
  message(`Fetching ${pdbId} from RCSB PDB…`);
  try {
    const result = await api("/api/structures/fetch", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pdb_id: pdbId }),
      signal: controller.signal,
    });
    if (controller.signal.aborted || pdbAbort !== controller) return;
    proteinSelection.addPdb(result.pdb_id);
    $("pdb-code").value = "";
    renderProteins();
    message(`${result.pdb_id} added as PDBx/mmCIF. Add local variant files to compare them.`);
  } catch (error) {
    if (!controller.signal.aborted && pdbAbort === controller) message(error.message, true);
  } finally {
    if (pdbAbort === controller) {
      pdbAbort = null;
      fetching = false;
      updateButtons();
    }
  }
}

function cancelPdbFetch() {
  if (pdbAbort) pdbAbort.abort();
  pdbAbort = null;
  fetching = false;
  updateButtons();
}

function renderProteins() {
  const list = $("protein-list");
  list.replaceChildren();
  proteins.forEach((protein, index) => {
    const row = node("div", undefined, "protein-item");
    const name = protein.file ? protein.file.name
      : protein.savedProteinId ? `${protein.filename} · ${operation === "full" ? "saved input" : "saved artifacts"}` : `${protein.pdbId} · RCSB PDB`;
    const filename = node("span", `${index + 1}. ${name}`, "filename");
    filename.title = name;
    const label = node("input");
    label.type = "text";
    label.value = protein.label;
    label.maxLength = 128;
    label.setAttribute("aria-label", `Row label for ${name}`);
    label.addEventListener("input", () => { protein.label = label.value; });
    const controls = node("div", undefined, "protein-controls");
    const up = node("button", "↑");
    up.type = "button";
    up.title = "Move protein up";
    up.setAttribute("aria-label", `Move ${name} up`);
    up.disabled = index === 0;
    up.onclick = () => {
      [proteins[index - 1], proteins[index]] = [proteins[index], proteins[index - 1]];
      renderProteins();
    };
    const down = node("button", "↓");
    down.type = "button";
    down.title = "Move protein down";
    down.setAttribute("aria-label", `Move ${name} down`);
    down.disabled = index === proteins.length - 1;
    down.onclick = () => {
      [proteins[index + 1], proteins[index]] = [proteins[index], proteins[index + 1]];
      renderProteins();
    };
    const remove = node("button", "×");
    remove.type = "button";
    remove.title = "Remove protein";
    remove.setAttribute("aria-label", `Remove ${name}`);
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

function syncPhStepValidity() {
  const input = $("ph-step");
  input.setCustomValidity(input.value !== "" && input.valueAsNumber <= 0
    ? "The pH step must be greater than zero." : "");
}

function stepPhInput(input, direction) {
  if (input.disabled || input.readOnly) return;
  const previous = input.value;
  // Use native decimal stepping without imposing a half-unit validation grid
  // on manually entered values. A zero calculation step is never useful.
  input.step = "0.5";
  try {
    if (direction > 0) input.stepUp();
    else input.stepDown();
    if (input.id === "ph-step" && input.valueAsNumber <= 0) input.value = previous;
  } finally {
    input.step = "any";
  }
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

function initializePhInputs() {
  document.querySelectorAll("[data-ph-input]").forEach((button) => {
    button.onclick = () => stepPhInput($(button.dataset.phInput), Number(button.dataset.direction));
  });
  ["ph-start", "ph-stop", "ph-step"].forEach((id) => {
    $(id).addEventListener("keydown", (event) => {
      if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
      event.preventDefault();
      stepPhInput($(id), event.key === "ArrowUp" ? 1 : -1);
    });
  });
  $("ph-step").addEventListener("input", syncPhStepValidity);
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
    ? `${proteins.length} protein${proteins.length === 1 ? "" : "s"} × ${count} pH value${count === 1 ? "" : "s"} = ${cells} ${operation === "full" ? "calculations" : "saved cells"}`
    : "Choose proteins and valid pH values to preview your calculation.";
}

function formData() {
  const inputs = proteinSelection.inputs();
  const defaults = readDefaultSettings($);
  if (defaults.foreground === "auto") defaults.foreground = defaults.background === "black" ? "white" : "black";
  const settings = {
    ...defaults,
    name: $("run-name").value,
    ph_mode: phMode(), ph_start: $("ph-start").value, ph_stop: $("ph-stop").value,
    ph_step: $("ph-step").value, ph_values: $("ph-values").value,
    labels: inputs.labels, structures: inputs.structures,
    source_run_id: templateSource,
    operation, source_manifest_sha256: sourceManifest,
    update_in_place: updateInPlace,
    reuse_view: reuseView && !$("view").files.length,
    reuse_ligand: reuseLigand && !$("ligand").files.length,
  };
  const data = new FormData();
  data.append("settings", JSON.stringify(settings));
  inputs.uploads.forEach((file) => data.append("proteins", file, file.name));
  if ($("view").files.length) data.append("view", $("view").files[0]);
  if ($("ligand").files.length) data.append("ligand", $("ligand").files[0]);
  return data;
}

async function send(validateOnly) {
  if (!proteins.length || busy || fetching || templateAbort || editingRunning) return;
  if (proteins.some((p) => p.file && !/\.(pdb|cif|mmcif)$/i.test(p.file.name))) {
    message("All protein structures must be .pdb, .cif or .mmcif files.", true);
    return;
  }
  if (!validateOnly && !requiredToolsReady()) {
    message("Resolve the missing calculation tools before starting a run.", true);
    return;
  }
  busy = true;
  updateButtons();
  message("");
  try {
    const data = formData();
    if (operation !== "full" && !validateOnly) {
      const plan = await api("/api/validate", { method: "POST", body: data });
      showPlan(plan);
      if (!updateInPlace && !window.confirm(`Reuse: ${plan.reuse.join(", ")}\nRun: ${plan.run.join(" → ")}\nCells: ${plan.total}\n\nCreate a separate copy?`)) return;
    }
    const result = await api(validateOnly ? "/api/validate" : "/api/runs", {
      method: "POST", body: data,
    });
    if (validateOnly) {
      if (operation !== "full") showPlan(result);
      message(`Inputs are valid. ${result.total} cells at pH ${result.ph_values.join(", ")}. Up to ${result.execution?.effective_workers || 1} cells in parallel.`);
    } else {
      templateSource = result.run_id;
      updateInPlace = true;
      sourceManifest = null;
      editingRunning = true;
      watchEditingRun(result.run_id);
      syncSavedInputs();
      message("Calculation queued. Further changes will update this calculation. New calculation starts a separate one.");
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
    pymolReady = data.checks.some((check) => check.name === "PyMOL" && check.ok);
    $("toolchain-title").textContent = data.ready ? "Calculation tools are ready" : "Some calculation tools need attention";
    $("toolchain-dot").className = data.ready ? "status-dot" : "status-dot failed";
    const chips = data.checks.map((check) => {
      const chip = node("span", `${check.ok ? "✓" : "!"} ${check.name}`, `tool-chip${check.ok ? "" : " failed"}`);
      chip.title = [check.version, check.message, check.path].filter(Boolean).join(" · ");
      if (!check.ok) chip.textContent += `: ${check.message}`;
      return chip;
    });
    $("toolchain-checks").replaceChildren(...chips);
    const panel = $("toolchain");
    const returnFocus = data.ready && panel.querySelector(".toolchain-body").contains(document.activeElement);
    panel.open = !data.ready;
    if (returnFocus) panel.querySelector("summary").focus();
  } catch (error) {
    toolsReady = false;
    pymolReady = false;
    $("toolchain-title").textContent = error.message;
    $("toolchain-dot").className = "status-dot failed";
    $("toolchain-checks").replaceChildren(node("p", "Tool checks could not finish. Check again to retry.", "field-hint"));
    $("toolchain").open = true;
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

function artifactUrl(runId, path, download = false, revision = null) {
  const query = new URLSearchParams();
  if (download) query.set("download", "true");
  if (revision) query.set("v", revision);
  const queryText = query.toString();
  return `/api/runs/${encodeURIComponent(runId)}/files/${path.split("/").map(encodeURIComponent).join("/")}${queryText ? `?${queryText}` : ""}`;
}

function reconcileEditingSession(run) {
  if (templateSource === run.run_id && updateInPlace) {
    editingRunning = !terminal.has(run.status);
    const digest = run.manifest_sha256 || run.submission_sha256;
    if (digest) sourceManifest = digest;
  }
}

function hideRunActionHelp() {
  $("run-actions").querySelectorAll('[role="tooltip"]').forEach((hint) => { hint.hidden = true; });
}

function initializeRunActionHelp() {
  const actions = $("run-actions");
  let dismissed = false;
  const show = (button) => {
    dismissed = false;
    hideRunActionHelp();
    $(button.getAttribute("aria-describedby")).hidden = false;
  };
  actions.querySelectorAll(".run-action").forEach((wrapper) => {
    const button = wrapper.querySelector("button");
    wrapper.addEventListener("pointerenter", () => show(button));
    button.addEventListener("focus", () => show(button));
  });
  actions.addEventListener("pointerleave", () => {
    if (dismissed) return;
    const focused = document.activeElement;
    if (actions.contains(focused) && focused.matches("button[aria-describedby]")) show(focused);
    else hideRunActionHelp();
  });
  actions.addEventListener("focusout", (event) => {
    if (!actions.contains(event.relatedTarget)) hideRunActionHelp();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { dismissed = true; hideRunActionHelp(); }
  });
}

function updateRunActionHelp(run) {
  $("run-actions").querySelectorAll(".run-action").forEach((wrapper) => {
    const button = wrapper.querySelector("button");
    const help = wrapper.querySelector("[data-help]");
    const needsInputs = button.id === "edit-calculation" || button.id === "use-template";
    help.textContent = !terminal.has(run.status)
      ? "Available after this calculation finishes or is cancelled."
      : needsInputs && !run.can_template ? "This calculation has no saved inputs or setup available to restore. Add proteins in a new calculation."
      : !needsInputs && !run.can_rework ? "This calculation has no saved artifacts available for this action."
      : help.dataset.help;
  });
}

function renderFigureKey(run) {
  const key = $("figure-key"), bar = $("figure-ramp");
  const colors = run.configuration?.render?.ramp_colors;
  // An in-place update can show the last good image while reporting the pending
  // configuration. Only label a finished image with its committed color ramp.
  key.hidden = true;
  bar.style.backgroundImage = "";
  if (!run.figure || !terminal.has(run.status) || !Array.isArray(colors) || colors.length !== 3) return;
  bar.style.backgroundImage = `linear-gradient(90deg, ${colors.join(", ")})`;
  if (!bar.style.backgroundImage) return;
  key.setAttribute("aria-label", `Electrostatic potential: negative ${colors[0]}, neutral ${colors[1]}, positive ${colors[2]}`);
  key.hidden = false;
}

function renderRun(run) {
  if (selected !== run.run_id) return;
  selectedStatus = run.status;
  selectedCanTemplate = Boolean(run.can_template);
  selectedCanRework = Boolean(run.can_rework);
  reconcileEditingSession(run);
  updateRunActionHelp(run);
  updateButtons();
  $("empty-state").hidden = true;
  $("run-detail").hidden = false;
  $("run-title").textContent = run.name || run.run_id;
  $("run-origin").textContent = run.source === "cli" ? "COMMAND-LINE CALCULATION" : "LOCAL CALCULATION";
  if (run.plan) $("run-origin").textContent = run.plan.operation === "compose" ? "EDITED FIGURE" : "RE-RENDERED SURFACES";
  $("run-plan").hidden = !run.plan;
  $("run-plan").textContent = run.plan
    ? `Source: ${run.plan.source_run_id}\nReuse: ${run.plan.reuse.join(", ")}\nRun: ${run.plan.run.join(" → ")}` : "";
  $("run-time").textContent = `${formatDate(run.created_at)} · ${run.run_id}`;
  $("run-status").textContent = run.status;
  $("run-status").className = `status-badge ${run.status}`;
  $("run-progress").value = run.progress;
  $("progress-label").textContent = `${run.completed} / ${run.total} cells complete`;
  $("cancel").hidden = !run.can_cancel;
  $("cancel").disabled = run.status === "cancelling";
  const activeCells = run.cells.filter((cell) => cell.status === "running");
  const active = activeCells[0];
  $("active-stage").textContent = active
    ? `${activeCells.length} active cell${activeCells.length === 1 ? "" : "s"} · ${active.protein} · pH ${active.ph} · ${stageNames[active.stage] || "Starting calculation"}`
    : run.status === "queued" ? "Waiting for the previous calculation to finish."
    : run.status === "running" ? "Checking tools or composing the final figure…"
    : run.status === "completed" ? "All surfaces rendered. Your map is ready."
    : run.status === "cancelling" ? "Stopping the calculation and its scientific tools…" : "";
  $("run-error").hidden = !run.error;
  $("run-error").textContent = run.error || "";
  $("figure-panel").hidden = !run.figure;
  renderFigureKey(run);
  if (run.figure) {
    const url = artifactUrl(run.run_id, run.figure, false, run.figure_revision);
    if ($("figure").getAttribute("src") !== url) $("figure").src = url;
    $("download-figure").href = artifactUrl(run.run_id, run.figure, true, run.figure_revision);
    $("full-figure").href = url;
  } else {
    $("figure").removeAttribute("src");
  }
  const rows = run.cells.map((cell) => {
    const row = node("tr");
    const charge = cell.metrics?.pqr?.total_charge
      ?? cell.stages.find((stage) => stage.metrics?.pqr)?.metrics.pqr.total_charge;
    const state = cell.status === "running" ? stageNames[cell.stage] || "Starting" : cell.status;
    row.append(node("td", cell.protein), node("td", cell.ph), node("td", state), node("td", typeof charge === "number" && Number.isFinite(charge) ? charge.toFixed(2) : "—"));
    return row;
  });
  $("cells").replaceChildren(...rows);
  $("tiles-details").hidden = !run.tiles.length;
  const tileKey = `${run.figure_revision || ""}|${run.tiles.join("|")}`;
  if ($("tiles").dataset.key !== `${run.run_id}|${tileKey}`) {
    $("tiles").dataset.key = `${run.run_id}|${tileKey}`;
    $("tiles").replaceChildren(...run.tiles.map((path) => {
      const link = node("a", undefined, "tile");
      link.href = artifactUrl(run.run_id, path, false, run.figure_revision);
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
  cancelTemplateLoad();
  hideRunActionHelp();
  selected = runId;
  selectedStatus = null;
  selectedCanTemplate = false;
  selectedCanRework = false;
  updateButtons();
  lastLogPaths = "";
  $("log-select").value = "";
  $("log-content").textContent = "Select a stage log to inspect its latest output.";
  document.querySelectorAll(".history-item").forEach((button) => {
    button.classList.toggle("selected", button.dataset.runId === runId);
  });
  await selection.select(runId);
}

function syncPhMode() {
  const range = phMode() === "range";
  $("range-fields").hidden = !range;
  $("ph-range-hint").hidden = !range;
  $("explicit-fields").hidden = range;
  ["ph-start", "ph-stop", "ph-step"].forEach((id) => { $(id).required = range; });
  $("ph-values").required = !range;
  syncPhStepValidity();
  updateCount();
}

function syncSavedInputs() {
  $("template-notice").hidden = templateSource === null;
  $("template-source").textContent = templateSource || "";
  $("template-action").textContent = updateInPlace ? "Editing calculation" : "Duplicating calculation";
  $("template-behavior").textContent = updateInPlace
    ? "Updates replace this calculation's current result after success. No previous versions are kept."
    : "Starting creates a separate calculation.";
  $("setup-title").textContent = updateInPlace ? "Edit this calculation" : "Set up a calculation";
  $("done-editing").hidden = !updateInPlace;
  $("saved-view").hidden = !reuseView;
  $("saved-ligand").hidden = !reuseLigand;
}

function showPlan(plan) {
  $("reuse-plan").hidden = false;
  $("reuse-plan").textContent = `Reuse: ${plan.reuse.join(", ")}\nRun: ${plan.run.join(" → ")}\nParallel cells: ${plan.execution?.effective_workers || 1}\n${plan.total} saved cells. ${updateInPlace ? "Update this calculation after success; keep the last good result if it fails." : "Create a separate copy."}`;
}

function syncOperation() {
  const reuse = operation !== "full", compose = operation === "compose";
  ["force-field", "ligand", "clear-saved-ligand"].forEach((id) => { $(id).disabled = reuse; });
  ["size", "level", "ramp", "ray", "surface-mode", "surface-solvent", "surface-ramp-above-mode",
    "ligand-representation", "view", "clear-saved-view"].forEach((id) => { $(id).disabled = compose; });
  $("reuse-plan").hidden = true;
  $("operation-notice").hidden = !reuse;
  $("operation-notice").textContent = compose
    ? "Edit saved tiles: change labels, row/column order, font, spacing, DPI and legend visibility. Choose only saved pH values. Opaque tile backgrounds cannot be changed without re-rendering."
    : "Re-render saved PQR/DX: change camera, colors, resolution and surface controls. Choose only saved proteins and pH values. Scientific inputs and force field stay fixed.";
  $("use-standard-settings").disabled = reuse;
  renderSettingsSummary();
  updateButtons();
}

function applyTemplate(data) {
  editingSelection.close();
  const form = templateFormValues(data.settings);
  $("run-form").reset();
  for (const [id, value] of Object.entries(form.values)) {
    const control = $(id);
    const text = String(value);
    if (control.tagName === "SELECT" && !Array.from(control.options).some((option) => option.value === text)) {
      const option = node("option", `${text} (saved)`);
      option.value = text;
      control.append(option);
    }
    control.value = text;
  }
  for (const [id, checked] of Object.entries(form.checked)) $(id).checked = checked;
  document.querySelectorAll('input[name="ph-mode"]').forEach((input) => {
    input.checked = input.value === form.phMode;
  });
  templateSource = data.settings.source_run_id;
  operation = data.settings.operation || "full";
  sourceManifest = data.settings.source_manifest_sha256 || null;
  updateInPlace = Boolean(data.settings.update_in_place);
  editingRunning = false;
  reuseView = data.has_view;
  reuseLigand = data.has_ligand;
  proteinSelection.loadSaved(data.proteins);
  syncSavedInputs();
  syncPhMode();
  renderProteins();
  syncOperation();
  setWizardStep(operation === "full" ? 1 : 3);
}

function cancelTemplateLoad() {
  if (templateAbort) templateAbort.abort();
  templateAbort = null;
  updateButtons();
}

async function useTemplate() {
  if (!selected || !selectedCanTemplate || busy || fetching || templateAbort) return;
  const runId = selected;
  const controller = new AbortController();
  templateAbort = controller;
  updateButtons();
  message("Loading the saved settings and checking input files…");
  try {
    const data = await api(`/api/runs/${encodeURIComponent(runId)}/template`, { signal: controller.signal });
    if (controller.signal.aborted || selected !== runId) return;
    applyTemplate(data);
    message("Setup loaded as a duplicate. Start calculation creates a separate history item.");
    $("run-name").scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (error) {
    if (!controller.signal.aborted && selected === runId) message(error.message, true);
  } finally {
    if (templateAbort === controller) { templateAbort = null; updateButtons(); }
  }
}

async function openEditor(mode) {
  if (!selected || !(mode === "full" ? selectedCanTemplate : selectedCanRework) || busy || fetching || templateAbort) return;
  const runId = selected;
  const controller = new AbortController();
  templateAbort = controller;
  updateButtons();
  try {
    const url = mode === "full" ? `/api/runs/${encodeURIComponent(runId)}/template?edit=true`
      : `/api/runs/${encodeURIComponent(runId)}/editor?operation=${mode}`;
    const data = await api(url, { signal: controller.signal });
    if (controller.signal.aborted || templateAbort !== controller || selected !== runId) return;
    applyTemplate(data);
    message("Editing this calculation. Adjust settings, update, and review again; the history item stays the same.");
    $("run-name").scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (error) {
    if (!controller.signal.aborted && templateAbort === controller) message(error.message, true);
  } finally {
    if (templateAbort === controller) { templateAbort = null; updateButtons(); }
  }
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

function watchEditingRun(runId) {
  editingSelection.select(runId).catch((error) => {
    if (templateSource === runId && updateInPlace) message(error.message, true);
  });
}

function renderSettingsSummary() {
  const settings = readDefaultSettings($);
  $("settings-summary").textContent = `${settings.size} px tiles · ${settings.dpi} DPI · ${settings.workers} parallel cells · ${settings.force_field} · ${settings.ray_trace ? "ray tracing" : "quick rendering"}`;
  $("settings-source").textContent = templateSource
    ? "This calculation keeps its saved settings. Changing global defaults does not alter saved calculations."
    : "Your standard settings are ready. Override them below only when this calculation needs something different.";
}

function renderWizard(step) {
  document.querySelectorAll("[data-wizard-step]").forEach((panel) => {
    panel.hidden = Number(panel.dataset.wizardStep) !== step;
  });
  document.querySelectorAll("[data-step]").forEach((button) => {
    const current = Number(button.dataset.step) === step;
    if (current) button.setAttribute("aria-current", "step");
    else button.removeAttribute("aria-current");
    button.disabled = busy || fetching || templateAbort !== null;
  });
  $("wizard-back").hidden = step === 1;
  $("wizard-back").disabled = busy || fetching || templateAbort !== null;
  $("wizard-next").hidden = step === 3;
  $("wizard-next").disabled = busy || fetching || templateAbort !== null;
  $("wizard-next").textContent = step === 1 ? "Next: pH →" : "Next: review →";
  $("validate").hidden = step !== 3;
  $("submit").hidden = step !== 3;
  renderSettingsSummary();
}

function validateWizardStep(from, to) {
  if (to > 1 && !proteins.length) { message("Add at least one protein structure first.", true); return false; }
  if (to === 3) {
    syncPhStepValidity();
    const ids = phMode() === "range" ? ["ph-start", "ph-stop", "ph-step"] : ["ph-values"];
    const invalid = ids.find((id) => !$(id).checkValidity());
    if (invalid) {
      wizard.go(2, true);
      $(invalid).reportValidity();
      return false;
    }
    if (phMode() === "range" && Number($("ph-start").value) > Number($("ph-stop").value)) {
      wizard.go(2, true);
      message("The pH range start must not exceed its stop.", true);
      return false;
    }
  }
  message("");
  return true;
}

function setWizardStep(step) { wizard.go(step); }

function applySavedDefaults() { applyDefaultSettings(preferences.defaults, $); }

function initializePreferences() {
  const clone = $("override-controls").cloneNode(true);
  clone.id = "default-advanced-controls";
  clone.querySelectorAll("[id]").forEach((element) => { element.id = `default-${element.id}`; });
  clone.querySelectorAll("[for]").forEach((element) => { element.htmlFor = `default-${element.htmlFor}`; });
  clone.querySelectorAll("[aria-describedby]").forEach((element) => {
    element.setAttribute("aria-describedby", element.getAttribute("aria-describedby").split(" ").map((id) => `default-${id}`).join(" "));
  });
  clone.querySelector(".workers-setting").remove();
  ["size", "dpi", "background"].forEach((id) => clone.querySelector(`#default-${id}`).closest("label").remove());
  $("preferences-fields").append(clone);
  applySavedDefaults();
  $("open-preferences").onclick = () => openPreferences(false);
  $("close-preferences").onclick = () => firstLaunchSetup ? persistPreferences("skip") : $("preferences-dialog").close();
  $("skip-preferences").onclick = () => persistPreferences("skip");
  $("reset-preferences").onclick = () => {
    if (window.confirm("Reset your standard settings to built-in defaults? Saved calculations and the current form will not change.")) persistPreferences("reset");
  };
  $("preferences-form").addEventListener("submit", (event) => { event.preventDefault(); persistPreferences("save"); });
  $("preferences-dialog").addEventListener("cancel", (event) => {
    if (preferencesBusy || firstLaunchSetup) { event.preventDefault(); if (!preferencesBusy) persistPreferences("skip"); }
  });
  $("run-form").addEventListener("input", renderSettingsSummary);
  $("run-form").addEventListener("change", renderSettingsSummary);
  $("use-standard-settings").onclick = () => {
    if (operation !== "full") return;
    applySavedDefaults(); renderSettingsSummary();
    message("Saved defaults applied to this calculation. Protein inputs and pH selection are unchanged.");
  };
  $("wizard-back").onclick = () => setWizardStep(wizard.step - 1);
  $("wizard-next").onclick = () => setWizardStep(wizard.step + 1);
  document.querySelectorAll("[data-step]").forEach((button) => {
    button.onclick = () => setWizardStep(Number(button.dataset.step));
  });
  if (!preferences.onboarding_completed) openPreferences(true);
  else if (preferences.warning) message(preferences.warning, true);
}

async function openPreferences(firstLaunch) {
  if (preferencesBusy || preferencesLoading || $("preferences-dialog").open) return;
  firstLaunchSetup = firstLaunch;
  if (!firstLaunch) {
    preferencesLoading = true;
    $("open-preferences").disabled = true;
    try { preferences = await api("/api/preferences"); }
    catch (error) { message(error.message, true); return; }
    finally { preferencesLoading = false; $("open-preferences").disabled = false; }
  }
  applyDefaultSettings(preferences.defaults, (id) => $(`default-${id}`));
  $("preferences-title").textContent = firstLaunch ? "Welcome — set your defaults" : "Standard settings";
  $("skip-preferences").hidden = !firstLaunch;
  $("preferences-message").hidden = !preferences.warning;
  $("preferences-message").textContent = preferences.warning || "";
  $("preferences-dialog").showModal();
}

async function persistPreferences(action) {
  if (preferencesBusy) return;
  preferencesBusy = true;
  $("preferences-form").inert = true;
  try {
    preferences = await api(action === "save" ? "/api/preferences" : `/api/preferences/${action}`, {
      method: action === "save" ? "PUT" : "POST",
      ...(action === "save" ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify({ defaults: readDefaultSettings((id) => $(`default-${id}`)) }) } : {}),
    });
    if (action === "reset") {
      applyDefaultSettings(preferences.defaults, (id) => $(`default-${id}`));
      $("preferences-message").hidden = true;
      firstLaunchSetup = false;
      $("skip-preferences").hidden = true;
      return;
    }
    if (firstLaunchSetup && !templateSource) { applySavedDefaults(); renderSettingsSummary(); }
    $("preferences-dialog").close();
    message(firstLaunchSetup ? "Defaults are ready. Add proteins to begin." : "Standard settings saved for new calculations. The current calculation is unchanged.");
    firstLaunchSetup = false;
  } catch (error) {
    if (action === "skip") {
      firstLaunchSetup = false;
      $("preferences-dialog").close();
      message(`Continuing with defaults. Could not remember setup dismissal: ${error.message}`, true);
      return;
    }
    $("preferences-message").textContent = error.message;
    $("preferences-message").hidden = false;
  } finally {
    preferencesBusy = false;
    $("preferences-form").inert = false;
  }
}

$("proteins").addEventListener("change", (event) => setProteins(event.target.files));
$("fetch-pdb").onclick = addPdb;
$("pdb-code").addEventListener("keydown", (event) => {
  if (event.key === "Enter") { event.preventDefault(); addPdb(); }
});
$("file-drop").addEventListener("dragover", (event) => { event.preventDefault(); $("file-drop").classList.add("dragging"); });
$("file-drop").addEventListener("dragleave", () => $("file-drop").classList.remove("dragging"));
$("file-drop").addEventListener("drop", (event) => { event.preventDefault(); $("file-drop").classList.remove("dragging"); setProteins(event.dataTransfer.files); });
document.querySelectorAll('input[name="ph-mode"]').forEach((input) => input.addEventListener("change", syncPhMode));
["ph-start", "ph-stop", "ph-step", "ph-values"].forEach((id) => $(id).addEventListener("input", updateCount));
$("run-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (wizard.step < 3) setWizardStep(wizard.step + 1);
  else send(false);
});
$("validate").onclick = () => send(true);
$("refresh-history").onclick = refreshHistory;
$("recheck-tools").onclick = checkTools;
$("use-template").onclick = useTemplate;
$("done-editing").onclick = () => {
  if (busy || editingRunning || templateAbort) return;
  if (!window.confirm("Finish editing? Only changes applied with Update or Rerun are saved. Unsubmitted form changes will be discarded.")) return;
  resetSetup();
  message("Calculation saved. The latest result is shown here; use Edit calculation or Re-render whenever you want to refine it again.");
};
$("edit-calculation").onclick = () => openEditor("full");
$("edit-figure").onclick = () => openEditor("compose");
$("rerender").onclick = () => openEditor("render");
$("clear-saved-view").onclick = () => { reuseView = false; syncSavedInputs(); };
$("clear-saved-ligand").onclick = () => { reuseLigand = false; syncSavedInputs(); };
$("view").addEventListener("change", () => { if ($("view").files.length) reuseView = false; syncSavedInputs(); });
$("ligand").addEventListener("change", () => { if ($("ligand").files.length) reuseLigand = false; syncSavedInputs(); });
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
function resetSetup() {
  editingSelection.close();
  cancelPdbFetch();
  cancelTemplateLoad();
  $("run-form").reset();
  applySavedDefaults();
  proteinSelection.loadSaved([]);
  templateSource = null;
  operation = "full";
  updateInPlace = false;
  editingRunning = false;
  sourceManifest = null;
  syncOperation();
  reuseView = false;
  reuseLigand = false;
  syncSavedInputs();
  syncPhMode();
  renderProteins();
  setWizardStep(1);
}

$("new-run").onclick = () => {
  resetSetup();
  selection.close();
  selected = null;
  selectedStatus = null;
  selectedCanTemplate = false;
  selectedCanRework = false;
  updateButtons();
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
  cancelPdbFetch();
  cancelTemplateLoad();
  selection.close();
  editingSelection.close();
  clearInterval(logTimer);
});
initializeRunActionHelp();
initializePhInputs();
initializePreferences();
setWizardStep(1);
checkTools();
refreshHistory();
updateCount();
document.documentElement.dataset.phmapReady = "true";
