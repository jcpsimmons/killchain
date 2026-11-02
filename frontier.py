"""Frontier builder adapters. Each runs a coding agent inside the PUBLIC
directory only, with the public brief and generated stubs. The agent is free
to dispatch to subagents in its own family; that is its business.

Backends: claude (Claude Code CLI), codex (OpenAI Codex CLI), fake.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class FrontierError(RuntimeError):
    pass


def _clean_env(private_root: Path) -> dict:
    """Environment for the frontier subprocess: strip anything pointing at the
    private tree, and make sure HARNESS_MODE is stub."""
    env = {k: v for k, v in os.environ.items()
           if str(private_root) not in v and k not in ("PYTHONPATH",)
           # a Claude Code parent marks its env; the child must start clean
           and k != "CLAUDECODE" and not k.startswith("CLAUDE_CODE_")}
    env["HARNESS_MODE"] = "stub"
    return env


def run(cfg: dict, prompt: str, public_dir: Path, private_root: Path, log: Path) -> str:
    backend = cfg.get("backend", "claude")
    if backend == "fake":
        from fakes import fake_frontier
        return fake_frontier(prompt, public_dir)
    if backend == "claude":
        exe = shutil.which("claude") or "claude"
        cmd = [exe, "-p", prompt, "--output-format", "text"]
        if cfg.get("claude_model"):
            cmd += ["--model", cfg["claude_model"]]
        cmd += list(cfg.get("claude_extra_args", []))
    elif backend == "codex":
        exe = shutil.which("codex") or "codex"
        cmd = [exe, "exec", "-C", str(public_dir)]
        if cfg.get("codex_model"):
            cmd += ["-m", cfg["codex_model"]]
        cmd += list(cfg.get("codex_extra_args", []))
        cmd += [prompt]
    else:
        raise FrontierError(f"unknown frontier backend {backend!r}")

    with log.open("w") as lf:
        lf.write(f"$ {' '.join(cmd[:3])} ... (cwd={public_dir})\n\n")
        lf.flush()
        proc = subprocess.run(cmd, cwd=public_dir, env=_clean_env(private_root),
                              stdout=lf, stderr=subprocess.STDOUT, text=True,
                              timeout=cfg.get("timeout", 3600))
    out = log.read_text()
    if proc.returncode != 0:
        raise FrontierError(f"{backend} exited {proc.returncode}; see {log}\n{out[-2000:]}")
    return out
