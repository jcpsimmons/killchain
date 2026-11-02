#!/usr/bin/env python3
"""Contract-first split build.

    prompt --> LOCAL model (sees everything) --> Contract
           --> regex gate + Jev detector on the PUBLIC VIEW only
           --> stubs + contract tests generated into public/
           --> FRONTIER agent (Claude Code / Codex) builds public half vs stubs
           --> LOCAL model implements private_impl/ to the contract
           --> contract tests in real mode prove the halves plug together

Only the public view (brief + interfaces + synthetic fixtures) ever leaves the
machine. The raw prompt, private_notes, private_plan and private_impl live
under paths.private_root and are never on the frontier's PYTHONPATH or cwd.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import detector  # noqa: E402
import frontier  # noqa: E402
import gate  # noqa: E402
import localmodel  # noqa: E402
import stubgen  # noqa: E402
from contract import Contract, ContractError  # noqa: E402


def log(msg: str) -> None:
    print(f"[harness] {msg}", flush=True)


def load_cfg(path: Path, overrides: argparse.Namespace) -> dict:
    cfg = tomllib.loads(path.read_text())
    if overrides.frontier:
        cfg["frontier"]["backend"] = overrides.frontier
    if overrides.local_url:
        cfg["local"]["base_url"] = overrides.local_url
    if overrides.no_detector:
        cfg["detector"]["enabled"] = False
    if overrides.detector_url:
        cfg["detector"]["base_url"] = overrides.detector_url
    if overrides.private_root:
        cfg["paths"]["private_root"] = overrides.private_root
    if overrides.workspace:
        cfg["paths"]["workspace"] = overrides.workspace
    for k in ("workspace", "private_root"):
        p = Path(cfg["paths"][k]).expanduser()
        cfg["paths"][k] = p if p.is_absolute() else (HERE / p).resolve()
    return cfg


def prompt_text(name: str, **kw) -> str:
    return (HERE / "prompts" / name).read_text().format(**kw)


# ---------------------------------------------------------------- step 1
def partition(cfg: dict, user_prompt: str, denylist, priv: Path) -> Contract:
    terms = [n.split(" ", 2)[-1].strip("'") for n, _ in denylist]
    det = cfg["detector"]
    det_on = det.get("enabled", True)
    lm = "- (raw-prompt probing is off)"
    if det_on and det.get("probe_raw_prompt", False):
        spans = detector.spans_from_prompt(user_prompt)
        log(f"pre-scoring {len(spans)} raw prompt span(s) with detector (leaves the box)")
        verdicts = detector.score_spans(det, spans, det["policy"], terms)
        (priv / "leakmap.json").write_text(json.dumps([v.__dict__ for v in verdicts], indent=2))
        lm = detector.leak_map(verdicts)
        log(f"leak map: {sum(v.flagged for v in verdicts)}/{len(verdicts)} span(s) PRIVATE")
    system = prompt_text("partition.md", leak_map=lm,
                         denylist="\n".join(f"- {t}" for t in terms) or "- (none)")
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user_prompt}]
    tries = int(cfg["local"].get("max_partition_tries", 4))
    max_probes = int(det.get("max_probe_rounds", 3))
    attempt = probes = 0
    while attempt < tries:
        attempt += 1
        log(f"partition attempt {attempt}/{tries} (local model)")
        reply = localmodel.chat(cfg["local"], messages)
        (priv / f"partition.attempt{attempt}.txt").write_text(reply)
        try:
            data = localmodel.extract_json(reply)
            if isinstance(data.get("probes"), list) and "public_brief" not in data:
                attempt -= 1  # probes do not spend a partition attempt
                probes += 1
                if not det_on or not det.get("probe_raw_prompt", False) or probes > max_probes:
                    answer = "PROBE RESULTS: probing is not available. Reply with the contract."
                else:
                    spans = [(f"probe[{n}]", str(t)) for n, t in enumerate(data["probes"])]
                    log(f"probe round {probes}/{max_probes}: scoring {len(spans)} span(s)")
                    vs = detector.score_spans(det, spans, det["policy"], terms)
                    (priv / f"probe.round{probes}.json").write_text(json.dumps([v.__dict__ for v in vs], indent=2))
                    answer = "PROBE RESULTS:\n" + detector.leak_map(vs) + "\n\nNow reply with the contract."
                messages += [{"role": "assistant", "content": reply},
                             {"role": "user", "content": answer}]
                continue
            c = Contract.from_dict(data)
            c.validate()
        except (ContractError, localmodel.LocalModelError) as e:
            problem = f"contract invalid: {e}"
            log(problem)
            messages += [{"role": "assistant", "content": reply},
                         {"role": "user", "content": f"PREVIOUS ATTEMPT REJECTED.\n{problem}\n"
                                                      "Return a corrected JSON object."}]
            continue

        problems: list[str] = []
        hits = gate.scan(c.public_text(), denylist)
        if hits:
            problems.append(gate.describe(hits))
        if det_on:
            spans = detector.spans_from_public_view(c.public_view())
            log(f"detector scoring {len(spans)} public span(s)")
            verdicts = detector.score_spans(det, spans, det["policy"], terms)
            (priv / f"detector.attempt{attempt}.json").write_text(
                json.dumps([v.__dict__ for v in verdicts], indent=2))
            log(detector.describe(verdicts))
            if any(v.flagged for v in verdicts):
                problems.append(detector.describe(verdicts))
        if not problems:
            return c
        feedback = "\n\n".join(problems)
        log("public view rejected; feeding back to local model")
        messages += [{"role": "assistant", "content": reply},
                     {"role": "user", "content":
                      "PREVIOUS ATTEMPT REJECTED. The public view below would leak. "
                      "Move the flagged material into private_notes, rephrase the public "
                      "brief at interface level, replace any real values in fixtures with "
                      "synthetic ones, and return the full corrected JSON object.\n\n" + feedback}]
    raise SystemExit("partition failed: local model could not produce a clean public view")


# ---------------------------------------------------------------- helpers
def run_tests(public: Path, mode: str, private: Path | None) -> tuple[bool, str]:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["HARNESS_MODE"] = mode
    if mode == "real":
        assert private is not None
        env["PYTHONPATH"] = str(private)
    p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                       cwd=public, env=env, capture_output=True, text=True, timeout=900)
    return p.returncode == 0, (p.stdout + p.stderr)


def confirm(pv: dict, yes: bool) -> None:
    print("\n" + "=" * 70 + "\nOUTBOUND PUBLIC VIEW (this is everything the frontier will see)\n" + "=" * 70)
    print(pv["public_brief"])
    print("-" * 70)
    for i in pv["interfaces"]:
        sig = ", ".join(f"{p['name']}: {p['type']}" for p in i["params"])
        print(f"{i['module']}.{i['function']}({sig}) -> {i['returns']}  [{len(i['fixtures'])} fixtures]")
        print(f"    {i['description']}")
    print("=" * 70)
    if yes:
        return
    if not sys.stdin.isatty():
        raise SystemExit("refusing to send without review; pass --yes in non-interactive runs")
    if input("Send to frontier? [y/N] ").strip().lower() != "y":
        raise SystemExit("aborted at review")


# ---------------------------------------------------------------- step 3
def implement_private(cfg: dict, user_prompt: str, c: Contract, public: Path, priv: Path) -> bool:
    tries = int(cfg["local"].get("max_impl_tries", 4))
    feedback = ""
    for attempt in range(1, tries + 1):
        log(f"private implementation attempt {attempt}/{tries} (local model)")
        user = prompt_text("local_implement.md", prompt=user_prompt,
                           contract=json.dumps(c.to_dict(), indent=2), feedback=feedback)
        reply = localmodel.chat(cfg["local"], [{"role": "user", "content": user}], max_tokens=16384)
        (priv / f"implement.attempt{attempt}.txt").write_text(reply)
        try:
            data = localmodel.extract_json(reply)
            files = data["files"]
        except (localmodel.LocalModelError, KeyError) as e:
            feedback = f"\n\nPREVIOUS ATTEMPT REJECTED: {e}. Return the JSON object described above."
            continue
        for rel, src in files.items():
            if not rel.startswith("private_impl/") or ".." in rel:
                raise SystemExit(f"local model tried to write outside private_impl/: {rel}")
            dest = priv / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(src)
        (priv / "private_impl" / "__init__.py").touch()
        ok, out = run_tests(public, "real", priv)
        (priv / f"tests.real.attempt{attempt}.txt").write_text(out)
        if ok:
            log("real-mode tests PASS: private implementation honours the contract")
            return True
        log("real-mode tests FAIL; feeding failures back to local model")
        feedback = ("\n\nPREVIOUS ATTEMPT REJECTED. The contract tests failed against your "
                    "implementation. Fix it and return the full JSON object again.\n\n"
                    "Test output:\n" + out[-6000:])
    return False


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt", nargs="?", help="task prompt (or use -f)")
    ap.add_argument("-f", "--file", help="read prompt from file")
    ap.add_argument("-c", "--config", default=str(HERE / "config.toml"))
    ap.add_argument("--run-id", help="reuse/continue a run directory")
    ap.add_argument("--frontier", choices=["claude", "codex", "fake"])
    ap.add_argument("--local-url", help="override local model base_url ('fake' for canned)")
    ap.add_argument("--detector-url", help="override detector base_url ('fake' for canned)")
    ap.add_argument("--no-detector", action="store_true")
    ap.add_argument("--private-root"); ap.add_argument("--workspace")
    ap.add_argument("--yes", action="store_true", help="skip the outbound review prompt")
    ap.add_argument("--skip-frontier", action="store_true", help="public/ already built; go to private step")
    ap.add_argument("--skip-private", action="store_true", help="stop after the frontier build")
    a = ap.parse_args()

    cfg = load_cfg(Path(a.config), a)
    if a.file:
        user_prompt = Path(a.file).read_text()
    elif a.prompt:
        user_prompt = a.prompt
    else:
        ap.error("give a prompt or -f FILE")

    run_id = a.run_id or dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    public = cfg["paths"]["workspace"] / run_id / "public"
    priv = cfg["paths"]["private_root"] / run_id
    public.mkdir(parents=True, exist_ok=True)
    priv.mkdir(parents=True, exist_ok=True)
    (priv / "prompt.md").write_text(user_prompt)
    denylist = gate.load_denylist(cfg["paths"]["private_root"] / "denylist.txt")
    log(f"run {run_id}: public={public} private={priv} denylist rules={len(denylist)}")

    contract_path = priv / "contract.json"
    if a.skip_frontier and contract_path.exists():
        c = Contract.from_json(contract_path.read_text())
    else:
        c = partition(cfg, user_prompt, denylist, priv)
        contract_path.write_text(json.dumps(c.to_dict(), indent=2))
        (public / "contract.public.json").write_text(json.dumps(c.public_view(), indent=2))
        written = stubgen.generate(c, public)
        log(f"generated {len(written)} stub/test files in public/")
        ok, out = run_tests(public, "stub", None)
        if not ok:
            raise SystemExit("generated contract tests fail in stub mode (harness bug):\n" + out)

    if not a.skip_frontier:
        confirm(c.public_view(), a.yes)
        fp = prompt_text("frontier_build.md", public_view=json.dumps(c.public_view(), indent=2))
        hits = gate.scan(fp, denylist)
        if hits:
            raise SystemExit(gate.describe(hits))
        (public / ".harness_frontier_prompt.md").write_text(fp)
        log(f"frontier build starting ({cfg['frontier']['backend']}) in {public}")
        frontier.run(cfg["frontier"], fp, public, cfg["paths"]["private_root"], priv / "frontier.log")
        ok, out = run_tests(public, "stub", None)
        (priv / "tests.stub.txt").write_text(out)
        log("stub-mode tests " + ("PASS" if ok else "FAIL (see tests.stub.txt); continuing"))

    if a.skip_private:
        log("stopping before private implementation (--skip-private)")
        return
    ok = implement_private(cfg, user_prompt, c, public, priv)
    print()
    log("DONE" if ok else "FINISHED WITH FAILING REAL-MODE TESTS")
    log(f"public half:  {public}")
    log(f"private half: {priv / 'private_impl'}")
    log(f"run it:       cd {public} && HARNESS_MODE=real PYTHONPATH={priv} python -m app.<entry>")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
