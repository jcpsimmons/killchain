# skoal: contract-first split build

An abliterated orchestrator model (refuseless by default, settings mirrored
from `~/.hermes/config.yaml`) is the front door. It is the only model that
sees the full prompt. It splits the job into a **public half** (built by a
frontier coding agent: Claude Code or Codex, which may fan out to its own
subagents) and a **private half** (implemented by the orchestrator itself),
joined by a contract. The partition rule is confidentiality: proprietary,
customer, secret, or export-controlled material never reaches the frontier
vendor. That is the contractor-plus-ITAR-module pattern: agree the interface,
build both sides against it, plug together.

```
prompt ─► orchestrator ─► contract.json  (brief + interfaces + decoy fixtures + private fixtures + notes)
                │
                ├─ regex gate   (denylist + secret patterns)      ┐ on the PUBLIC VIEW only,
                ├─ Jev detector (per-span leak probability)       ┘ flagged spans go back to orchestrator
                │
                ├─► public/   contracts/ stubs + tests/test_contracts.py  ─► frontier agent builds app + tests
                │                                                            (HARNESS_MODE=stub: fixtures answer)
                └─► private/  prompt, contract, private_impl/              ◄─ orchestrator implements to contract
                                                                             (HARNESS_MODE=real: tests prove the fit)
```

## Trust boundaries

To the **orchestrator (refuseless)**: the full prompt, the contract, and the
private implementation loop. This is the party you trust with everything.

To the **frontier agent**: only `contract.public.json`, the public brief,
interface signatures and descriptions, and synthetic fixtures.

To **OpenRouter/TypeSafe (Jev)**: the same public view, span by span, plus,
when `probe_raw_prompt = true` (the default), the raw prompt split into spans
and any subsets the orchestrator asks to probe. Turn that off if OpenRouter is
not in your trust boundary; the public-view scoring still runs.

The raw prompt, `private_notes`, `private_plan`, and `private_impl/` never
reach the frontier: they live under `paths.private_root` (default
`~/skoal-private/`), which is never the frontier's cwd or on its PYTHONPATH,
and the frontier subprocess gets an environment scrubbed of anything pointing
there.

The denylist lives in the private root too (`denylist.txt`), because it names
the things you are protecting. First run writes a template.

## Layers of protection on the outbound view

1. **Orchestrator partition** (semantic): told the rule is confidentiality, told
   the denylist, told fixtures must be synthetic.
2. **Regex gate** (deterministic): denylist literals and regexes plus built-in
   secret patterns. Any hit blocks the send.
3. **Jev detector** (probabilistic): `typesafe/jev-1.14` via the OpenRouter
   Decisions API, one `noul` question per span against the policy text in
   `config.toml`. Spans at or above `threshold` block the send. Live check on
   this machine: benign interface description 0.10, a span naming a codename,
   weights and a customer 0.98, a synthetic fixture 0.46.
4. **Human review**: the full outbound view is printed and you confirm, unless
   `--yes`.

Rejections from 2 and 3 are fed back to the orchestrator with the exact spans,
so it re-partitions.

**Subset testing.** Before the orchestrator partitions, the harness scores the
raw prompt span by span with Jev and puts the resulting leak map in the
partition prompt (`leakmap.json` in the run's private dir). The orchestrator
can also reply `{"probes": ["exact text", ...]}` up to `max_probe_rounds`
times to score any subset it is unsure about before committing to a contract.
Live check on a synthetic prompt: the generic CLI paragraphs scored 0.04 and
0.05, the paragraph with the formula, codename and customer scored 0.97.

## Run

```bash
python3 harness.py "build X using our proprietary Y"
```

```bash
python3 harness.py -f prompt.md --frontier codex --yes
```

Flags: `--frontier claude|codex|fake`, `--orch-url`, `--detector-url`,
`--no-detector`, `--run-id`, `--skip-frontier` (public already built, redo
private), `--skip-private` (stop after frontier), `--private-root`,
`--workspace`.

Offline end-to-end with canned backends (exercises both feedback loops):

```bash
python3 harness.py "rank leads" --orch-url fake --frontier fake --detector-url fake --yes
```

Then run the result with both halves together:

```bash
cd workspace/<run>/public && HARNESS_MODE=real PYTHONPATH=~/skoal-private/<run> python -m app.<entry>
```

## Config

`config.toml`: `[orchestrator]` is any OpenAI-compatible chat endpoint;
default is refuseless with the key read from `$REFUSELESS_API_KEY` or from
`~/.hermes/.env` at runtime (never stored in the repo). `[frontier]` picks
`claude` or `codex` and their flags. `[detector]` holds the Jev model,
threshold and policy text and needs `OPENROUTER_API_KEY`.

## Contract shape

Each interface is one Python function in `contracts/<module>.py`, generated
from the contract and never edited by either side. In stub mode it answers
from `_fixtures.json` (exact input match, else first fixture). In real mode
it delegates to `private_impl.<module>.<function>`.

Two fixture sets per interface. **Public fixtures** are decoys: right shape,
wrong values, never computed with the real logic, because a few correct
input/output points would let the frontier vendor fit the model. They drive
stub mode. **Private fixtures** are computed with the real logic, live only
in the private run dir (`private_fixtures.json`), and drive
`tests/test_contracts.py` in real mode via `HARNESS_FIXTURES`. Passing there
is the integration proof. The harness rejects a public fixture that equals a
private one.

v1 limits: Python interfaces only; the orchestrator writes files by returning
JSON (no tool use needed on its side); one contract round, no
renegotiation mid-build.
