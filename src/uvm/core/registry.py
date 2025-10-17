"""Registry module for managing and persisting Python environment entries.

This module defines the EnvironmentEntry dataclass and the Registry class
which handle loading, validating, and atomically persisting a JSON-backed
registry of discovered Python environments. It provides helpers for safe
file locking, permission handling, and (de)serialisation of entries.

Docstrings follow the Google style.
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, cast
from uuid import uuid4

from uvm import log

from ..utilities.path_utils import (
    ensure_managed_env_path,
    get_uvm_home,
    normalize_env_path,
    validate_safe_path,
)

__all__ = [
    "EnvironmentEntry",
    "Registry",
    "RegistryError",
    "RegistryCorruptedError",
    "EntryAlreadyExistsError",
    "EntryNotFoundError",
]

REGISTRY_FILENAME = "registry.json"
REGISTRY_VERSION = 1


class RegistryError(RuntimeError):
    """Base class for registry-related errors."""


class RegistryCorruptedError(RegistryError):
    """Raised when the registry file contains invalid or unreadable data."""


class EntryAlreadyExistsError(RegistryError):
    """Raised when attempting to create a duplicate registry entry."""


class EntryNotFoundError(RegistryError):
    """Raised when requesting an entry that is not present in the registry."""


def _ensure_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Ensure metadata is a valid dictionary with string keys.

    Args:
        metadata: The metadata mapping to validate and coerce.

    Returns:
        A dictionary with validated string keys and their values.

    Raises:
        ValueError: If metadata is not a mapping or contains non-string keys.
    """
    if not metadata:
        return {}
    if not isinstance(metadata, Mapping):
        raise ValueError("metadata must be a mapping.")
    coerced: dict[str, Any] = {}
    for key, value in metadata.items():
        if not isinstance(key, str):
            raise ValueError("metadata keys must be strings.")
        coerced[key] = value
    return coerced


@dataclass
class EnvironmentEntry:
    """Represents a registered Python environment entry.

    Attributes:
        name: The unique name of the environment.
        location: The filesystem path to the environment.
        python_version: The Python version associated with the environment.
        created_at: The timestamp when the environment was created.
        is_project_local: Whether this environment is local to a project.
        metadata: Additional metadata associated with the environment.
    """

    name: str
    location: Path
    python_version: str | None = None
    created_at: datetime | None = None
    is_project_local: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Postprocess parameters after init.

        Raises:
            ValueError: If name of the environment is missing.
        """
        name = self.name.strip()
        if not name:
            raise ValueError("Environment name cannot be blank.")
        self.name = name
        self.location = normalize_env_path(self.location)
        if not self.location.exists():
            # Do not require existence during creation, but ensure path validity.
            validate_safe_path(self.location, allow_outside_home=True)

        if self.created_at:
            if self.created_at.tzinfo is None:
                self.created_at = self.created_at.replace(tzinfo=timezone.utc)
            else:
                self.created_at = self.created_at.astimezone(timezone.utc)

        self.metadata = _ensure_metadata(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        """Convert the environment entry to a dictionary representation.

        Returns:
            A dictionary containing all the environment entry attributes.
        """
        data: dict[str, Any] = {
            "name": self.name,
            "location": str(self.location),
            "python_version": self.python_version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "is_project_local": self.is_project_local,
            "metadata": self.metadata,
        }
        return data

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> EnvironmentEntry:
        """Create an EnvironmentEntry instance from a dictionary representation.

        Args:
            payload: A dictionary containing the environment entry data.

        Returns:
            A new EnvironmentEntry instance.

        Raises:
            RegistryCorruptedError: If required fields are missing from the payload.
        """
        try:
            name = payload["name"]
            location_str = payload["location"]
        except KeyError as exc:  # pragma: no cover - defensive guard
            raise RegistryCorruptedError(
                "Registry entry missing required fields.",
            ) from exc

        python_version = payload.get("python_version")
        created_at_raw = payload.get("created_at")
        created_at = datetime.fromisoformat(created_at_raw) if created_at_raw else None
        is_project_local = bool(payload.get("is_project_local", False))
        metadata = payload.get("metadata") or {}

        return cls(
            name=name,
            location=normalize_env_path(location_str),
            python_version=python_version,
            created_at=created_at,
            is_project_local=is_project_local,
            metadata=_ensure_metadata(metadata),
        )

    def with_update(self, **changes: dict[str, Any]) -> EnvironmentEntry:
        """Create a new EnvironmentEntry with updated attributes.

        Args:
            **changes: Keyword arguments representing the attributes to update.

        Returns:
            A new EnvironmentEntry instance with the updated attributes.
        """
        updated = self.to_dict()
        updated.update(changes)
        return EnvironmentEntry.from_dict(updated)


def _serialise_entries(entries: Mapping[str, EnvironmentEntry]) -> dict[str, Any]:
    """Serialize environment entries into a dictionary format.

    Args:
        entries: A mapping of environment names to EnvironmentEntry instances.

    Returns:
        A dictionary containing the registry version and serialized environments.
    """
    return {
        "version": REGISTRY_VERSION,
        "environments": {
            name: entry.to_dict()
            for name, entry in sorted(entries.items(), key=lambda item: item[0])
        },
    }


def _deserialise_entries(payload: Mapping[str, Any]) -> dict[str, EnvironmentEntry]:
    try:
        version = int(payload.get("version", REGISTRY_VERSION))
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive guard
        raise RegistryCorruptedError("Invalid registry version.") from exc

    if version != REGISTRY_VERSION:
        raise RegistryCorruptedError(f"Unsupported registry version: {version}")

    environments = payload.get("environments", {})
    if not isinstance(environments, Mapping):
        raise RegistryCorruptedError("Registry payload missing 'environments' mapping.")

    entries: dict[str, EnvironmentEntry] = {}
    for name, entry_payload in sorted(environments.items(), key=lambda item: item[0]):
        if not isinstance(entry_payload, Mapping):
            raise RegistryCorruptedError("Registry entry must be a mapping.")
        entry = EnvironmentEntry.from_dict(
            {**entry_payload, "name": entry_payload.get("name", name)},
        )
        entries[entry.name] = entry
    return entries


def _ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError as e:
        # On Windows or restricted filesystems chmod may fail.
        log.error(e)


def _apply_file_permissions(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError as e:
        log.error(e)


if os.name == "nt":
    try:
        import msvcrt
    except ImportError:  # pragma: no cover - fallback for exotic interpreters
        msvcrt = None  # type: ignore[assignment]

    def _lock_file_handle(file_obj: io.BufferedRandom) -> None:
        if msvcrt is None:  # pragma: no cover - fallback path
            return
        msvcrt.locking(file_obj.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock_file_handle(file_obj: io.BufferedRandom) -> None:
        if msvcrt is None:  # pragma: no cover - fallback path
            return
        msvcrt.locking(file_obj.fileno(), msvcrt.LK_UNLCK, 1)

else:  # POSIX
    try:
        import fcntl
    except ImportError:  # pragma: no cover - fallback for platforms without fcntl
        fcntl = None  # type: ignore[assignment]

    def _lock_file_handle(file_obj: io.BufferedRandom) -> None:
        if fcntl is None:  # pragma: no cover - fallback path
            return
        fcntl_module = cast(Any, fcntl)
        fcntl_module.flock(file_obj.fileno(), fcntl_module.LOCK_EX)

    def _unlock_file_handle(file_obj: io.BufferedRandom) -> None:
        if fcntl is None:  # pragma: no cover - fallback path
            return
        fcntl_module = cast(Any, fcntl)
        fcntl_module.flock(file_obj.fileno(), fcntl_module.LOCK_UN)


class Registry:
    """Stores the environment details."""

    def __init__(
        self,
        registry_path: Path | None = None,
        *,
        environ: Mapping[str, str] | MutableMapping[str, str] | None = None,
    ) -> None:
        """Stores the environment details.

        Args:
            registry_path (Path | None, optional): Path to the registry.
            Defaults to None.
            environ (Mapping[str, str] | MutableMapping[str, str] | None, optional): Environment variables.
            Defaults to None.
        """  # noqa: E501
        self._environ = os.environ if environ is None else environ
        base_path = get_uvm_home(self._environ)
        default_path = base_path / REGISTRY_FILENAME
        self._path = (
            normalize_env_path(registry_path)
            if registry_path
            else default_path.resolve(strict=False)
        )
        validate_safe_path(self._path, allow_outside_home=True)

        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        self._entries: dict[str, EnvironmentEntry] = {}
        self._loaded = False
        self._memory_lock = RLock()

    @property
    def path(self) -> Path:
        """Return the path to the registry file."""
        return self._path

    def list_environments(self, *, validate: bool = True) -> list[EnvironmentEntry]:
        """List all environment entries, optionally filtering for valid locations.

        Args:
            validate: If True, only return entries with existing filesystem locations.

        Returns:
            A list of environment entries.
        """
        with self._memory_lock:
            self._ensure_loaded()
            entries = list(self._entries.values())

        if not validate:
            return entries

        return [entry for entry in entries if entry.location.exists()]

    def get(self, name: str, *, validate: bool = True) -> EnvironmentEntry | None:
        """Retrieve an environment entry by name.

        Args:
            name: The name of the environment to retrieve.
            validate: If True, return None if the environment's location does not exist.

        Returns:
            The matching EnvironmentEntry, or None if not found or invalid.
        """
        with self._memory_lock:
            self._ensure_loaded()
            entry = self._entries.get(name)

        if entry and validate and not entry.location.exists():
            return None
        return entry

    def add(
        self,
        entry: EnvironmentEntry,
        *,
        overwrite: bool = False,
        require_managed: bool = False,
    ) -> None:
        """Add a new environment entry to the registry.

        Args:
            entry: The EnvironmentEntry to add.
            overwrite: If True, replace an existing entry with the same name.
            require_managed: If True, ensure the environment is in a UVM-managed path.

        Raises:
            EntryAlreadyExistsError: If an entry with the same name exists and
                overwrite is False.
        """
        if require_managed:
            ensure_managed_env_path(entry.location, environ=self._environ)

        with self._memory_lock:
            self._ensure_loaded()
            if not overwrite and entry.name in self._entries:
                raise EntryAlreadyExistsError(
                    f"Environment '{entry.name}' already exists.",
                )
            self._entries[entry.name] = entry
            payload = _serialise_entries(self._entries)

        self._write_payload(payload)

    def update(self, entry: EnvironmentEntry) -> None:
        """Update an existing environment entry.

        Args:
            entry: The environment entry with updated attributes.

        Raises:
            EntryNotFoundError: If no entry with the given name exists.
        """
        with self._memory_lock:
            self._ensure_loaded()
            if entry.name not in self._entries:
                raise EntryNotFoundError(f"Environment '{entry.name}' not found.")
            self._entries[entry.name] = entry
            payload = _serialise_entries(self._entries)

        self._write_payload(payload)

    def remove(self, name: str) -> bool:
        """Remove an environment entry by name.

        Args:
            name: The name of the environment to remove.

        Returns:
            True if the entry was removed, False if it was not found.
        """
        with self._memory_lock:
            self._ensure_loaded()
            if name not in self._entries:
                return False
            del self._entries[name]
            payload = _serialise_entries(self._entries)

        self._write_payload(payload)
        return True

    def sync(self) -> list[str]:
        """Remove all entries with non-existent filesystem locations.

        Returns:
            A list of names of the removed stale entries.
        """
        with self._memory_lock:
            self._ensure_loaded()
            stale = [
                name
                for name, entry in self._entries.items()
                if not entry.location.exists()
            ]
            if not stale:
                return []
            for name in stale:
                del self._entries[name]
            payload = _serialise_entries(self._entries)

        self._write_payload(payload)
        return stale

    def reload(self) -> None:
        """Force a reload of the registry from disk."""
        with self._memory_lock:
            self._loaded = False
            self._entries.clear()
            self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if not self._path.exists():
            self._entries = {}
            self._loaded = True
            return

        with self._file_lock():
            payload = self._read_payload()

        self._entries = payload
        self._loaded = True

    def _read_payload(self) -> dict[str, EnvironmentEntry]:
        if not self._path.exists():
            return {}

        try:
            with self._path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except json.JSONDecodeError as exc:
            raise RegistryCorruptedError("Registry file is not valid JSON.") from exc

        if not isinstance(data, Mapping):
            raise RegistryCorruptedError("Registry file must contain a JSON object.")
        return _deserialise_entries(data)

    def _write_payload(self, payload: dict[str, Any]) -> None:
        _ensure_directory(self._path.parent)
        with self._file_lock():
            tmp_name = f"{self._path.name}.tmp-{uuid4().hex}"
            tmp_path = self._path.with_name(tmp_name)

            try:
                with tmp_path.open("w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2, sort_keys=True)
                    handle.write("\n")
                _apply_file_permissions(tmp_path)
                tmp_path.replace(self._path)
                _apply_file_permissions(self._path)
            except Exception:
                # Clean up the temporary file if something goes wrong.
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError as e:
                        log.error(e)
                raise

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        _ensure_directory(self._lock_path.parent)
        with self._lock_path.open("a+b") as lock_handle:
            try:
                _lock_file_handle(lock_handle)
            except OSError as e:
                # Fallback: proceed without lock if the platform does not support it.
                log.error(e)
            try:
                yield
            finally:
                try:
                    _unlock_file_handle(lock_handle)
                except OSError as e:
                    log.error(e)
