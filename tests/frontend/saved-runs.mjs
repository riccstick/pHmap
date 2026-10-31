import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { runInNewContext } from "node:vm";

const source = await readFile(new URL("../../src/phmap/gui/static/app.js", import.meta.url), "utf8");
const functions = ["requiredToolsReady", "syncOperation", "showPlan", "send", "reconcileEditingSession", "artifactUrl", "updateButtons"].map((name) => {
  const match = source.match(new RegExp(`(?:async )?function ${name}\\([^)]*\\) \\{[\\s\\S]*?\\n\\}`));
  assert.ok(match, name);
  return match[0];
}).join("\n");

function harness() {
  const controls = new Map();
  const requests = [], confirmations = [], messages = [];
  const payload = { snapshot: "settings captured before planning" };
  let accept = true;
  const state = {
    operation: "compose", toolsReady: false, pymolReady: false,
    updateInPlace: false, editingRunning: false, templateSource: "source-run", sourceManifest: "old-hash",
    selectedCanTemplate: true, selectedCanRework: true, URLSearchParams,
    terminal: new Set(["completed", "failed", "cancelled", "interrupted"]),
    proteins: [{ savedProteinId: "wt" }], busy: false, fetching: false, templateAbort: null,
    $: (id) => { if (!controls.has(id)) controls.set(id, {}); return controls.get(id); },
    updateButtons() {}, message: (text) => messages.push(text), formData: () => payload,
    api: async (url, options) => {
      requests.push({ url, options });
      return url === "/api/validate"
        ? { reuse: ["PNG tiles"], run: ["composition"], total: 2, ph_values: ["5", "7"] }
        : { run_id: state.updateInPlace ? state.templateSource : "new-run" };
    },
    window: { confirm: (text) => { confirmations.push(text); return accept; } },
    refreshHistory() {}, selectRun() {}, syncSavedInputs() {}, watchEditingRun() {}, renderSettingsSummary() {},
  };
  const methods = runInNewContext(`${functions}\n({ requiredToolsReady, syncOperation, send, reconcileEditingSession, artifactUrl, updateButtons });`, state);
  return { state, methods, controls, requests, confirmations, messages, payload,
    decline: () => { accept = false; } };
}

{
  const test = harness();
  assert.equal(test.methods.requiredToolsReady(), true, "Compose works without scientific tools.");
  test.state.operation = "render";
  assert.equal(test.methods.requiredToolsReady(), false);
  test.state.pymolReady = true;
  assert.equal(test.methods.requiredToolsReady(), true, "Re-render requires only PyMOL.");
  test.state.operation = "full";
  assert.equal(test.methods.requiredToolsReady(), false);
}
{
  const test = harness();
  test.methods.syncOperation();
  assert.equal(test.controls.get("level").disabled, true);
  assert.equal(test.controls.get("force-field").disabled, true);
  assert.equal(test.controls.get("view").disabled, true);
  test.state.operation = "render";
  test.methods.syncOperation();
  assert.equal(test.controls.get("level").disabled, false);
  assert.equal(test.controls.get("force-field").disabled, true);
  assert.equal(test.controls.get("view").disabled, false);
  test.state.operation = "full";
  test.methods.syncOperation();
  assert.equal(test.controls.get("force-field").disabled, false);
}
{
  const test = harness();
  test.decline();
  await test.methods.send(false);
  assert.deepEqual(test.requests.map((item) => item.url), ["/api/validate"]);
  assert.match(test.confirmations[0], /Reuse: PNG tiles/);
  assert.match(test.confirmations[0], /Run: composition/);
  assert.equal(test.state.busy, false);
}
{
  const test = harness();
  await test.methods.send(false);
  assert.deepEqual(test.requests.map((item) => item.url), ["/api/validate", "/api/runs"]);
  assert.equal(test.requests[0].options.body, test.payload);
  assert.equal(test.requests[1].options.body, test.payload, "Submit exactly the settings that were planned.");
  assert.equal(test.state.busy, false);
}
{
  const test = harness();
  await test.methods.send(true);
  assert.equal(test.confirmations.length, 0);
  assert.deepEqual(test.requests.map((item) => item.url), ["/api/validate"]);
  assert.equal(test.controls.get("reuse-plan").hidden, false);
}
console.log("Saved-run editor checks passed");

{
  const test = harness();
  test.state.updateInPlace = true;
  await test.methods.send(false);
  assert.equal(test.confirmations.length, 0, "Refining the current calculation needs no copy confirmation.");
  assert.equal(test.state.templateSource, "source-run");
  assert.equal(test.state.editingRunning, true);
  assert.equal(test.state.updateInPlace, true);
  await test.methods.send(false);
  assert.equal(test.requests.length, 2, "Block duplicate submissions while this calculation is updating.");
  test.methods.reconcileEditingSession({ run_id: "other-run", status: "completed", manifest_sha256: "other-hash" });
  assert.equal(test.state.sourceManifest, null);
  test.methods.reconcileEditingSession({ run_id: "source-run", status: "completed", manifest_sha256: "new-hash" });
  assert.equal(test.state.sourceManifest, "new-hash", "Next refinement uses the newly committed manifest.");
  assert.equal(test.state.editingRunning, false);
  test.methods.updateButtons();
  assert.equal(test.controls.get("submit").textContent, "Update figure →");
}
{
  const test = harness();
  const oldPreview = test.methods.artifactUrl("source-run", "output/pHmap.png", false, "old");
  const newPreview = test.methods.artifactUrl("source-run", "output/pHmap.png", false, "new");
  assert.notEqual(oldPreview, newPreview, "Replacing a PNG at the same path must refresh browser caches.");
  assert.match(newPreview, /\?v=new$/);
}
