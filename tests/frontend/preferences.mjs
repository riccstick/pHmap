import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { runInNewContext } from "node:vm";

const helpers = await readFile(new URL("../../src/phmap/gui/static/preferences.js", import.meta.url), "utf8");
const app = await readFile(new URL("../../src/phmap/gui/static/app.js", import.meta.url), "utf8");
const { readDefaultSettings, applyDefaultSettings, CalculationWizard, defaultSettingControls } = runInNewContext(
  `${helpers}\n({ readDefaultSettings, applyDefaultSettings, CalculationWizard, defaultSettingControls });`,
);
const defaults = {
  workers: 4, size: 1200, dpi: 450, level: 3, background: null, foreground: "auto",
  ramp_colors: ["green", "white", "orange"], force_field: "AMBER", ray_trace: false,
  surface_mode: 4, surface_solvent: true, surface_ramp_above_mode: false,
  ligand_representation: null, font_size: 32, outer_margin: 0, column_gap: 10,
  row_gap: 20, label_gap: 12, show_colorbar: false, ph_label_precision: 0,
};

{
  const controls = new Map();
  for (const [id] of Object.values(defaultSettingControls)) controls.set(id, { value: "" });
  controls.set("ramp", { tagName: "SELECT", value: "", options: [],
    ownerDocument: { createElement: () => ({}) }, append(option) { this.options.push(option); } });
  applyDefaultSettings(defaults, (id) => controls.get(id));
  assert.deepEqual(JSON.parse(JSON.stringify(readDefaultSettings((id) => controls.get(id)))), defaults);
  assert.equal(controls.get("background").value, "transparent");
  assert.equal(controls.get("foreground").value, "auto");
  assert.equal(controls.get("ramp").options[0].value, "green,white,orange");
  assert.equal(controls.get("legend").checked, false);
  assert.deepEqual(JSON.parse(JSON.stringify(readDefaultSettings((id) => controls.get(id), ["size"]))), { size: 1200 });
  applyDefaultSettings({ dpi: 600 }, (id) => controls.get(id), ["dpi"]);
  assert.equal(controls.get("dpi").value, "600");
  assert.equal(controls.get("size").value, "1200", "A scoped editor must not touch unrelated controls.");
}

{
  const rendered = [];
  let allowed = false;
  const wizard = new CalculationWizard((step) => rendered.push(step), () => allowed);
  assert.equal(wizard.go(2), false);
  assert.equal(wizard.step, 1);
  allowed = true;
  assert.equal(wizard.go(2), true);
  assert.equal(wizard.go(3), true);
  assert.equal(wizard.go(1), true);
  assert.equal(wizard.go(0), false);
  allowed = false;
  assert.equal(wizard.go(3, true), true, "Saved artifact editors may open the review directly.");
  assert.deepEqual(rendered, [2, 3, 1, 3]);
}

const extract = (name) => {
  const match = app.match(new RegExp(`(?:async )?function ${name}\\([^)]*\\) \\{[\\s\\S]*?\\n\\}`));
  assert.ok(match, name);
  return match[0];
};

function preferencesHarness() {
  const controls = new Map();
  const requests = [], messages = [];
  let applied = 0, failed = false;
  const state = {
    preferencesBusy: false, firstLaunchSetup: false, templateSource: "existing-calculation",
    preferences: { defaults: { ...defaults, size: 500 } },
    readDefaultSettings: () => defaults,
    applyDefaultSettings() {}, applySavedDefaults: () => { applied += 1; }, renderSettingsSummary() {},
    $: (id) => { if (!controls.has(id)) controls.set(id, { open: true, close() { this.open = false; } }); return controls.get(id); },
    api: async (url, options) => { requests.push({ url, options }); if (failed) throw new Error("read-only settings directory"); return { defaults }; },
    message: (text) => messages.push(text),
  };
  const persist = runInNewContext(`${extract("persistPreferences")}\npersistPreferences;`, state);
  return { controls, state, requests, messages, persist, get applied() { return applied; }, fail() { failed = true; } };
}

{
  const test = preferencesHarness();
  await test.persist("save");
  assert.equal(test.requests[0].url, "/api/preferences");
  assert.equal(test.requests[0].options.method, "PUT");
  assert.deepEqual(JSON.parse(test.requests[0].options.body), { defaults });
  assert.equal(test.applied, 0, "Saving global defaults must not overwrite an existing calculation.");
  assert.equal(test.state.templateSource, "existing-calculation");
  assert.equal(test.controls.get("preferences-dialog").open, false);
}
{
  const test = preferencesHarness();
  test.state.firstLaunchSetup = true;
  test.state.templateSource = null;
  await test.persist("save");
  assert.equal(test.applied, 1, "First-launch save initializes the new calculation.");
}
{
  const test = preferencesHarness();
  test.state.firstLaunchSetup = true;
  test.fail();
  await test.persist("skip");
  assert.equal(test.controls.get("preferences-dialog").open, false, "Skip remains possible without writable preferences.");
  assert.equal(test.state.preferencesBusy, false);
  assert.match(test.messages[0], /Could not remember/);
}
{
  const test = preferencesHarness();
  test.fail();
  await test.persist("save");
  assert.equal(test.controls.get("preferences-message").hidden, false);
  assert.equal(test.controls.get("preferences-form").inert, false);
}

{
  const controls = new Map();
  const sessions = [];
  const state = {
    selected: "history-B", templateSource: "editing-A", updateInPlace: true, editingRunning: true,
    sourceManifest: null, terminal: new Set(["completed", "failed", "cancelled", "interrupted"]),
    busy: false, fetching: false, templateAbort: null, proteins: [{}], operation: "full",
    calculationLoadFailed: false,
    toolsReady: true, pymolReady: true, selectedCanTemplate: true, selectedCanRework: true,
    $: (id) => { if (!controls.has(id)) controls.set(id, {}); return controls.get(id); },
    refreshHistory() {}, message() {}, api() {},
    RunSelection: class { constructor(options) { Object.assign(this, options); sessions.push(this); } select() { return Promise.resolve(); } },
  };
  const controller = app.match(/const editingSelection = new RunSelection\(\{[\s\S]*?\n\}\);/)[0];
  const methods = runInNewContext(`${["reconcileEditingSession", "updateButtons", "requiredToolsReady", "watchEditingRun"].map(extract).join("\n")}\n${controller}\n({ watchEditingRun });`, state);
  methods.watchEditingRun("editing-A");
  sessions[0].onUpdate({ run_id: "editing-A", status: "completed", manifest_sha256: "fresh-hash" });
  assert.equal(state.selected, "history-B");
  assert.equal(state.editingRunning, false);
  assert.equal(state.sourceManifest, "fresh-hash");
  assert.equal(controls.get("done-editing").disabled, false);
  assert.equal(controls.get("submit").disabled, false);
  sessions[0].onUpdate({ run_id: "editing-A", status: "cancelled", submission_sha256: "queued-setup-hash" });
  assert.equal(state.sourceManifest, "queued-setup-hash", "Cancelled unstarted jobs retain a resumable setup digest.");
}
console.log("Preferences and wizard checks passed");
