"""Where killchain keeps its things.

An installed command must never write into site-packages, and a user's config
must survive an upgrade. So everything mutable lives under XDG-style user
directories, and the package only ever ships immutable defaults.
"""
from __future__ import annotations

import os
import shutil
import tomllib
from pathlib import Path

PKG = Path(__file__).resolve().parent
DEFAULT_CONFIG = PKG / "config.toml"


def config_dir() -> Path:
    """~/.config/killchain, or $XDG_CONFIG_HOME/killchain."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / "killchain"


def data_dir() -> Path:
    """~/.local/share/killchain, or $XDG_DATA_HOME/killchain."""
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".local" / "share"
    return root / "killchain"


def private_root() -> Path:
    """Where the private half lives. Never inside the workspace."""
    override = os.environ.get("KILLCHAIN_PRIVATE_ROOT")
    return Path(override).expanduser() if override else Path.home() / "killchain-private"


def secure_mkdir(p: Path) -> None:
    """Create `p` (and parents) with mode 0700, tightening one that already exists.

    The private root holds the raw prompt and the real fixtures, so it should
    not be readable by other local accounts. mkdir's mode is masked by the
    umask, so chmod explicitly afterwards.
    """
    p.mkdir(parents=True, exist_ok=True)
    try:
        p.chmod(0o700)
    except OSError:
        pass  # filesystems without POSIX modes (exFAT, some network mounts)


def user_config() -> Path:
    return config_dir() / "config.toml"


def ensure_user_config() -> Path:
    """Copy the shipped default into place on first run. Returns its path."""
    dst = user_config()
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEFAULT_CONFIG, dst)
    return dst


def resolve_config(explicit: str | None) -> Path:
    """--config wins, then $KILLCHAIN_CONFIG, then the user file (created if absent)."""
    if explicit:
        p = Path(explicit).expanduser()
        if not p.exists():
            raise SystemExit(f"no config at {p}")
        return p
    env = os.environ.get("KILLCHAIN_CONFIG")
    if env:
        p = Path(env).expanduser()
        if not p.exists():
            raise SystemExit(f"$KILLCHAIN_CONFIG points at {p}, which does not exist")
        return p
    return ensure_user_config()


def load_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(f"{path} is not valid TOML: {e}")


def prompt(name: str) -> str:
    """Prompt templates ship inside the package and are read-only."""
    return (PKG / "prompts" / name).read_text()


def denylist_path(cfg: dict) -> Path:
    """Denylist lives next to the user config, seeded empty on first use."""
    raw = str(cfg.get("paths", {}).get("denylist") or "").strip()
    if raw:
        return Path(raw).expanduser()
    p = config_dir() / "denylist.txt"
    if not p.exists():
        from killchain.gate import DENYLIST_TEMPLATE

        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(DENYLIST_TEMPLATE)
    return p
