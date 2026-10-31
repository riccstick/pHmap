"use strict";

// One mapping for per-calculation overrides and the standard-settings editor.
const defaultSettingControls = {
  workers: ["workers", "number"], size: ["size", "number"], dpi: ["dpi", "number"],
  level: ["level", "number"], background: ["background", "background"],
  foreground: ["foreground", "string"], ramp_colors: ["ramp", "colors"],
  force_field: ["force-field", "string"], ray_trace: ["ray", "boolean"],
  surface_mode: ["surface-mode", "number"], surface_solvent: ["surface-solvent", "boolean"],
  surface_ramp_above_mode: ["surface-ramp-above-mode", "boolean"],
  ligand_representation: ["ligand-representation", "representation"],
  font_size: ["font-size", "number"], outer_margin: ["outer-margin", "number"],
  column_gap: ["column-gap", "number"], row_gap: ["row-gap", "number"],
  label_gap: ["label-gap", "number"], show_colorbar: ["legend", "boolean"],
  ph_label_precision: ["ph-label-precision", "precision"],
};

function readDefaultSettings(control) {
  return Object.fromEntries(Object.entries(defaultSettingControls).map(([key, [id, type]]) => {
    const element = control(id), value = element.value;
    const converted = type === "boolean" ? element.checked
      : type === "number" ? Number(value)
      : type === "precision" ? (value === "" ? null : Number(value))
      : type === "background" ? (value === "transparent" ? null : value)
      : type === "representation" ? (value === "none" ? null : value)
      : type === "colors" ? value.split(",") : value;
    return [key, converted];
  }));
}

function applyDefaultSettings(settings, control) {
  for (const [key, [id, type]] of Object.entries(defaultSettingControls)) {
    const element = control(id), value = settings[key];
    if (type === "boolean") { element.checked = value; continue; }
    const text = type === "background" && value === null ? "transparent"
      : type === "representation" && value === null ? "none"
      : type === "precision" && value === null ? ""
      : type === "colors" ? value.join(",") : String(value);
    if (element.tagName === "SELECT" && !Array.from(element.options).some((option) => option.value === text)) {
      const option = element.ownerDocument.createElement("option");
      option.value = text;
      option.textContent = `${text} (saved)`;
      element.append(option);
    }
    element.value = text;
  }
}

// Navigation is presentation-only; server validation remains authoritative.
class CalculationWizard {
  constructor(render, validate) { this.step = 1; this.render = render; this.validate = validate; }
  go(step, force = false) {
    if (!Number.isInteger(step) || step < 1 || step > 3) return false;
    if (!force && step > this.step && !this.validate(this.step, step)) return false;
    this.step = step;
    this.render(step);
    return true;
  }
}
