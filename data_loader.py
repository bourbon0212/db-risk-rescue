"""Loads and indexes the mock dataset. No Streamlit dependency — kept pure/testable."""

import json
from pathlib import Path

from models import MockDataset

MOCK_DATA_PATH = Path(__file__).parent / "data" / "mock_data.json"
REAL_DATA_PATH = Path(__file__).parent / "data" / "real_dataset.json"


def load_dataset(path: Path = MOCK_DATA_PATH) -> MockDataset:
    return MockDataset.model_validate(json.loads(path.read_text()))
