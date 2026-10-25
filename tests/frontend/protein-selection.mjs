import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { runInNewContext } from "node:vm";

const source = await readFile(new URL("../../src/phmap/gui/static/protein-selection.js", import.meta.url), "utf8");
const ProteinSelection = runInNewContext(`${source}\nProteinSelection;`);
const plain = (value) => JSON.parse(JSON.stringify(value));

const selection = new ProteinSelection();
selection.addPdb("3SQR");
selection.addFiles([{ name: "variant.pdb" }, { name: "variant.mmcif" }]);
assert.deepEqual(plain(selection.inputs()), {
  labels: ["3SQR", "variant", "variant (2)"],
  structures: [{ pdb_id: "3SQR" }, { upload_index: 0 }, { upload_index: 1 }],
  uploads: [{ name: "variant.pdb" }, { name: "variant.mmcif" }],
});
// Subsequent file selections append to, rather than replace, the reference and variants.
selection.addFiles([{ name: "another.CIF" }]);
assert.equal(selection.rows.length, 4);
assert.equal(selection.rows[0].pdbId, "3SQR");
assert.throws(() => selection.addFiles([{ name: "variant.pdf" }]), /\.cif/);
assert.equal(selection.rows.length, 4);
selection.rows[2].label = "Mutant";
[selection.rows[0], selection.rows[2]] = [selection.rows[2], selection.rows[0]];
assert.deepEqual(plain(selection.inputs().structures), [
  { upload_index: 0 }, { upload_index: 1 }, { pdb_id: "3SQR" }, { upload_index: 2 },
]);
assert.deepEqual(plain(selection.inputs().labels), ["Mutant", "variant", "3SQR", "another"]);
assert.deepEqual(plain(selection.inputs().uploads.map((file) => file.name)), [
  "variant.mmcif", "variant.pdb", "another.CIF",
]);
while (selection.rows.length < 32) selection.addPdb("3SQR");
assert.throws(() => selection.addPdb("3SQR"), /32/);
assert.throws(() => selection.addFiles([{ name: "overflow.cif" }]), /32/);
assert.equal(selection.rows.length, 32);
const sameRows = selection.rows;
selection.loadSaved([
  { id: "wt", filename: "wt.cif", label: "Wild type" },
  { id: "mut", filename: "mut.pdb", label: "Mutant" },
]);
assert.equal(selection.rows, sameRows, "Keep the array held by the page controller.");
selection.addFiles([{ name: "variant.mmcif" }]);
selection.addPdb("3SQR");
[selection.rows[0], selection.rows[1]] = [selection.rows[1], selection.rows[0]];
assert.deepEqual(plain(selection.inputs().structures), [
  { saved_protein_id: "mut" }, { saved_protein_id: "wt" }, { upload_index: 0 }, { pdb_id: "3SQR" },
]);
assert.equal(selection.inputs().uploads.length, 1);
selection.loadSaved([]);
assert.equal(selection.rows.length, 0);
console.log("Protein-selection checks passed");
