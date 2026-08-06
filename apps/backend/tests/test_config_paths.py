"""
Tests for configuration path resolution.

Regression coverage for the container startup crash: the module previously
did `Path(__file__).resolve().parents[4]`, which assumes a fixed depth. In
the Docker image only `app/` is copied under /app, so the real path is
`/app/app/core/config.py` — exactly four parents — and `parents[4]` raised
`IndexError: 4`, crashing both the API and worker at import time.

These tests verify the resolution helpers work for BOTH layouts:
  - local checkout: .../ai-code-breaker/apps/backend/app/core/config.py
    (a repo root with runner/ exists several levels up)
  - container: /app/app/core/config.py (no repo root above; must not raise)

They also confirm settings can be constructed with no .env present, and that
an env var override for the runner path takes effect.
"""

import os
from pathlib import Path

from app.core import config as config_module
from app.core.config import Settings, get_settings


def test_find_repo_root_local_checkout():
    # From the real module location, a repo root (containing runner/) is
    # discoverable by walking up — no fixed depth assumed.
    module_dir = Path(config_module.__file__).resolve().parent
    root = config_module._find_repo_root(module_dir)
    assert root is not None
    assert (root / "runner").is_dir()


def test_find_repo_root_container_layout_returns_none(tmp_path):
    # Simulate the container: /app/app/core with NO runner/ or .git above it.
    core = tmp_path / "app" / "app" / "core"
    core.mkdir(parents=True)
    # Must return None gracefully rather than raising — this is the crash fix.
    assert config_module._find_repo_root(core) is None


def test_container_style_path_does_not_index_error():
    # The exact container path shape has four parents; the OLD code did
    # parents[4] and raised IndexError. The new helper must handle it.
    container_core = Path("/app/app/core")
    # Should not raise; returns None because nothing repo-like is above /app.
    result = config_module._find_repo_root(container_core)
    assert result is None


def test_default_runner_path_never_raises():
    # Whatever the layout, computing the default runner path must not raise
    # and must yield a Path ending in runner/runner.py.
    path = config_module._default_runner_script_path()
    assert isinstance(path, Path)
    assert path.name == "runner.py"
    assert path.parent.name == "runner"


def test_settings_construct_without_env_file(monkeypatch):
    # Simulate "no .env present" (container case): settings still construct
    # purely from environment/defaults and don't error.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u@h:5432/d")
    monkeypatch.setenv("REDIS_URL", "redis://h:6379/0")
    settings = Settings()
    assert settings.database_url.startswith("postgresql")
    assert settings.redis_url.startswith("redis")


def test_runner_script_path_env_override(monkeypatch):
    # The env var must win over the computed default, so no layout is
    # load-bearing.
    monkeypatch.setenv("RUNNER_SCRIPT_PATH", "/custom/runner.py")
    settings = Settings()
    assert str(settings.runner_script_path) == "/custom/runner.py"


def test_optional_env_file_helper_returns_none_or_existing(tmp_path, monkeypatch):
    # In a directory with no .env and no discoverable repo root, the helper
    # returns None (meaning "no env file"), which is a safe value for
    # pydantic-settings.
    monkeypatch.chdir(tmp_path)
    # Can't easily relocate __file__, but we can assert the return type is
    # either None or a path to an existing file — never a path to a
    # nonexistent file (which could confuse loaders).
    result = config_module._optional_env_file()
    assert result is None or Path(result).is_file()
