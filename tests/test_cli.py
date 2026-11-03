"""End-to-end CLI smoke tests, offline.

These run the real entry point with canned orchestrator/detector responses and a
fake frontier agent, so they exercise argument parsing, config resolution,
workspace creation, the gate, and the contract write order without any network
call or API key.
"""
from __future__ import annotations

import json
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
        # Belt and braces: no model keys should be needed for these runs.
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


def test_version_exits_zero(sandbox):
    home, work = sandbox
    r = run_cli("--version", cwd=work, home=home)
    assert r.returncode == 0, r.stderr
    assert "killchain" in (r.stdout + r.stderr)


def test_help_exits_zero(sandbox):
    home, work = sandbox
    r = run_cli("--help", cwd=work, home=home)
    assert r.returncode == 0
    assert "--doctor" in r.stdout


def test_missing_prompt_errors(sandbox):
    home, work = sandbox
    r = run_cli("--frontier", "fake", cwd=work, home=home)
    assert r.returncode != 0
    assert "prompt" in (r.stderr + r.stdout).lower()


def test_doctor_runs_and_reports_checks(sandbox):
    home, work = sandbox
    r = run_cli("--doctor", cwd=work, home=home)
    out = r.stdout + r.stderr
    assert "config" in out
    assert "workspace" in out
    # Missing API keys are expected here; the run must still report cleanly
    # rather than raising a traceback.
    assert "Traceback" not in out


def test_offline_partition_succeeds(sandbox):
    home, work = sandbox
    r = run_cli("--frontier", "fake", "--orch-url", "fake", "--no-detector",
                "--yes", PROMPT, cwd=work, home=home)
    assert r.returncode == 0, r.stderr
    assert "DONE" in (r.stdout + r.stderr)


def test_offline_partition_writes_both_halves(sandbox):
    home, work = sandbox
    r = run_cli("--frontier", "fake", "--orch-url", "fake", "--no-detector",
                "--yes", PROMPT, cwd=work, home=home)
    assert r.returncode == 0, r.stderr

    public = list((home / ".local" / "share" / "killchain" / "workspace")
                  .glob("*/public"))
    assert public, "no public half written"
    run_dir = public[0].parent

    assert (public[0] / "contract.public.json").exists()
    private = home / "killchain-private" / run_dir.name
    assert (private / "contract.json").exists()
    assert (private / "prompt.md").read_text() == PROMPT


def test_public_view_does_not_leak_private_keys(sandbox):
    home, work = sandbox
    r = run_cli("--frontier", "fake", "--orch-url", "fake", "--no-detector",
                "--yes", PROMPT, cwd=work, home=home)
    assert r.returncode == 0, r.stderr
    public = next((home / ".local" / "share" / "killchain" / "workspace")
                  .glob("*/public/contract.public.json"))
    text = public.read_text()
    doc = json.loads(text)
    for banned in ("private_fixtures", "private_notes", "private_plan",
                   "private_impl"):
        assert banned not in text, f"{banned} leaked into the public half"
        assert banned not in doc


def test_private_root_is_0700(sandbox):
    if os.name != "posix":
        pytest.skip("POSIX modes")
    home, work = sandbox
    r = run_cli("--frontier", "fake", "--orch-url", "fake", "--no-detector",
                "--yes", PROMPT, cwd=work, home=home)
    assert r.returncode == 0, r.stderr
    # Deliberately not asserting on the parent: its mode comes from the umask
    # of whatever created it. The run directories are what hold the secrets.
    runs = [p for p in (home / "killchain-private").iterdir() if p.is_dir()]
    assert runs, "no run directory under the private root"
    for d in runs:
        assert oct(d.stat().st_mode & 0o777) == "0o700", d


def test_private_root_env_var_is_honoured(sandbox):
    """Regression: $KILLCHAIN_PRIVATE_ROOT was documented but silently ignored.

    Config ships a private_root default, and load_cfg overwrote the env var
    with it, so the private half landed in the real home directory instead.
    """
    home, work = sandbox
    r = run_cli("--frontier", "fake", "--orch-url", "fake", "--no-detector",
                "--yes", PROMPT, cwd=work, home=home)
    assert r.returncode == 0, r.stderr
    runs = [p for p in (home / "killchain-private").iterdir() if p.is_dir()]
    assert runs, "private half not written under $KILLCHAIN_PRIVATE_ROOT"


def test_workspace_created_outside_project(sandbox):
    """Regression: workspace() used to return a path it never created."""
    home, work = sandbox
    r = run_cli("--frontier", "fake", "--orch-url", "fake", "--no-detector",
                "--yes", PROMPT, cwd=work, home=home)
    assert r.returncode == 0, r.stderr
    assert (home / ".local" / "share" / "killchain" / "workspace").is_dir()


def test_env_var_config_missing_errors_cleanly(sandbox):
    home, work = sandbox
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home),
           "PYTHONPATH": str(REPO), "KILLCHAIN_CONFIG": str(work / "nope.toml")}
    r = subprocess.run([sys.executable, "-m", "killchain.harness", "--doctor"],
                       cwd=str(work), env=env, capture_output=True, text=True,
                       timeout=120)
    assert r.returncode != 0
    assert "Traceback" not in (r.stdout + r.stderr)
