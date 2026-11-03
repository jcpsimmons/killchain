#!/usr/bin/env python3
"""Refusal-first split build.

    prompt --> ORCHESTRATOR model (abliterated; sees everything) --> Contract
           --> risk scorer (Jev, per dimension) + regex gate on the PUBLIC VIEW
           --> stubs + contract tests generated into public/
           --> FRONTIER agent (Claude Code / Codex) builds public half vs stubs
           --> ORCHESTRATOR model implements private_impl/ to the contract
           --> contract tests in real mode prove the halves plug together

The split is by refusal risk, not confidentiality: anything that would make a
frontier coding agent refuse, hedge, simplify, or guess is routed to the
abliterated model and never described to the frontier. Only the public view
(brief + interfaces + decoy fixtures) leaves the machine, so the frontier sees
an ordinary app with a few functions it must not implement.

The raw prompt, private_notes, private_plan and private_impl live under
paths.private_root and are never on the frontier's PYTHONPATH or cwd.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import killchain.detector as detector
import killchain.frontier as frontier
import killchain.gate as gate
import killchain.localmodel as localmodel
import killchain.stubgen as stubgen
from killchain import paths, routing
from killchain.contract import Contract, ContractError


def log(msg: str) -> None:
    print(f"[harness] {msg}", flush=True)


def load_cfg(path: Path, overrides: argparse.Namespace) -> dict:
    cfg = tomllib.loads(path.read_text())
    if overrides.frontier:
        cfg["frontier"]["backend"] = overrides.frontier
    if overrides.orch_url:
        cfg["orchestrator"]["base_url"] = overrides.orch_url
    if overrides.no_detector:
        cfg["risk"]["enabled"] = False
    if overrides.detector_url:
        cfg["risk"]["base_url"] = overrides.detector_url
    if overrides.private_root:
        cfg["paths"]["private_root"] = overrides.private_root
    if overrides.workspace:
        cfg["paths"]["workspace"] = overrides.workspace
    for note in routing.apply(cfg):
        log(f"routing: {note}")
    # Relative paths resolve against the cwd, not the package: an installed
    # tool has no business writing next to its own source.
    # workspace: "" = per-project .killchain/ (or the global data dir outside
    # a project); a path = exactly that path.
    raw_ws = str(cfg["paths"].get("workspace") or "").strip()
    cfg["paths"]["workspace"] = paths.workspace() if not raw_ws else _abs(raw_ws)
    # Precedence: --private-root, then $KILLCHAIN_PRIVATE_ROOT, then the file.
    # The env var is documented in README and config.toml, so it has to win
    # over the config file's own default or it does nothing.
    if not overrides.private_root and os.environ.get("KILLCHAIN_PRIVATE_ROOT"):
        cfg["paths"]["private_root"] = os.environ["KILLCHAIN_PRIVATE_ROOT"]
    cfg["paths"]["private_root"] = _abs(cfg["paths"]["private_root"])
    return cfg


def _abs(raw: str) -> Path:
    p = Path(raw).expanduser()
    return p if p.is_absolute() else Path.cwd() / p


def prompt_text(name: str, **kw) -> str:
    return paths.prompt(name).format(**kw)


# ---------------------------------------------------------------- step 1
def dimensions_text() -> str:
    return "\n".join(f"- {k}: {d['title']}. {d['risky']}" for k, d in routing.DIMENSIONS.items())


def partition(cfg: dict, user_prompt: str, denylist, priv: Path) -> Contract:
    terms = [n.split(" ", 2)[-1].strip("'") for n, _ in denylist]
    det = cfg["risk"]
    det_on = det.get("enabled", True)
    rm = "- (raw-prompt scoring is off)"
    if det_on and det.get("probe_raw_prompt", False):
        spans = detector.spans_from_prompt(user_prompt)
        log(f"pre-scoring {len(spans)} raw prompt span(s) for refusal risk (leaves the box)")
        verdicts = detector.score_spans(det, spans, terms)
        (priv / "riskmap.json").write_text(json.dumps([v.__dict__ for v in verdicts], indent=2))
        rm = detector.risk_map(verdicts)
        log(f"risk map: {sum(v.flagged for v in verdicts)}/{len(verdicts)} span(s) PRIVATE")
    system = prompt_text("partition.md", risk_map=rm, dimensions=dimensions_text(),
                         safe=routing.SAFE.strip(),
                         denylist="\n".join(f"- {t}" for t in terms) or "- (none)")
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user_prompt}]
    tries = int(cfg["orchestrator"].get("max_partition_tries", 4))
    max_probes = int(det.get("max_probe_rounds", 3))
    attempt = probes = 0
    while attempt < tries:
        attempt += 1
        log(f"partition attempt {attempt}/{tries} (orchestrator)")
        reply = localmodel.chat(cfg["orchestrator"], messages)
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
                    vs = detector.score_spans(det, spans, terms)
                    (priv / f"probe.round{probes}.json").write_text(json.dumps([v.__dict__ for v in vs], indent=2))
                    answer = "PROBE RESULTS:\n" + detector.risk_map(vs) + "\n\nNow reply with the contract."
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
        verdicts: list[detector.RiskVerdict] = []
        overall: detector.RiskVerdict | None = None
        if det_on:
            spans = detector.spans_from_public_view(c.public_view())
            log(f"risk scoring {len(spans)} public span(s) across {len(routing.DIMENSIONS)} dimensions")
            verdicts = detector.score_spans(det, spans, terms)
            log(detector.describe(verdicts))
            # Always recorded: the holistic score belongs in the review even
            # when it passes, because it is what a refusal actually keys on.
            overall = detector.score_whole_view(det, c.public_view(), terms)
            verdicts.append(overall)
            (priv / f"detector.attempt{attempt}.json").write_text(
                json.dumps([v.__dict__ for v in verdicts], indent=2))
            if overall.flagged:
                log(detector.describe([overall]))
            if any(v.flagged for v in verdicts):
                problems.append(detector.describe(verdicts))
        if not problems:
            report = {"attempt": attempt,
                      "spans": [v.__dict__ for v in verdicts],
                      "hints": [h.__dict__ for h in gate.hints(c.public_text())]}
            (priv / "riskreport.json").write_text(json.dumps(report, indent=2))
            return c
        feedback = "\n\n".join(problems)
        log("public view rejected; feeding back to orchestrator")
        messages += [{"role": "assistant", "content": reply},
                     {"role": "user", "content":
                      "PREVIOUS ATTEMPT REJECTED. The public view below would invite a refusal, "
                      "a hedge, or a negotiated-down build. Move the flagged material into the "
                      "private interfaces and private_notes, and rewrite the public brief so it "
                      "reads as ordinary engineering: describe behaviour by input, output and CLI "
                      "surface rather than by brand, licence, or the technique involved. Drop "
                      "hedge words and name no product the agent will compare against. Keep the "
                      "public fixtures decoys. Return the full corrected JSON object.\n\n" + feedback}]
    raise SystemExit("partition failed: orchestrator could not produce a de-risked public view")


# ---------------------------------------------------------------- helpers
def run_tests(public: Path, mode: str, private: Path | None,
              contracts_only: bool = False) -> tuple[bool, str]:
    """contracts_only runs just the generated conformance tests: that is the
    orchestrator's responsibility. The full suite also covers the frontier's
    own tests, which can fail for reasons the private side cannot fix."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["HARNESS_MODE"] = mode
    if mode == "real":
        assert private is not None
        env["PYTHONPATH"] = str(private)
        env["HARNESS_FIXTURES"] = str(private / "private_fixtures.json")
    if contracts_only:
        cmd = [sys.executable, "-m", "unittest", "tests.test_contracts"]
    else:
        cmd = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."]
    try:
        p = subprocess.run(cmd, cwd=public, env=env, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired as e:
        return False, f"test suite timed out after 900s\n{(e.stdout or '')[-4000:]}"
    return p.returncode == 0, (p.stdout + p.stderr)


def confirm(pv: dict, yes: bool, report_path: Path | None = None) -> None:
    print("\n" + "=" * 70 + "\nOUTBOUND PUBLIC VIEW (this is everything the frontier will see)\n" + "=" * 70)
    print(pv["public_brief"])
    print("-" * 70)
    for i in pv["interfaces"]:
        sig = ", ".join(f"{p['name']}: {p['type']}" for p in i["params"])
        print(f"{i['module']}.{i['function']}({sig}) -> {i['returns']}  [{len(i['fixtures'])} fixtures]")
        print(f"    {i['description']}")
        for k, f in enumerate(i["fixtures"]):
            print(f"    decoy fixture {k}: {json.dumps(f['inputs'], sort_keys=True)} -> "
                  f"{json.dumps(f['output'])}")
    print("=" * 70)
    if report_path and report_path.exists():
        report = json.loads(report_path.read_text())
        spans = sorted(report.get("spans", []), key=lambda s: -max(s["scores"].values(), default=0))
        if spans:
            print("RISK AT PARTITION (top scored spans; below threshold = routed to the frontier)")
            for s in spans[:8]:
                dim, p = max(s["scores"].items(), key=lambda kv: kv[1]) if s["scores"] else ("-", 0.0)
                bar = routing.threshold_for(s["label"], dim)
                mark = "PRIVATE" if s["flagged"] else "public "
                print(f"  {mark} {dim} p={p:.2f} (bar {bar:.2f}) {s['label']}")
        for h in report.get("hints", []):
            print(f"  hint [{h['rule']}] {h['match']!r}")
        print("=" * 70)
    if yes:
        return
    if not sys.stdin.isatty():
        raise SystemExit("refusing to send without review; pass --yes in non-interactive runs")
    if input("Send to frontier? [y/N] ").strip().lower() != "y":
        raise SystemExit("aborted at review")


# ---------------------------------------------------------------- step 3
def implement_private(cfg: dict, user_prompt: str, c: Contract, public: Path, priv: Path) -> bool:
    tries = int(cfg["orchestrator"].get("max_impl_tries", 4))
    feedback = ""
    for attempt in range(1, tries + 1):
        log(f"private implementation attempt {attempt}/{tries} (orchestrator)")
        user = prompt_text("local_implement.md", prompt=user_prompt,
                           contract=json.dumps(c.to_dict(), indent=2), feedback=feedback)
        reply = localmodel.chat(cfg["orchestrator"], [{"role": "user", "content": user}], max_tokens=16384)
        (priv / f"implement.attempt{attempt}.txt").write_text(reply)
        try:
            data = localmodel.extract_json(reply)
            files = data["files"]
            if not isinstance(files, dict) or not files:
                raise ValueError("'files' must be a non-empty object of path -> file contents")
            bad = [r for r in files if not r.startswith("private_impl/") or ".." in r]
            if bad:
                raise ValueError(f"every path must live under private_impl/: {bad}")
            not_str = [r for r, s in files.items() if not isinstance(s, str)]
            if not_str:
                raise ValueError(f"file contents must be strings (send JSON-escaped source): {not_str}")
        except (localmodel.LocalModelError, KeyError, ValueError) as e:
            feedback = f"\n\nPREVIOUS ATTEMPT REJECTED: {e}. Return the JSON object described above."
            continue
        # Rebuild the tree each attempt so a dropped file cannot linger and
        # shadow the new one.
        if (priv / "private_impl").exists():
            shutil.rmtree(priv / "private_impl")
        for rel, src in files.items():
            dest = priv / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(src)
        (priv / "private_impl" / "__init__.py").touch()
        ok, out = run_tests(public, "real", priv, contracts_only=True)
        (priv / f"tests.real.attempt{attempt}.txt").write_text(out)
        if ok:
            log("contract tests PASS in real mode: private implementation honours the contract")
            full_ok, full_out = run_tests(public, "real", priv)
            (priv / "tests.real.full.txt").write_text(full_out)
            log("full suite in real mode: " + ("PASS" if full_ok else
                "FAIL (frontier-side tests; see tests.real.full.txt)"))
            return True
        log("contract tests FAIL in real mode; feeding failures back to orchestrator")
        feedback = ("\n\nPREVIOUS ATTEMPT REJECTED. The contract tests failed against your "
                    "implementation. Fix it and return the full JSON object again.\n\n"
                    "Test output:\n" + out[-6000:])
    return False


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(
        prog="killchain",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Docs and war stories: https://youtube.com/@drjoshcsimmons",
    )
    ap.add_argument("prompt", nargs="?", help="task prompt (or use -f)")
    ap.add_argument("-f", "--file", help="read prompt from file")
    ap.add_argument("-c", "--config", default=None,
                    help="config.toml (default: ~/.config/killchain/config.toml)")
    ap.add_argument("--run-id", help="reuse/continue a run directory")
    ap.add_argument("--frontier", choices=["claude", "codex", "fake"])
    ap.add_argument("--orch-url", help="override orchestrator base_url ('fake' for canned)")
    ap.add_argument("--detector-url", "--risk-url", dest="detector_url",
                    help="override risk detector base_url ('fake' for canned)")
    ap.add_argument("--no-detector", "--no-risk", dest="no_detector", action="store_true",
                    help="skip model scoring entirely (gate only)")
    ap.add_argument("--private-root")
    ap.add_argument("--workspace")
    ap.add_argument("--yes", action="store_true", help="skip the outbound review prompt")
    ap.add_argument("--skip-frontier", action="store_true", help="public/ already built; go to private step")
    ap.add_argument("--skip-private", action="store_true", help="stop after the frontier build")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__import__('killchain').__version__}")
    ap.add_argument("--doctor", action="store_true",
                    help="check config, keys, frontier agent and network, then exit")
    a = ap.parse_args()

    if a.doctor:
        from killchain import doctor
        cfg = tomllib.loads(paths.resolve_config(a.config).read_text())
        raise SystemExit(doctor.run(cfg))

    cfg = load_cfg(paths.resolve_config(a.config), a)
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
    paths.secure_mkdir(priv)
    (priv / "prompt.md").write_text(user_prompt)
    dl_path = paths.denylist_path(cfg)
    dl_path.parent.mkdir(parents=True, exist_ok=True)
    denylist = gate.load_denylist(dl_path)
    log(f"run {run_id}: public={public} private={priv} denylist rules={len(denylist)}")

    contract_path = priv / "contract.json"
    if a.skip_frontier and contract_path.exists():
        c = Contract.from_json(contract_path.read_text())
    else:
        c = partition(cfg, user_prompt, denylist, priv)
        contract_path.write_text(json.dumps(c.to_dict(), indent=2))
        (public / "contract.public.json").write_text(json.dumps(c.public_view(), indent=2))
        written = stubgen.generate(c, public)
        stubgen.write_private_fixtures(c, priv / "private_fixtures.json")
        log(f"generated {len(written)} stub/test files in public/; private fixtures kept in private/")
        ok, out = run_tests(public, "stub", None)
        if not ok:
            raise SystemExit("generated contract tests fail in stub mode (harness bug):\n" + out)

    if not a.skip_frontier:
        confirm(c.public_view(), a.yes, priv / "riskreport.json")
        fp = prompt_text("frontier_build.md", public_view=json.dumps(c.public_view(), indent=2))
        hits = gate.scan(fp, denylist)
        if hits:
            raise SystemExit(gate.describe(hits))
        (public / ".harness_frontier_prompt.md").write_text(fp)
        backend = routing.pick_frontier(c.public_view(), cfg["frontier"]["backend"])
        cfg["frontier"]["backend"] = backend
        log(f"frontier build starting ({backend}, chosen by routing.pick_frontier) in {public}")
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
    log(f"risk report:  {priv / 'riskreport.json'}")
    log(f"run it:       cd {public} && HARNESS_MODE=real PYTHONPATH={priv} python -m app.<entry>")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
