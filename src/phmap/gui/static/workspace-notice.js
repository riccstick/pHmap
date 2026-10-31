"use strict";

// Only a notice bound to this run may be removed by its progress snapshots.
// Later errors and unrelated messages must survive an older job finishing.
class WorkspaceNotice {
  constructor({ root, terminal, onDismiss = () => {} }) {
    Object.assign(this, { root, terminal, onDismiss });
    this.title = root.querySelector("[data-notice-title]");
    this.text = root.querySelector("[data-notice-text]");
    this.progress = null;
    root.querySelector("[data-notice-dismiss]").onclick = () => this.dismiss();
  }

  render(title, text, kind) {
    // Repeated progress snapshots should not re-announce unchanged text.
    if (this.title.textContent !== title) this.title.textContent = title;
    if (this.text.textContent !== text) this.text.textContent = text;
    if (this.root.dataset.kind !== kind) this.root.dataset.kind = kind;
    this.root.hidden = false;
  }

  message(text, error = false) {
    this.progress = null;
    if (!text) { this.dismiss(); return; }
    this.render(error ? "Action needs attention" : "pHmap", text, error ? "error" : "info");
  }

  track(runId, operation) {
    this.progress = { runId, operation };
    this.renderProgress("queued");
  }

  renderProgress(status) {
    const mode = this.progress.operation;
    const titles = mode === "compose" ? ["Figure update queued", "Updating figure"]
      : mode === "render" ? ["Surface re-render queued", "Re-rendering surfaces"]
      : ["Calculation queued", "Calculating surface maps"];
    const detail = mode === "compose" ? "Reusing saved images. Results refresh automatically."
      : mode === "render" ? "Reusing saved electrostatic maps. Results refresh automatically."
      : "Your surface map will appear here when it is ready.";
    this.render(status === "cancelling" ? "Stopping calculation" : titles[status === "queued" ? 0 : 1],
      status === "cancelling" ? "Waiting for the calculation tools to stop." : detail, "progress");
  }

  observe(run) {
    if (!this.progress || this.progress.runId !== run.run_id) return;
    if (this.terminal.has(run.status)) this.dismiss();
    else this.renderProgress(run.status);
  }

  select(runId) {
    if (this.progress && this.progress.runId !== runId) this.dismiss();
  }

  dismiss() {
    this.progress = null;
    if (!this.root.hidden) this.onDismiss();
    this.root.hidden = true;
    this.title.textContent = "";
    this.text.textContent = "";
  }
}
