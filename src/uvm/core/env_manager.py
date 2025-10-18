"""Environment management module for creating and validating virtual environments.

This module provides a high-level interface for creating virtual environments
using `uv venv` and for parsing `pyvenv.cfg` to extract environment metadata.
It handles both project-local and globally managed environments, ensuring they
are correctly structured and registered.
"""

from __future__ import annotations

import re
import subprocess  # nosec: B404
from datetime import datetime, timezone
from pathlib import Path

from uvm.core.registry import EnvironmentEntry
from uvm.utilities.path_utils import get_managed_env_root, normalize_env_path

__all__ = [
    "EnvironmentManagerError",
    "EnvironmentCreationError",
    "InvalidEnvironmentError",
    "create_environment",
    "get_python_version",
    "validate_environment",
]


class EnvironmentManagerError(RuntimeError):
    """Base class for environment manager-related errors."""


class EnvironmentCreationError(EnvironmentManagerError):
    """Raised when environment creation fails."""


class InvalidEnvironmentError(EnvironmentManagerError):
    """Raised when an environment fails validation."""


def create_environment(
    name: str,
    location: Path,
    python: str | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> EnvironmentEntry:
    """Create a new virtual environment at the specified location.

    Args:
        name: The name for the environment entry.
        location: The path where the environment will be created.
        python: Optional Python version specifier (e.g., "3.11", "python3.11").
        environ: Optional environment variables for the subprocess.

    Returns:
        An EnvironmentEntry representing the created environment.

    Raises:
        EnvironmentCreationError: If environment creation fails.
    """
    # Normalize the location path
    env_path = normalize_env_path(location)

    # Validate Python version specifier
    if python and not re.match(r"^[\w\.\-]+$", python):
        raise ValueError(f"Invalid Python version specifier: {python}")

    # Build the uv venv command
    cmd = ["uv", "venv"]
    if python:
        cmd.extend(["--python", python])
    cmd.append(str(env_path))

    # Execute the command
    try:
        subprocess.run(  # nosec: B603
            cmd,
            capture_output=True,
            text=True,
            check=True,
            env=environ,
        )
    except subprocess.CalledProcessError as exc:
        raise EnvironmentCreationError(
            f"Failed to create environment at '{env_path}': {exc.stderr}",
        ) from exc

    # Validate the created environment
    if not validate_environment(env_path):
        raise EnvironmentCreationError(
            f"Created environment at '{env_path}' is invalid",
        )

    # Extract Python version
    python_version = get_python_version(env_path)

    # Determine if this is a project-local environment
    managed_root = get_managed_env_root(environ)
    is_project_local = not env_path.is_relative_to(managed_root)

    # Import here to avoid circular import
    from .registry import EnvironmentEntry

    # Create the EnvironmentEntry
    return EnvironmentEntry(
        name=name,
        location=env_path,
        python_version=python_version,
        created_at=datetime.now(timezone.utc),
        is_project_local=is_project_local,
    )


def get_python_version(env_path: Path) -> str | None:
    """Extract the Python version from a virtual environment's pyvenv.cfg.

    Args:
        env_path: Path to the virtual environment directory.

    Returns:
        The Python version string (e.g., "3.11.0") or None if not found.
    """
    cfg_path = env_path / "pyvenv.cfg"
    if not cfg_path.exists():
        return None

    try:
        with cfg_path.open("r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None

    # Parse the pyvenv.cfg file
    # Look for version_info or similar keys
    for line in content.splitlines():
        line = line.strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        # Common keys that contain version information
        if key in ("version", "version_info"):
            return value

    return None


def validate_environment(env_path: Path) -> bool:
    """Validate that a directory contains a valid virtual environment.

    Args:
        env_path: Path to the directory to validate.

    Returns:
        True if the directory appears to be a valid virtual environment.
    """
    if not env_path.exists() or not env_path.is_dir():
        return False

    # Check for pyvenv.cfg
    if not (env_path / "pyvenv.cfg").exists():
        return False

    # Check for activation scripts directory
    # On Windows: Scripts/, on Unix: bin/
    scripts_dir = env_path / "Scripts" if Path.cwd().drive else env_path / "bin"
    if not scripts_dir.exists() or not scripts_dir.is_dir():
        return False

    # Check for python executable in scripts directory
    python_exe = (
        scripts_dir / "python.exe" if Path.cwd().drive else scripts_dir / "python"
    )
    return python_exe.exists()
