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
  w.HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
}
w.HTMLElement.prototype.scrollIntoView = function () {};
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
      click("edit-figure");
      await waitFor(() => $("submit").textContent === "Update figure →" && !$("submit").disabled, "open figure editor");
      assert.equal($("wizard-review").hidden, false);
      setValue("font-size", "34");
      const oldFigure = $("figure").getAttribute("src"); click("submit");
      assert.equal((await completed(oldFigure)).run_id, originalId);
      click("rerender");
      await waitFor(() => $("submit").textContent === "Update surface renders →" && !$("submit").disabled, "open re-render editor");
      click("clear-saved-view");
      setValue("size", "96"); setValue("surface-mode", "2");
      setValue("ramp", "blue,white,red");
      assert.match($("figure-ramp").style.backgroundImage, /red, white, blue/, "Unsubmitted settings must not relabel a saved figure.");
      const oldRender = $("figure").getAttribute("src"); click("submit");
      await waitFor(() => $("run-status").textContent !== "completed", "re-render started");
      assert.equal($("figure-key").hidden, true, "Pending colors must not label the last good image.");
      const rendered = await completed(oldRender);
      assert.equal(rendered.configuration.render.size, 96);
      assert.equal(rendered.configuration.view, null);
      assert.equal($("figure-key").hidden, false);
      assert.match($("figure-ramp").style.backgroundImage, /blue, white, red/);
      assert.match($("figure-key").getAttribute("aria-label"), /negative blue, neutral white, positive red/);
      click("edit-calculation");
      await waitFor(() => $("wizard-proteins").hidden === false && !$("submit").disabled, "load full calculation settings");
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
      click("edit-figure");
      await waitFor(() => $("submit").textContent === "Update figure →" && !$("submit").disabled, "reopen original editor");
      click("done-editing");
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
      assert.equal($("edit-calculation").disabled, false);
      assert.equal($("rerender").disabled, true);
      assert.match($("rerender-help").textContent, /no saved artifacts/);
      click("submit");
      await waitFor(() => $("run-status").textContent === "queued" && !$("cancel").disabled, "resume cancelled setup");
      assert.equal(runId(), queuedId); click("cancel");
      await waitFor(() => $("run-status").textContent === "cancelled", "cancel resumed setup");
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
