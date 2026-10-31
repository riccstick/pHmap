"""Execute the rendered page's complete scripts against the real local HTTP API.

The DOM test environment does not render pixels. Native dialog presentation,
file-picker selection, scrolling and EventSource transport are simulated. HTTP
requests, queued workers, models, artifacts and page handlers run their real code;
scientific commands and RCSB replies use the existing offline test fixtures.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from fake_structure import StructureResponse
from fake_tools import fake_tools, tool
from fastapi.testclient import TestClient

from phmap.gui.app import create_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("scenario", [
    "workflow", "calculation-dialog", "artifact-lifecycle", "dialog-help", "dialog-feedback",
    "compact-layout", "ph-controls",
    "first-launch", "first-launch-close", "first-launch-escape",
    "startup-failure", "outdated-server", "stale-main-script", "missing-styles",
])
def test_full_page_ui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str,
) -> None:
    node = shutil.which("node")
    available = (ROOT / "node_modules/jsdom/package.json").is_file()
    if node is None or not available:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail("Full-page UI tests require Node.js and npm ci")
        pytest.skip("Run npm ci with a supported Node.js version for full-page UI tests")
    fake_tools(tmp_path)
    tool_path = str(tmp_path / "tools") + os.pathsep + os.environ["PATH"]
    monkeypatch.setenv("PATH", tool_path)
    opener = Mock()
    opener.open.side_effect = lambda *_args, **_kwargs: StructureResponse()
    monkeypatch.setattr("phmap.structures.urllib.request.build_opener", lambda *_args: opener)
    with TestClient(create_app(tmp_path / "runs"), base_url="http://127.0.0.1") as client:
        page = client.get("/").text
        if scenario == "outdated-server":
            import re

            page = re.sub(r'(name="phmap-ui-version" content=")[^"]+', r'\1', page)
        from html.parser import HTMLParser

        class Assets(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.scripts: list[str] = []
                self.styles: list[str] = []

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                values = dict(attrs)
                if tag == "script" and values.get("src"):
                    self.scripts.append(str(values["src"]))
                if tag == "link" and values.get("rel") == "stylesheet":
                    self.styles.append(str(values["href"]))

        assets = Assets()
        assets.feed(page)
        fixture = {
            "scenario": scenario, "html": page,
            "scripts": [{"url": url, "source": client.get(url).text} for url in assets.scripts],
            "styles": ([] if scenario == "missing-styles"
                       else [client.get(url).text for url in assets.styles]),
        }
        process = subprocess.Popen(
            [node, str(ROOT / "tests/frontend/ui-integration.mjs")], cwd=ROOT,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8",
        )
        assert process.stdin is not None and process.stdout is not None
        timeout = threading.Timer(60, process.kill)
        timeout.start()
        passed = False
        try:
            process.stdin.write(json.dumps(fixture) + "\n")
            process.stdin.flush()
            for line in process.stdout:
                request = json.loads(line)
                if request.get("done"):
                    passed = True
                    break
                if request.get("test_action") == "slow_backend":
                    tool(tmp_path, "apbs", '''
                        import sys
                        import time
                        if sys.argv[1:] == ["--version"]:
                            print("APBS 3.4.1")
                            raise SystemExit(0)
                        time.sleep(30)
                    ''')
                    result: dict[str, Any] = {"status": 200, "json": {"ready": True}}
                elif request.get("test_action") == "tool_availability":
                    monkeypatch.setenv("PATH", tool_path if request["available"] else "")
                    result = {"status": 200, "json": {"ready": True}}
                else:
                    kwargs: dict[str, Any] = {"headers": request.get("headers", {})}
                    if "json" in request:
                        kwargs["json"] = request["json"]
                    if "fields" in request:
                        kwargs["data"] = request["fields"]
                        if request.get("files"):
                            kwargs["files"] = [
                                (item["field"], (item["name"], base64.b64decode(item["data"]),
                                                 item["type"]))
                                for item in request["files"]
                            ]
                    response = client.request(
                        request.get("method", "GET"), request["url"], **kwargs,
                    )
                    result = {"status": response.status_code}
                    if "application/json" in response.headers.get("content-type", ""):
                        result["json"] = response.json()
                    else:
                        result["bytes"] = base64.b64encode(response.content).decode()
                process.stdin.write(json.dumps({"id": request["id"], **result}) + "\n")
                process.stdin.flush()
            process.stdin.close()
            process.wait(timeout=10)
            assert process.stderr is not None
            errors = process.stderr.read()
            assert process.returncode == 0 and passed, errors
        finally:
            timeout.cancel()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
