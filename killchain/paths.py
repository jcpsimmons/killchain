"""Where killchain keeps its things.

An installed command must never write into site-packages, and a user's config
must survive an upgrade. So everything mutable lives under XDG-style user
directories, and the package only ever ships immutable defaults.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tomllib
from pathlib import Path

PKG = Path(__file__).resolve().parent
DEFAULT_CONFIG = PKG / "config.toml"


def find_project_root(start: Path | None = None) -> Path | None:
    """Nearest ancestor holding a project marker, or None.

    Run dirs default to `<root>/.killchain/` so the public half sits with the
    repo it builds, and stays put no matter which subdirectory you ran from.
    """
    cur = (start or Path.cwd()).resolve()
    for d in (cur, *cur.parents):
        if (d / ".git").exists() or (d / "pyproject.toml").exists() or (d / ".killchain").exists():
            return d
    return None


def workspace() -> Path:
    """The per-project run directory, creating it (gitignored) on first use."""
    root = find_project_root()
    if root is None:
        return data_dir() / "workspace"
    ws = root / ".killchain" / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    ignore = root / ".gitignore"
    try:
        existing = ignore.read_text().splitlines() if ignore.exists() else []
        if not any(line.strip().rstrip("/") == ".killchain" for line in existing):
            with ignore.open("a") as f:
                if existing and existing[-1].strip():
                    f.write("\n")
                f.write("# killchain run state\n.killchain/\n")
    except OSError:
        pass  # read-only checkout; not worth failing a run over
    return ws


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
    """Copy the shipped default into place on first run. Returns its path.

    A config seeded by an older version keeps pinning old defaults forever (it
    is a full copy, not an overlay), so a renamed or re-defaulted setting
    silently never takes effect. We record which default a file was seeded
    from and, when the shipped default moves on, rewrite only the keys the
    user left at their seeded value. Anything they edited is preserved.
    """
    dst = user_config()
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        shutil.copyfile(DEFAULT_CONFIG, dst)
        _stamp(dst)
        return dst
    try:
        if _stamp_of(DEFAULT_CONFIG) != _recorded_shipped(dst):
            _reseed(dst)
    except OSError:
        pass
    return dst


def _stamp_of(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def _stamp(p: Path) -> None:
    """Record (shipped version, file as written).

    Two hashes because the seeded file is not necessarily equal to the shipped
    default: a reseed prepends a note and can carry user values. We only need
    to know whether the shipped default has moved since we last wrote.
    """
    mark = p.parent / ".config-seeded"
    mark.write_text(f"{p.name} shipped={_stamp_of(DEFAULT_CONFIG)} "
                    f"written={_stamp_of(p)}\n")


def _recorded_shipped(dst: Path) -> str:
    mark = dst.parent / ".config-seeded"
    if not mark.exists():
        return ""
    for field in mark.read_text().split():
        if field.startswith("shipped="):
            return field.split("=", 1)[1]
    return ""


def _seeded_copy(dst: Path) -> Path:
    return dst.parent / (dst.name + ".seeded")


def _reseed(dst: Path) -> Path:
    """Keep a user's edits, adopt the new defaults for everything untouched."""
    prev: dict = {}
    backup = _seeded_copy(dst)
    if backup.exists():
        try:
            prev = load_toml(backup)
        except SystemExit:
            prev = {}
    user = load_toml(dst)
    new = load_toml(DEFAULT_CONFIG)
    changed: list[str] = []
    # Which values differ from the shipped default, so the renderer can leave
    # every other line byte-identical (no churn, no reflowed comments).
    overrides: dict[str, set[str]] = {}
    for sec, vals in new.items():
        if not isinstance(vals, dict):
            continue
        for k, default in vals.items():
            mine = user.get(sec, {}).get(k, _MISSING)
            if mine is _MISSING or mine == prev.get(sec, {}).get(k, _MISSING):
                continue  # untouched by the user: take the new default
            vals[k] = mine
            changed.append(f"{sec}.{k}")
            overrides.setdefault(sec, set()).add(k)
    text = _render_keeping_comments(new, overrides)
    if changed:
        header = ("# Updated by killchain to the new shipped defaults.\n"
                  f"# Keys you had changed were kept: {', '.join(changed)}\n")
        if "\n[orchestrator]" in text:
            text = text.replace("\n[orchestrator]", "\n" + header + "\n[orchestrator]", 1)
    dst.write_text(text)
    shutil.copyfile(dst, backup)
    _stamp(dst)
    print(f"[killchain] refreshed {dst} to the installed defaults", file=sys.stderr)
    return dst


_MISSING = object()


_KV = __import__("re").compile(r"^([A-Za-z0-9_]+)\s*=")


def _render_keeping_comments(new: dict, overrides: dict[str, set[str]]) -> str:
    """Start from the shipped default and rewrite only the assigned values.

    Comments and layout are the documentation; losing them on upgrade would be
    worse than the staleness we are fixing.
    """
    out: list[str] = []
    sec = ""
    for raw in DEFAULT_CONFIG.read_text().splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            sec = line[1:-1]
            out.append(raw)
            continue
        m = _KV.match(line)
        if m and m.group(1) in overrides.get(sec, ()):
            out.append(f"{m.group(1)} = {_toml_val(new[sec][m.group(1)])}")
            continue
        out.append(raw)
    return "\n".join(out) + "\n"


def render_toml(cfg: dict) -> str:
    """Write a flat two-level TOML file back out. Good enough for our config."""
    lines: list[str] = []
    for sec, vals in cfg.items():
        if not isinstance(vals, dict):
            lines.append(f"{sec} = {_toml_val(vals)}")
            continue
        if lines:
            lines.append("")
        lines.append(f"[{sec}]")
        for k, v in vals.items():
            lines.append(f"{k} = {_toml_val(v)}")
    return "\n".join(lines) + "\n"


def _toml_val(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_val(x) for x in v) + "]"
    if isinstance(v, str):
        esc = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{esc}"'
    return f'"{v}"'


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
