"""killchain doctor: tell a new user exactly what is missing.

Every check reports ok / warn / fail and nothing here raises. The point is that
a first run should never be a stack trace about a missing key three steps in.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import killchain.localmodel as localmodel
from killchain import paths

OK, WARN, FAIL = "ok", "warn", "FAIL"


@dataclass
class Check:
    name: str
    state: str
    detail: str
    fix: str = ""


def _key_state(cfg: dict, label: str) -> tuple[str, str, str]:
    name = cfg.get("api_key_env", "")
    if name and os.environ.get(name):
        return f"${name} is set", "", f"{label} key found in the environment"
    env_file = Path(cfg.get("env_file", "~/.hermes/.env")).expanduser()
    if name and env_file.exists():
        try:
            found = any(line.startswith(name + "=") for line in env_file.read_text().splitlines())
        except OSError:
            found = False
        if found:
            return f"${name} found in {env_file}", "", f"{label} key read from {env_file}"
    return "", f"set ${name}", f"{label} key not found"


def run(cfg: dict) -> int:
    checks: list[Check] = []

    # --- config
    try:
        cp = paths.resolve_config(None)
        checks.append(Check("config", OK, str(cp)))
    except SystemExit as e:
        # Config normally auto-seeds on first run, so this only fires when
        # --config or $KILLCHAIN_CONFIG points somewhere wrong. Creating a file
        # at the user's path silently would be the wrong fix; say how to reset.
        checks.append(Check("config", FAIL, str(e),
                            "unset $KILLCHAIN_CONFIG (or fix --config), or run "
                            "`killchain --config <path>` once that file exists"))

    # --- frontier agent
    backend = cfg.get("frontier", {}).get("backend", "claude")
    exe = {"claude": "claude", "codex": "codex"}.get(backend)
    if backend == "fake":
        checks.append(Check("frontier", WARN, "backend=fake (canned replies)", "set backend in [frontier]"))
    elif exe and shutil.which(exe):
        checks.append(Check("frontier", OK, f"{exe} on PATH ({shutil.which(exe)})"))
    else:
        checks.append(Check("frontier", FAIL, f"{backend} backend but no `{exe}` on PATH",
                            f"install {exe}, or use --frontier codex/claude/fake"))

    # --- orchestrator key
    och = cfg.get("orchestrator", {})
    detail, fix, msg = _key_state(och, "orchestrator")
    if och.get("base_url") == "fake":
        checks.append(Check("orchestrator", WARN, "base_url=fake (canned replies)"))
    else:
        checks.append(Check("orchestrator", OK if detail else FAIL, detail or msg, fix))

    # --- risk detector key
    risk = cfg.get("risk", {})
    if not risk.get("enabled", True):
        checks.append(Check("risk scorer", WARN, "disabled (gate only)", "enable [risk] to score spans"))
    elif risk.get("base_url") == "fake":
        checks.append(Check("risk scorer", WARN, "base_url=fake (canned replies)"))
    else:
        detail, fix, msg = _key_state(risk, "OpenRouter")
        checks.append(Check("risk scorer", OK if detail else FAIL, detail or msg, fix))

    # --- private root: must exist, be writable, and NOT be world-readable
    root = Path(cfg.get("paths", {}).get("private_root", str(paths.private_root()))).expanduser()
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        mode = root.stat().st_mode
        if mode & 0o077:
            # Holds the raw prompt and real fixtures; fix it rather than nag.
            paths.secure_mkdir(root)
            mode = root.stat().st_mode
            if mode & 0o077:
                checks.append(Check("private root", WARN, f"{root} still group/world-accessible",
                                    f"chmod 700 {root}"))
            else:
                checks.append(Check("private root", OK, f"{root} (tightened to 0700)"))
        else:
            checks.append(Check("private root", OK, f"{root} (mode {oct(mode & 0o777)})"))
    except OSError as e:
        checks.append(Check("private root", FAIL, f"{root}: {e}", "pass --private-root to a writable dir"))

    # --- workspace
    ws = Path(cfg.get("paths", {}).get("workspace", "")).expanduser()
    try:
        ws.mkdir(parents=True, exist_ok=True)
        checks.append(Check("workspace", OK, str(ws)))
    except OSError as e:
        checks.append(Check("workspace", FAIL, f"{ws}: {e}", "pass --workspace"))

    # --- denylist
    dl = paths.denylist_path(cfg)
    try:
        import killchain.gate as gate
        rules = gate.load_denylist(dl)
        if rules:
            checks.append(Check("denylist", OK, f"{len(rules)} rule(s) in {dl}"))
        else:
            checks.append(Check("denylist", WARN, f"empty ({dl}) — gate will only catch builtin secrets",
                                "add product names, codenames and licence names"))
    except SystemExit as e:
        checks.append(Check("denylist", FAIL, str(e), f"fix or delete {dl}"))

    # --- the one check that proves the network path works
    if risk.get("enabled", True) and risk.get("base_url") != "fake" and _key_state(risk, "OpenRouter")[0]:
        import killchain.detector as detector
        try:
            spans = detector.spans_from_prompt("a small test span about sorting a list")
            got = detector.score_spans(risk, spans[:1], [])
            score = got[0].scores.get("overall", got[0].scores)
            checks.append(Check("risk round-trip", OK, f"scored 1 span: {score}"))
        except Exception as e:  # noqa: BLE001 - doctor must never raise
            checks.append(Check("risk round-trip", FAIL, f"{type(e).__name__}: {e}",
                                "check OPENROUTER_API_KEY and network"))

    # --- orchestrator round-trip (cheap, skipped when the base_url is fake)
    if och.get("base_url") != "fake" and _key_state(och, "orchestrator")[0]:
        try:
            localmodel.chat({**och, "timeout": 30}, [{"role": "user", "content": "reply with: ok"}],
                            max_tokens=8)
            checks.append(Check("orchestrator round-trip", OK, "chat endpoint answered"))
        except Exception as e:  # noqa: BLE001
            checks.append(Check("orchestrator round-trip", FAIL, f"{type(e).__name__}: {e}",
                                "check the key, base_url and model name"))

    # --- report
    width = max(len(c.name) for c in checks)
    icon = {OK: "ok  ", WARN: "warn", FAIL: "FAIL"}
    failed = 0
    for c in checks:
        print(f"  {icon[c.state]}  {c.name.ljust(width)}  {c.detail}")
        if c.fix:
            print(f"          {''.ljust(width)}  -> {c.fix}")
        failed += c.state == FAIL
    fatal = [c for c in checks if c.state == FAIL and c.name in ("config", "frontier", "orchestrator")]
    print()
    if failed:
        print(f"{failed} check(s) failed.")
    else:
        print("all checks passed — try: killchain \"your task\"")
    if failed:
        print("Still stuck? War stories and how this works: "
              "https://youtube.com/@drjoshcsimmons")
    return 1 if fatal else 0
