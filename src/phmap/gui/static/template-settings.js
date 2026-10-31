"use strict";

// Restore values verbatim, including custom CLI colors not in the GUI presets.
function templateFormValues(settings) {
  return {
    values: {
      "run-name": settings.name,
      "ph-start": settings.ph_start, "ph-stop": settings.ph_stop,
      "ph-step": settings.ph_step, "ph-values": settings.ph_values,
      size: settings.size, dpi: settings.dpi, level: settings.level,
      workers: settings.workers ?? 1,
      background: settings.background === null ? "transparent" : settings.background,
      foreground: settings.foreground, ramp: settings.ramp_colors.join(","),
      "force-field": settings.force_field,
      "surface-mode": settings.surface_mode,
      "ligand-representation": settings.ligand_representation === null ? "none" : settings.ligand_representation,
      "font-size": settings.font_size, "outer-margin": settings.outer_margin,
      "column-gap": settings.column_gap, "row-gap": settings.row_gap, "label-gap": settings.label_gap,
      "ph-label-precision": settings.ph_label_precision === null ? "" : settings.ph_label_precision,
    },
    checked: {
      ray: settings.ray_trace, "surface-solvent": settings.surface_solvent,
      "surface-ramp-above-mode": settings.surface_ramp_above_mode, legend: settings.show_colorbar,
    },
    phMode: settings.ph_mode,
  };
}
