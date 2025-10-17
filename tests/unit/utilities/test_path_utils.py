from __future__ import annotations

from pathlib import Path

import pytest

from uvm.utilities.path_utils import (
    InvalidPathError,
    OutsideManagedRootError,
    ensure_managed_env_path,
    expand_user_path,
    get_managed_env_root,
    is_within_home,
    normalize_env_path,
    validate_safe_path,
)


def test_expand_user_path_expands_tilde() -> None:
    """Expand tilde to the user's home directory.

    Verifies that expand_user_path('~') returns Path.home().
    """
    expanded = expand_user_path("~")
    assert expanded == Path.home()


def test_validate_safe_path_rejects_traversal() -> None:
    """Reject paths containing parent directory traversal.

    Ensures validate_safe_path raises InvalidPathError for '../evil'.
    """
    with pytest.raises(InvalidPathError):
        validate_safe_path("../evil")


def test_is_within_home_identifies_descendants() -> None:
    """Identify paths inside and outside the user's home directory.

    Confirms is_within_home returns True for descendants and False for outside paths.
    """
    home = Path.home()
    assert is_within_home(home / "nested" / "dir")
    outside = (
        Path(home.anchor) / "outside-home" if home.anchor else Path("/outside-home")
    )
    assert not is_within_home(outside)


def test_ensure_managed_env_path_round_trips(uvm_home: Path) -> None:
    """Normalize a path under the managed env root.

    Checks that ensure_managed_env_path returns the normalized path for a
    target inside the managed root.
    """
    environ = {"UVM_HOME": str(uvm_home)}
    managed_root = get_managed_env_root(environ)
    target = managed_root / "demo"
    normalized = ensure_managed_env_path(target, environ=environ)
    assert normalized == normalize_env_path(target)


def test_ensure_managed_env_path_rejects_outside_root(uvm_home: Path) -> None:
    """Reject paths outside the managed environment root.

    Ensures ensure_managed_env_path raises OutsideManagedRootError for paths
    outside the managed root.
    """
    environ = {"UVM_HOME": str(uvm_home)}
    outside = uvm_home.parent / "elsewhere"
    with pytest.raises(OutsideManagedRootError):
        ensure_managed_env_path(outside, environ=environ)
