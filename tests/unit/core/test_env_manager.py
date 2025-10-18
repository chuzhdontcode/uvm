"""Tests for the env_manager module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from uvm.core import env_manager


class TestCreateEnvironment:
    """Tests for create_environment function."""

    @patch("uvm.core.env_manager.subprocess.run")
    @patch("uvm.core.env_manager.validate_environment")
    @patch("uvm.core.env_manager.get_python_version")
    @patch("uvm.core.env_manager.get_managed_env_root")
    def test_create_environment_success(
        self,
        mock_get_managed_root: MagicMock,
        mock_get_python_version: MagicMock,
        mock_validate_environment: MagicMock,
        mock_subprocess_run: MagicMock,
    ) -> None:
        """Test successful environment creation."""
        # Setup mocks
        mock_get_managed_root.return_value = Path("/home/user/.uvm/envs")
        mock_validate_environment.return_value = True
        mock_get_python_version.return_value = "3.11.0"
        mock_subprocess_run.return_value = MagicMock()

        # Test
        result = env_manager.create_environment(
            name="test-env",
            location=Path("/tmp/test-env"),
            python="3.11",
        )

        # Assertions
        assert result.name == "test-env"
        assert (
            result.location == Path("/tmp/test-env").resolve()
        )  # Account for path normalization
        assert result.python_version == "3.11.0"
        assert result.is_project_local is True  # Not in managed root
        assert result.created_at is not None

        # Verify subprocess was called correctly
        expected_path = str(Path("/tmp/test-env").resolve())
        mock_subprocess_run.assert_called_once_with(
            ["uv", "venv", "--python", "3.11", expected_path],
            capture_output=True,
            text=True,
            check=True,
            env=None,
        )

    @patch("uvm.core.env_manager.subprocess.run")
    def test_create_environment_subprocess_failure(
        self,
        mock_subprocess_run: MagicMock,
    ) -> None:
        """Test environment creation failure due to subprocess error."""
        # Setup mock to raise CalledProcessError
        mock_subprocess_run.side_effect = subprocess.CalledProcessError(
            1,
            ["uv", "venv"],
            stderr="uv command failed",
        )

        # Test
        with pytest.raises(env_manager.EnvironmentCreationError) as exc_info:
            env_manager.create_environment(
                name="test-env",
                location=Path("/tmp/test-env"),
            )

        assert "Failed to create environment" in str(exc_info.value)
        assert "uv command failed" in str(exc_info.value)

    @patch("uvm.core.env_manager.subprocess.run")
    @patch("uvm.core.env_manager.validate_environment")
    def test_create_environment_validation_failure(
        self,
        mock_validate_environment: MagicMock,
        mock_subprocess_run: MagicMock,
    ) -> None:
        """Test environment creation failure due to validation error."""
        # Setup mocks
        mock_subprocess_run.return_value = MagicMock()
        mock_validate_environment.return_value = False

        # Test
        with pytest.raises(env_manager.EnvironmentCreationError) as exc_info:
            env_manager.create_environment(
                name="test-env",
                location=Path("/tmp/test-env"),
            )

        assert "is invalid" in str(exc_info.value)

    def test_create_environment_invalid_python_specifier(self) -> None:
        """Test environment creation failure due to invalid Python version specifier."""
        # Test with malicious input
        with pytest.raises(
            ValueError,
            match=r"Invalid Python version specifier: malicious; rm -rf /",
        ):
            env_manager.create_environment(
                name="test-env",
                location=Path("/tmp/test-env"),
                python="malicious; rm -rf /",
            )

        # Test with special characters
        with pytest.raises(
            ValueError,
            match=r"Invalid Python version specifier: 3.11 && echo hacked",
        ):
            env_manager.create_environment(
                name="test-env",
                location=Path("/tmp/test-env"),
                python="3.11 && echo hacked",
            )


class TestGetPythonVersion:
    """Tests for get_python_version function."""

    def test_get_python_version_success(self, tmp_path: Path) -> None:
        """Test successful Python version extraction."""
        # Create a mock pyvenv.cfg file
        venv_dir = tmp_path / "test-venv"
        venv_dir.mkdir()
        cfg_file = venv_dir / "pyvenv.cfg"

        cfg_content = """home = /usr/bin/python3
implementation = CPython
version_info = 3.11.0.final.0
"""
        cfg_file.write_text(cfg_content)

        # Test
        result = env_manager.get_python_version(venv_dir)

        assert result == "3.11.0.final.0"

    def test_get_python_version_no_cfg_file(self, tmp_path: Path) -> None:
        """Test when pyvenv.cfg file doesn't exist."""
        venv_dir = tmp_path / "test-venv"
        venv_dir.mkdir()

        result = env_manager.get_python_version(venv_dir)

        assert result is None

    def test_get_python_version_no_version_key(self, tmp_path: Path) -> None:
        """Test when pyvenv.cfg doesn't contain version info."""
        venv_dir = tmp_path / "test-venv"
        venv_dir.mkdir()
        cfg_file = venv_dir / "pyvenv.cfg"

        cfg_content = """home = /usr/bin/python3
implementation = CPython
"""
        cfg_file.write_text(cfg_content)

        result = env_manager.get_python_version(venv_dir)

        assert result is None


class TestValidateEnvironment:
    """Tests for validate_environment function."""

    def test_validate_environment_valid(self, tmp_path: Path) -> None:
        """Test validation of a valid environment."""
        # Create mock environment structure
        venv_dir = tmp_path / "test-venv"
        venv_dir.mkdir()

        # Create required files
        (venv_dir / "pyvenv.cfg").write_text("version = 3.11.0")
        scripts_dir = venv_dir / "Scripts"  # Windows-style
        scripts_dir.mkdir()
        (scripts_dir / "python.exe").write_text("# mock python")

        result = env_manager.validate_environment(venv_dir)

        assert result is True

    def test_validate_environment_invalid_no_cfg(self, tmp_path: Path) -> None:
        """Test validation fails when pyvenv.cfg is missing."""
        venv_dir = tmp_path / "test-venv"
        venv_dir.mkdir()

        result = env_manager.validate_environment(venv_dir)

        assert result is False

    def test_validate_environment_invalid_no_scripts(self, tmp_path: Path) -> None:
        """Test validation fails when scripts directory is missing."""
        venv_dir = tmp_path / "test-venv"
        venv_dir.mkdir()
        (venv_dir / "pyvenv.cfg").write_text("version = 3.11.0")

        result = env_manager.validate_environment(venv_dir)

        assert result is False

    def test_validate_environment_invalid_no_python(self, tmp_path: Path) -> None:
        """Test validation fails when python executable is missing."""
        venv_dir = tmp_path / "test-venv"
        venv_dir.mkdir()

        (venv_dir / "pyvenv.cfg").write_text("version = 3.11.0")
        scripts_dir = venv_dir / "Scripts"
        scripts_dir.mkdir()

        result = env_manager.validate_environment(venv_dir)

        assert result is False

    def test_validate_environment_nonexistent_path(self) -> None:
        """Test validation of non-existent path."""
        result = env_manager.validate_environment(Path("/nonexistent/path"))

        assert result is False
