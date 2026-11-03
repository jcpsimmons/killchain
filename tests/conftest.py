"""Shared fixtures.

Every test runs against a throwaway HOME so nothing touches the developer's
real ~/.config or ~/.local. Tests that need user state ask for `home`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated HOME/XDG root. Returns the home path."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    return h


@pytest.fixture
def project(tmp_path, monkeypatch):
    """An isolated project dir with a .git marker; cwd is set inside it."""
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def no_project(tmp_path, monkeypatch):
    """A cwd with no project marker anywhere above it except the tmp root."""
    d = tmp_path / "loose"
    d.mkdir()
    monkeypatch.chdir(d)
    return d
