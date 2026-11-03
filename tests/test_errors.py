"""Error paths a new user hits first.

The rule these tests enforce: a configuration mistake must produce a short
message plus a fix hint, never a Python traceback. A traceback reads as "this
tool is broken" when the real answer is "set a variable".
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

PROMPT = "Write a function that adds two integers."


def run_cli(*args: str, cwd: Path, home: Path) -> subprocess.CompletedProcess:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "PYTHONPATH": str(REPO),
        "XDG_CONFIG_HOME": "",
        "XDG_DATA_HOME": "",
        "KILLCHAIN_PRIVATE_ROOT": str(home / "killchain-private"),
    }
    return subprocess.run(
        [sys.executable, "-m", "killchain.harness", *args],
        cwd=str(cwd), env=env, capture_output=True, text=True, timeout=180,
    )


@pytest.fixture
def sandbox(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    return home, work


def assert_clean_failure(r: subprocess.CompletedProcess) -> str:
    """Exit 1, no traceback, and a message worth reading."""
    out = r.stdout + r.stderr
    assert r.returncode == 1, f"expected exit 1, got {r.returncode}\n{out}"
    assert "Traceback (most recent call last)" not in out, (
        "a config error leaked a traceback:\n" + out
    )
    return out


# ------------------------------------------------------- unreachable backend
def test_unreachable_orchestrator_is_a_clean_error(sandbox):
    home, work = sandbox
    r = run_cli(PROMPT, "--frontier", "fake", "--orch-url", "http://127.0.0.1:9",
                "--no-detector", "--yes", cwd=work, home=home)
    out = assert_clean_failure(r)
    assert "orchestrator" in out.lower()
    assert "unreachable" in out.lower()
    # The message must say what to do about it.
    assert "--doctor" in out or "base_url" in out


def test_missing_detector_key_is_a_clean_error(sandbox):
    home, work = sandbox
    r = run_cli(PROMPT, "--frontier", "fake", "--orch-url", "fake", "--yes",
                cwd=work, home=home)
    out = assert_clean_failure(r)
    assert "OPENROUTER_API_KEY" in out
    assert "Traceback" not in out


def test_missing_frontier_binary_is_a_clean_error(sandbox, monkeypatch):
    home, work = sandbox
    # An empty PATH means neither claude nor codex can be found.
    env = {
        "PATH": "",
        "HOME": str(home),
        "PYTHONPATH": str(REPO),
        "KILLCHAIN_PRIVATE_ROOT": str(home / "killchain-private"),
    }
    r = subprocess.run(
        [sys.executable, "-m", "killchain.harness", PROMPT, "--frontier", "claude",
         "--orch-url", "fake", "--no-detector", "--yes"],
        cwd=str(work), env=env, capture_output=True, text=True, timeout=180,
    )
    out = assert_clean_failure(r)
    assert "frontier" in out.lower()


# ------------------------------------------------------------ the "run it" hint
def test_run_it_hint_mentions_fixtures(sandbox):
    """The hint must be copy-pasteable, so it needs HARNESS_FIXTURES."""
    home, work = sandbox
    r = run_cli(PROMPT, "--frontier", "fake", "--orch-url", "fake",
                "--no-detector", "--yes", cwd=work, home=home)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "HARNESS_FIXTURES=" in out, (
        "the 'run it' hint omits HARNESS_FIXTURES, so real-mode tests fail:\n" + out
    )


# ------------------------------------------------- gate-only runs are labelled
def test_no_detector_run_says_it_was_not_scored(sandbox):
    """A gate-only pass must not read as a scored partition."""
    home, work = sandbox
    r = run_cli(PROMPT, "--frontier", "fake", "--orch-url", "fake",
                "--no-detector", "--yes", cwd=work, home=home)
    assert r.returncode == 0
    out = (r.stdout + r.stderr).lower()
    assert "not scored" in out or "unscored" in out, (
        "--no-detector skipped the risk dimensions but the output does not say so:\n" + out
    )


# ------------------------------------------------------- attempt counter display
def test_no_duplicate_attempt_line(sandbox):
    """A probe round must not reprint the same attempt number twice."""
    home, work = sandbox
    r = run_cli(PROMPT, "--frontier", "fake", "--orch-url", "fake",
                "--no-detector", "--yes", cwd=work, home=home)
    assert r.returncode == 0
    lines = [ln for ln in (r.stdout + r.stderr).splitlines()
             if "partition attempt" in ln]
    assert lines, "expected at least one attempt line"
    assert len(lines) == len(set(lines)), (
        "the same attempt number was logged twice:\n" + "\n".join(lines)
    )
