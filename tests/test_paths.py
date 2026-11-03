"""paths.py decides where state is written and how config survives upgrades.

The reseed logic is the subtle part: a config is a full copy, not an overlay, so
a stale copy would pin old defaults forever. These tests pin both halves of the
contract: user edits survive, untouched keys adopt the new default.
"""
from __future__ import annotations

import os
import tomllib

import pytest

from killchain import paths


# --------------------------------------------------------- project root
def test_find_project_root_finds_git_dir(project):
    assert paths.find_project_root() == project.resolve()


def test_find_project_root_walks_up_from_subdir(project, monkeypatch):
    sub = project / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert paths.find_project_root() == project.resolve()


def test_find_project_root_returns_none_without_marker(no_project):
    assert paths.find_project_root() == None  # noqa: E711


def test_find_project_root_accepts_pyproject(tmp_path, monkeypatch):
    root = tmp_path / "p2"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    monkeypatch.chdir(root)
    assert paths.find_project_root() == root.resolve()


# ------------------------------------------------------------- workspace
def test_workspace_lives_under_project(project, home):
    ws = paths.workspace()
    assert ws == project.resolve() / ".killchain" / "workspace"
    assert ws.is_dir()


def test_workspace_is_stable_across_calls(project, home):
    assert paths.workspace() == paths.workspace()


def test_workspace_adds_gitignore_entry_once(project, home):
    paths.workspace()
    paths.workspace()
    ignore = project / ".gitignore"
    text = ignore.read_text()
    assert text.count(".killchain/") == 1


def test_workspace_preserves_existing_gitignore(project, home):
    ignore = project / ".gitignore"
    ignore.write_text("dist/\n")
    paths.workspace()
    text = ignore.read_text()
    assert "dist/" in text
    assert ".killchain/" in text


def test_workspace_does_not_duplicate_trailing_slash_form(project, home):
    ignore = project / ".gitignore"
    ignore.write_text(".killchain\n")
    paths.workspace()
    assert ignore.read_text().count(".killchain") == 1


def test_workspace_falls_back_to_xdg_without_project(no_project, home):
    ws = paths.workspace()
    assert ws == home / ".local" / "share" / "killchain" / "workspace"
    assert ws.is_dir()


def test_workspace_survives_readonly_gitignore(project, home):
    ignore = project / ".gitignore"
    ignore.write_text("dist/\n")
    ignore.chmod(0o444)
    try:
        assert paths.workspace().is_dir()
    finally:
        ignore.chmod(0o644)


# ---------------------------------------------------------- xdg layout
def test_config_dir_default(home):
    assert paths.config_dir() == home / ".config" / "killchain"


def test_config_dir_honours_xdg(home, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "cfg"))
    assert paths.config_dir() == home / "cfg" / "killchain"


def test_data_dir_honours_xdg(home, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "data"))
    assert paths.data_dir() == home / "data" / "killchain"


def test_private_root_default(home):
    assert paths.private_root() == home / "killchain-private"


def test_private_root_override(home, monkeypatch):
    monkeypatch.setenv("KILLCHAIN_PRIVATE_ROOT", str(home / "elsewhere"))
    assert paths.private_root() == home / "elsewhere"


def test_private_root_not_inside_workspace(project, home):
    assert paths.workspace() not in paths.private_root().parents
    assert paths.private_root() not in paths.workspace().parents


# ---------------------------------------------------------- secure_mkdir
@pytest.mark.skipif(os.name != "posix", reason="POSIX modes")
def test_secure_mkdir_sets_0700(tmp_path):
    d = tmp_path / "secret" / "nested"
    paths.secure_mkdir(d)
    assert oct(d.stat().st_mode & 0o777) == "0o700"


@pytest.mark.skipif(os.name != "posix", reason="POSIX modes")
def test_secure_mkdir_tightens_existing(tmp_path):
    d = tmp_path / "loose"
    d.mkdir(mode=0o777)
    d.chmod(0o777)
    paths.secure_mkdir(d)
    assert oct(d.stat().st_mode & 0o777) == "0o700"


def test_secure_mkdir_idempotent(tmp_path):
    d = tmp_path / "x"
    paths.secure_mkdir(d)
    paths.secure_mkdir(d)
    assert d.is_dir()


# -------------------------------------------------------------- config
def test_ensure_user_config_seeds_from_default(home):
    p = paths.ensure_user_config()
    assert p == home / ".config" / "killchain" / "config.toml"
    assert p.read_text() == paths.DEFAULT_CONFIG.read_text()
    assert (p.parent / ".config-seeded").exists()


def test_ensure_user_config_parses(home):
    p = paths.ensure_user_config()
    assert tomllib.loads(p.read_text())


def test_ensure_user_config_leaves_current_file_alone(home):
    p = paths.ensure_user_config()
    p.write_text(p.read_text() + "\n# my note\n")
    before = p.read_text()
    paths.ensure_user_config()
    assert p.read_text() == before


def test_resolve_config_prefers_explicit(tmp_path, home):
    explicit = tmp_path / "c.toml"
    explicit.write_text("[harness]\nx = 1\n")
    assert paths.resolve_config(str(explicit)) == explicit


def test_resolve_config_uses_env(home, monkeypatch, tmp_path):
    env = tmp_path / "env.toml"
    env.write_text("[harness]\nx = 1\n")
    monkeypatch.setenv("KILLCHAIN_CONFIG", str(env))
    assert paths.resolve_config(None) == env


def test_resolve_config_falls_back_to_user(home):
    assert paths.resolve_config(None) == paths.user_config()


def test_load_toml_errors_clearly(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text("this is not toml [[[")
    with pytest.raises(SystemExit, match="not valid TOML|bad"):
        paths.load_toml(p)


def test_load_toml_missing_file_exits(tmp_path):
    with pytest.raises(SystemExit):
        paths.load_toml(tmp_path / "absent.toml")


# ------------------------------------------------------------- reseed
def _shipped(home) -> dict:
    return tomllib.loads(paths.DEFAULT_CONFIG.read_text())


def test_reseed_keeps_user_edit_when_default_moves(home, monkeypatch, tmp_path):
    """The upgrade path: shipped default changes, the user's own value survives.

    Simulated by pointing DEFAULT_CONFIG at a copy we mutate, so the real
    shipped file is never touched.
    """
    p = paths.ensure_user_config()
    cfg = tomllib.loads(p.read_text())
    sec, key = _first_scalar(cfg)

    # User changes one value away from the default.
    user_text = p.read_text().replace(f"{key} = ", f"{key} = ", 1)
    p.write_text(user_text.replace(f"{key} = true", f"{key} = false", 1)
                 if cfg[sec][key] is True else user_text)
    mine = tomllib.loads(p.read_text())[sec][key]

    # Shipped default moves on in a different key.
    new_shipped = tmp_path / "config.toml"
    new_shipped.write_text(paths.DEFAULT_CONFIG.read_text().replace(
        "loopback_ok = true", "loopback_ok = false", 1))
    monkeypatch.setattr(paths, "DEFAULT_CONFIG", new_shipped)

    # Pretend the seeded baseline was the *old* default, so the user's key
    # looks edited relative to it.
    backup = paths._seeded_copy(p)
    backup.write_text(paths.DEFAULT_CONFIG.read_text())

    paths._reseed(p)
    after = tomllib.loads(p.read_text())
    assert after[sec][key] == mine, "user value must survive a reseed"


def test_render_keeping_comments_preserves_comments():
    new = _shipped(None)
    text = paths._render_keeping_comments(new, {})
    shipped = paths.DEFAULT_CONFIG.read_text()
    assert text == shipped  # no overrides, byte-identical


def test_render_keeping_comments_rewrites_only_overrides():
    new = _shipped(None)
    sec, key = _first_scalar(new)
    text = paths._render_keeping_comments(new, {sec: {key}})
    assert f"[{sec}]" in text
    assert "#" in text  # comments survived
    # And the file still parses after a targeted rewrite.
    assert tomllib.loads(text)


def _first_scalar(cfg: dict) -> tuple[str, str]:
    for sec, vals in cfg.items():
        if not isinstance(vals, dict):
            continue
        for k, v in vals.items():
            if isinstance(v, (bool, int, float)):
                return sec, k
    raise AssertionError("shipped config has no scalar key")


def test_reseed_reports_to_stderr(home, capsys):
    p = paths.ensure_user_config()
    paths._reseed(p)
    assert "[killchain]" in capsys.readouterr().err


def test_reseed_is_not_run_when_shipped_default_is_unchanged(home, capsys):
    """No churn on every launch: a matching stamp must mean no reseed."""
    paths.ensure_user_config()
    capsys.readouterr()
    paths.ensure_user_config()
    assert capsys.readouterr().err == ""


def test_reseed_preserves_comments(home):
    p = paths.ensure_user_config()
    paths._reseed(p)
    assert "#" in p.read_text()
    assert tomllib.loads(p.read_text())


def test_reseed_writes_backup(home):
    p = paths.ensure_user_config()
    backup = paths._seeded_copy(p)
    backup.write_text(p.read_text())
    paths._reseed(p)
    assert backup.exists()


def test_stamp_records_shipped_hash(home):
    p = paths.ensure_user_config()
    assert paths._recorded_shipped(p) == paths._stamp_of(paths.DEFAULT_CONFIG)


def test_recorded_shipped_empty_without_marker(home):
    p = home / "config.toml"
    p.write_text("")
    assert paths._recorded_shipped(p) == ""
