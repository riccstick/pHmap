"""Bounded HTTPS retrieval of PDBx/mmCIF structures from the official RCSB archive."""

from __future__ import annotations

import http.client
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from phmap import __version__
from phmap.errors import ConfigurationError, StructureFetchError, StructureNotFoundError
from phmap.json_state import read_json, write_json
from phmap.locking import LocalLock
from phmap.manifest import sha256_file, utc_now
from phmap.models import InputValidationError
from phmap.structure_formats import read_mmcif

_PDB_ID = re.compile(r"(?:[1-9][a-z0-9]{3}|pdb_[a-z0-9]{8})\Z", re.IGNORECASE)
_MAX_DOWNLOAD = 64 * 1024 * 1024


def normalize_pdb_id(value: str) -> str:
    if not isinstance(value, str) or not _PDB_ID.fullmatch(value.strip()):
        raise InputValidationError("Use a PDB ID such as 3SQR or pdb_00003sqr, not a URL")
    value = value.strip()
    return "pdb_" + value[4:].upper() if value.lower().startswith("pdb_") else value.upper()


def pdb_download_url(pdb_id: str) -> str:
    return f"https://files.rcsb.org/download/{normalize_pdb_id(pdb_id).lower()}.cif"


def _source_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".source.json")


def structure_source(path: Path) -> dict[str, Any]:
    """Only reuse fetch provenance if its ID, URL, and content checksum agree."""

    source = read_json(_source_path(path))
    try:
        pdb_id = normalize_pdb_id(source["pdb_id"])
        if (
            source.get("provider") == "RCSB PDB"
            and source.get("url") == pdb_download_url(pdb_id)
            and isinstance(source.get("downloaded_at"), str)
            and source.get("sha256") == sha256_file(path)
        ):
            return source
    except (KeyError, InputValidationError, OSError):
        pass
    return {}


def copy_structure_source(original: Path, staged: Path) -> None:
    source = structure_source(original)
    if source:
        write_json(_source_path(staged), source)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: urllib.request.Request, fp: Any, code: int, msg: str,
        headers: Any, newurl: str,
    ) -> None:
        # A structure ID must never turn this local service into a general URL proxy.
        return None


def _download(url: str, destination: Path) -> None:
    """Download bounded bytes; parse and cache publication are separate steps."""

    opener = urllib.request.build_opener(_NoRedirect())
    request = urllib.request.Request(url, headers={
        "User-Agent": f"pHmap/{__version__}", "Accept": "text/plain",
    })
    started = time.monotonic()
    try:
        with opener.open(request, timeout=20) as response:
            header = response.headers.get("Content-Length")
            try:
                length = None if header is None else int(header)
            except ValueError as exc:
                raise StructureFetchError("RCSB returned an invalid download length") from exc
            if length is not None and (length < 0 or length > _MAX_DOWNLOAD):
                raise StructureFetchError("RCSB structure exceeds the 64 MiB size limit")
            size = 0
            with destination.open("xb") as output:
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > _MAX_DOWNLOAD:
                        raise StructureFetchError("RCSB structure exceeds the 64 MiB size limit")
                    if time.monotonic() - started > 60:
                        raise StructureFetchError("RCSB structure download timed out")
                    output.write(chunk)
            if length is not None and size != length:
                raise StructureFetchError("RCSB returned an incomplete structure download")
    except http.client.IncompleteRead as exc:
        raise StructureFetchError("RCSB returned an incomplete structure download") from exc


def _publish_cache(download: Path, metadata: Path, destination: Path) -> None:
    """Roll back data publication if its provenance cannot be published too.

    The caller holds a cross-process entry lock. Existing bytes and metadata
    remain usable after a failed refresh, not just after network failures.
    """

    backup = download.parent / "previous.cif"
    had_previous = destination.is_file()
    if had_previous:
        shutil.copy2(destination, backup)
    download.replace(destination)
    try:
        metadata.replace(_source_path(destination))
    except OSError:
        if had_previous:
            backup.replace(destination)
        else:
            destination.unlink(missing_ok=True)
        raise


def _fetch_locked(pdb_id: str, path: Path, *, refresh: bool) -> Path:
    source = structure_source(path) if path.is_file() else {}
    if not refresh and source and normalize_pdb_id(source["pdb_id"]) == pdb_id:
        return path
    with tempfile.TemporaryDirectory(prefix=".download-", dir=path.parent) as directory:
        temporary = Path(directory) / "structure.cif"
        url = pdb_download_url(pdb_id)
        _download(url, temporary)
        try:
            read_mmcif(temporary)
        except ConfigurationError as exc:
            raise StructureFetchError(
                f"RCSB returned an invalid coordinate document: {exc}"
            ) from exc
        source = {
            "provider": "RCSB PDB", "pdb_id": pdb_id, "url": url,
            "downloaded_at": utc_now(), "sha256": sha256_file(temporary),
        }
        metadata = Path(directory) / "source.json"
        write_json(metadata, source)
        _publish_cache(temporary, metadata, path)
    return path


def fetch_structure(pdb_id: str, cache_dir: Path, *, refresh: bool = False) -> Path:
    """Fetch a complete asymmetric-unit mmCIF and retain verified provenance.

    TLS verification and system proxies remain enabled. Locks protect one cache
    entry across both threads and CLI processes, without blocking unrelated IDs.
    """

    pdb_id = normalize_pdb_id(pdb_id)
    cache_dir = cache_dir.expanduser().resolve()
    lock = LocalLock(cache_dir / ".locks" / f"{pdb_id.lower()}.lock")
    try:
        lock.acquire(timeout=90)
        return _fetch_locked(pdb_id, cache_dir / f"{pdb_id.lower()}.cif", refresh=refresh)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise StructureNotFoundError(f"PDB entry {pdb_id} was not found in RCSB PDB") from exc
        raise StructureFetchError(f"RCSB PDB download failed (HTTP {exc.code})") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise StructureFetchError(
            f"Could not download {pdb_id}. Check internet access, VPN/proxy and trusted "
            "certificates, or upload a local .cif, .mmcif or .pdb file instead."
        ) from exc
    except ConfigurationError as exc:
        raise StructureFetchError(f"Structure cache is busy: {exc}") from exc
    except OSError as exc:
        raise StructureFetchError(
            f"Could not access the structure cache at {cache_dir}: {exc}"
        ) from exc
    finally:
        lock.release()
