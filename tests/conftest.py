from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

test_logger = logging.getLogger("test")


@pytest.fixture(scope="session", autouse=True)
def _add_src_to_path() -> Iterator[None]:
    """Ensure the src/ directory is importable during tests."""
    project_root = Path(__file__).resolve().parent.parent
    src_path = project_root / "src"
    sys.path.insert(0, str(src_path))
    try:
        yield
    finally:
        try:
            sys.path.remove(str(src_path))
        except ValueError as e:
            test_logger.error(e)


@pytest.fixture
def uvm_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated UVM_HOME per test to keep registry operations sandboxed."""
    home = tmp_path / "uvm-home"
    home.mkdir()
    monkeypatch.setenv("UVM_HOME", str(home))
    return home


@pytest.fixture
def registry_path(tmp_path: Path) -> Path:
    """Filesystem path for a temporary registry file."""
    """Filesystem path for a temporary registry file."""
    return tmp_path / "registry.json"
