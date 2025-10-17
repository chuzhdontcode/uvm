from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping
from pathlib import Path

__all__ = [
    "InvalidPathError",
    "OutsideManagedRootError",
    "UVM_HOME_ENV_VAR",
    "get_uvm_home",
    "get_managed_env_root",
    "expand_user_path",
    "normalize_env_path",
    "validate_safe_path",
    "ensure_managed_env_path",
    "is_within_home",
]

PathType = str | os.PathLike[str] | Path

UVM_HOME_ENV_VAR = "UVM_HOME"
_DEFAULT_HOME_SUBDIR = ".uvm"
_MANAGED_ENVS_DIRNAME = "envs"


class InvalidPathError(ValueError):
    """Raised when a supplied path does not meet validation requirements."""


class OutsideManagedRootError(InvalidPathError):
    """Raised when a path falls outside the managed uvm environment root."""


def _as_path(value: PathType) -> Path:
    """Convert a path-like value to a pathlib.Path instance.

    Args:
        value: The path-like value to convert.

    Returns:
        A pathlib.Path instance.

    Raises:
        InvalidPathError: If the value cannot be converted to a path.
    """
    try:
        return Path(value)
    except TypeError as exc:  # pragma: no cover - defensive guard
        raise InvalidPathError("Unsupported path-like value.") from exc


def expand_user_path(value: PathType) -> Path:
    """Expand a user path containing '~' into a Path without resolving symlinks.

    Args:
        value: Path-like input possibly containing a leading '~' to expand.

    Returns:
        The expanded pathlib.Path instance.

    Raises:
        InvalidPathError: If the resulting path is empty.
    """
    path = _as_path(value).expanduser()
    if not path.parts:
        raise InvalidPathError("Path cannot be empty.")
    return path


def normalize_env_path(value: PathType) -> Path:
    """Normalize an environment path to an absolute, canonical Path.

    This expands the user component and resolves symlinks while preserving
    non-existent paths.

    Args:
        value: The path-like value to normalize.

    Returns:
        An absolute, canonical pathlib.Path instance (resolve(strict=False)).
    """
    path = expand_user_path(value)
    # Path.resolve(strict=False) keeps non-existent paths while canonicalising.
    return path.resolve(strict=False)


def _contains_traversal(path: Path) -> bool:
    """Return whether the given Path contains parent directory traversal ('..').

    Args:
        path: The pathlib.Path to inspect.

    Returns:
        True if any path component is '..', False otherwise.
    """
    return any(part == ".." for part in path.parts)


def validate_safe_path(value: PathType, *, allow_outside_home: bool = False) -> Path:
    """Validate and normalize a path for safe use in uvm.

    The function ensures the path:
    - can be converted to a Path,
    - does not contain NUL bytes,
    - does not contain parent directory traversal ('..'),
    - resolves to an absolute location,
    - optionally is within the current user's home directory.

    Args:
        value: The path-like value to validate.
        allow_outside_home: If True, permit paths outside the user's home.

    Returns:
        The normalized pathlib.Path instance.

    Raises:
        InvalidPathError: If any validation check fails.
    """
    raw = _as_path(value)
    raw_str = str(raw)
    if "\x00" in raw_str:
        raise InvalidPathError("Path cannot contain null bytes.")
    if _contains_traversal(raw):
        raise InvalidPathError("Path cannot contain parent directory traversal ('..').")

    normalized = normalize_env_path(raw)
    if not normalized.is_absolute():
        raise InvalidPathError("Path must resolve to an absolute location.")

    if not allow_outside_home and not is_within_home(normalized):
        raise InvalidPathError("Path must be within the current user's home directory.")

    return normalized


def is_within_home(value: PathType) -> bool:
    """Determine whether a path is located inside the current user's home directory.

    Args:
        value: Path-like value to check.

    Returns:
        True if the resolved path is a descendant of the user's $HOME, False otherwise.
    """
    path = normalize_env_path(value)
    home = Path.home().resolve(strict=False)
    try:
        path.relative_to(home)
    except ValueError:
        return False
    return True


def get_uvm_home(
    environ: Mapping[str, str] | MutableMapping[str, str] | None = None,
) -> Path:
    """Get the UVM root directory used to store configuration and environments.

    The function checks the UVM_HOME environment variable first; if set, its
    value is validated (paths outside home are allowed). Otherwise it returns
    the default location under the user's home directory (~/.uvm).

    Args:
        environ: Optional mapping to read environment variables from
                        (defaults to os.environ).

    Returns:
        A pathlib.Path pointing to the uvm home directory (not strictly resolved).
    """
    env = os.environ if environ is None else environ
    raw_home = env.get(UVM_HOME_ENV_VAR)
    if raw_home:
        return validate_safe_path(raw_home, allow_outside_home=True)

    default_home = Path.home() / _DEFAULT_HOME_SUBDIR
    return default_home.resolve(strict=False)


def get_managed_env_root(
    environ: Mapping[str, str] | MutableMapping[str, str] | None = None,
) -> Path:
    """Return the directory where uvm stores managed environments.

    Args:
        environ: Optional environment mapping to pass to get_uvm_home.

    Returns:
        The pathlib.Path to the managed environments root (uvm_home / 'envs').
    """
    return (get_uvm_home(environ) / _MANAGED_ENVS_DIRNAME).resolve(strict=False)


def ensure_managed_env_path(
    value: PathType,
    *,
    environ: Mapping[str, str] | MutableMapping[str, str] | None = None,
) -> Path:
    """Normalize and ensure the path resides under the managed environment directory.

    The function validates and normalizes the provided path and then confirms
    it is a descendant of the managed environments root (uvm_home / 'envs').

    Args:
        value: The path-like environment location to validate.
        environ: Optional environment mapping to pass to get_managed_env_root.

    Returns:
        The normalized pathlib.Path instance when it is under the managed root.

    Raises:
        OutsideManagedRootError: If the path is not inside the managed environment root.
    """
    normalized = validate_safe_path(value, allow_outside_home=True)
    root = get_managed_env_root(environ)
    try:
        normalized.relative_to(root)
    except ValueError as exc:
        raise OutsideManagedRootError(
            f"Environment path '{normalized}' is outside managed root '{root}'.",
        ) from exc
    return normalized
