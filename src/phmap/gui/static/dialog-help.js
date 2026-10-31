"use strict";

// Shared explanations for original controls and their scoped dialog copies.
const dialogFieldHelp = {
  size: "Width and height of each protein/pH image in pixels. Larger images take longer to render.",
  dpi: "Resolution metadata for publication output. Increase tile size, not DPI, to create more image pixels.",
  level: "Symmetric electrostatic potential limits, from the negative value to the positive value. This changes coloring, not the calculation.",
  background: "Background of the rendered surfaces and composed figure. Opaque saved tiles need Re-render before their background can change.",
  ramp: "Colors for negative, neutral, and positive potential, in that order.",
  "force-field": "Atomic charges and radii used by PDB2PQR. Changing the force field requires a full calculation.",
  foreground: "Color of figure labels and borders. Automatic contrast uses white on black backgrounds and black otherwise.",
  "outer-margin": "Space around the complete figure, in pixels.",
  "column-gap": "Horizontal space between pH columns, in pixels.",
  "row-gap": "Vertical space between protein rows, in pixels.",
  "label-gap": "Space between labels and surface images, in pixels.",
  legend: "Show one shared electrostatic potential scale in the final figure.",
  ray: "Ray tracing improves the final surface image but takes longer than quick rendering.",
  "ph-step": "Arrow buttons use half-unit increments. You can type finer steps such as 0.2. The calculation step must be greater than zero.",
  "ph-values": "Separate values with commas or spaces. Their order becomes the column order. Values must be between 0 and 14.",
  camera: "Reuse the saved camera, orient structures automatically, or upload an 18-number PyMOL view in a text file.",
};

class DialogHelp {
  constructor(root = document) {
    this.root = root;
    this.controls = new WeakSet();
    this.dialogs = new WeakSet();
    root.addEventListener("pointerdown", (event) => {
      this.closeOthers(event.target.closest?.(".field-help"));
    });
    root.addEventListener("focusin", (event) => {
      this.closeOthers(event.target.closest?.(".field-help"));
    });
    root.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      const open = this.root.querySelectorAll(".field-help[open]");
      if (!open.length) return;
      event.preventDefault(); // First Escape closes help, not the containing dialog.
      const focused = document.activeElement;
      for (const help of open) {
        help.open = false;
        if (help.contains(focused)) help.querySelector("summary").focus();
      }
    });
  }

  closeOthers(current = null) {
    this.root.querySelectorAll(".field-help[open]").forEach((help) => {
      if (help !== current) help.open = false;
    });
  }

  place(help) {
    const popup = help.querySelector(".field-help-content");
    const bounds = help.closest("dialog").getBoundingClientRect();
    const anchor = help.querySelector("summary").getBoundingClientRect();
    // Keep help inside the modal, including fields in the left grid column.
    if (bounds.width) {
      popup.style.maxWidth = `${Math.max(0, bounds.width - 24)}px`;
      popup.style.removeProperty("transform");
      const rect = popup.getBoundingClientRect();
      const shift = Math.max(bounds.left + 12 - rect.left,
        Math.min(0, bounds.right - 12 - rect.right));
      popup.style.transform = `translateX(${shift}px)`;
    }
    help.dataset.side = bounds.bottom - anchor.bottom < popup.offsetHeight + 8
      && anchor.top - bounds.top > popup.offsetHeight + 8 ? "above" : "below";
  }

  enhance() {
    this.root.querySelectorAll("dialog input[id], dialog select[id]").forEach((control) => {
      if (this.controls.has(control)) return;
      const base = control.id.replace(/^(?:default|artifact)-/, "");
      let hint = this.root.querySelector(`[data-help-for="${control.id}"]`);
      if (!hint && !dialogFieldHelp[base]) return;
      const label = control.labels?.[0];
      if (!label) return;
      const copy = label.cloneNode(true);
      copy.querySelectorAll("input, select").forEach((element) => element.remove());
      const name = copy.textContent.replace(/\s+/g, " ").trim();
      const help = document.createElement("details");
      help.className = "field-help";
      const summary = document.createElement("summary");
      summary.textContent = "?";
      summary.setAttribute("aria-label", `Help for ${name}`);
      // Native toggle events are queued; close the previous help before a
      // keyboard, pointer, or scripted click opens this disclosure.
      summary.addEventListener("click", () => this.closeOthers(help));
      const popup = document.createElement("div");
      popup.className = "field-help-content";
      if (!hint) {
        hint = document.createElement("p");
        hint.id = `${control.id}-help`;
        hint.className = "field-hint";
        hint.textContent = dialogFieldHelp[base];
      }
      const describedBy = new Set((control.getAttribute("aria-describedby") || "").split(" ").filter(Boolean));
      describedBy.add(hint.id);
      control.setAttribute("aria-describedby", [...describedBy].join(" "));
      popup.append(hint);
      help.append(summary, popup);
      const heading = document.createElement("div");
      heading.className = "field-heading";
      if (label.contains(control) && !["checkbox", "radio"].includes(control.type)) {
        const field = document.createElement("div");
        field.className = "field-option";
        label.replaceWith(field);
        label.htmlFor = control.id;
        heading.append(label, help);
        field.append(heading, control);
      } else {
        label.replaceWith(heading);
        heading.append(label, help);
      }
      help.addEventListener("toggle", () => {
        if (help.open) { this.closeOthers(help); this.place(help); }
      });
      this.controls.add(control);
    });
    this.root.querySelectorAll("dialog").forEach((dialog) => {
      if (this.dialogs.has(dialog)) return;
      dialog.addEventListener("close", () => {
        dialog.querySelectorAll(".field-help[open]").forEach((help) => { help.open = false; });
      });
      this.dialogs.add(dialog);
    });
  }
}
