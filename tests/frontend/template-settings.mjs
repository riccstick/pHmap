import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { runInNewContext } from "node:vm";

const source = await readFile(new URL("../../src/phmap/gui/static/template-settings.js", import.meta.url), "utf8");
const restore = runInNewContext(`${source}\ntemplateFormValues;`);
const values = {
  name: "Source (copy)", ph_mode: "explicit", ph_values: "7.125, 5.0", ph_start: "3", ph_stop: "8", ph_step: "1",
  size: 128, dpi: 450, workers: 4, level: 3, background: null, foreground: "#773366", ramp_colors: ["green", "white", "orange"],
  force_field: "AMBER", ray_trace: false, surface_mode: 4, surface_solvent: true,
  surface_ramp_above_mode: false, ligand_representation: null,
  font_size: 34, outer_margin: 0, column_gap: 21, row_gap: 33, label_gap: 10, show_colorbar: false,
  ph_label_precision: 0,
};
const form = restore(values);
assert.equal(form.values["ph-values"], "7.125, 5.0");
assert.equal(form.phMode, "explicit");
assert.equal(form.values.background, "transparent");
assert.equal(form.values.foreground, "#773366");
assert.equal(form.values.ramp, "green,white,orange");
assert.equal(form.values["ligand-representation"], "none");
assert.equal(form.values["ph-label-precision"], 0);
assert.equal(form.values["font-size"], 34);
assert.equal(form.values["outer-margin"], 0);
assert.equal(form.values.workers, 4);
assert.equal(restore({ ...values, workers: undefined }).values.workers, 1);
assert.equal(form.checked.ray, false);
assert.equal(form.checked.legend, false);
assert.equal(form.checked["surface-solvent"], true);
assert.equal(form.checked["surface-ramp-above-mode"], false);
assert.equal(restore({ ...values, ph_label_precision: null }).values["ph-label-precision"], "");
console.log("Template-settings checks passed");
