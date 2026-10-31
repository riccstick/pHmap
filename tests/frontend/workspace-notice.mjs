import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { runInNewContext } from "node:vm";

const source = await readFile(new URL("../../src/phmap/gui/static/workspace-notice.js", import.meta.url), "utf8");
const WorkspaceNotice = runInNewContext(`${source}\nWorkspaceNotice;`);
const terminal = new Set(["completed", "failed", "cancelled", "interrupted"]);
function harness() {
  const title = { textContent: "" }, text = { textContent: "" }, close = {};
  const elements = { "[data-notice-title]": title, "[data-notice-text]": text, "[data-notice-dismiss]": close };
  const root = { hidden: true, dataset: {}, querySelector: (selector) => elements[selector] };
  const notice = new WorkspaceNotice({ root, terminal });
  return { notice, root, title, text, close };
}

for (const operation of ["compose", "render", "full"]) {
  for (const status of terminal) {
    const test = harness();
    test.notice.track("run-A", operation);
    assert.equal(test.root.hidden, false);
    assert.equal(test.root.dataset.kind, "progress");
    test.notice.observe({ run_id: "run-A", status: "running" });
    assert.match(test.title.textContent, /Updating|Re-rendering|Calculating/);
    test.notice.observe({ run_id: "run-B", status: "completed" });
    assert.equal(test.root.hidden, false, "Another run must not dismiss this update.");
    test.notice.observe({ run_id: "run-A", status: "cancelling" });
    assert.equal(test.title.textContent, "Stopping calculation");
    assert.equal(test.root.hidden, false, "Cancellation is not complete until confirmed.");
    test.notice.observe({ run_id: "run-A", status });
    assert.equal(test.root.hidden, true, `${operation}/${status} must clear the progress notice.`);
    assert.equal(test.text.textContent, "");
  }
}

for (const error of [false, true]) {
  const test = harness();
  test.notice.track("run-A", "compose");
  test.notice.message("A newer message", error);
  test.notice.observe({ run_id: "run-A", status: "completed" });
  test.notice.observe({ status: "completed" });
  assert.equal(test.root.hidden, false);
  assert.equal(test.root.dataset.kind, error ? "error" : "info");
  assert.equal(test.text.textContent, "A newer message");
  test.close.onclick();
  assert.equal(test.root.hidden, true);
}
{
  const test = harness();
  test.notice.track("run-A", "render");
  test.close.onclick();
  test.notice.observe({ run_id: "run-A", status: "running" });
  assert.equal(test.root.hidden, true, "Progress must not revive a manually dismissed notice.");
  test.notice.track("run-A", "compose");
  test.notice.select("run-B");
  assert.equal(test.root.hidden, true, "Do not show another calculation's progress notice in history.");
  test.notice.message("Global settings saved");
  test.notice.select("run-A");
  assert.equal(test.root.hidden, false, "History selection must preserve unrelated information.");
}
{
  const test = harness();
  const app = await readFile(new URL("../../src/phmap/gui/static/app.js", import.meta.url), "utf8");
  const queued = app.match(/async function artifactQueued\([^)]*\) \{[\s\S]*?\n\}/)[0];
  const apply = runInNewContext(`(${queued})`, {
    workspaceNotice: test.notice, busy: false, selectedCanTemplate: true, selectedCanRework: true,
    templateSource: null, updateInPlace: false, updateButtons() {}, refreshHistory: async () => {},
    selectRun: async (id) => test.notice.observe({ run_id: id, status: "completed" }),
    message: (text, error) => test.notice.message(text, error),
  });
  await apply("fast-run", "compose");
  assert.equal(test.root.hidden, true, "An update finished before its first snapshot must not leave a queued banner.");
}
console.log("Workspace notice checks passed");
