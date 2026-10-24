import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Script, runInNewContext } from "node:vm";

const source = await readFile(new URL("../../src/phmap/gui/static/app.js", import.meta.url), "utf8");
new Script(source); // Also catch syntax errors in the complete page script.
const template = await readFile(new URL("../../src/phmap/gui/templates/index.html", import.meta.url), "utf8");
const formFunction = source.match(/function formData\(\) \{[\s\S]*?\n\}/)?.[0];
assert.ok(formFunction, "Test the actual form serializer, not a duplicate implementation.");

const controls = Object.fromEntries(Object.entries({
  background: "white", "run-name": "Test", "ph-start": "3", "ph-stop": "8", "ph-step": "1",
  "ph-values": "", size: "500", dpi: "300", level: "5", ramp: "red,white,blue", "force-field": "PARSE",
  "surface-mode": "0", "ligand-representation": "sticks",
  foreground: "auto", "font-size": "22", "outer-margin": "24",
  "column-gap": "16", "row-gap": "16", "label-gap": "16", "ph-label-precision": "",
}).map(([key, value]) => [key, { value }]));
controls.ray = { checked: true };
controls["surface-solvent"] = { checked: false };
controls["surface-ramp-above-mode"] = { checked: true };
controls.legend = { checked: true };
controls.view = { files: [] };
controls.ligand = { files: [] };
class FormDataFixture extends Map {
  append(key, value) { this.set(key, value); }
}
const serialize = runInNewContext(`(${formFunction})`, {
  $: (id) => { assert.ok(controls[id], `Unknown form control: ${id}`); return controls[id]; },
  phMode: () => "range",
  proteinSelection: { inputs: () => ({ labels: ["WT"], structures: [{ pdb_id: "3SQR" }], uploads: [] }) },
  FormData: FormDataFixture,
});
const settings = () => JSON.parse(serialize().get("settings"));
assert.equal(settings().surface_mode, 0);
assert.equal(settings().surface_solvent, false);
assert.equal(settings().surface_ramp_above_mode, true);
assert.equal(settings().ligand_representation, "sticks");
assert.equal(settings().font_size, 22);
assert.equal(settings().outer_margin, 24);
assert.equal(settings().show_colorbar, true);
assert.equal(settings().ph_label_precision, null);
controls.background.value = "black";
assert.equal(settings().foreground, "white");
controls.foreground.value = "#555555";
assert.equal(settings().foreground, "#555555");
controls.background.value = "transparent";
assert.equal(settings().background, null);
controls["font-size"].value = "42";
controls["outer-margin"].value = "0";
controls["column-gap"].value = "23";
controls["row-gap"].value = "35";
controls["label-gap"].value = "8";
controls["ph-label-precision"].value = "0";
controls.legend.checked = false;
assert.equal(settings().font_size, 42);
assert.equal(settings().outer_margin, 0);
assert.equal(settings().column_gap, 23);
assert.equal(settings().row_gap, 35);
assert.equal(settings().label_gap, 8);
assert.equal(settings().ph_label_precision, 0);
assert.equal(settings().show_colorbar, false);
assert.match(template, /id="legend"[^>]*checked/);

for (let mode = 0; mode <= 4; mode += 1) {
  for (const representation of ["sticks", "lines", "dots", "spheres", "none"]) {
    for (const solvent of [false, true]) {
      for (const above of [false, true]) {
        controls["surface-mode"].value = String(mode);
        controls["ligand-representation"].value = representation;
        controls["surface-solvent"].checked = solvent;
        controls["surface-ramp-above-mode"].checked = above;
        const result = settings();
        assert.equal(result.surface_mode, mode);
        assert.equal(result.surface_solvent, solvent);
        assert.equal(result.surface_ramp_above_mode, above);
        assert.equal(result.ligand_representation, representation === "none" ? null : representation);
      }
    }
  }
}
// Keep the rendered form's choices/defaults aligned with the request serializer.
for (const [id, values] of Object.entries({
  "surface-mode": ["0", "1", "2", "3", "4"],
  "ligand-representation": ["sticks", "lines", "dots", "spheres", "none"],
})) {
  const select = template.match(new RegExp(`<select id="${id}"[^>]*>([\\s\\S]*?)</select>`));
  assert.ok(select, id);
  assert.deepEqual([...select[1].matchAll(/<option value="([^"]+)"/g)].map((m) => m[1]), values);
}
assert.match(template, /id="surface-ramp-above-mode"[^>]*checked/);
assert.doesNotMatch(template, /id="surface-solvent"[^>]*checked/);
console.log("Render-settings checks passed (all modes, representations, and boolean combinations)");
