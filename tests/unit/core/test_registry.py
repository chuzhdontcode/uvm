from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from uvm.core.registry import EntryAlreadyExistsError, EnvironmentEntry, Registry
from uvm.utilities.path_utils import OutsideManagedRootError, get_managed_env_root


def _make_entry(name: str, location: Path) -> EnvironmentEntry:
    """Create a test EnvironmentEntry with consistent defaults.

    Args:
        name: The name of the environment.
        location: The filesystem path for the environment.

    Returns:
        An EnvironmentEntry with predictable test values.
    """
    return EnvironmentEntry(
        name=name,
        location=location,
        python_version="3.11",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        metadata={"created_by": "test"},
    )


def test_registry_add_get_and_persist(tmp_path: Path, registry_path: Path) -> None:
    """Verify add, get and persistence behavior of the Registry.

    This test adds an entry, asserts it can be retrieved from the in-memory
    registry, and then creates a fresh Registry instance pointing at the same
    registry file to confirm the entry was persisted to disk.
    """
    env_dir = tmp_path / "envs" / "demo"
    env_dir.mkdir(parents=True)
    entry = _make_entry("demo", env_dir)

    registry = Registry(registry_path=registry_path)
    registry.add(entry)

    fetched = registry.get("demo")
    assert fetched is not None
    assert fetched.name == entry.name
    assert fetched.location == env_dir.resolve(strict=False)

    # Load via a fresh instance to confirm persistence.
    registry_reload = Registry(registry_path=registry_path)
    listed = registry_reload.list_environments()
    assert len(listed) == 1
    assert listed[0].name == "demo"


def test_registry_prevents_duplicates_without_overwrite(
    tmp_path: Path,
    registry_path: Path,
) -> None:
    """Ensure duplicate entries are rejected unless overwrite is specified.

    The registry should raise EntryAlreadyExistsError when adding an entry
    with an existing name and overwrite=False, but accept the operation when
    overwrite=True.
    """
    env_dir = tmp_path / "envs" / "dup"
    env_dir.mkdir(parents=True)
    entry = _make_entry("dup", env_dir)
    registry = Registry(registry_path=registry_path)
    registry.add(entry)

    with pytest.raises(EntryAlreadyExistsError):
        registry.add(_make_entry("dup", env_dir))

    registry.add(_make_entry("dup", env_dir), overwrite=True)
    assert registry.get("dup") is not None


def test_registry_remove_and_sync(tmp_path: Path, registry_path: Path) -> None:
    """Verify removal and sync of stale (missing) entries.

    The test simulates a stale registry entry by removing the environment
    directory on disk and then calling registry.sync(), which should return
    the list of removed names.
    """
    env_dir = tmp_path / "envs" / "obsolete"
    env_dir.mkdir(parents=True)
    entry = _make_entry("obsolete", env_dir)
    registry = Registry(registry_path=registry_path)
    registry.add(entry)

    # Remove the directory to simulate a stale entry.
    env_dir.rmdir()
    removed = registry.sync()
    assert removed == ["obsolete"]
    assert registry.get("obsolete") is None


def test_registry_list_can_skip_validation(tmp_path: Path, registry_path: Path) -> None:
    """Assert list_environments(validate=False) returns entries even if missing.

    By default list_environments() filters out entries whose location no longer
    exists. Passing validate=False should include those entries in the result.
    """
    env_dir = tmp_path / "envs" / "missing"
    env_dir.mkdir(parents=True)
    entry = _make_entry("missing", env_dir)
    registry = Registry(registry_path=registry_path)
    registry.add(entry)

    env_dir.rmdir()
    assert registry.list_environments() == []
    names = [item.name for item in registry.list_environments(validate=False)]
    assert names == ["missing"]


def test_registry_require_managed_environments(
    uvm_home: Path,
    registry_path: Path,
) -> None:
    """Require environments to be located inside the managed UVM root.

    When require_managed=True the registry should accept paths under the
    managed root and raise OutsideManagedRootError for paths outside it.
    """
    environ = {"UVM_HOME": str(uvm_home)}
    managed_root = get_managed_env_root(environ)
    managed_dir = managed_root / "managed"
    managed_dir.mkdir(parents=True)
    registry = Registry(registry_path=registry_path, environ=environ)

    registry.add(_make_entry("managed", managed_dir), require_managed=True)
    assert registry.get("managed") is not None

    outside_dir = uvm_home.parent / "external"
    outside_dir.mkdir(parents=True, exist_ok=True)
    with pytest.raises(OutsideManagedRootError):
        registry.add(_make_entry("external", outside_dir), require_managed=True)
