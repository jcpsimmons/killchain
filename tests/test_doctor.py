"""doctor.py is the first thing a new user runs, so it must never raise.

The contract: always returns an int, prints one line per check, and only
fatal categories (config / frontier / orchestrator) make the exit code nonzero.
"""
from __future__ import annotations

import pytest

from killchain import doctor


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    for var in ("REFUSELESS_API_KEY", "KILLCHAIN_ORCH_KEY", "OPENROUTER_API_KEY",
                "KILLCHAIN_RISK_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def base_cfg(**over):
    cfg = {
        "frontier": {"backend": "fake"},
        "orchestrator": {"base_url": "fake", "api_key_env": "REFUSELESS_API_KEY"},
        "risk": {"enabled": False, "base_url": "fake",
                 "api_key_env": "OPENROUTER_API_KEY"},
        "paths": {"private_root": "~/killchain-private", "workspace": ""},
    }
    for k, v in over.items():
        cfg[k] = v
    return cfg


def test_doctor_returns_int(home, capsys):
    assert isinstance(doctor.run(base_cfg()), int)


def test_doctor_all_fake_passes(home, capsys):
    assert doctor.run(base_cfg()) == 0


def test_doctor_prints_each_check(home, capsys):
    doctor.run(base_cfg())
    out = capsys.readouterr().out
    for name in ("config", "frontier", "workspace", "private root"):
        assert name in out.lower(), f"{name} check missing from output"


def test_doctor_missing_orchestrator_key_is_fatal(home, capsys):
    code = doctor.run(base_cfg(orchestrator={"base_url": "https://api.example.com"}))
    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL" in out


def test_doctor_missing_orchestrator_key_shows_fix(home, capsys):
    doctor.run(base_cfg(orchestrator={"base_url": "https://api.example.com"}))
    out = capsys.readouterr().out
    assert "->" in out
    assert "export" in out.lower() or "set" in out.lower()


def test_doctor_missing_frontier_binary_is_fatal(home, capsys, monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda x: None)
    code = doctor.run(base_cfg(frontier={"backend": "claude"}))
    assert code == 1


def test_doctor_frontier_on_path_is_ok(home, capsys, monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda x: f"/usr/bin/{x}")
    assert doctor.run(base_cfg(frontier={"backend": "claude"})) == 0


def test_doctor_denylist_empty_warns_not_fails(home, capsys):
    code = doctor.run(base_cfg())
    out = capsys.readouterr().out
    assert "denylist" in out.lower()
    assert code == 0


def test_doctor_bad_config_path_is_fatal(home, capsys, monkeypatch):
    monkeypatch.setenv("KILLCHAIN_CONFIG", str(home / "nope.toml"))
    code = doctor.run(base_cfg())
    assert code == 1


def test_doctor_reports_private_root_mode(home, capsys):
    doctor.run(base_cfg())
    out = capsys.readouterr().out
    assert "0700" in out


def test_doctor_never_prints_a_traceback(home, capsys):
    # A half-populated config is the realistic first-run shape.
    code = doctor.run({})
    out = capsys.readouterr().out
    assert "Traceback" not in out
    assert isinstance(code, int)


def test_doctor_summary_line(home, capsys):
    doctor.run(base_cfg())
    out = capsys.readouterr().out
    assert "all checks passed" in out
