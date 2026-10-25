from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
from pathlib import Path
from unittest.mock import Mock

import pytest
from fake_structure import CIF, StructureResponse
from fake_tools import fake_tools as _fake_tools
from fake_tools import tool as _executable
from fastapi.testclient import TestClient

from phmap.cli import main
from phmap.gui.app import create_app
from phmap.gui.jobs import JobManager, write_json


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    _fake_tools(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "tools") + os.pathsep + os.environ["PATH"])
    app = create_app(tmp_path / "runs")
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client


def _token(client: TestClient) -> dict[str, str]:
    page = client.get("/")
    assert page.status_code == 200
    match = re.search(r'name="phmap-token" content="([^"]+)"', page.text)
    assert match is not None
    return {"X-Phmap-Token": match.group(1)}


def _submit(client: TestClient, *, endpoint: str = "/api/runs", **settings):  # type: ignore[no-untyped-def]
    return client.post(
        endpoint, headers=_token(client),
        data={"settings": json.dumps({"ph_mode": "explicit", "ph_values": "5, 7",
                                      "size": 64, "ray_trace": False, **settings})},
        files=[("proteins", ("protein sample.pdb", b"ATOM\n", "chemical/x-pdb"))],
    )


def _wait(client: TestClient, run_id: str, expected: set[str]) -> dict:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        data = client.get(f"/api/runs/{run_id}").json()
        if data["status"] in expected:
            return data
        time.sleep(0.05)
    pytest.fail(f"Run never reached {expected}: {data}")


def test_gui_loads_the_progress_controller_before_the_page_script(client: TestClient) -> None:
    page = client.get("/").text
    assert page.index("/static/run-selection.js") < page.index("/static/app.js")
    assert page.index("/static/protein-selection.js") < page.index("/static/app.js")
    assert page.index("/static/template-settings.js") < page.index("/static/app.js")
    assert client.get("/static/run-selection.js").status_code == 200
    assert client.get("/static/protein-selection.js").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/template-settings.js").status_code == 200
    assert 'id="use-template"' in page


def test_history_template_restores_inputs_settings_and_creates_a_separate_full_run(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = client.post(
        "/api/runs", headers=_token(client),
        data={"settings": json.dumps({
            "name": "Original comparison", "labels": ["WT", "Variant"],
            "ph_start": "3", "ph_stop": "4", "ph_step": "1", "size": 64,
            "ray_trace": False, "force_field": "AMBER", "surface_mode": 4,
            "background": "red", "foreground": "blue", "ramp_colors": ["green", "white", "orange"],
            "font_size": 30, "show_colorbar": False, "ph_label_precision": 1,
        })},
        files=[("proteins", ("wt.cif", CIF)), ("proteins", ("variant.pdb", b"ATOM\n")),
               ("view", ("view.txt", ", ".join(str(n) for n in range(18)).encode())),
               ("ligand", ("ligand.mol2", b"shared ligand\n"))],
    )
    assert original.status_code == 202, original.text
    original_id = original.json()["run_id"]
    finished = _wait(client, original_id, {"completed", "failed"})
    assert finished["status"] == "completed", finished["error"]
    manifest_url = f"/api/runs/{original_id}/files/manifest.json"
    original_manifest_bytes = client.get(manifest_url).content
    original_manifest = json.loads(original_manifest_bytes)
    original_figure = client.get(f"/api/runs/{original_id}/files/output/pHmap.png").content
    # Saved run inputs, not the queue's original upload paths, must be used.
    for path in {Path(item["original_path"]) for item in original_manifest["inputs"]}:
        if not path.is_relative_to(Path(finished["directory"])):
            path.unlink(missing_ok=True)
    fetch = Mock(side_effect=AssertionError("Saved templates must not download structures"))
    monkeypatch.setattr("phmap.gui.app.fetch_structure", fetch)
    template = client.get(f"/api/runs/{original_id}/template")
    assert template.status_code == 200, template.text
    setup = template.json()
    settings = setup["settings"]
    assert settings["name"] == "Original comparison (copy)"
    assert settings["ph_mode"] == "range"
    assert [settings[key] for key in ("ph_start", "ph_stop", "ph_step")] == ["3", "4", "1"]
    assert settings["reuse_view"] and settings["reuse_ligand"]
    assert settings["surface_mode"] == 4 and settings["force_field"] == "AMBER"
    assert settings["background"] == "red" and settings["foreground"] == "blue"
    assert settings["ramp_colors"] == ["green", "white", "orange"]
    assert settings["font_size"] == 30 and not settings["show_colorbar"]
    assert len(client.get("/api/runs").json()) == 1, "Loading must not submit a job"
    settings.update(
        ph_mode="explicit", ph_values="7.25,4.5", labels=["Changed variant", "Changed WT"],
        surface_mode=2, font_size=40,
    )
    settings["structures"].reverse()
    rerun = client.post(
        "/api/runs", headers=_token(client), data={"settings": json.dumps(settings)},
    )
    assert rerun.status_code == 202, rerun.text
    run_id = rerun.json()["run_id"]
    assert run_id != original_id
    result = _wait(client, run_id, {"completed", "failed"})
    assert result["status"] == "completed", result["error"]
    manifest = client.get(f"/api/runs/{run_id}/files/manifest.json").json()
    config = manifest["configuration"]
    assert [p["id"] for p in config["proteins"]] == ["variant", "wt"]
    assert [p["label"] for p in config["proteins"]] == settings["labels"]
    assert config["ph_values"] == ["7.25", "4.5"]
    assert config["view"] == list(range(18))
    assert config["scientific"]["force_field"] == "AMBER"
    assert config["render"]["surface_mode"] == 2
    assert config["composition"]["font_size"] == 40
    assert manifest["based_on"]["run_id"] == original_id
    assert manifest["based_on"]["operation"] == "full-recalculation"
    assert len(manifest["based_on"]["manifest_sha256"]) == 64
    assert all([stage["name"] for stage in task["stages"]] == ["pdb2pqr", "apbs", "pymol"]
               for task in manifest["tasks"])
    assert (Path(result["directory"]) / "inputs/wt.cif").read_bytes() == CIF
    assert (Path(result["directory"]) / "inputs/wt.mol2").read_bytes() == b"shared ligand\n"
    assert client.get(manifest_url).content == original_manifest_bytes
    assert client.get(f"/api/runs/{original_id}/files/output/pHmap.png").content == original_figure
    fetch.assert_not_called()


@pytest.mark.parametrize("settings", [
    {"structures": [{"saved_protein_id": "wt"}]},
    {"source_run_id": "../outside", "structures": [{"saved_protein_id": "wt"}]},
    {"source_run_id": "source", "structures": [{"saved_protein_id": "../../secret"}]},
    {"source_run_id": "source", "structures": [{"saved_protein_id": "wt", "pdb_id": "3SQR"}]},
    {"reuse_view": True}, {"reuse_ligand": True},
])
def test_invalid_template_references_are_rejected_without_running_tools(
    client: TestClient, settings: dict,
) -> None:
    response = client.post(
        "/api/runs", headers=_token(client), data={"settings": json.dumps(settings)},
    )
    assert response.status_code == 422, response.text
    assert client.get("/api/runs").json() == []


def test_modified_source_input_cannot_be_submitted_as_a_template(client: TestClient) -> None:
    source = _submit(client).json()["run_id"]
    result = _wait(client, source, {"completed", "failed"})
    assert result["status"] == "completed", result["error"]
    template = client.get(f"/api/runs/{source}/template").json()
    path = next((Path(result["directory"]) / "inputs").glob("*.pdb"))
    path.write_bytes(b"Changed after template was loaded\n")
    response = client.post(
        "/api/runs", headers=_token(client), data={"settings": json.dumps(template["settings"])},
    )
    assert response.status_code == 422
    assert "checksum" in response.text
    assert len(client.get("/api/runs").json()) == 1
    assert client.get(f"/api/runs/{source}/template").status_code == 422


def test_non_aligned_range_survives_a_completed_gui_run(client: TestClient) -> None:
    submitted = _submit(
        client, ph_mode="range", ph_start="3", ph_stop="8", ph_step="2",
    )
    assert submitted.status_code == 202, submitted.text
    run_id = submitted.json()["run_id"]
    result = _wait(client, run_id, {"completed", "failed"})
    assert result["status"] == "completed", result["error"]
    assert result["configuration"]["ph_values"] == ["3", "5", "7"]
    assert result["configuration"]["ph_range"] == {"start": "3", "stop": "8", "step": "2"}
    settings = client.get(f"/api/runs/{run_id}/template").json()["settings"]
    assert (settings["ph_start"], settings["ph_stop"], settings["ph_step"]) == ("3", "8", "2")


def test_cancelled_queued_job_can_restore_and_rerun_its_prepared_setup(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = threading.Event(), threading.Event()
    execute = JobManager._execute

    def hold_first(self: JobManager, run_id: str) -> None:
        entered.set()
        assert release.wait(10)
        with self._condition:
            self._jobs[run_id]["status"] = "completed"
            self._save(run_id)

    monkeypatch.setattr(JobManager, "_execute", hold_first)
    try:
        assert _submit(client).status_code == 202
        assert entered.wait(2)
        queued = client.post(
            "/api/runs", headers=_token(client),
            data={"settings": json.dumps({
                "ph_mode": "range", "ph_start": "3", "ph_stop": "8", "ph_step": "2",
                "force_field": "AMBER", "font_size": 34, "size": 64, "ray_trace": False,
            })},
            files=[("proteins", ("protein.pdb", b"ATOM\n")),
                   ("ligand", ("ligand.mol2", b"ligand contents\n")),
                   ("view", ("view.txt", ", ".join(str(n) for n in range(18)).encode()))],
        )
        assert queued.status_code == 202, queued.text
        source_id = queued.json()["run_id"]
        cancelled = client.post(f"/api/runs/{source_id}/cancel", headers=_token(client))
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["can_template"] is True
        assert not Path(cancelled.json()["directory"]).exists()
        template = client.get(f"/api/runs/{source_id}/template")
        assert template.status_code == 200, template.text
        settings = template.json()["settings"]
        assert settings["force_field"] == "AMBER" and settings["font_size"] == 34
        assert (settings["ph_start"], settings["ph_stop"], settings["ph_step"]) == ("3", "8", "2")
        assert settings["reuse_view"] and settings["reuse_ligand"]
        rerun = client.post(
            "/api/runs", headers=_token(client), data={"settings": json.dumps(settings)},
        )
        assert rerun.status_code == 202, rerun.text
        monkeypatch.setattr(JobManager, "_execute", execute)
        release.set()
        result = _wait(client, rerun.json()["run_id"], {"completed", "failed"})
        assert result["status"] == "completed", result["error"]
        config = result["configuration"]
        assert config["ph_values"] == ["3", "5", "7"]
        assert config["view"] == list(range(18))
        assert config["scientific"]["force_field"] == "AMBER"
        manifest = client.get(f"/api/runs/{result['run_id']}/files/manifest.json").json()
        assert manifest["based_on"]["run_id"] == source_id
        assert len(manifest["based_on"]["submission_sha256"]) == 64
    finally:
        release.set()


def test_gui_upload_validation_run_history_and_downloads(client: TestClient) -> None:
    assert client.get("/api/doctor").json()["ready"]
    validation = _submit(client, endpoint="/api/validate")
    assert validation.status_code == 200
    assert validation.json()["total"] == 2
    assert client.get("/api/runs").json() == []

    response = _submit(client, name="Wild type", labels=["WT"])
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    data = _wait(client, run_id, {"completed", "failed"})
    assert data["status"] == "completed", data["error"]
    assert data["completed"] == data["total"] == 2
    assert data["progress"] == 100
    assert [cell["protein"] for cell in data["cells"]] == ["WT", "WT"]
    assert len(data["tiles"]) == 2
    assert client.get("/api/runs").json()[0]["name"] == "Wild type"

    image = client.get(f"/api/runs/{run_id}/files/output/pHmap.png?download=true")
    assert image.content.startswith(b"\x89PNG")
    assert "attachment" in image.headers["content-disposition"]
    manifest = client.get(f"/api/runs/{run_id}/files/manifest.json").json()
    assert manifest["status"] == "completed"
    log = client.get(f"/api/runs/{run_id}/log/{data['logs'][0]}")
    assert log.status_code == 200
    events = client.get(f"/api/runs/{run_id}/events")
    assert "event: progress" in events.text
    assert '"status":"completed"' in events.text


def test_gui_advanced_settings_reach_validation_worker_manifest_and_script(
    client: TestClient,
) -> None:
    settings = {
        "surface_mode": 4, "surface_solvent": True,
        "surface_ramp_above_mode": False, "ligand_representation": None,
    }
    validation = _submit(client, endpoint="/api/validate", **settings)
    assert validation.status_code == 200, validation.text
    assert settings.items() <= validation.json()["render"].items()
    submitted = _submit(client, **settings)
    assert submitted.status_code == 202, submitted.text
    run_id = submitted.json()["run_id"]
    data = _wait(client, run_id, {"completed", "failed"})
    assert data["status"] == "completed", data["error"]
    manifest = client.get(f"/api/runs/{run_id}/files/manifest.json").json()
    assert settings.items() <= manifest["configuration"]["render"].items()
    script_path = next(Path(data["directory"]).rglob("render.pml"))
    script = script_path.read_text(encoding="utf-8")
    assert "set surface_mode, 4\n" in script
    assert "set surface_solvent, 1\n" in script
    assert "set surface_ramp_above_mode, 0\n" in script
    assert "molecule and hetatm" not in script


def test_gui_publication_settings_reach_validation_worker_and_manifest(client: TestClient) -> None:
    styles = {
        "font_size": 34, "outer_margin": 0, "column_gap": 27, "row_gap": 39,
        "label_gap": 10, "show_colorbar": False, "ph_label_precision": 1,
        "foreground": "#555555",
    }
    validation = _submit(client, endpoint="/api/validate", ph_values="5.125,7.5", **styles)
    assert validation.status_code == 200, validation.text
    assert styles.items() <= validation.json()["composition"].items()
    assert validation.json()["composition"]["ph_labels"] == ["5.1", "7.5"]
    submitted = _submit(client, ph_values="5.125,7.5", **styles)
    assert submitted.status_code == 202, submitted.text
    run_id = submitted.json()["run_id"]
    result = _wait(client, run_id, {"completed", "failed"})
    assert result["status"] == "completed", result["error"]
    manifest = client.get(f"/api/runs/{run_id}/files/manifest.json").json()
    assert styles.items() <= manifest["configuration"]["composition"].items()
    assert manifest["configuration"]["composition"]["ph_labels"] == ["5.1", "7.5"]
    assert manifest["configuration"]["ph_values"] == ["5.125", "7.5"]
    for task in manifest["tasks"]:
        assert f"--with-ph={task['ph']}" in task["stages"][0]["argv"]


@pytest.mark.parametrize("settings", [
    {"font_size": 0}, {"font_size": 257}, {"font_size": True},
    {"row_gap": -1}, {"column_gap": 1025}, {"outer_margin": 1.5}, {"label_gap": -1},
    {"show_colorbar": "false"}, {"ph_label_precision": 1.5}, {"ph_label_precision": True},
    {"ph_mode": "explicit", "ph_values": "5.11,5.14", "ph_label_precision": 1},
])
def test_gui_rejects_invalid_publication_settings_before_fetching(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, settings: dict,
) -> None:
    fetch = Mock(side_effect=AssertionError("Must not fetch"))
    monkeypatch.setattr("phmap.gui.app.fetch_structure", fetch)
    response = client.post(
        "/api/runs", headers=_token(client),
        data={"settings": json.dumps({"structures": [{"pdb_id": "3SQR"}], **settings})},
    )
    assert response.status_code == 422, response.text
    fetch.assert_not_called()
    assert client.get("/api/runs").json() == []


@pytest.mark.parametrize("settings", [
    {"surface_mode": -1}, {"surface_mode": 5}, {"surface_mode": 1.5},
    {"surface_mode": True}, {"surface_solvent": "false"}, {"surface_solvent": 1},
    {"surface_ramp_above_mode": "off"}, {"ligand_representation": "sticks; quit"},
    {"ligand_representation": "none"},
])
def test_gui_rejects_invalid_advanced_settings_before_fetching_or_queueing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, settings: dict,
) -> None:
    fetch = Mock(side_effect=AssertionError("Must not fetch"))
    monkeypatch.setattr("phmap.gui.app.fetch_structure", fetch)
    response = client.post(
        "/api/runs", headers=_token(client),
        data={"settings": json.dumps({"structures": [{"pdb_id": "3SQR"}], **settings})},
    )
    assert response.status_code == 422, response.text
    assert client.get("/api/runs").json() == []
    fetch.assert_not_called()


def test_gui_rejects_cross_origin_writes_invalid_settings_and_camera(
    client: TestClient,
) -> None:
    assert client.post("/api/runs").status_code == 403
    assert client.get("/api/runs", headers={"Host": "evil.example"}).status_code == 400
    assert client.post(
        "/api/runs", headers={**_token(client), "Origin": "https://evil.example"}
    ).status_code == 403
    assert _submit(client, ph_values="7,7.0").status_code == 422
    assert _submit(client, level=float("nan")).status_code == 422
    assert _submit(client, background="white; quit").status_code == 422
    response = client.post(
        "/api/runs", headers=_token(client),
        files=[("proteins", ("protein.pdb", b"ATOM\n")),
               ("view", ("view.txt", b"set_view (0); quit"))],
    )
    assert response.status_code == 422
    assert client.get("/api/runs").json() == []


def test_multiple_uploads_keep_selected_order_and_labels(client: TestClient) -> None:
    response = client.post(
        "/api/validate", headers=_token(client),
        data={"settings": json.dumps({"labels": ["Mutant", "Wild type"],
                                      "ph_start": "3", "ph_stop": "8", "ph_step": "1"})},
        files=[("proteins", ("mutant.pdb", b"ATOM\n")),
               ("proteins", ("protein.pdb", b"ATOM\n"))],
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == 12
    assert [protein["label"] for protein in data["proteins"]] == ["Mutant", "Wild type"]
    assert data["ph_values"] == ["3", "4", "5", "6", "7", "8"]


def test_pdb_id_fetch_is_protected_and_returns_mmcif_provenance(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = Mock()
    opener.open.side_effect = lambda *args, **kwargs: StructureResponse()
    monkeypatch.setattr("phmap.structures.urllib.request.build_opener", lambda *_: opener)
    assert client.post("/api/structures/fetch", json={"pdb_id": "3sqr"}).status_code == 403
    headers = _token(client)
    assert client.post(
        "/api/structures/fetch", headers=headers, json={"pdb_id": "https://evil.example"},
    ).status_code == 422
    opener.open.assert_not_called()
    response = client.post("/api/structures/fetch", headers=headers, json={"pdb_id": "3sqr"})
    assert response.status_code == 200, response.text
    assert response.json()["pdb_id"] == "3SQR"
    assert response.json()["format"] == "mmcif"
    assert response.json()["source"]["provider"] == "RCSB PDB"
    client.post("/api/structures/fetch", headers=headers, json={"pdb_id": "3SQR"})
    assert opener.open.call_count == 1


def test_gui_mixes_fetched_reference_and_local_variants_in_explicit_row_order(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = Mock()
    opener.open.side_effect = lambda *args, **kwargs: StructureResponse()
    monkeypatch.setattr("phmap.structures.urllib.request.build_opener", lambda *_: opener)
    response = client.post(
        "/api/runs", headers=_token(client),
        data={"settings": json.dumps({
            "ph_mode": "explicit", "ph_values": "7", "size": 64, "ray_trace": False,
            "labels": ["Variant 2", "WT", "Variant 1"],
            "structures": [{"upload_index": 1}, {"pdb_id": "3sqr"}, {"upload_index": 0}],
        })},
        files=[("proteins", ("variant1.pdb", b"ATOM\n")),
               ("proteins", ("variant2.MMCIF", CIF))],
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    run = _wait(client, run_id, {"completed", "failed"})
    assert run["status"] == "completed", run["error"]
    assert [row["protein"] for row in run["cells"]] == ["Variant 2", "WT", "Variant 1"]
    manifest = client.get(f"/api/runs/{run_id}/files/manifest.json").json()
    assert [p["format"] for p in manifest["configuration"]["proteins"]] == [
        "mmcif", "mmcif", "pdb",
    ]
    reference = next(entry for entry in manifest["inputs"] if entry.get("source"))
    assert reference["source"]["pdb_id"] == "3SQR"
    assert reference["source"]["sha256"] == reference["sha256"]
    assert client.get(f"/api/runs/{run_id}/files/inputs/variant2.cif").content == CIF
    assert opener.open.call_count == 1  # The isolated CLI worker does not fetch again.


def test_gui_supports_pdb_id_without_any_uploads(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = Mock()
    opener.open.side_effect = lambda *args, **kwargs: StructureResponse()
    monkeypatch.setattr("phmap.structures.urllib.request.build_opener", lambda *_: opener)
    response = client.post(
        "/api/validate", headers=_token(client),
        data={"settings": json.dumps({"structures": [{"pdb_id": "3sqr"}]})},
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 6


@pytest.mark.parametrize("structures", [
    [{"upload_index": 0}, {"upload_index": 0}], [{"upload_index": 1}],
    [{"pdb_id": "3sqr", "upload_index": 0}], [{}], [{"pdb_id": "../3sqr"}],
])
def test_gui_rejects_ambiguous_or_invalid_structure_rows(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, structures: list[dict],
) -> None:
    fetch = Mock(side_effect=AssertionError("Invalid rows must not download anything"))
    monkeypatch.setattr("phmap.gui.app.fetch_structure", fetch)
    assert _submit(client, endpoint="/api/validate", structures=structures).status_code == 422
    fetch.assert_not_called()


def test_gui_returns_clear_not_found_status_for_missing_pdb_entries(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = Mock()
    opener.open.side_effect = urllib.error.HTTPError(
        "https://files.rcsb.org", 404, "Missing", {}, None,
    )
    monkeypatch.setattr("phmap.structures.urllib.request.build_opener", lambda *_: opener)
    response = client.post(
        "/api/structures/fetch", headers=_token(client), json={"pdb_id": "9zzz"},
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


@pytest.mark.parametrize("invalid", ["label", "count", "view", "cif"])
def test_gui_preflights_local_inputs_before_fetching_remote_rows(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, invalid: str,
) -> None:
    fetch = Mock(side_effect=AssertionError("Invalid selections must not download"))
    monkeypatch.setattr("phmap.gui.app.fetch_structure", fetch)
    settings = {"structures": [{"pdb_id": "3sqr"}, {"upload_index": 0}]}
    files = [("proteins", ("variant.pdb", b"ATOM\n"))]
    if invalid == "label":
        settings["labels"] = ["WT", "Invalid/name"]
    elif invalid == "count":
        settings["labels"] = ["Only one label"]
    elif invalid == "view":
        files.append(("view", ("view.txt", b"not a camera view")))
    else:
        files = [("proteins", ("variant.cif", b"not mmcif"))]
    response = client.post(
        "/api/validate", headers=_token(client),
        data={"settings": json.dumps(settings)}, files=files,
    )
    assert response.status_code == 422, response.text
    fetch.assert_not_called()


def test_cli_run_history_is_visible_and_artifact_paths_are_contained(
    client: TestClient, tmp_path: Path,
) -> None:
    root = tmp_path / "runs" / "cli-smoke"
    root.mkdir()
    write_json(root / "manifest.json", {
        "run_id": "cli-smoke", "status": "completed", "created_at": "2026-01-01T00:00:00Z",
        "configuration": {"proteins": [{"id": "wt", "label": "WT"}], "ph_values": ["5"]},
        "tasks": [{"protein_id": "wt", "ph": "5", "status": "completed", "stages": []}],
    })
    outside = tmp_path / "secret.log"
    outside.write_text("private", encoding="utf-8")
    (root / "escaped.log").symlink_to(outside)
    (root / "nested").mkdir()
    data = client.get("/api/runs").json()[0]
    assert data["source"] == "cli"
    assert client.get("/api/runs/cli-smoke").json()["completed"] == 1
    assert client.get("/api/runs/cli-smoke/files/escaped.log").status_code == 404
    escaped = client.get("/api/runs/cli-smoke/files/nested/%2e%2e/%2e%2e/secret.log")
    assert escaped.status_code == 404
    assert client.post("/api/runs/cli-smoke/cancel", headers=_token(client)).status_code == 409


def test_cancel_active_and_queued_jobs_preserves_artifacts(
    client: TestClient, tmp_path: Path,
) -> None:
    _executable(tmp_path, "apbs", r'''
        import pathlib
        import sys
        import time
        if sys.argv[1:] == ["--version"]:
            print("APBS 3.4.1")
            raise SystemExit(0)
        pathlib.Path("started").touch()
        time.sleep(30)
    ''')
    first = _submit(client).json()["run_id"]
    started = tmp_path / "runs" / first / "work" / "protein-sample" / "ph-5" / "started"
    deadline = time.monotonic() + 10
    while not started.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert started.exists()
    active = client.get(f"/api/runs/{first}").json()
    assert active["cells"][0]["stage"] == "apbs"
    second = _submit(client).json()["run_id"]
    assert client.get(f"/api/runs/{second}").json()["status"] == "queued"
    assert client.post(f"/api/runs/{second}/cancel", headers=_token(client)).status_code == 200
    assert _wait(client, second, {"cancelled"})["completed"] == 0
    assert not (tmp_path / "runs" / second).exists()
    client.post(f"/api/runs/{first}/cancel", headers=_token(client))
    data = _wait(client, first, {"cancelled"})
    assert not data["can_cancel"]
    assert started.exists()
    assert not (tmp_path / "runs" / first / "output" / "pHmap.png").exists()
    manifest = json.loads((tmp_path / "runs" / first / "manifest.json").read_text())
    assert manifest["status"] == "cancelled"
    assert manifest["tasks"][0]["status"] == "cancelled"
    assert manifest["tasks"][0]["stages"][-1]["name"] == "apbs"
    assert manifest["tasks"][0]["stages"][-1]["status"] == "cancelled"


def test_queue_restart_marks_abandoned_job_interrupted(tmp_path: Path) -> None:
    records = tmp_path / "runs" / ".gui" / "jobs"
    records.mkdir(parents=True)
    write_json(records / "old-run.json", {
        "run_id": "old-run", "status": "running", "configuration": {},
    })
    manager = JobManager(tmp_path / "runs")
    manager.start()
    try:
        assert manager.snapshot("old-run")["status"] == "interrupted"
    finally:
        manager.close()


def test_gui_command_preserves_cli_options(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr("phmap.gui.app.serve", lambda **kwargs: calls.append(kwargs))
    assert main(["gui", "--port", "9000", "--runs-dir", "my-runs", "--no-browser"]) == 0
    assert calls == [{"runs_dir": Path("my-runs"), "port": 9000, "open_browser": False}]
    assert main(["validate", "example/mutant2.pdb", "--ph-range", "3", "8", "1"]) == 0

    def interrupted(**kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("phmap.gui.app.serve", interrupted)
    assert main(["gui", "--no-browser"]) == 130
