from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import pytest

from phmap.json_state import read_json, write_json


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
