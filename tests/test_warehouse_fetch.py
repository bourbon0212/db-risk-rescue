"""Tests for warehouse_fetch.py (DATA_SPEC.md §8.3): fetching the gitignored
data/warehouse.duckdb from the WAREHOUSE_URL secret on a deploy.

Every test here is offline -- requests.get is monkeypatched, per CLAUDE.md's
offline-only directive. The two properties worth protecting are that a failed
download never leaves anything at the destination path (db.py opens it
read-only, so a truncated file would stay broken), and that every failure mode
degrades to a reason string rather than an exception, so app.py can fall
through to Snapshot (SPEC.md §4.2).
"""

import tempfile
from pathlib import Path

import duckdb
import pytest
import requests

import warehouse_fetch
from warehouse_fetch import (
    SECRET_KEY,
    download_warehouse,
    ensure_warehouse,
    looks_like_duckdb,
)


class _FakeResponse:
    """The slice of requests.Response that download_warehouse touches."""

    def __init__(self, body: bytes = b"", status_error: Exception | None = None):
        self._body = body
        self._status_error = status_error

    def raise_for_status(self) -> None:
        if self._status_error is not None:
            raise self._status_error

    def iter_content(self, chunk_size: int):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start : start + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> bool:
        return False


def _serve(monkeypatch, response: _FakeResponse) -> list[str]:
    """Point requests.get at `response`; returns the list it records URLs in."""
    requested_urls: list[str] = []

    def fake_get(url, **kwargs):
        requested_urls.append(url)
        return response

    monkeypatch.setattr(warehouse_fetch.requests, "get", fake_get)
    return requested_urls


def _duckdb_bytes() -> bytes:
    """A real (empty) DuckDB file's bytes -- the magic's offset is a storage
    format detail, so assert against the real thing rather than a handmade
    header that could drift from it."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "probe.duckdb"
        duckdb.connect(str(path)).close()
        return path.read_bytes()


@pytest.fixture(autouse=True)
def _clear_cache():
    # ensure_warehouse is @st.cache_resource: without this, the first test's
    # result would be served to every later one.
    ensure_warehouse.clear()
    yield
    ensure_warehouse.clear()


# --- looks_like_duckdb --------------------------------------------------------

def test_recognizes_a_real_duckdb_file(tmp_path):
    path = tmp_path / "real.duckdb"
    path.write_bytes(_duckdb_bytes())
    assert looks_like_duckdb(path)


def test_rejects_an_html_error_page(tmp_path):
    path = tmp_path / "not_a_db"
    path.write_bytes(b"<!DOCTYPE html><html><body>404 Not Found</body></html>")
    assert not looks_like_duckdb(path)


def test_rejects_a_file_shorter_than_the_header(tmp_path):
    path = tmp_path / "truncated"
    path.write_bytes(b"DU")
    assert not looks_like_duckdb(path)


# --- download_warehouse -------------------------------------------------------

def test_downloads_a_warehouse_into_a_nonexistent_directory(tmp_path, monkeypatch):
    body = _duckdb_bytes()
    urls = _serve(monkeypatch, _FakeResponse(body))
    dest = tmp_path / "data" / "warehouse.duckdb"  # data/ doesn't exist yet

    assert download_warehouse("https://example.invalid/w.duckdb", dest) == dest
    assert dest.read_bytes() == body
    assert urls == ["https://example.invalid/w.duckdb"]
    duckdb.connect(str(dest), read_only=True).close()  # a working database, not just plausible bytes


def test_leaves_nothing_behind_when_the_body_is_not_a_database(tmp_path, monkeypatch):
    _serve(monkeypatch, _FakeResponse(b"<html>Sign in to continue</html>"))
    dest = tmp_path / "warehouse.duckdb"

    with pytest.raises(ValueError, match="DuckDB"):
        download_warehouse("https://example.invalid/w.duckdb", dest)

    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []  # the .part file is cleaned up too


def test_leaves_nothing_behind_when_the_request_fails(tmp_path, monkeypatch):
    _serve(monkeypatch, _FakeResponse(status_error=requests.HTTPError("404 Not Found")))
    dest = tmp_path / "warehouse.duckdb"

    with pytest.raises(requests.HTTPError):
        download_warehouse("https://example.invalid/w.duckdb", dest)

    assert list(tmp_path.iterdir()) == []


def test_a_failed_fetch_does_not_destroy_an_existing_warehouse(tmp_path, monkeypatch):
    """The .part indirection matters most here: dest already holds a good
    database (a local build), and a later failed fetch must not touch it."""
    dest = tmp_path / "warehouse.duckdb"
    dest.write_bytes(_duckdb_bytes())
    _serve(monkeypatch, _FakeResponse(b"<html>500</html>"))

    with pytest.raises(ValueError):
        download_warehouse("https://example.invalid/w.duckdb", dest)

    assert looks_like_duckdb(dest)


# --- ensure_warehouse ---------------------------------------------------------

def test_skips_the_network_entirely_when_the_file_is_already_there(tmp_path, monkeypatch):
    dest = tmp_path / "warehouse.duckdb"
    dest.write_bytes(_duckdb_bytes())

    def fail(*args, **kwargs):
        raise AssertionError("ensure_warehouse must not download over an existing file")

    monkeypatch.setattr(warehouse_fetch.requests, "get", fail)
    monkeypatch.setattr(warehouse_fetch, "warehouse_url", lambda: "https://example.invalid/w")

    assert ensure_warehouse(dest) is None


def test_reports_a_missing_secret_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(warehouse_fetch, "warehouse_url", lambda: None)

    reason = ensure_warehouse(tmp_path / "warehouse.duckdb")

    assert reason is not None and SECRET_KEY in reason


def test_reports_a_failed_download_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(warehouse_fetch, "warehouse_url", lambda: "https://example.invalid/w")
    _serve(monkeypatch, _FakeResponse(status_error=requests.HTTPError("404")))
    dest = tmp_path / "warehouse.duckdb"

    reason = ensure_warehouse(dest)

    assert reason is not None and SECRET_KEY in reason
    assert not dest.exists()


def test_fetches_and_reports_success_when_the_secret_is_set(tmp_path, monkeypatch):
    monkeypatch.setattr(warehouse_fetch, "warehouse_url", lambda: "https://example.invalid/w")
    _serve(monkeypatch, _FakeResponse(_duckdb_bytes()))
    dest = tmp_path / "warehouse.duckdb"

    assert ensure_warehouse(dest) is None
    assert looks_like_duckdb(dest)


# --- warehouse_url ------------------------------------------------------------

def test_a_local_clone_with_no_secrets_file_reports_no_url(monkeypatch):
    """st.secrets raises FileNotFoundError rather than returning a default when
    .streamlit/secrets.toml doesn't exist -- the normal local case."""

    class _Raising:
        def __getitem__(self, key):
            raise FileNotFoundError("No secrets files found")

    monkeypatch.setattr(warehouse_fetch.st, "secrets", _Raising())
    assert warehouse_fetch.warehouse_url() is None


def test_a_secrets_file_without_the_key_reports_no_url(monkeypatch):
    monkeypatch.setattr(warehouse_fetch.st, "secrets", {})
    assert warehouse_fetch.warehouse_url() is None


def test_a_blank_secret_reports_no_url(monkeypatch):
    """An empty placeholder left in the Cloud secrets editor should degrade
    like an absent one, not be requested as a URL."""
    monkeypatch.setattr(warehouse_fetch.st, "secrets", {SECRET_KEY: "   "})
    assert warehouse_fetch.warehouse_url() is None


def test_a_configured_secret_is_stripped_and_returned(monkeypatch):
    monkeypatch.setattr(
        warehouse_fetch.st, "secrets", {SECRET_KEY: " https://example.invalid/w "}
    )
    assert warehouse_fetch.warehouse_url() == "https://example.invalid/w"
