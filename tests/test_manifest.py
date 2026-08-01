import json
from decimal import Decimal
from pathlib import Path

from phmap.manifest import RunManifest, sha256_file


def test_manifest_records_inputs_tasks_and_outputs(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    original = tmp_path / "original.pdb"
    staged = tmp_path / "staged.pdb"
    output = tmp_path / "result.png"
    original.write_text("ATOM\n", encoding="utf-8")
    staged.write_text("ATOM\n", encoding="utf-8")
    output.write_bytes(b"png-placeholder")

    manifest = RunManifest(manifest_path, "smoke")
    manifest.set_configuration({"ph": [Decimal("5.0"), Decimal("7.0")]})
    manifest.record_input("protein", original, staged)
    task = manifest.start_task("mutant2@5.0", "mutant2", Decimal("5.0"))
    manifest.record_stage(task, {"name": "pdb2pqr", "status": "completed"})
    manifest.finish_task(task, "completed")
    manifest.record_output("figure", output)
    manifest.set_status("completed")

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["status"] == "completed"
    assert data["configuration"]["ph"] == ["5.0", "7.0"]
    assert data["inputs"][0]["sha256"] == sha256_file(staged)
    assert data["tasks"][0]["stages"][0]["name"] == "pdb2pqr"
    assert data["outputs"][0]["sha256"] == sha256_file(output)

