"use strict";

// Separate form state: opening or cancelling a result editor never replaces a
// calculation draft, its input files, or the user's reusable defaults.
class ArtifactEditor {
  static layout = ["font_size", "dpi", "foreground", "background", "outer_margin",
    "column_gap", "row_gap", "label_gap", "show_colorbar", "ph_label_precision"];
  static rendering = ["size", "level", "ramp_colors", "ray_trace", "workers"];
  static surfaces = ["surface_mode", "surface_solvent", "surface_ramp_above_mode", "ligand_representation"];

  constructor({ request, queued, ready }) {
    Object.assign(this, { request, queued, ready });
    this.state = null;
    this.loading = false;
    this.busy = false;
    this.submitting = false;
    this.controller = null;
    this.opener = null;
    this.get = (id) => document.getElementById(`artifact-${id}`);
    this.control = (id) => this.get(id);
    this.dialog = this.get("dialog");
    for (const [group, fields] of [["layout", ArtifactEditor.layout],
      ["render", ArtifactEditor.rendering], ["surface", ArtifactEditor.surfaces]]) {
      for (const field of fields) this.cloneControl(field, this.get(`${group}-fields`));
    }
    this.backgroundField = this.control("background").closest("div");
    this.get("close").onclick = () => this.close();
    this.get("cancel").onclick = () => this.close();
    this.dialog.addEventListener("cancel", (event) => { event.preventDefault(); this.close(); });
    this.get("form").addEventListener("submit", (event) => { event.preventDefault(); this.send(false); });
    this.get("validate").onclick = () => this.send(true);
    this.get("camera").onchange = () => this.syncCamera();
  }

  cloneControl(field, target) {
    const [id] = defaultSettingControls[field];
    const original = document.getElementById(id);
    const sourceLabel = original.closest("label") || document.querySelector(`label[for="${id}"]`);
    const label = sourceLabel.cloneNode(true);
    const input = original.cloneNode(true);
    input.id = `artifact-${id}`;
    input.disabled = false;
    input.removeAttribute("name");
    label.htmlFor = input.id;
    const nested = label.querySelector(`[id="${id}"]`);
    if (nested) nested.replaceWith(input);
    else label.append(input);
    const wrapper = document.createElement("div");
    wrapper.append(label);
    const hints = (original.getAttribute("aria-describedby") || "").split(" ").filter(Boolean);
    if (hints.length) {
      input.setAttribute("aria-describedby", hints.map((hint) => `artifact-${hint}`).join(" "));
      for (const id of hints) {
        const hint = document.getElementById(id).cloneNode(true);
        hint.id = `artifact-${id}`;
        if (hint.hasAttribute("data-help-for")) hint.dataset.helpFor = input.id;
        wrapper.append(hint);
      }
    }
    target.append(wrapper);
  }

  showMessage(text, error = false) {
    const element = this.get("message");
    element.textContent = text;
    element.className = error ? "message error" : "message success";
    element.hidden = !text;
    element.setAttribute("role", error ? "alert" : "status");
    if (error && text) {
      element.tabIndex = -1;
      element.focus({ preventScroll: true });
      element.scrollIntoView({ block: "nearest" });
    }
  }

  updateButtons() {
    const editable = Boolean(this.state?.data) && !this.loading && !this.busy;
    const ready = this.ready(this.state?.mode);
    this.get("body").inert = !editable;
    this.get("validate").disabled = !editable;
    this.get("submit").disabled = !editable || !ready;
    this.get("tools-warning").hidden = !this.state?.data || this.state.mode !== "render" || ready;
    this.get("submit").textContent = this.submitting ? "Starting update…"
      : this.busy ? "Checking settings…"
      : this.state?.mode === "render" ? "Re-render surfaces" : "Update figure";
    this.get("close").disabled = this.submitting;
    this.get("cancel").disabled = this.submitting;
  }

  close() {
    // A submitted update must get its receipt before the dialog can disappear.
    // Closing during a read-only load or validation is safe and aborts that work.
    if (this.submitting) return;
    this.controller?.abort();
    this.controller = null;
    this.state = null;
    this.loading = false;
    this.busy = false;
    const wasOpen = this.dialog.open;
    if (wasOpen) this.dialog.close();
    if (wasOpen && this.opener?.isConnected) this.opener.focus();
  }

  async open(runId, mode, opener) {
    if (this.dialog.open || this.submitting || !["compose", "render"].includes(mode)) return;
    const state = this.state = { runId, mode, data: null, rows: [] };
    const controller = this.controller = new AbortController();
    this.opener = opener;
    this.get("form").reset();
    this.get("body").hidden = true;
    this.get("loading").hidden = false;
    this.get("title").textContent = mode === "render" ? "Re-render surfaces" : "Edit figure";
    this.get("source").textContent = runId;
    this.get("intro").textContent = mode === "render"
      ? "Reuse electrostatic maps; change surface appearance."
      : "Restyle saved images without recalculating.";
    this.showMessage("");
    this.loading = true;
    this.updateButtons();
    this.dialog.showModal();
    try {
      const data = await this.request(`/api/runs/${encodeURIComponent(runId)}/editor?operation=${mode}`, { signal: controller.signal });
      if (this.state !== state || controller.signal.aborted) return;
      state.data = data;
      state.rows = data.proteins.map((row) => ({ ...row, included: true }));
      const keys = [...ArtifactEditor.layout, ...ArtifactEditor.rendering, ...ArtifactEditor.surfaces];
      applyDefaultSettings(data.settings, this.control, keys);
      this.get("source").textContent = data.settings.name;
      this.get("source").title = runId;
      this.get("render-section").hidden = mode !== "render";
      this.get(mode === "render" ? "render-fields" : "layout-fields").append(this.backgroundField);
      this.get("layout-section").open = mode === "compose";
      this.get("selection-section").open = false;
      this.get("ph-values").value = data.settings.ph_values;
      this.get("ph-available").textContent = `Saved pH values: ${data.settings.ph_values}`;
      const lockedBackground = mode === "compose" && data.tile_background !== null;
      this.control("background").disabled = lockedBackground;
      this.get("background-hint").hidden = !lockedBackground;
      this.get("camera").querySelector('[value="saved"]').disabled = !data.has_view;
      this.get("camera").value = data.has_view ? "saved" : "auto";
      this.syncCamera();
      this.renderRows();
      this.get("body").hidden = false;
    } catch (error) {
      if (this.state === state && !controller.signal.aborted) {
        state.data = null;
        this.showMessage(error.message, true);
      }
    } finally {
      if (this.state === state) {
        this.loading = false;
        this.get("loading").hidden = true;
        this.updateButtons();
        if (state.data) this.control(mode === "render" ? "size" : "font-size").focus();
      }
    }
  }

  syncCamera() {
    const upload = this.get("camera").value === "upload";
    this.get("view-label").hidden = !upload;
    this.get("view").required = upload;
  }

  renderRows() {
    const list = this.get("proteins");
    list.replaceChildren();
    this.state.rows.forEach((row, index) => {
      const wrapper = document.createElement("div");
      wrapper.className = "artifact-protein";
      const include = document.createElement("input");
      include.type = "checkbox";
      include.checked = row.included;
      include.setAttribute("aria-label", `Include ${row.id}`);
      include.onchange = () => { row.included = include.checked; };
      const label = document.createElement("input");
      label.type = "text";
      label.value = row.label;
      label.maxLength = 128;
      label.setAttribute("aria-label", `Row label for ${row.id}`);
      label.oninput = () => { row.label = label.value; };
      wrapper.append(include, label);
      for (const [direction, text, description] of [[-1, "↑", "up"], [1, "↓", "down"]]) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "icon-button";
        button.textContent = text;
        button.setAttribute("aria-label", `Move ${row.id} ${description}`);
        button.disabled = index + direction < 0 || index + direction >= this.state.rows.length;
        button.onclick = () => {
          const rows = this.state.rows;
          [rows[index], rows[index + direction]] = [rows[index + direction], rows[index]];
          this.renderRows();
          const moved = list.children[index + direction];
          const next = moved.querySelectorAll("button")[direction === -1 ? 0 : 1];
          const focus = next.disabled ? moved.querySelector('input[type="text"]') : next;
          focus.focus({ preventScroll: true });
          focus.scrollIntoView({ block: "nearest" });
        };
        wrapper.append(button);
      }
      list.append(wrapper);
    });
  }

  formData() {
    const { data, mode, rows } = this.state;
    const fields = mode === "compose" ? ArtifactEditor.layout
      : [...ArtifactEditor.layout, ...ArtifactEditor.rendering, ...ArtifactEditor.surfaces];
    const values = readDefaultSettings(this.control, fields);
    if (values.foreground === "auto") values.foreground = values.background === "black" ? "white" : "black";
    const selected = rows.filter((row) => row.included);
    if (!selected.length) throw new Error("Select at least one saved protein row.");
    if (selected.some((row) => !row.label.trim())) throw new Error("Each selected protein needs a row label.");
    const camera = this.get("camera").value;
    const file = this.get("view").files[0];
    if (mode === "render" && camera === "upload" && !file) throw new Error("Choose a PyMOL camera file, or select automatic orientation.");
    const settings = { ...data.settings, ...values,
      operation: mode, update_in_place: true,
      ph_mode: "explicit", ph_values: this.get("ph-values").value,
      structures: selected.map((row) => ({ saved_protein_id: row.id })),
      labels: selected.map((row) => row.label),
      reuse_view: mode === "compose" ? data.settings.reuse_view : camera === "saved",
    };
    const body = new FormData();
    body.append("settings", JSON.stringify(settings));
    if (mode === "render" && camera === "upload" && file) body.append("view", file, file.name);
    return body;
  }

  async send(validateOnly) {
    if (!this.state?.data || this.loading || this.busy) return;
    const state = this.state;
    const controller = this.controller;
    this.busy = true;
    this.showMessage("");
    this.updateButtons();
    try {
      if (!validateOnly && !this.ready(state.mode)) throw new Error("PyMOL is not ready. Check the tool status before re-rendering.");
      const body = this.formData();
      const plan = await this.request("/api/validate", { method: "POST", body, signal: controller.signal });
      if (this.state !== state || controller.signal.aborted) return;
      if (validateOnly) {
        this.showMessage(`Settings are valid. ${plan.total} saved cells. Reuse: ${plan.reuse.join(", ")}. Run: ${plan.run.join(", ")}.`);
        return;
      }
      this.submitting = true;
      this.updateButtons();
      const result = await this.request("/api/runs", { method: "POST", body });
      this.submitting = false;
      this.close();
      await this.queued(result.run_id, state.mode);
    } catch (error) {
      if (this.state === state && !controller.signal.aborted) {
        const message = /manifest changed|calculation changed/i.test(error.message)
          ? `${error.message} Close and reopen this editor to load the latest result.` : error.message;
        this.showMessage(message, true);
      }
    } finally {
      if (this.state === state) {
        this.busy = false;
        this.submitting = false;
        this.updateButtons();
      }
    }
  }
}
