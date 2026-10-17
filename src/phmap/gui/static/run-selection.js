"use strict";

// Own asynchronous requests and progress streams independently of the page DOM.
class RunSelection {
  constructor({ load, onUpdate, onError, terminal, connect = (url) => new EventSource(url) }) {
    Object.assign(this, { load, onUpdate, onError, terminal, connect });
    this.generation = 0;
    this.abort = null;
    this.stream = null;
    this.timer = null;
  }

  disconnect() {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    const stream = this.stream;
    this.stream = null;
    if (stream) stream.close();
  }

  close() {
    this.generation += 1;
    if (this.abort) this.abort.abort();
    this.abort = null;
    this.disconnect();
  }

  async select(runId) {
    this.close();
    const generation = this.generation;
    const abort = this.abort = new AbortController();
    const current = () => this.generation === generation;
    let finished = false;
    const apply = (run, progress) => {
      if (!current() || finished) return;
      finished = this.terminal.has(run.status);
      this.onUpdate(run, progress);
      if (finished) this.disconnect();
    };
    let run;
    try {
      run = await this.load(runId, { signal: abort.signal });
    } catch (error) {
      if (!current() || error.name === "AbortError") return;
      throw error;
    }
    if (!current()) return;
    apply(run, false);
    if (this.terminal.has(run.status)) return;
    const stream = this.connect(`/api/runs/${encodeURIComponent(runId)}/events`);
    this.stream = stream;
    stream.addEventListener("progress", (event) => {
      if (current() && this.stream === stream) apply(JSON.parse(event.data), true);
    });
    stream.onerror = () => {
      if (!current() || this.stream !== stream || this.timer !== null) return;
      // EventSource reconnects itself; one fetch also reconciles a missed final event.
      this.timer = setTimeout(async () => {
        this.timer = null;
        try {
          const latest = await this.load(runId, { signal: abort.signal });
          apply(latest, true);
        } catch (error) {
          if (current() && !finished && error.name !== "AbortError") this.onError(error);
        }
      }, 1500);
    };
  }
}
