// Full-page DOM integration: actual markup/scripts and actual FastAPI/worker requests.
// This is not pixel/browser-rendering QA. Native dialogs, file-picker selection,
// scrolling and SSE transport are simulated; all page handlers run unmodified.
import assert from "node:assert/strict";
import { createInterface } from "node:readline";
import { stdin, stdout } from "node:process";
import { Script } from "node:vm";
import { JSDOM, VirtualConsole } from "jsdom";

const input = createInterface({ input: stdin });
let receiveFixture;
const initial = new Promise((resolve) => { receiveFixture = resolve; });
const pending = new Map();
const httpRequests = [];
let sequence = 0;
input.on("line", (line) => {
  const data = JSON.parse(line);
  if (receiveFixture) { const resolve = receiveFixture; receiveFixture = null; resolve(data); }
  else { pending.get(data.id)?.(data); pending.delete(data.id); }
});
const fixture = await initial;
const startupFailure = ["startup-failure", "outdated-server", "stale-main-script", "missing-styles"].includes(fixture.scenario);
function request(message) {
  if (message.url) httpRequests.push(message.url);
  return new Promise((resolve) => {
    const id = ++sequence;
    pending.set(id, resolve);
    stdout.write(`${JSON.stringify({ id, ...message })}\n`);
  });
}

const errors = [];
const virtualConsole = new VirtualConsole();
virtualConsole.on("jsdomError", (error) => errors.push(error.stack));
const dom = new JSDOM(fixture.html, {
  url: "http://127.0.0.1/", runScripts: "outside-only", virtualConsole,
});
const w = dom.window, document = w.document;
const $ = (id) => { const element = document.getElementById(id); assert.ok(element, id); return element; };
for (const source of fixture.styles) {
  const style = document.createElement("style");
  style.textContent = source;
  document.head.append(style);
}
if (!w.HTMLDialogElement.prototype.showModal) {
  w.HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  w.HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
    this.dispatchEvent(new w.Event("close"));
  };
}
const scrollRequests = [];
w.HTMLElement.prototype.scrollIntoView = function (options) { scrollRequests.push({ element: this, options }); };
w.confirm = () => true;
w.fetch = async (url, options = {}) => {
  const message = { url: String(url), method: options.method || "GET", headers: options.headers || {} };
  if (options.body instanceof w.FormData) {
    message.fields = {}; message.files = [];
    for (const [key, value] of options.body) {
      if (typeof value === "string") message.fields[key] = value;
      else message.files.push({ field: key, name: value.name, type: value.type,
        data: Buffer.from(await value.arrayBuffer()).toString("base64") });
    }
  } else if (options.body) message.json = JSON.parse(options.body);
  const response = await request(message);
  if (options.signal?.aborted) throw new w.DOMException("Request aborted", "AbortError");
  return { ok: response.status >= 200 && response.status < 300, status: response.status,
    json: async () => response.json };
};
const streams = new Set();
w.EventSource = class {
  constructor(url) { this.url = url; this.listeners = new Map(); streams.add(this); this.poll(); }
  addEventListener(type, listener) { this.listeners.set(type, listener); }
  async poll() {
    if (this.closed) return;
    try {
      const response = await w.fetch(this.url.replace(/\/events$/, ""));
      const data = await response.json();
      if (!this.closed) this.listeners.get("progress")?.({ data: JSON.stringify(data) });
    } catch (error) { if (!this.closed) this.onerror?.(error); }
    if (!this.closed) this.timer = setTimeout(() => this.poll(), 40);
  }
  close() { this.closed = true; clearTimeout(this.timer); streams.delete(this); }
};

async function waitFor(condition, description, limit = 15000) {
  const deadline = Date.now() + limit;
  while (Date.now() < deadline) {
    if (!$('ui-error').hidden && !startupFailure) {
      throw new Error(`${$('ui-error-detail').textContent}\n${errors.join("\n")}`);
    }
    if (condition()) return;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error(`Timed out: ${description}. ${$("form-message").textContent}. ${errors.join("\n")}`);
}
function click(id) { assert.equal($(id).disabled, false, id); $(id).click(); }
function setValue(id, value) {
  $(id).value = value;
  $(id).dispatchEvent(new w.Event("input", { bubbles: true }));
  $(id).dispatchEvent(new w.Event("change", { bubbles: true }));
}
function chooseFile(id, name, contents) {
  const input = $(id);
  let files = [new w.File([contents], name)];
  Object.defineProperty(input, "files", { configurable: true, get: () => files });
  Object.defineProperty(input, "value", { configurable: true,
    get: () => files.length ? `C:\\fakepath\\${files[0].name}` : "",
    set: (value) => { assert.equal(value, ""); files = []; },
  });
  input.form.addEventListener("reset", () => { files = []; }, { once: true });
  input.dispatchEvent(new w.Event("change", { bubbles: true }));
}
function go(step) { document.querySelector(`[data-step="${step}"]`).click(); }
const json = async (url) => (await request({ url })).json;
const runId = () => $("run-time").textContent.split(" · ").at(-1);
async function completed(previousRevision = null) {
  await waitFor(() => $("run-status").textContent === "completed" && !$("submit").disabled
    && (!previousRevision || $("figure").getAttribute("src") !== previousRevision), "completed calculation");
  const detail = await json(`/api/runs/${runId()}`);
  assert.equal(detail.status, "completed");
  return detail;
}

async function checkPhControls() {
  click("new-run");
  const arrow = (id, direction) => {
    document.querySelector(`[data-ph-input="${id}"][data-direction="${direction}"]`).click();
  };
  const key = (id, value) => $(id).dispatchEvent(new w.KeyboardEvent("keydown", {
    key: value, bubbles: true, cancelable: true,
  }));
  chooseFile("proteins", "protein.pdb", "ATOM\n");
  click("wizard-next");
  assert.equal($("wizard-ph").hidden, false);
  for (const id of ["ph-start", "ph-stop", "ph-step"]) {
    assert.equal($(id).min, "0");
    assert.equal($(id).max, "14");
    setValue(id, "3"); arrow(id, 1); assert.equal($(id).value, "3.5");
    arrow(id, -1); assert.equal($(id).value, "3");
    key(id, "ArrowUp"); assert.equal($(id).value, "3.5");
    key(id, "ArrowDown"); assert.equal($(id).value, "3");
    for (let i = 0; i < 30; i++) arrow(id, 1);
    assert.equal($(id).value, "14", "Incrementing must stop at the upper bound.");
    for (let i = 0; i < 30; i++) arrow(id, -1);
    assert.equal($(id).value, id === "ph-step" ? "0.5" : "0");
    setValue(id, "0.2");
    assert.equal($(id).checkValidity(), true, "Typed decimals need not be multiples of 0.5.");
    arrow(id, -1);
    assert.equal($(id).value, id === "ph-step" ? "0.2" : "0");
    setValue(id, "0.00001");
    assert.equal($(id).checkValidity(), true, "Do not impose a new precision floor.");
  }
  setValue("ph-start", "0"); setValue("ph-stop", "14"); setValue("ph-step", "7");
  for (const [id, invalid] of [["ph-start", "-0.1"], ["ph-stop", "14.1"], ["ph-step", "0"]]) {
    const original = $(id).value;
    setValue(id, invalid);
    assert.equal($(id).checkValidity(), false);
    go(3);
    assert.equal($("wizard-ph").hidden, false, "Invalid bounds or zero steps must block review.");
    setValue(id, original);
  }
  go(3); click("validate");
  await waitFor(() => $("form-message").textContent.includes("Inputs are valid"), "inclusive pH bounds");
  assert.match($("form-message").textContent, /pH 0, 7, 14/);
  go(2);
  setValue("ph-start", "0.2"); setValue("ph-stop", "1.2"); setValue("ph-step", "0.2");
  go(3);
  assert.equal($("wizard-review").hidden, false);
  click("validate");
  await waitFor(() => $("form-message").textContent.includes("Inputs are valid"), "typed fine pH steps");
  assert.match($("form-message").textContent, /pH 0\.2, 0\.4, 0\.6, 0\.8, 1\.0, 1\.2/);
  assert.equal((await json("/api/runs")).length, 0, "Validation must not start work.");
}

async function checkDialogHelp() {
  function helpFor(id) {
    const control = $(id);
    const description = (control.getAttribute("aria-describedby") || "").split(" ")
      .map((id) => document.getElementById(id)).find((node) => node?.closest(".field-help"));
    assert.ok(description, `An on-demand explanation must exist for ${id}.`);
    assert.ok(control.labels.length, `${id} must keep its accessible label.`);
    const help = description.closest(".field-help");
    assert.match(help.querySelector("summary").getAttribute("aria-label"), /^Help for \S/);
    assert.equal(help.closest("dialog"), control.closest("dialog"), "Scoped help must not refer to a different form.");
    return help;
  }
  const helpControls = ["pdb-code", "view", "ligand", "workers", "size", "dpi", "level", "background",
    "ramp", "force-field", "font-size", "foreground", "outer-margin", "column-gap", "row-gap", "label-gap",
    "ph-label-precision", "legend", "ray", "surface-mode", "surface-solvent", "surface-ramp-above-mode",
    "ligand-representation", "ph-step", "ph-values"];
  for (const id of helpControls) {
    assert.equal(helpFor(id).open, false, "Long explanations start collapsed.");
    if (!["pdb-code", "view", "ligand", "ph-step", "ph-values"].includes(id)) {
      assert.equal(helpFor(`default-${id}`).open, false);
    }
  }
  for (const id of ["size", "dpi", "font-size", "level", "workers", "surface-mode", "surface-solvent",
    "surface-ramp-above-mode", "ligand-representation", "camera", "ph-values"]) helpFor(`artifact-${id}`);
  const ids = Array.from(document.querySelectorAll("[id]"), (node) => node.id);
  assert.equal(new Set(ids).size, ids.length, "Enhancement must not duplicate hint or field IDs.");
  document.querySelectorAll("dialog [aria-describedby]").forEach((control) => {
    for (const id of control.getAttribute("aria-describedby").split(" ")) assert.ok($(id));
  });
  assert.equal($("ligand").labels[0].textContent.includes("experimental"), true);
  assert.equal($("ph-range-hint").closest(".field-help"), null, "pH bounds stay visible.");

  const defaults = await json("/api/preferences");
  click("open-preferences");
  await waitFor(() => $("preferences-dialog").open, "open defaults for help checks");
  const sizeHelp = helpFor("default-size"), dpiHelp = helpFor("default-dpi");
  const before = httpRequests.length;
  sizeHelp.querySelector("summary").click();
  await waitFor(() => sizeHelp.open, "open default field help");
  assert.equal(httpRequests.length, before, "Opening help must not submit or load settings.");
  assert.match(sizeHelp.textContent, /pixels/);
  dpiHelp.querySelector("summary").click();
  await waitFor(() => dpiHelp.open && !sizeHelp.open, "only one explanation at a time");
  dpiHelp.querySelector("summary").focus();
  const escape = new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
  dpiHelp.querySelector("summary").dispatchEvent(escape);
  assert.equal(escape.defaultPrevented, true);
  assert.equal(dpiHelp.open, false);
  assert.equal($("preferences-dialog").open, true, "First Escape dismisses help, not the editor.");
  assert.equal(document.activeElement, dpiHelp.querySelector("summary"));
  const secondEscape = new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
  document.dispatchEvent(secondEscape);
  assert.equal(secondEscape.defaultPrevented, false, "Next Escape can reach the native dialog dismissal.");
  sizeHelp.querySelector("summary").click();
  await waitFor(() => sizeHelp.open, "reopen help");
  $("default-size").focus();
  assert.equal(sizeHelp.open, false, "Tabbing back to another field dismisses help.");
  sizeHelp.querySelector("summary").click();
  await waitFor(() => sizeHelp.open, "open help before outside click");
  $("preferences-title").dispatchEvent(new w.Event("pointerdown", { bubbles: true }));
  assert.equal(sizeHelp.open, false);
  sizeHelp.querySelector("summary").click();
  await waitFor(() => sizeHelp.open, "open help before closing dialog");
  click("close-preferences");
  assert.equal(sizeHelp.open, false);
  assert.deepEqual((await json("/api/preferences")).defaults, defaults.defaults);

  click("new-run");
  chooseFile("proteins", "source.pdb", "ATOM\n");
  go(2); setValue("ph-start", "5"); setValue("ph-stop", "5"); go(3);
  $("calculation-overrides").open = true;
  const rayHelp = helpFor("ray"), checked = $("ray").checked;
  rayHelp.querySelector("summary").click();
  await waitFor(() => rayHelp.open, "checkbox explanation");
  assert.equal($("ray").checked, checked, "Help outside a label must not toggle its checkbox.");
  setValue("size", "64");
  $("ray").checked = false;
  click("submit");
  const source = await completed();
  assert.equal(rayHelp.open, false, "Queued calculation cleanup also closes field help.");
  assert.equal(source.configuration.render.size, 64);

  await openArtifact("compose");
  const fontHelp = helpFor("artifact-font-size");
  fontHelp.querySelector("summary").click();
  await waitFor(() => fontHelp.open, "figure editor explanation");
  assert.match(fontHelp.textContent, /Pillow/);
  assert.equal($("artifact-background-hint").hidden, false, "Opaque background restrictions stay visible.");
  click("artifact-cancel");
  assert.equal(fontHelp.open, false);
  await openArtifact("render");
  const surfaceHelp = helpFor("artifact-surface-mode");
  surfaceHelp.closest(".settings-details").open = true;
  surfaceHelp.querySelector("summary").click();
  await waitFor(() => surfaceHelp.open, "surface-rendering explanation");
  assert.match(surfaceHelp.textContent, /HETATM/);
  click("artifact-close");
  assert.equal(surfaceHelp.open, false);
  assert.equal((await json(`/api/runs/${source.run_id}`)).manifest_sha256, source.manifest_sha256);
}

async function checkDialogFeedback() {
  function checkError(id) {
    assert.equal($(id).hidden, false);
    assert.equal($(id).getAttribute("role"), "alert");
    assert.equal(document.activeElement, $(id), "An error must receive focus outside the disabled form body.");
    assert.equal(scrollRequests.at(-1).element, $(id), "An error must be revealed even after scrolling to lower fields.");
    assert.equal(scrollRequests.at(-1).options.block, "nearest");
  }
  click("new-run");
  for (const name of ["A", "B", "C"]) chooseFile("proteins", `${name}.pdb`, "ATOM\n");
  go(2); setValue("ph-start", "5"); setValue("ph-stop", "5"); go(3);
  setValue("size", "64"); $("ray").checked = false;
  $("calculation-overrides").open = true;
  $("font-size").closest(".settings-details").open = true;
  $("calculation-dialog").scrollTop = 700;
  setValue("font-size", "0");
  click("submit");
  await waitFor(() => !$("form-message").hidden && !$("submit").disabled, "full calculation validation error");
  checkError("form-message");
  assert.equal((await json("/api/runs")).length, 0);
  setValue("font-size", "22"); click("submit");
  const source = await completed();
  for (const mode of ["compose", "render"]) {
    await openArtifact(mode);
    $("artifact-dialog").scrollTop = 700;
    if (mode === "render") $("artifact-layout-section").open = true;
    setValue("artifact-font-size", "0"); click("artifact-validate");
    await waitFor(() => $("artifact-message").textContent.includes("font_size")
      && !$("artifact-validate").disabled, `${mode} validation error`);
    checkError("artifact-message");
    setValue("artifact-font-size", "22");
    $("artifact-selection-section").open = true;
    const rows = $("artifact-proteins");
    const ids = () => Array.from(rows.children, (row) => row.querySelector('input[type="text"]').value);
    const move = (row, direction) => {
      const button = rows.children[row].querySelectorAll("button")[direction === -1 ? 0 : 1];
      button.focus(); button.click();
    };
    move(0, 1);
    assert.deepEqual(ids(), ["B", "A", "C"]);
    assert.equal(document.activeElement, rows.children[1].querySelectorAll("button")[1]);
    document.activeElement.click();
    assert.deepEqual(ids(), ["B", "C", "A"]);
    assert.equal(document.activeElement, rows.children[2].querySelector('input[type="text"]'),
      "At the last row, focus the moved label instead of its disabled Down button.");
    move(2, -1);
    assert.deepEqual(ids(), ["B", "A", "C"]);
    assert.equal(document.activeElement, rows.children[1].querySelectorAll("button")[0]);
    document.activeElement.click();
    assert.deepEqual(ids(), ["A", "B", "C"]);
    assert.equal(document.activeElement, rows.children[0].querySelector('input[type="text"]'),
      "At the first row, focus the moved label instead of its disabled Up button.");
    click("artifact-cancel");
  }
  assert.equal((await json(`/api/runs/${source.run_id}`)).manifest_sha256, source.manifest_sha256);
  const fetch = w.fetch;
  w.fetch = async (url, options) => {
    if (String(url).includes("/editor?operation=compose")) throw new Error("Saved settings unavailable");
    return fetch(url, options);
  };
  click("edit-figure");
  await waitFor(() => $("artifact-message").textContent.includes("Saved settings unavailable"), "editor load error");
  checkError("artifact-message");
  assert.equal($("artifact-submit").disabled, true);
  click("artifact-cancel");
  w.fetch = fetch;
}

function checkCompactLayout() {
  const heading = document.querySelector("main h1");
  assert.equal(heading.textContent, "Surface maps");
  assert.equal(document.querySelectorAll("main h1").length, 1);
  assert.equal(document.querySelector(".workspace-header").contains($("toolchain")), true);
  assert.equal(w.getComputedStyle(heading).fontSize, "20px");
  assert.equal(w.getComputedStyle(document.querySelector(".page-content")).paddingTop, "20px");
  assert.equal(w.getComputedStyle(document.querySelector(".workspace-header")).marginBottom, "16px");
  assert.equal(document.querySelector(".topbar, .page-heading, .local-badge, .empty-graphic"), null);
  assert.equal($("empty-state").textContent.includes("Run history"), true);
  assert.equal($("figure-panel").contains($("figure-key")), true);
  assert.equal($("figure-key").hidden, true, "A key belongs to a result, not an empty workspace.");
  assert.equal($("calculation-dialog").open, false);
  assert.equal($("calculation-dialog").contains($("run-form")), true);
  assert.equal(document.querySelector(".workspace-grid"), null, "The result view must not reserve a configuration column.");
}

async function checkToolDisclosure() {
  const summary = document.querySelector("#toolchain > summary");
  assert.equal($("toolchain").open, false, "Successful preflight should not take extra rows.");
  summary.click();
  assert.equal($("toolchain").open, true);
  assert.ok($("toolchain-checks").querySelectorAll(".tool-chip").length >= 5);
  $("recheck-tools").focus();
  click("recheck-tools");
  await waitFor(() => !$("recheck-tools").disabled, "repeat healthy tool check");
  assert.equal($("toolchain").open, false);
  assert.equal(document.activeElement, summary, "Collapsing must not strand keyboard focus inside hidden details.");
  await request({ test_action: "tool_availability", available: false });
  summary.click(); click("recheck-tools");
  await waitFor(() => $("toolchain-title").textContent.includes("need attention"), "missing tools");
  assert.equal($("toolchain").open, true, "Missing tools must expose their details automatically.");
  assert.ok($("toolchain-checks").querySelector(".tool-chip.failed"));
  assert.equal($("toolchain-dot").className, "status-dot failed");
  assert.equal($("submit").disabled, true);
  click("new-run");
  assert.equal($("calculation-dialog").open, true);
  assert.equal($("calculation-tools-warning").hidden, false);
  assert.match($("calculation-tools-warning").textContent, /check the tool status/);
  click("done-editing");
  await request({ test_action: "tool_availability", available: true });
  click("recheck-tools");
  await waitFor(() => $("toolchain-title").textContent === "Calculation tools are ready"
    && !$("recheck-tools").disabled, "recovered toolchain");
  assert.equal($("toolchain").open, false);
  assert.equal($("toolchain-checks").querySelector(".tool-chip.failed"), null);
  const fetch = w.fetch;
  w.fetch = async (url, options) => {
    if (url === "/api/doctor") throw new Error("Tool status request failed");
    return fetch(url, options);
  };
  summary.click(); click("recheck-tools");
  await waitFor(() => $("toolchain-title").textContent === "Tool status request failed"
    && !$("recheck-tools").disabled, "failed tool status request");
  assert.equal($("toolchain").open, true);
  assert.equal($("toolchain-checks").querySelector(".tool-chip"), null, "Do not show stale green checks after a failed request.");
  assert.match($("toolchain-checks").textContent, /Check again/);
  w.fetch = fetch;
  click("recheck-tools");
  await waitFor(() => $("toolchain-title").textContent === "Calculation tools are ready"
    && !$("recheck-tools").disabled, "retry tool status request");
  assert.equal($("toolchain").open, false);
}

function checkRunActionHelp() {
  const actions = $("run-actions");
  const ids = ["edit-calculation", "rerender", "edit-figure", "use-template"];
  assert.equal(actions.getAttribute("role"), "group");
  assert.equal(actions.getAttribute("aria-label"), "Calculation actions");
  assert.equal(actions.parentElement.id, "run-detail");
  assert.equal(w.getComputedStyle(actions).position, "sticky");
  assert.equal($("edit-calculation").classList.contains("primary"), true);
  assert.equal($("use-template").parentElement.classList.contains("duplicate-action"), true);
  assert.ok(actions.compareDocumentPosition($("figure-panel")) & w.Node.DOCUMENT_POSITION_FOLLOWING);
  assert.ok(!Array.from($("run-detail").querySelectorAll("p")).some((p) => p.textContent.includes("Edit calculation reruns all stages")));
  for (const id of ids) {
    const button = $(id), help = $(button.getAttribute("aria-describedby"));
    assert.equal(button.hidden, false);
    assert.equal(button.disabled, false);
    assert.equal(help.getAttribute("role"), "tooltip");
    assert.equal(help.hidden, true, "Explanations must not be displayed permanently.");
    button.parentElement.dispatchEvent(new w.Event("pointerenter"));
    assert.equal(help.hidden, false, `${id} hover must show its explanation.`);
    assert.match(help.textContent, /saved inputs|PQR\/DX|saved images|copy of saved/i);
    assert.equal(actions.querySelectorAll('[role="tooltip"]:not([hidden])').length, 1);
    assert.equal(button.contains(help), false);
    const before = httpRequests.length;
    help.click();
    assert.equal(httpRequests.length, before, "Clicking an explanation must not load an editor or start work.");
    document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    assert.equal(help.hidden, true, "Escape must also dismiss a hovered tooltip when focus is elsewhere.");
    actions.dispatchEvent(new w.Event("pointerleave"));
    assert.equal(help.hidden, true);
    button.focus();
    assert.equal(help.hidden, false, "Keyboard focus must show the same explanation.");
    button.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    assert.equal(help.hidden, true);
    actions.dispatchEvent(new w.Event("pointerleave"));
    assert.equal(help.hidden, true, "Escape dismissal must survive pointer exit while focus stays put.");
    button.blur();
  }
  assert.match($("edit-calculation-help").textContent, /after success|failure/);
  assert.match($("rerender-help").textContent, /without rerunning electrostatics/);
  assert.match($("edit-figure-help").textContent, /without recalculating or rendering/);
  assert.match($("use-template-help").textContent, /separate calculation|unchanged/);
}

async function openArtifact(mode) {
  click(mode === "compose" ? "edit-figure" : "rerender");
  await waitFor(() => $("artifact-dialog").open && !$("artifact-body").hidden
    && !$("artifact-validate").disabled, `open ${mode} dialog`);
}

async function checkArtifactDialogs(sourceId) {
  const before = await json(`/api/runs/${sourceId}`);
  const defaults = await json("/api/preferences");
  setValue("run-name", "Unsubmitted calculation draft");
  setValue("font-size", "31");
  const mainLabels = Array.from(document.querySelectorAll(".protein-item input"), (input) => input.value);
  await openArtifact("compose");
  assert.equal($("artifact-render-section").hidden, true);
  assert.equal($("artifact-layout-section").open, true);
  assert.equal($("artifact-font-size").value, "30", "Load saved settings, not an unfinished wizard draft.");
  assert.equal($("artifact-background").disabled, true, "Opaque tile backgrounds cannot be edited by composition.");
  assert.equal(document.getElementById("artifact-force-field"), null);
  setValue("artifact-font-size", "0"); click("artifact-submit");
  await waitFor(() => $("artifact-message").textContent.includes("font_size")
    && !$("artifact-validate").disabled, "inline invalid-setting error");
  assert.equal($("artifact-dialog").open, true);
  assert.equal((await json(`/api/runs/${sourceId}`)).manifest_sha256, before.manifest_sha256);
  setValue("artifact-font-size", "34");
  setValue("artifact-ph-values", "6"); click("artifact-validate");
  await waitFor(() => $("artifact-message").textContent.includes("saved pH")
    && !$("artifact-validate").disabled, "reject new artifact pH columns");
  setValue("artifact-ph-values", "7, 5");
  const rows = $("artifact-proteins");
  const edited = rows.querySelector('input[type="text"]');
  edited.value = "Figure-specific row label";
  edited.dispatchEvent(new w.Event("input"));
  rows.children[0].querySelectorAll("button")[1].click();
  assert.equal(rows.children[1].querySelector('input[type="text"]').value, "Figure-specific row label");
  rows.children[0].querySelector('input[type="checkbox"]').click();
  click("artifact-validate");
  await waitFor(() => $("artifact-message").textContent.includes("Settings are valid")
    && !$("artifact-validate").disabled, "check figure settings without queueing");
  assert.match($("artifact-message").textContent, /2 saved cells/);
  click("artifact-cancel");
  assert.equal($("artifact-dialog").open, false);
  assert.equal($("run-name").value, "Unsubmitted calculation draft");
  assert.equal($("font-size").value, "31");
  assert.deepEqual(Array.from(document.querySelectorAll(".protein-item input"), (input) => input.value), mainLabels);
  assert.equal((await json(`/api/runs/${sourceId}`)).manifest_sha256, before.manifest_sha256);
  assert.deepEqual((await json("/api/preferences")).defaults, defaults.defaults);
  await openArtifact("compose");
  assert.equal($("artifact-font-size").value, "30", "Cancel must discard only the modal's edits.");
  $("artifact-dialog").dispatchEvent(new w.Event("cancel", { cancelable: true }));
  assert.equal($("artifact-dialog").open, false);
  await openArtifact("render");
  assert.equal($("artifact-render-section").hidden, false);
  assert.equal($("artifact-layout-section").open, false);
  assert.equal($("artifact-background").disabled, false);
  assert.equal($("artifact-render-fields").contains($("artifact-background")), true);
  assert.equal($("artifact-camera").value, "saved");
  setValue("artifact-camera", "upload");
  assert.equal($("artifact-view-label").hidden, false);
  click("artifact-submit");
  await waitFor(() => $("artifact-message").textContent.includes("camera file")
    && !$("artifact-validate").disabled, "require an explicitly selected camera upload");
  chooseFile("artifact-view", "camera.txt", Array.from({ length: 18 }, (_, i) => i + 1).join(", "));
  click("artifact-validate");
  await waitFor(() => $("artifact-message").textContent.includes("Settings are valid")
    && !$("artifact-validate").disabled, "validate a replacement camera");
  click("artifact-close");
  assert.equal($("artifact-dialog").open, false);
  assert.equal($("run-name").value, "Unsubmitted calculation draft");
}

async function checkArtifactLifecycle() {
  click("new-run");
  chooseFile("proteins", "source.pdb", "ATOM\n");
  go(2); document.querySelector('input[name="ph-mode"][value="explicit"]').click();
  setValue("ph-values", "5"); go(3); setValue("size", "64");
  $("ray").checked = false;
  click("submit");
  const first = await completed();
  const fetch = w.fetch;
  let releaseRead, readReached = false;
  const readGate = new Promise((resolve) => { releaseRead = resolve; });
  w.fetch = async (url, options) => {
    const response = await fetch(url, options);
    if (String(url).endsWith("/editor?operation=compose")) { readReached = true; await readGate; }
    return response; // Deliberately ignore cancellation here to test the state guard.
  };
  click("edit-figure");
  await waitFor(() => $("artifact-dialog").open && readReached, "open loading editor");
  click("artifact-cancel");
  await openArtifact("render");
  releaseRead();
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal($("artifact-title").textContent, "Re-render surfaces");
  assert.equal($("artifact-render-section").hidden, false);
  click("artifact-cancel");
  w.fetch = fetch;
  await openArtifact("compose");
  let releaseValidation, validationReached = false;
  const validationGate = new Promise((resolve) => { releaseValidation = resolve; });
  w.fetch = async (url, options) => {
    const response = await fetch(url, options);
    if (url === "/api/validate") { validationReached = true; await validationGate; }
    return response;
  };
  click("artifact-validate");
  await waitFor(() => validationReached && $("artifact-validate").disabled, "read-only validation pending");
  click("artifact-cancel");
  w.fetch = fetch;
  await openArtifact("render");
  releaseValidation();
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal($("artifact-title").textContent, "Re-render surfaces");
  assert.equal($("artifact-message").hidden, true, "An old validation must not populate a new dialog.");
  click("artifact-cancel");
  await openArtifact("compose");
  setValue("artifact-font-size", "35");
  let posts = 0, releasePost;
  const postGate = new Promise((resolve) => { releasePost = resolve; });
  w.fetch = async (url, options) => {
    if (url === "/api/runs" && options?.method === "POST") { posts += 1; await postGate; }
    return fetch(url, options);
  };
  click("artifact-submit");
  await waitFor(() => posts === 1, "update awaiting queue receipt");
  assert.equal($("artifact-cancel").disabled, true);
  $("artifact-form").dispatchEvent(new w.Event("submit", { bubbles: true, cancelable: true }));
  $("artifact-dialog").dispatchEvent(new w.Event("cancel", { cancelable: true }));
  assert.equal($("artifact-dialog").open, true, "Do not abort a submission with an uncertain outcome.");
  assert.equal(posts, 1, "Double-submit must queue only one update.");
  const oldFigure = $("figure").getAttribute("src");
  releasePost();
  await waitFor(() => !$("artifact-dialog").open, "close accepted update");
  const updated = await completed(oldFigure);
  assert.equal(updated.run_id, first.run_id);
  assert.equal(updated.configuration.composition.font_size, 35);
  w.fetch = fetch;
  await openArtifact("compose");
  const external = (await json(`/api/runs/${first.run_id}/editor?operation=compose`)).settings;
  external.font_size = 39;
  const queued = await request({ url: "/api/runs", method: "POST",
    headers: { "X-Phmap-Token": document.querySelector('meta[name="phmap-token"]').content },
    fields: { settings: JSON.stringify(external) } });
  assert.equal(queued.status, 202);
  let snapshot;
  const deadline = Date.now() + 15000;
  do {
    snapshot = await json(`/api/runs/${first.run_id}`);
    if (snapshot.status === "completed") break;
    await new Promise((resolve) => setTimeout(resolve, 20));
  } while (Date.now() < deadline);
  assert.equal(snapshot.status, "completed");
  assert.equal(snapshot.configuration.composition.font_size, 39);
  click("artifact-validate");
  await waitFor(() => $("artifact-message").textContent.includes("manifest changed")
    && !$("artifact-validate").disabled, "stale editor cannot overwrite an external update");
  assert.equal($("artifact-dialog").open, true);
  assert.match($("artifact-message").textContent, /Close and reopen/);
  click("artifact-cancel");
  assert.equal((await json(`/api/runs/${first.run_id}`)).configuration.composition.font_size, 39);
  assert.equal((await json("/api/runs")).length, 1);
}

async function checkCalculationDialog() {
  assert.equal($("calculation-dialog").open, false);
  click("new-run");
  assert.equal($("calculation-dialog").open, true);
  assert.equal($("submit").form, $("run-form"));
  assert.equal($("wizard-proteins").hidden, false);
  chooseFile("proteins", "discarded.pdb", "ATOM\n");
  $("calculation-dialog").dispatchEvent(new w.Event("cancel", { cancelable: true }));
  assert.equal($("calculation-dialog").open, false);
  assert.equal(document.querySelectorAll(".protein-item").length, 0);
  assert.equal((await json("/api/runs")).length, 0);
  click("new-run"); click("close-calculation");
  assert.equal($("calculation-dialog").open, false);
  click("new-run");
  chooseFile("proteins", "source.pdb", "ATOM\n");
  click("wizard-next");
  assert.equal(document.activeElement, $("ph-start"));
  document.querySelector('input[name="ph-mode"][value="explicit"]').click();
  setValue("ph-values", "5"); click("wizard-next"); setValue("size", "64");
  click("submit");
  const first = await completed();
  assert.equal($("calculation-dialog").open, false);
  assert.equal($("run-detail").hidden, false);
  assert.equal($("workspace-message").hidden, true, "Finished calculations must not leave stale progress notices.");
  const figure = $("figure").getAttribute("src");
  click("new-run");
  assert.equal($("run-detail").hidden, false, "Opening setup must not clear the selected result.");
  assert.equal($("figure").getAttribute("src"), figure);
  click("done-editing");
  assert.equal($("calculation-dialog").open, false);
  assert.equal(runId(), first.run_id);
  const fetch = w.fetch;
  let readReached = false, release;
  const gate = new Promise((resolve) => { release = resolve; });
  w.fetch = async (url, options) => {
    const response = await fetch(url, options);
    if (String(url).endsWith("/template?edit=true")) { readReached = true; await gate; }
    return response;
  };
  click("edit-calculation");
  await waitFor(() => readReached && $("calculation-dialog").open, "loading calculation setup");
  assert.equal($("run-form").hidden, true);
  click("done-editing");
  click("new-run");
  release();
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal($("setup-title").textContent, "New calculation");
  assert.equal(document.querySelectorAll(".protein-item").length, 0);
  click("close-calculation");
  w.fetch = async (url, options) => {
    if (String(url).endsWith("/template?edit=true")) throw new Error("Saved input setup is unavailable");
    return fetch(url, options);
  };
  click("edit-calculation");
  await waitFor(() => $("form-message").textContent.includes("Saved input setup is unavailable"), "inline setup load failure");
  assert.equal($("run-form").hidden, true);
  assert.equal($("submit").disabled, true);
  assert.equal($("done-editing").disabled, false);
  click("done-editing");
  assert.equal($("run-detail").hidden, false);
  assert.equal($("figure").getAttribute("src"), figure);
  w.fetch = fetch;
  click("new-run"); chooseFile("proteins", "second.pdb", "ATOM\n");
  go(2); document.querySelector('input[name="ph-mode"][value="explicit"]').click();
  setValue("ph-values", "5"); go(3); setValue("size", "64");
  let posts = 0, releasePost;
  const postGate = new Promise((resolve) => { releasePost = resolve; });
  w.fetch = async (url, options) => {
    if (url === "/api/runs" && options?.method === "POST") { posts += 1; await postGate; }
    return fetch(url, options);
  };
  click("submit");
  await waitFor(() => posts === 1, "queue receipt pending");
  assert.equal($("close-calculation").disabled, true);
  assert.equal($("done-editing").disabled, true);
  $("calculation-dialog").dispatchEvent(new w.Event("cancel", { cancelable: true }));
  $("run-form").dispatchEvent(new w.Event("submit", { cancelable: true }));
  assert.equal($("calculation-dialog").open, true);
  assert.equal(posts, 1);
  releasePost();
  await waitFor(() => !$("calculation-dialog").open && runId() !== first.run_id, "new result takes over after queueing");
  assert.equal((await completed()).status, "completed");
  assert.equal((await json("/api/runs")).length, 2);
  w.fetch = fetch;
}

try {
  for (const script of fixture.scripts) {
    if (fixture.scenario === "startup-failure" && script.url.includes("preferences.js")) continue;
    if (fixture.scenario === "stale-main-script" && script.url.includes("/app.js")) continue;
    try { new Script(script.source, { filename: script.url }).runInContext(dom.getInternalVMContext()); }
    catch (error) {
      if (fixture.scenario !== "startup-failure") throw error;
      w.dispatchEvent(new w.ErrorEvent("error", { message: error.message, filename: script.url, error }));
    }
  }
  if (startupFailure) {
    await waitFor(() => !$("ui-error").hidden, "visible startup error");
    assert.equal($("run-form").inert, true);
    assert.equal($("preferences-dialog").open, false, "An initial modal must not hide the reload action.");
    assert.equal($("submit").disabled, true);
    assert.match($("reload-interface").href, /\?ui=[a-f0-9]{16}$/);
    if (fixture.scenario === "outdated-server") assert.match($("ui-error-detail").textContent, /restart pHmap/);
    if (fixture.scenario === "missing-styles") assert.match($("ui-error-detail").textContent, /stylesheet/);
    assert.equal((await request({ url: new URL($("reload-interface").href).pathname
      + new URL($("reload-interface").href).search })).status, 200);
  } else {
    assert.equal(document.documentElement.dataset.phmapReady, "true", "Complete scripts must initialize.");
    await waitFor(() => $("toolchain-title").textContent === "Calculation tools are ready", "tool preflight");
    checkCompactLayout();
    assert.equal($("preferences-dialog").open, true, "First launch must offer setup.");
    assert.equal(w.getComputedStyle($("open-preferences")).backgroundColor, "rgb(33, 72, 61)");
    assert.equal(w.getComputedStyle(document.querySelector(".wizard-nav")).display, "grid");
    assert.equal(w.getComputedStyle(document.querySelector('[data-step="1"]')).borderRadius, "6px");
    if (fixture.scenario === "first-launch-close") click("close-preferences");
    else if (fixture.scenario === "first-launch-escape") $("preferences-dialog").dispatchEvent(new w.Event("cancel", { cancelable: true }));
    else click("skip-preferences");
    await waitFor(() => !$("preferences-dialog").open, "skip initial setup");
    assert.equal((await json("/api/preferences")).onboarding_completed, true);
    if (fixture.scenario.startsWith("first-launch")) {
      click("open-preferences");
      await waitFor(() => $("preferences-dialog").open, "reopen standard settings");
      assert.equal($("skip-preferences").hidden, true);
      click("close-preferences");
      assert.equal($("preferences-dialog").open, false);
    } else if (fixture.scenario === "compact-layout") {
      await checkToolDisclosure();
    } else if (fixture.scenario === "ph-controls") {
      await checkPhControls();
    } else if (fixture.scenario === "dialog-help") {
      await checkDialogHelp();
    } else if (fixture.scenario === "dialog-feedback") {
      await checkDialogFeedback();
    } else if (fixture.scenario === "artifact-lifecycle") {
      await checkArtifactLifecycle();
    } else if (fixture.scenario === "calculation-dialog") {
      await checkCalculationDialog();
    } else {
      click("open-preferences");
      await waitFor(() => $("preferences-dialog").open, "open standard settings");
      setValue("default-size", "64"); setValue("default-workers", "2"); setValue("default-dpi", "450");
      $("default-ray").checked = false;
      click("save-preferences");
      await waitFor(() => !$("preferences-dialog").open, "save standard settings");
      assert.equal($("size").value, "500", "Saving defaults must not silently replace the current form.");
      click("new-run");
      assert.equal($("size").value, "64");
      assert.equal($("dpi").value, "450");
      go(2);
      assert.equal($("wizard-proteins").hidden, false, "Navigation explains missing structures.");
      setValue("pdb-code", "3sqr"); click("fetch-pdb");
      await waitFor(() => document.querySelectorAll(".protein-item").length === 1, "fetch PDB structure");
      chooseFile("proteins", "variant.pdb", "ATOM\n");
      assert.equal(document.querySelectorAll(".protein-item").length, 2);
      const variantLabel = document.querySelectorAll(".protein-item input")[1];
      variantLabel.value = "Variant A";
      variantLabel.dispatchEvent(new w.Event("input", { bubbles: true }));
      document.querySelectorAll('.protein-controls button[title="Move protein up"]')[1].click();
      assert.match(document.querySelector(".filename").textContent, /variant/);
      document.querySelectorAll('.protein-controls button[title="Move protein down"]')[0].click();
      assert.match(document.querySelector(".filename").textContent, /RCSB PDB/);
      document.querySelectorAll('.protein-controls button[title="Move protein up"]')[1].click();
      assert.equal(document.querySelector(".protein-item input").value, "Variant A");
      chooseFile("proteins", "discard.pdb", "ATOM\n");
      document.querySelectorAll('.protein-controls button[title="Remove protein"]')[2].click();
      assert.equal(document.querySelectorAll(".protein-item").length, 2);
      chooseFile("proteins", "unsupported.txt", "invalid");
      assert.equal(document.querySelectorAll(".protein-item").length, 2);
      assert.match($("form-message").textContent, /Choose .pdb/);
      const drop = new w.Event("drop", { bubbles: true, cancelable: true });
      Object.defineProperty(drop, "dataTransfer", { value: { files: [new w.File(["ATOM\n"], "dropped.pdb")] } });
      $("file-drop").dispatchEvent(drop);
      assert.equal(document.querySelectorAll(".protein-item").length, 3);
      document.querySelectorAll('.protein-controls button[title="Remove protein"]')[2].click();
      chooseFile("view", "view.txt", Array.from({ length: 18 }, (_, i) => i).join(", "));
      chooseFile("ligand", "ligand.mol2", "test ligand\n");
      click("wizard-next");
      assert.equal($("wizard-ph").hidden, false);
      document.querySelector('input[name="ph-mode"][value="explicit"]').click();
      setValue("ph-values", "5, 7");
      click("wizard-next");
      assert.equal($("wizard-review").hidden, false);
      assert.equal(document.querySelector('[data-step="3"]').getAttribute("aria-current"), "step");
      click("wizard-back"); assert.equal($("wizard-ph").hidden, false); go(3);
      $("calculation-overrides").querySelector("summary").click();
      assert.equal($("calculation-overrides").open, true);
      setValue("font-size", "30");
      click("use-standard-settings");
      assert.equal($("font-size").value, "22");
      assert.equal($("ph-values").value, "5, 7");
      setValue("font-size", "30");
      click("validate");
      await waitFor(() => $("form-message").textContent.includes("Inputs are valid"), "validate inputs");
      assert.equal((await json("/api/runs")).length, 0, "Validate must not queue work.");
      click("submit");
      const first = await completed();
      assert.equal($("workspace-message").hidden, true);
      assert.equal($("calculation-dialog").open, false, "Accepted calculations return to full-width results.");
      assert.equal($("figure-key").hidden, false);
      assert.match($("figure-ramp").style.backgroundImage, /red, white, blue/);
      assert.equal(first.completed, 4);
      assert.equal(first.configuration.view.length, 18);
      assert.equal(first.cells[0].protein, "Variant A");
      assert.ok(first.configuration.proteins.every((protein) => protein.ligand));
      checkRunActionHelp();
      const originalId = first.run_id;
      const download = await request({ url: $("download-figure").getAttribute("href") });
      assert.equal(download.status, 200);
      assert.equal(Buffer.from(download.bytes, "base64").subarray(1, 4).toString(), "PNG");
      const manifest = await json($("download-manifest").getAttribute("href"));
      assert.equal(manifest.status, "completed");
      await checkArtifactDialogs(originalId);
      await openArtifact("compose");
      assert.equal($("wizard-review").hidden, false);
      setValue("artifact-font-size", "34");
      const oldFigure = $("figure").getAttribute("src"); click("artifact-submit");
      await waitFor(() => !$("artifact-dialog").open, "close accepted figure update");
      assert.equal((await completed(oldFigure)).run_id, originalId);
      assert.equal($("workspace-message").hidden, true, "A figure update clears its notice when finished.");
      assert.equal($("run-name").value, "Unsubmitted calculation draft");
      assert.equal($("font-size").value, "31");
      await openArtifact("render");
      setValue("artifact-camera", "auto");
      setValue("artifact-size", "96"); setValue("artifact-surface-mode", "2");
      setValue("artifact-ramp", "blue,white,red");
      assert.match($("figure-ramp").style.backgroundImage, /red, white, blue/, "Unsubmitted settings must not relabel a saved figure.");
      const oldRender = $("figure").getAttribute("src"); click("artifact-submit");
      await waitFor(() => $("run-status").textContent !== "completed", "re-render started");
      assert.equal($("figure-key").hidden, true, "Pending colors must not label the last good image.");
      const rendered = await completed(oldRender);
      assert.equal($("workspace-message").hidden, true, "A re-render clears its notice when finished.");
      assert.equal(rendered.configuration.render.size, 96);
      assert.equal(rendered.configuration.view, null);
      assert.equal($("figure-key").hidden, false);
      assert.match($("figure-ramp").style.backgroundImage, /blue, white, red/);
      assert.match($("figure-key").getAttribute("aria-label"), /negative blue, neutral white, positive red/);
      click("edit-calculation");
      await waitFor(() => $("calculation-dialog").open && $("wizard-proteins").hidden === false && !$("submit").disabled, "load full calculation settings");
      click("clear-saved-ligand");
      go(2); setValue("ph-values", "5"); go(3); click("submit");
      await waitFor(() => $("run-status").textContent !== "completed", "queue full recalculation");
      const recalculated = await completed();
      assert.equal(recalculated.run_id, originalId);
      assert.ok(recalculated.configuration.proteins.every((protein) => !protein.ligand));
      click("use-template");
      await waitFor(() => $("template-action").textContent === "Duplicating calculation" && !$("submit").disabled, "load duplicate");
      go(3); click("submit");
      await waitFor(() => runId() !== originalId, "select duplicated calculation");
      const duplicate = await completed();
      assert.notEqual(duplicate.run_id, originalId);
      $("edit-calculation").parentElement.dispatchEvent(new w.Event("pointerenter"));
      assert.equal($("edit-calculation-help").hidden, false);
      document.querySelector(`[data-run-id="${originalId}"]`).click();
      assert.equal($("run-actions").querySelectorAll('[role="tooltip"]:not([hidden])').length, 0);
      for (const id of ["edit-calculation", "rerender", "edit-figure", "use-template"]) {
        assert.equal($(id).disabled, true, "Disable stale capabilities while another history entry loads.");
      }
      await waitFor(() => runId() === originalId, "select original history entry");
      $("log-select").value = Array.from($("log-select").options).find((option) => option.value.endsWith("pdb2pqr.stdout.log")).value;
      $("log-select").dispatchEvent(new w.Event("change"));
      await waitFor(() => $("log-content").textContent.includes("prepared"), "load calculation log");
      await openArtifact("compose");
      click("artifact-cancel");
      click("edit-calculation");
      await waitFor(() => $("calculation-dialog").open && !$("run-form").inert, "reopen full configuration");
      click("done-editing");
      assert.equal($("calculation-dialog").open, false);
      assert.equal($("template-notice").hidden, true);
      assert.equal($("wizard-proteins").hidden, false);
      click("open-preferences");
      await waitFor(() => $("preferences-dialog").open, "open reset settings");
      click("reset-preferences");
      await waitFor(() => $("default-size").value === "500" && !$("preferences-form").inert, "reset defaults");
      click("close-preferences");
      assert.equal($("size").value, "64", "Reset must leave the current form unchanged.");
      click("new-run"); assert.equal($("size").value, "500");
      await request({ test_action: "slow_backend" });
      chooseFile("proteins", "cancel.pdb", "ATOM\n");
      go(2); document.querySelector('input[name="ph-mode"][value="explicit"]').click();
      setValue("ph-values", "5, 7"); go(3); setValue("size", "64"); click("submit");
      await waitFor(() => $("cells").textContent.includes("Calculating potential"), "active APBS stage");
      assert.equal($("workspace-message").hidden, false);
      assert.equal($("workspace-message").dataset.kind, "progress");
      assert.equal(w.getComputedStyle($("workspace-message")).borderRadius, "8px");
      assert.match($("workspace-message").textContent, /Calculating surface maps/);
      document.querySelector("[data-notice-dismiss]").click();
      assert.equal($("workspace-message").hidden, true);
      for (const id of ["edit-calculation", "rerender", "edit-figure", "use-template"]) {
        assert.equal($(id).hidden, false, "Actions should remain discoverable during a calculation.");
        assert.equal($(id).disabled, true);
      }
      $("edit-calculation").parentElement.dispatchEvent(new w.Event("pointerenter"));
      assert.match($("edit-calculation-help").textContent, /after this calculation finishes/);
      $("run-actions").dispatchEvent(new w.Event("pointerleave"));
      const activeId = runId();
      click("new-run"); chooseFile("proteins", "queued.pdb", "ATOM\n");
      go(3); setValue("size", "64"); click("submit");
      await waitFor(() => $("run-status").textContent === "queued" && !$("cancel").disabled, "queued calculation");
      const queuedId = runId(); click("cancel");
      await waitFor(() => $("run-status").textContent === "cancelled" && !$("submit").disabled, "cancel queued calculation");
      assert.equal($("workspace-message").hidden, true);
      assert.equal($("edit-calculation").disabled, false);
      assert.equal($("rerender").disabled, true);
      assert.match($("rerender-help").textContent, /no saved artifacts/);
      click("edit-calculation");
      await waitFor(() => $("calculation-dialog").open && !$("run-form").inert, "edit a cancelled queued setup");
      go(3); click("submit");
      await waitFor(() => $("run-status").textContent === "queued" && !$("cancel").disabled, "resume cancelled setup");
      assert.equal(runId(), queuedId); click("cancel");
      await waitFor(() => $("run-status").textContent === "cancelled", "cancel resumed setup");
      assert.equal($("workspace-message").hidden, true);
      document.querySelector(`[data-run-id="${activeId}"]`).click();
      await waitFor(() => runId() === activeId && !$("cancel").disabled, "select active calculation");
      click("cancel");
      await waitFor(() => $("run-status").textContent === "cancelled", "cancel active calculation");
      document.querySelector("#toolchain > summary").click();
      click("recheck-tools");
      await waitFor(() => $("toolchain-title").textContent === "Calculation tools are ready" && !$("recheck-tools").disabled, "recheck toolchain");
    }
    assert.equal($("ui-error").hidden, true);
    assert.deepEqual(errors, []);
  }
} finally {
  for (const stream of streams) stream.close();
  dom.window.close();
  input.close();
}
stdout.write(`${JSON.stringify({ done: true, scenario: fixture.scenario })}\n`);
