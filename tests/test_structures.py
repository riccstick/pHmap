import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from unittest.mock import Mock

import pytest
from fake_structure import CIF, StructureResponse

from phmap.errors import StructureFetchError, StructureNotFoundError
from phmap.models import InputValidationError
from phmap.structures import (
    copy_structure_source,
    fetch_structure,
    normalize_pdb_id,
    pdb_download_url,
    structure_source,
)


def _cache_contents(path: Path) -> list[Path]:
    return [entry for entry in path.iterdir() if entry.name != ".locks"]


@pytest.fixture
def opener(monkeypatch: pytest.MonkeyPatch) -> Mock:
    opener = Mock()
    opener.open.side_effect = lambda *args, **kwargs: StructureResponse()
    monkeypatch.setattr("phmap.structures.urllib.request.build_opener", lambda *_: opener)
    return opener


@pytest.mark.parametrize(("value", "normalized"), [
    ("3sqr", "3SQR"), (" 3SQR ", "3SQR"), ("pdb_00003sqr", "pdb_00003SQR"),
    ("PDB_00003SQR", "pdb_00003SQR"), ("pdb_10000001", "pdb_10000001"),
])
def test_standard_and_extended_pdb_ids(value: str, normalized: str) -> None:
    assert normalize_pdb_id(value) == normalized
    assert pdb_download_url(value) == f"https://files.rcsb.org/download/{normalized.lower()}.cif"


@pytest.mark.parametrize("value", [
    "", "0000", "3sq", "3sqrr", "3sqr.cif", "../3sqr", "3sqr?x=1", "3sqr\nnext",
    "https://files.rcsb.org/download/3sqr.cif", "pdb_../../etc", None, True,
])
def test_invalid_ids_never_make_requests(value, tmp_path: Path, opener: Mock) -> None:
    with pytest.raises(InputValidationError):
        fetch_structure(value, tmp_path)
    opener.open.assert_not_called()


def test_download_is_cached_with_verified_provenance(tmp_path: Path, opener: Mock) -> None:
    path = fetch_structure("3sqr", tmp_path)
    assert path.name == "3sqr.cif"
    assert path.read_bytes() == CIF
    source = structure_source(path)
    assert source["pdb_id"] == "3SQR"
    assert source["provider"] == "RCSB PDB"
    assert source["url"] == "https://files.rcsb.org/download/3sqr.cif"
    assert source["downloaded_at"]
    assert len(source["sha256"]) == 64
    assert opener.open.call_args.args[0].full_url == source["url"]
    assert opener.open.call_args.kwargs["timeout"] == 20
    assert fetch_structure("3SQR", tmp_path) == path
    assert opener.open.call_count == 1
    fetch_structure("3sqr", tmp_path, refresh=True)
    assert opener.open.call_count == 2


def test_changed_cache_is_not_silently_reused(tmp_path: Path, opener: Mock) -> None:
    path = fetch_structure("3sqr", tmp_path)
    path.write_bytes(b"altered")
    assert structure_source(path) == {}
    assert fetch_structure("3sqr", tmp_path).read_bytes() == CIF
    assert opener.open.call_count == 2


def test_provenance_can_follow_staged_input_copies(tmp_path: Path, opener: Mock) -> None:
    original = fetch_structure("3sqr", tmp_path / "cache")
    copy = tmp_path / "staged.cif"
    copy.write_bytes(original.read_bytes())
    copy_structure_source(original, copy)
    assert structure_source(original) == structure_source(copy)


@pytest.mark.parametrize("body", [b"", b"<html>Error</html>", b"data_missing\n#\n", b"\xff"])
def test_invalid_downloads_are_not_cached(tmp_path: Path, opener: Mock, body: bytes) -> None:
    opener.open.side_effect = lambda *args, **kwargs: StructureResponse(body)
    with pytest.raises(StructureFetchError):
        fetch_structure("3sqr", tmp_path)
    assert _cache_contents(tmp_path) == []


@pytest.mark.parametrize("length", [None, "1000"])
def test_download_limit_is_enforced_with_or_without_a_length_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, opener: Mock, length: str | None,
) -> None:
    monkeypatch.setattr("phmap.structures._MAX_DOWNLOAD", 32)
    opener.open.side_effect = lambda *args, **kwargs: StructureResponse(length=length)
    with pytest.raises(StructureFetchError, match="size limit"):
        fetch_structure("3sqr", tmp_path)
    assert _cache_contents(tmp_path) == []


@pytest.mark.parametrize("failure", [404, 500, 302, "network", "timeout"])
def test_fetch_failures_are_actionable_and_preserve_a_previous_cached_copy(
    tmp_path: Path, opener: Mock, failure: int | str,
) -> None:
    path = fetch_structure("3sqr", tmp_path)
    original_source = structure_source(path)
    if isinstance(failure, int):
        error = urllib.error.HTTPError(pdb_download_url("3sqr"), failure, "Failed", {}, None)
    elif failure == "network":
        error = urllib.error.URLError("Connection failed")
    else:
        error = TimeoutError("Timed out")
    opener.open.side_effect = error
    expected = StructureNotFoundError if failure == 404 else StructureFetchError
    with pytest.raises(expected):
        fetch_structure("3sqr", tmp_path, refresh=True)
    assert path.read_bytes() == CIF
    assert structure_source(path) == original_source
    assert len(_cache_contents(tmp_path)) == 2


def test_headers_without_coordinate_rows_are_not_cached(tmp_path: Path, opener: Mock) -> None:
    header_only = CIF.split(b"ATOM 1")[0] + b"#\n"
    opener.open.side_effect = lambda *args, **kwargs: StructureResponse(header_only)
    with pytest.raises(StructureFetchError, match="coordinate"):
        fetch_structure("3sqr", tmp_path)
    assert _cache_contents(tmp_path) == []


def test_incomplete_response_is_not_cached(tmp_path: Path, opener: Mock) -> None:
    opener.open.side_effect = lambda *args, **kwargs: StructureResponse(length=str(len(CIF) + 1))
    with pytest.raises(StructureFetchError, match="incomplete"):
        fetch_structure("3sqr", tmp_path)
    assert _cache_contents(tmp_path) == []


def test_cache_entry_must_match_the_requested_id(tmp_path: Path, opener: Mock) -> None:
    first = fetch_structure("1crn", tmp_path)
    wrong = tmp_path / "3sqr.cif"
    wrong.write_bytes(first.read_bytes())
    copy_structure_source(first, wrong)
    assert structure_source(wrong)["pdb_id"] == "1CRN"
    fetch_structure("3sqr", tmp_path)
    assert structure_source(wrong)["pdb_id"] == "3SQR"
    assert opener.open.call_count == 2


@pytest.mark.parametrize("failure", ["serialization", "publication"])
def test_refresh_write_failure_preserves_previous_content_and_provenance(
    tmp_path: Path, opener: Mock, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    path = fetch_structure("3sqr", tmp_path)
    previous_source = structure_source(path)
    changed = CIF.replace(b"0.0 0.0 0.0", b"1.0 0.0 0.0")
    opener.open.side_effect = lambda *args, **kwargs: StructureResponse(changed)
    if failure == "serialization":
        monkeypatch.setattr(
            "phmap.structures.write_json", Mock(side_effect=PermissionError("Cannot write")),
        )
    else:
        replace = Path.replace

        def fail_metadata_publish(source: Path, target: Path) -> Path:
            if target == path.with_suffix(".cif.source.json"):
                raise PermissionError("Cannot replace metadata")
            return replace(source, target)

        monkeypatch.setattr(Path, "replace", fail_metadata_publish)
    with pytest.raises(StructureFetchError, match="cache"):
        fetch_structure("3sqr", tmp_path, refresh=True)
    assert path.read_bytes() == CIF
    assert structure_source(path) == previous_source
    assert len(_cache_contents(tmp_path)) == 2


def test_concurrent_requests_for_the_same_id_share_one_download(
    tmp_path: Path, opener: Mock,
) -> None:
    entered = Event()
    release = Event()

    class HeldResponse(StructureResponse):
        def read(self, size: int = -1) -> bytes:
            entered.set()
            assert release.wait(5)
            return super().read(size)

    opener.open.side_effect = lambda *args, **kwargs: HeldResponse()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(fetch_structure, "3sqr", tmp_path)
        try:
            assert entered.wait(2)
            second = pool.submit(fetch_structure, "3sqr", tmp_path)
        finally:
            release.set()
        assert first.result(timeout=5) == second.result(timeout=5)
    assert opener.open.call_count == 1


def test_unrelated_ids_can_download_concurrently(tmp_path: Path, opener: Mock) -> None:
    both_entered = Barrier(2)

    def open_response(*args, **kwargs):  # type: ignore[no-untyped-def]
        both_entered.wait(timeout=3)
        return StructureResponse()

    opener.open.side_effect = open_response
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda code: fetch_structure(code, tmp_path), ["3sqr", "1crn"]))
    assert len(results) == 2
    assert opener.open.call_count == 2
