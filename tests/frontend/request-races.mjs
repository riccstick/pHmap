import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { runInNewContext } from "node:vm";

const app = await readFile(new URL("../../src/phmap/gui/static/app.js", import.meta.url), "utf8");
const proteinSource = await readFile(new URL("../../src/phmap/gui/static/protein-selection.js", import.meta.url), "utf8");
const ProteinSelection = runInNewContext(`${proteinSource}\nProteinSelection;`);
const functions = ["addPdb", "cancelPdbFetch", "useTemplate", "cancelTemplateLoad", "openEditor", "resetSetup"].map((name) => {
  const match = app.match(new RegExp(`(?:async )?function ${name}\\([^)]*\\) \\{[\\s\\S]*?\\n\\}`));
  assert.ok(match, name);
  return match[0];
}).join("\n");
const resetBody = app.match(/\$\("new-run"\)\.onclick = \(\) => \{([\s\S]*?)\n\};/)[1];
const finishBody = app.match(/\$\("done-editing"\)\.onclick = \(\) => \{([\s\S]*?)\n\};/)[1];

function harness() {
  const selection = new ProteinSelection();
  const requests = [], messages = [], applied = [];
  let closed = false;
  const controls = {
    "pdb-code": { value: "3SQR" },
    "run-form": { reset() { controls["pdb-code"].value = ""; } },
    "run-name": { focus() {}, scrollIntoView() {} },
    "new-run": {}, "edit-calculation": {}, "use-template": {},
    "run-detail": { hidden: false }, "empty-state": { hidden: true },
  };
  const state = {
    AbortController, terminal: new Set(["completed", "failed", "cancelled", "interrupted"]),
    busy: false, fetching: false, pdbAbort: null, templateAbort: null,
    selected: "source-run", selectedStatus: "completed", selectedCanTemplate: true,
    templateSource: "source-run", reuseView: false, reuseLigand: false,
    operation: "full", sourceManifest: null, selectedCanRework: true,
    calculationLoadFailed: false,
    updateInPlace: true, editingRunning: false,
    window: { confirm: () => true },
    proteins: selection.rows, proteinSelection: selection,
    $: (id) => { assert.ok(controls[id], id); return controls[id]; },
    api: (url, options) => new Promise((resolve, reject) => requests.push({ url, options, resolve, reject })),
    message: (text) => messages.push(text), applyTemplate: (value) => applied.push(value),
    renderProteins() {}, updateButtons() {}, syncSavedInputs() {}, syncPhMode() {}, syncOperation() {},
    selection: { close() {} }, editingSelection: { close() {} }, refreshHistory() {},
    applySavedDefaults() {}, setWizardStep() {},
    artifactEditor: { close() {} },
    openCalculationDialog() {}, closeCalculationDialog() { closed = true; },
  };
  const methods = runInNewContext(`${functions}\nfunction newCalculation() {${resetBody}\n}\n`
    + `function finishEditing() {${finishBody}\n}\n`
    + "({ addPdb, cancelPdbFetch, useTemplate, cancelTemplateLoad, openEditor, newCalculation, finishEditing });", state);
  return { state, controls, requests, messages, applied, methods, rows: selection.rows,
    get closed() { return closed; } };
}

{
  const test = harness();
  const pending = test.methods.addPdb();
  test.methods.newCalculation();
  assert.equal(test.requests[0].options.signal.aborted, true);
  const messages = test.messages.length;
  // Deliberately ignore cancellation at the transport layer to test the guard too.
  test.requests[0].resolve({ pdb_id: "3SQR" });
  await pending;
  assert.equal(test.rows.length, 0);
  assert.equal(test.state.fetching, false);
  assert.equal(test.messages.length, messages);
}
{
  const test = harness();
  const pending = test.methods.addPdb();
  test.methods.newCalculation();
  const messages = test.messages.length;
  test.requests[0].reject(new Error("Old network failure"));
  await pending;
  assert.equal(test.messages.length, messages);
}
{
  const test = harness();
  const first = test.methods.addPdb();
  test.methods.newCalculation();
  test.controls["pdb-code"].value = "1CRN";
  const second = test.methods.addPdb();
  const current = test.state.pdbAbort;
  test.requests[0].resolve({ pdb_id: "3SQR" });
  await first;
  assert.equal(test.state.fetching, true, "An old finally block must not finish the new request.");
  assert.equal(test.state.pdbAbort, current);
  test.requests[1].resolve({ pdb_id: "1CRN" });
  await second;
  assert.deepEqual(Array.from(test.rows, (row) => row.pdbId), ["1CRN"]);
  assert.equal(test.state.fetching, false);
}
{
  const test = harness();
  const pending = test.methods.useTemplate();
  test.methods.newCalculation();
  test.requests[0].resolve({ source: "old template" });
  await pending;
  assert.equal(test.applied.length, 0);
  assert.equal(test.state.templateSource, null);
}
{
  const test = harness();
  const first = test.methods.useTemplate();
  test.methods.cancelTemplateLoad();
  test.state.selected = "other-run";
  const second = test.methods.useTemplate();
  const current = test.state.templateAbort;
  test.requests[0].resolve({ source: "old template" });
  await first;
  assert.equal(test.applied.length, 0);
  assert.equal(test.state.templateAbort, current);
  test.requests[1].resolve({ source: "new template" });
  await second;
  assert.deepEqual(test.applied, [{ source: "new template" }]);
}
{
  const test = harness();
  test.state.selectedStatus = "cancelled";
  test.state.selectedCanTemplate = false;
  await test.methods.useTemplate();
  assert.equal(test.requests.length, 0, "Older jobs without a saved setup must not offer an action.");
}
console.log("Request-race checks passed");

{
  const test = harness();
  const pending = test.methods.openEditor("full");
  assert.match(test.requests[0].url, /template\?edit=true$/);
  test.methods.newCalculation();
  test.requests[0].resolve({ source: "old editor" });
  await pending;
  assert.equal(test.applied.length, 0);
  assert.equal(test.state.operation, "full");
}
{
  const test = harness();
  test.methods.finishEditing();
  assert.equal(test.state.selected, "source-run", "Done editing keeps the reviewed result visible.");
  assert.equal(test.closed, true, "Cancel delegates to the dialog close controller.");
}
