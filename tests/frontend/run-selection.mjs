// Dependency-free controller checks: node tests/frontend/run-selection.mjs
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { runInNewContext } from "node:vm";

const source = await readFile(new URL("../../src/phmap/gui/static/run-selection.js", import.meta.url), "utf8");

function harness() {
  const requests = [];
  const streams = [];
  const updates = [];
  const errors = [];
  const timers = new Map();
  let timerId = 0;
  const RunSelection = runInNewContext(`${source}\nRunSelection;`, {
    AbortController,
    setTimeout: (callback) => {
      const id = ++timerId;
      timers.set(id, async () => { timers.delete(id); await callback(); });
      return id;
    },
    clearTimeout: (id) => timers.delete(id),
  });
  const selection = new RunSelection({
    terminal: new Set(["completed", "failed", "cancelled", "interrupted"]),
    load: (runId, { signal }) => new Promise((resolve, reject) => {
      requests.push({ runId, signal, resolve, reject });
    }),
    onUpdate: (run) => updates.push(run),
    onError: (error) => errors.push(error),
    connect: (url) => {
      const stream = {
        url, closed: false,
        close() { this.closed = true; },
        addEventListener(name, handler) { this[name] = handler; },
        emit(data) { this.progress({ data: JSON.stringify(data) }); },
      };
      streams.push(stream);
      return stream;
    },
  });
  return { selection, requests, streams, updates, errors, timers };
}

const checks = [
  ["out-of-order responses cannot select an old run", async () => {
    const h = harness();
    const first = h.selection.select("A");
    const second = h.selection.select("B");
    assert.equal(h.requests[0].signal.aborted, true);
    h.requests[1].resolve({ run_id: "B", status: "running" });
    await second;
    h.requests[0].resolve({ run_id: "A", status: "running" });
    await first;
    assert.equal(h.streams.length, 1);
    assert.equal(h.streams[0].url, "/api/runs/B/events");
    assert.deepEqual(h.updates.map((run) => run.run_id), ["B"]);
  }],
  ["late events cannot close another run's stream", async () => {
    const h = harness();
    let selected = h.selection.select("A");
    h.requests[0].resolve({ run_id: "A", status: "running" });
    await selected;
    selected = h.selection.select("B");
    h.requests[1].resolve({ run_id: "B", status: "running" });
    await selected;
    assert.equal(h.streams[0].closed, true);
    h.streams[0].emit({ run_id: "A", status: "completed" });
    assert.equal(h.streams[1].closed, false);
    assert.equal(h.updates.length, 2);
    h.streams[1].emit({ run_id: "B", status: "completed" });
    assert.equal(h.streams[1].closed, true);
    assert.equal(h.updates.at(-1).status, "completed");
  }],
  ["selecting a completed run needs no stream", async () => {
    const h = harness();
    const selected = h.selection.select("done");
    h.requests[0].resolve({ run_id: "done", status: "completed" });
    await selected;
    assert.equal(h.streams.length, 0);
  }],
  ["closing cancels requests and ignores their late responses", async () => {
    const h = harness();
    const selected = h.selection.select("A");
    h.selection.close();
    assert.equal(h.requests[0].signal.aborted, true);
    h.requests[0].resolve({ run_id: "A", status: "running" });
    await selected;
    assert.equal(h.updates.length, 0);
    assert.equal(h.streams.length, 0);
  }],
  ["reconnect reconciliation finishes a run and clears its timer", async () => {
    const h = harness();
    const selected = h.selection.select("A");
    h.requests[0].resolve({ run_id: "A", status: "running" });
    await selected;
    h.streams[0].onerror();
    h.streams[0].onerror();
    assert.equal(h.timers.size, 1);
    const reconcile = [...h.timers.values()][0]();
    h.requests[1].resolve({ run_id: "A", status: "completed" });
    await reconcile;
    assert.equal(h.streams[0].closed, true);
    assert.equal(h.updates.at(-1).status, "completed");
    assert.equal(h.timers.size, 0);
  }],
  ["late reconnect failures do not overwrite the new selection", async () => {
    const h = harness();
    let selected = h.selection.select("A");
    h.requests[0].resolve({ run_id: "A", status: "running" });
    await selected;
    h.streams[0].onerror();
    const reconcile = [...h.timers.values()][0]();
    selected = h.selection.select("B");
    h.requests[2].resolve({ run_id: "B", status: "running" });
    await selected;
    h.requests[1].reject(new Error("Old connection failed"));
    await reconcile;
    assert.deepEqual(h.errors, []);
    assert.equal(h.streams[1].closed, false);
  }],
  ["initial failures are reported to the caller", async () => {
    const h = harness();
    const selected = h.selection.select("missing");
    h.requests[0].reject(new Error("Run not found"));
    await assert.rejects(selected, /Run not found/);
  }],
  ["reconnect responses cannot undo a terminal progress event", async () => {
    for (const fail of [false, true]) {
      const h = harness();
      const selected = h.selection.select("A");
      h.requests[0].resolve({ run_id: "A", status: "running" });
      await selected;
      h.streams[0].onerror();
      const reconcile = [...h.timers.values()][0]();
      h.streams[0].emit({ run_id: "A", status: "completed" });
      if (fail) h.requests[1].reject(new Error("Connection lost"));
      else h.requests[1].resolve({ run_id: "A", status: "running" });
      await reconcile;
      assert.equal(h.updates.at(-1).status, "completed");
      assert.deepEqual(h.errors, []);
    }
  }],
  ["closing removes pending reconciliation timers", async () => {
    const h = harness();
    const selected = h.selection.select("A");
    h.requests[0].resolve({ run_id: "A", status: "running" });
    await selected;
    h.streams[0].onerror();
    assert.equal(h.timers.size, 1);
    h.selection.close();
    assert.equal(h.timers.size, 0);
    assert.equal(h.streams[0].closed, true);
  }],
];

for (const [name, check] of checks) {
  await check();
  console.log(`PASS ${name}`);
}
console.log(`${checks.length} run-selection checks passed`);
