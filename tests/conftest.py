from pathlib import Path

import pytest

from commerce_mock import default_seed_dir
from commerce_mock.store import CommerceStore
from support.service import SupportService


@pytest.fixture
def store() -> CommerceStore:
    return CommerceStore(default_seed_dir())


@pytest.fixture
def service(store: CommerceStore) -> SupportService:
    return SupportService(store)


@pytest.fixture
def seed_bytes() -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in Path(default_seed_dir()).iterdir() if path.is_file()}
