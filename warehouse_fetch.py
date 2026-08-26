"""Fetches `data/warehouse.duckdb` from a URL at first use, so the Warehouse
backend works on a deploy where the file can't ship with the code
(DATA_SPEC.md §8.3). It's a ~58 MB binary build output and gitignored for the
reasons in `.gitignore`; hosting it as a GitHub Release asset keeps it out of
the repo's history while still leaving it one HTTPS GET away.

Split in two on purpose, the same way `data_loader.py` and `db.py` are kept
free of Streamlit: `download_warehouse()` is plain requests + filesystem and
carries the test coverage, and `ensure_warehouse()` is the thin
Streamlit-aware wrapper that reads the secret and caches the outcome.

Nothing here runs when the file is already on disk -- a local clone that ran
`python -m pipelines.build_warehouse` never touches the network, with or
without a configured URL.
"""

from pathlib import Path

import requests
import streamlit as st

import db

SECRET_KEY = "WAREHOUSE_URL"
DOWNLOAD_TIMEOUT_SECONDS = 30  # per-read, not for the whole transfer
CHUNK_BYTES = 1024 * 1024

# DuckDB writes this magic at byte 8 of its file header. Checking it rejects
# the failure that a status code doesn't: a URL that resolves to an HTML page
# (a private-repo login redirect, an asset renamed between releases) answers
# 200 with a body that would otherwise be saved and then fail much later, deep
# inside duckdb.connect(), as an unreadable-database error.
DUCKDB_MAGIC = b"DUCK"
DUCKDB_MAGIC_OFFSET = 8


def looks_like_duckdb(path: Path) -> bool:
    with open(path, "rb") as handle:
        header = handle.read(DUCKDB_MAGIC_OFFSET + len(DUCKDB_MAGIC))
    return header[DUCKDB_MAGIC_OFFSET:] == DUCKDB_MAGIC


def download_warehouse(
    url: str,
    dest: Path = db.WAREHOUSE_PATH,
    timeout: float = DOWNLOAD_TIMEOUT_SECONDS,
) -> Path:
    """Stream `url` to `dest`, atomically. Raises on any HTTP error or on a
    body that isn't a DuckDB database; the caller decides what to do about it.

    Written to a sibling `.part` file and renamed into place only once the
    whole body has arrived and passed the magic-byte check -- the same
    guarantee `pipelines/download_raw_data.py` gives its downloads, and the
    one that matters here: a half-written file at `dest` would be
    indistinguishable from a built warehouse on the next run, and `db.py`
    opens it read-only, so nothing downstream would ever repair it.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part_path = dest.with_suffix(dest.suffix + ".part")

    try:
        with requests.get(url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            with open(part_path, "wb") as handle:
                for chunk in response.iter_content(chunk_size=CHUNK_BYTES):
                    handle.write(chunk)

        if not looks_like_duckdb(part_path):
            raise ValueError(
                f"{SECRET_KEY} returned something that isn't a DuckDB database "
                "(no DUCK header) -- check that it points at the release asset "
                "itself, and that the release is public"
            )
    except BaseException:
        part_path.unlink(missing_ok=True)
        raise

    part_path.replace(dest)
    return dest


def warehouse_url() -> str | None:
    """The configured `WAREHOUSE_URL` secret, or None if there isn't one.

    `st.secrets` raises rather than returning a default in the two cases that
    both mean "not configured": no secrets file at all (`FileNotFoundError`,
    the normal state of a local clone) and a file without this key
    (`KeyError`). A blank value counts as absent too, so a placeholder entry
    left empty in the Cloud secrets editor degrades instead of 404ing.
    """
    try:
        url = str(st.secrets[SECRET_KEY]).strip()
    except (KeyError, FileNotFoundError):
        return None
    return url or None


@st.cache_resource(show_spinner="Fetching the warehouse database (one-time, ~58 MB)...")
def ensure_warehouse(dest: Path = db.WAREHOUSE_PATH) -> str | None:
    """Make `dest` exist if it can. Returns None once it does, or a short
    lowercase reason phrase for `app.py`'s sidebar warning if it can't.

    Failures are returned rather than raised, and are cached alongside the
    successes: `@st.cache_resource` runs this once per server process, so a
    missing secret or a dead URL costs one attempt, not one per rerun. The
    price is that fixing the URL needs an app reboot to take effect -- the
    right trade when the alternative is re-attempting a 58 MB download on
    every widget interaction.
    """
    if dest.exists():
        return None

    url = warehouse_url()
    if url is None:
        return f"no `{SECRET_KEY}` secret is configured"

    try:
        download_warehouse(url, dest)
    except Exception as exc:  # noqa: BLE001 - any failure degrades the same way
        return f"the `{SECRET_KEY}` download failed ({type(exc).__name__})"
    return None
