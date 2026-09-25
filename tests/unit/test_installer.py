"""Tests for the install.py CLI and core.installer service layer.

Covers:
  - Port validation (install.py)
  - Positive integer validation (install.py)
  - Log level validation (install.py)
  - CLI state persistence (install.py _save_state / _load_state / _clear_state)
  - Shared installer write_env_file (core.installer)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TestPortValidation:
    def test_valid_port(self):
        from install import _validate_port
        ok, msg = _validate_port("5432")
        assert ok is True

    def test_port_too_high(self):
        from install import _validate_port
        ok, msg = _validate_port("99999")
        assert ok is False
        assert "1–65535" in msg

    def test_port_zero(self):
        from install import _validate_port
        ok, msg = _validate_port("0")
        assert ok is False

    def test_port_not_integer(self):
        from install import _validate_port
        ok, msg = _validate_port("abc")
        assert ok is False
        assert "integer" in msg.lower()

    def test_port_1(self):
        from install import _validate_port
        ok, _ = _validate_port("1")
        assert ok is True

    def test_port_65535(self):
        from install import _validate_port
        ok, _ = _validate_port("65535")
        assert ok is True


class TestPositiveIntValidation:
    def test_valid(self):
        from install import _validate_positive_int
        ok, _ = _validate_positive_int("8")
        assert ok is True

    def test_zero(self):
        from install import _validate_positive_int
        ok, msg = _validate_positive_int("0")
        assert ok is False
        assert ">= 1" in msg

    def test_negative(self):
        from install import _validate_positive_int
        ok, _ = _validate_positive_int("-5")
        assert ok is False

    def test_not_integer(self):
        from install import _validate_positive_int
        ok, msg = _validate_positive_int("abc")
        assert ok is False


class TestLogLevelValidation:
    def test_valid_levels(self):
        from install import _validate_log_level
        for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            ok, _ = _validate_log_level(level)
            assert ok is True, f"{level} should be valid"

    def test_case_insensitive(self):
        from install import _validate_log_level
        ok, _ = _validate_log_level("debug")
        assert ok is True

    def test_invalid_level(self):
        from install import _validate_log_level
        ok, msg = _validate_log_level("TRACE")
        assert ok is False
        assert "DEBUG" in msg


class TestCLIStatePersistence:
    """Test the CLI's _save_state / _load_state / _clear_state helpers."""

    def test_save_and_load(self, tmp_path):
        import install
        original = install.INSTALL_STATE_FILE
        install.INSTALL_STATE_FILE = tmp_path / ".install_state.json"
        try:
            install._save_state(["prerequisites"])
            install._save_state(["prerequisites", "configuration"])
            prev = install._load_state()
            assert "prerequisites" in prev
            assert "configuration" in prev
        finally:
            install.INSTALL_STATE_FILE = original

    def test_clear_state(self, tmp_path):
        import install
        state_file = tmp_path / ".install_state.json"
        state_file.write_text('{"stages": ["a"]}')
        original = install.INSTALL_STATE_FILE
        install.INSTALL_STATE_FILE = state_file
        try:
            install._clear_state()
            assert not state_file.exists()
        finally:
            install.INSTALL_STATE_FILE = original

    def test_load_missing_returns_empty(self, tmp_path):
        import install
        original = install.INSTALL_STATE_FILE
        install.INSTALL_STATE_FILE = tmp_path / "nonexistent.json"
        try:
            assert install._load_state() == []
        finally:
            install.INSTALL_STATE_FILE = original


class TestEnvironmentMapping:
    def test_development_mapping(self):
        env_map = {"1": "development", "2": "staging", "3": "production"}
        assert env_map["1"] == "development"
        assert env_map["2"] == "staging"
        assert env_map["3"] == "production"


class TestSharedInstallerWriteEnv:
    """Test core.installer.write_env_file — the shared .env writer."""

    def _base_config(self):
        return {
            "FLASK_ENV": "production",
            "FLASK_DEBUG": "false",
            "FLASK_HOST": "0.0.0.0",
            "FLASK_PORT": "5000",
            "DB_HOST": "localhost",
            "DB_PORT": "5432",
            "DB_NAME": "analysis",
            "DB_USER": "postgres",
            "DB_PASSWORD": "testpw",
            "APP_ADMIN_USERNAME": "admin",
            "APP_ADMIN_PASSWORD": "adminpw",
            "PASSWORD_MIN_LENGTH": "12",
            "SECURITY_MAX_FAILED_LOGINS": "5",
            "SECURITY_LOCKOUT_MINUTES": "15",
            "SECURITY_SESSION_HOURS": "12",
            "SECURITY_SESSION_IDLE_HOURS": "6",
            "RATE_LIMIT_PER_MINUTE": "60",
            "RATE_LIMIT_PER_HOUR": "600",
            "MAX_WORKERS": "8",
            "FILE_PROCESSING_TIMEOUT": "1200",
            "INGESTION_ROOTS": "",
            "LOG_LEVEL": "INFO",
        }

    def test_generates_secret_key_when_empty(self, tmp_path):
        from core.installer import write_env_file
        config = self._base_config()
        env_path = write_env_file(config, project_root=tmp_path)
        content = env_path.read_text()
        assert "FLASK_SECRET_KEY=" in content
        for line in content.splitlines():
            if line.startswith("FLASK_SECRET_KEY="):
                key = line.split("=", 1)[1]
                assert len(key) >= 32, "Generated key should be >= 32 chars"
                break
        else:
            pytest.fail("FLASK_SECRET_KEY line not found")

    def test_uses_provided_secret_key(self, tmp_path):
        from core.installer import write_env_file
        config = self._base_config()
        config["FLASK_SECRET_KEY"] = "my-custom-secret-key-that-is-long-enough!!"
        env_path = write_env_file(config, project_root=tmp_path)
        content = env_path.read_text()
        assert "FLASK_SECRET_KEY=my-custom-secret-key-that-is-long-enough!!" in content

    def test_contains_all_sections(self, tmp_path):
        from core.installer import write_env_file
        config = self._base_config()
        env_path = write_env_file(config, project_root=tmp_path)
        content = env_path.read_text()
        assert "DB_HOST=localhost" in content
        assert "DB_PASSWORD=testpw" in content
        assert "APP_ADMIN_USERNAME=admin" in content
        assert "MAX_WORKERS=8" in content

    def test_special_credentials_round_trip_without_interpolation(self, tmp_path, monkeypatch):
        from core.init import _load_dotenv_file
        from core.installer import write_env_file

        password = 'pa${HOME} # "literal" \\token'
        config = self._base_config()
        config["DB_PASSWORD"] = password
        write_env_file(config, project_root=tmp_path)

        monkeypatch.delenv("DB_PASSWORD", raising=False)
        _load_dotenv_file(tmp_path)
        assert os.environ["DB_PASSWORD"] == password

    def test_external_environment_value_keeps_precedence(self, tmp_path, monkeypatch):
        from core.init import _load_dotenv_file
        from core.installer import write_env_file

        config = self._base_config()
        config["DB_PASSWORD"] = "from-file"
        write_env_file(config, project_root=tmp_path)
        monkeypatch.setenv("DB_PASSWORD", "from-environment")

        _load_dotenv_file(tmp_path)
        assert os.environ["DB_PASSWORD"] == "from-environment"
