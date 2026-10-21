from __future__ import annotations

import json
import os
import re
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
    assert client.get("/static/run-selection.js").status_code == 200
    assert client.get("/static/protein-selection.js").status_code == 200
    assert client.get("/static/app.js").status_code == 200


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
