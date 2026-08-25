"""DuckDB connection helper for the Warehouse backend (DATA_SPEC.md §6). No
Streamlit dependency -- kept pure/testable, mirroring data_loader.py's role
for the JSON path."""

from pathlib import Path

import duckdb

WAREHOUSE_PATH = Path(__file__).parent / "data" / "warehouse.duckdb"


def get_connection(
    path: Path = WAREHOUSE_PATH, read_only: bool = False
) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(path), read_only=read_only)
