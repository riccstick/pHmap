"use strict";

// Ordered input selection, independent of the DOM and transport.
class ProteinSelection {
  constructor() { this.rows = []; }

  checkCapacity(extra) {
    if (this.rows.length + extra > 32) throw new Error("Use at most 32 protein structures per comparison.");
  }

  uniqueLabel(base) {
    let label = base;
    for (let index = 2; this.rows.some((p) => p.label.toLowerCase() === label.toLowerCase()); index += 1) {
      label = `${base} (${index})`;
    }
    return label;
  }

  addFiles(files) {
    const additions = Array.from(files);
    if (additions.some((file) => !/\.(pdb|cif|mmcif)$/i.test(file.name))) {
      throw new Error("Choose .pdb, .cif or .mmcif protein structure files.");
    }
    this.checkCapacity(additions.length);
    additions.forEach((file) => {
      this.rows.push({ file, label: this.uniqueLabel(file.name.replace(/\.(pdb|cif|mmcif)$/i, "")) });
    });
  }

  addPdb(pdbId) {
    this.checkCapacity(1);
    this.rows.push({ pdbId, label: this.uniqueLabel(pdbId) });
  }

  loadSaved(proteins) {
    if (proteins.length > 32) throw new Error("Use at most 32 protein structures per comparison.");
    const rows = proteins.map((p) => ({ savedProteinId: p.id, filename: p.filename, label: p.label }));
    this.rows.splice(0, this.rows.length, ...rows);
  }

  inputs() {
    let uploadIndex = 0;
    return {
      labels: this.rows.map((p) => p.label),
      structures: this.rows.map((p) => p.file ? { upload_index: uploadIndex++ }
        : p.savedProteinId ? { saved_protein_id: p.savedProteinId } : { pdb_id: p.pdbId }),
      uploads: this.rows.filter((p) => p.file).map((p) => p.file),
    };
  }
}
