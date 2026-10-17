from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest

from phmap.json_state import read_json, write_json


def _windows_permission_error(code: int) -> PermissionError:
    error = PermissionError("File is locked")
    error.winerror = code
    return error


def test_state_round_trip_preserves_decimal_and_path_values(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    write_json(state, {"ph": Decimal("5.0"), "path": tmp_path}, sort_keys=True)
    assert read_json(state) == {"ph": "5.0", "path": str(tmp_path)}
    assert state.read_text(encoding="utf-8").endswith("\n")
    assert list(tmp_path.iterdir()) == [state]


@pytest.mark.parametrize("payload", ["not JSON", "[]", "null", '"text"'])
def test_invalid_or_missing_state_is_tolerated(tmp_path: Path, payload: str) -> None:
    state = tmp_path / "state.json"
    assert read_json(state) == {}
    state.write_text(payload, encoding="utf-8")
    assert read_json(state) == {}


def test_failed_write_preserves_previous_state_and_removes_temporary_file(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    write_json(state, {"original": True})
    with pytest.raises(TypeError):
        write_json(state, {"invalid": object()})
    assert read_json(state) == {"original": True}
    assert list(tmp_path.iterdir()) == [state]


def test_concurrent_writers_do_not_share_temporary_files(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda value: write_json(state, {"writer": value}), range(20)))
    assert read_json(state)["writer"] in range(20)
    assert list(tmp_path.iterdir()) == [state]


def test_successful_write_does_not_unlink_the_renamed_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    unlink = Mock(side_effect=_windows_permission_error(5))
    monkeypatch.setattr(Path, "unlink", unlink)
    state = tmp_path / "state.json"

    write_json(state, {"updated": True})

    assert read_json(state) == {"updated": True}
    unlink.assert_not_called()
    assert list(tmp_path.iterdir()) == [state]


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_write_retries_transient_windows_replace_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, winerror: int,
) -> None:
    state = tmp_path / "state.json"
    write_json(state, {"original": True})
    replace = Path.replace
    attempts: list[Path] = []
    sleep = Mock()

    def locked_replace(source: Path, target: Path) -> Path:
        attempts.append(source)
        if len(attempts) <= 2:
            assert read_json(target) == {"original": True}
            raise _windows_permission_error(winerror)
        return replace(source, target)

    monkeypatch.setattr(Path, "replace", locked_replace)
    monkeypatch.setattr("phmap.json_state.time.sleep", sleep)

    write_json(state, {"updated": True})

    assert len(attempts) == 3
    assert len(set(attempts)) == 1
    assert sleep.call_count == 2
    assert read_json(state) == {"updated": True}
    assert list(tmp_path.iterdir()) == [state]


@pytest.mark.parametrize("winerror", [None, 5])
def test_persistent_replace_error_preserves_state_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, winerror: int | None,
) -> None:
    state = tmp_path / "state.json"
    write_json(state, {"original": True})
    error = (
        PermissionError("Access denied")
        if winerror is None else _windows_permission_error(winerror)
    )
    replace = Mock(side_effect=error)
    sleep = Mock()
    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr("phmap.json_state.time.sleep", sleep)

    with pytest.raises(PermissionError) as caught:
        write_json(state, {"updated": True})

    assert caught.value is error
    if winerror is None:
        sleep.assert_not_called()
        assert replace.call_count == 1
    else:
        assert 1 < replace.call_count <= 10
        assert sleep.call_count == replace.call_count - 1
        assert sum(call.args[0] for call in sleep.call_args_list) <= 1
    assert read_json(state) == {"original": True}
    assert list(tmp_path.iterdir()) == [state]


@pytest.mark.parametrize("persistent", [False, True])
def test_temporary_cleanup_errors_preserve_the_original_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, persistent: bool,
) -> None:
    state = tmp_path / "state.json"
    write_json(state, {"original": True})
    unlink = Path.unlink
    attempts = 0
    sleep = Mock()

    def locked_unlink(path: Path, *, missing_ok: bool = False) -> None:
        nonlocal attempts
        attempts += 1
        if persistent or attempts <= 2:
            raise _windows_permission_error(5)
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    monkeypatch.setattr("phmap.json_state.time.sleep", sleep)

    with pytest.raises(TypeError, match="not JSON serializable"):
        write_json(state, {"invalid": object()})

    assert read_json(state) == {"original": True}
    if persistent:
        assert 1 < attempts <= 10
        assert sum(call.args[0] for call in sleep.call_args_list) <= 1
        assert len(list(tmp_path.glob(".state.json.*.tmp"))) == 1
    else:
        assert attempts == 3
        assert list(tmp_path.iterdir()) == [state]
