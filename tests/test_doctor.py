import subprocess

from phmap.doctor import check_tool


def test_check_tool_reports_missing_executable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("phmap.doctor.shutil.which", lambda _: None)

    result = check_tool("APBS", "apbs", ("--version",))

    assert not result.ok
    assert "not found" in result.message


def test_check_tool_captures_version(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("phmap.doctor.shutil.which", lambda _: "/tools/apbs")
    monkeypatch.setattr(
        "phmap.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "APBS 3.4.1\n", ""),
    )

    result = check_tool("APBS", "apbs", ("--version",))

    assert result.ok
    assert result.path == "/tools/apbs"
    assert result.version == "APBS 3.4.1"
