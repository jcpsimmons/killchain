# killchain: refusal-first split build

An abliterated orchestrator model ([refuseless](https://refuseless.com/?ref=1ei3kxnd)
by default, settings mirrored from `~/.hermes/config.yaml`) is the front door.
It is the only model that sees the full prompt. It splits the job into a
**public half** (built by a frontier coding agent: Claude Code or Codex, which
may fan out to its own subagents) and a **private half** (implemented by the
orchestrator itself), joined by a contract.

The partition rule is **refusal risk, nothing else**. Claude and Codex are good
and fast, but they hedge, caveat, simplify, or refuse when a task looks
reverse-engineered, unlicensed, regulated, exploitative, or oversized. Those
parts go to the uncensored model. The frontier gets the rest of the app and
never learns how the interesting bits work, so it has no position to take.

It is not a cost trick and not only a quality trick: a brief that says "match
the behaviour of X" gets you `from memory` disclaimers and invented constants.
A brief that says "given these fields return a float in [0, 1], monotonic in
both" gets you working code.

## Install

Python 3.11+, no dependencies. Pick one:

```bash
uv tool install killchain          # installs the `killchain` command
pipx install killchain             # same, if you use pipx
pip install killchain              # into the current environment
uvx killchain "your task"          # zero-install trial, no venv needed
```

From a checkout, before it is on an index:

```bash
uv tool install git+https://github.com/jcpsimmons/killchain
# or, for development:
uv venv && uv pip install -e ".[dev]"
python harness.py --version
```

Then find out what is missing before you waste a run:

```bash
killchain --doctor
```

`doctor` checks your config, the `claude`/`codex` binary, both API keys, the
private directory mode, the denylist, and does one live round-trip to each
endpoint. It exits non-zero only if something fatal to a run is wrong.

## What you need

Three things, and a run fails without any of them. killchain has no Python
dependencies, but it is a front end for two models and one coding agent.

**1. A frontier coding agent on your PATH.** One is enough, not both: `claude`
or `codex`. This is what builds the public half. Set which one in `[frontier]`:

```toml
[frontier]
backend = "claude"        # or "codex"
```

The default is `claude`. If the binary is missing, `doctor` reports the
configured backend and no executable on PATH. Install the CLI you already have
a subscription for and point the config at it.

**2. An abliterated orchestrator.** This model reads the full prompt and
implements the private half, which is the entire point of the tool, so a
censored model here defeats it. Default is [refuseless](https://refuseless.com/?ref=1ei3kxnd):

```toml
[orchestrator]
base_url = "https://api.refuseless.com/v1"
model = "deepseek-v4.1-flash-abliterated"
api_key_env = "REFUSELESS_API_KEY"
```

Sign up at [refuseless.com/?ref=1ei3kxnd](https://refuseless.com/?ref=1ei3kxnd)
to get the key. Any OpenAI-compatible chat endpoint works if you would rather
run your own, so `base_url` is the only thing to change. The key is read from
`$REFUSELESS_API_KEY`, falling back to `env_file` (`~/.hermes/.env` by
default). killchain never writes it to disk.

**3. An OpenRouter key.** The risk scorer is Jev (`typesafe/jev-1.14`) through
the OpenRouter Decisions API:

```toml
[risk]
api_key_env = "OPENROUTER_API_KEY"
```

That is what decides which spans carry refusal risk, so a run without it has
only the regex gate left. `--no-detector` says so loudly when you mean that.

| Check | Fix if missing |
| --- | --- |
| `claude` or `codex` on PATH | install the CLI, or set `[frontier] backend` to the one you have |
| `$REFUSELESS_API_KEY` | key from [refuseless](https://refuseless.com/?ref=1ei3kxnd), or point `base_url` at another endpoint |
| `$OPENROUTER_API_KEY` | key from openrouter.ai |

All three are bypassed only in the `fake` combination the tests use
(`--frontier fake --orch-url fake --detector-url fake`).

`doctor` exits non-zero on a missing frontier binary, a missing orchestrator
key, or a bad config. A missing OpenRouter key does not, on its own, make the
exit code non-zero even though that check fails. Read the check lines, not just
the exit code: a run with no risk scorer still works, but the refusal-routing
is reduced to the regex gate, which is not what you installed this for.


## First run

Nothing is written into the directory you installed to. On first use killchain
creates:

| Path | What |
| --- | --- |
| `~/.config/killchain/config.toml` | your config, seeded from the shipped default |
| `~/.config/killchain/denylist.txt` | literal terms and `re:` patterns the gate rejects |
| `~/.local/share/killchain/workspace/` | public halves, one directory per run |
| `~/killchain-private/` | mode 0700: raw prompt, contract, `private_impl/`, risk report |

Override with `$XDG_CONFIG_HOME`, `$XDG_DATA_HOME`, `$KILLCHAIN_CONFIG`,
`$KILLCHAIN_PRIVATE_ROOT`, or per run with `--private-root` / `--workspace`.

## How it works

```
prompt ─► orchestrator ─► contract.json  (brief + interfaces + decoy fixtures + private fixtures + notes)
                │
                ├─ risk score (8 dimensions, per span)  ┐ on the PUBLIC VIEW, plus
                ├─ whole-view pass (is the task itself scary?) ┘ flagged spans go back to orchestrator
                ├─ regex gate + framing hints
                │
                ├─► public/   contracts/ stubs + tests/test_contracts.py  ─► frontier agent builds app + tests
                │                                                            (HARNESS_MODE=stub: fixtures answer)
                └─► private/  prompt, contract, private_impl/              ◄─ orchestrator implements to contract
                                                                              (HARNESS_MODE=real: tests prove the fit)
```

## Where to edit the routing

**[killchain/routing.py](https://github.com/jcpsimmons/killchain/blob/main/killchain/routing.py)** is the one file that decides what the frontier is
allowed to see. Everything else is plumbing that imports from it:

| In routing.py      | What it controls                                                      |
|-------------------|-----------------------------------------------------------------------|
| `DIMENSIONS`      | the refusal dimensions, and the argument for each. Their `question` text is what Jev is actually asked |
| `SAFE`            | what a frontier agent builds without comment; the "false" criteria    |
| `HARD_RULES`      | regexes that block a send outright, no model involved                 |
| `FRAMING_HINTS`   | non-blocking warnings for clone/hedge/licence wording                 |
| `THRESHOLDS`      | risk probability needed to pull a span, per dimension and span kind    |
| `classify()`      | the final per-span decision; any dimension over its bar routes private |
| `pick_frontier()` | which coding agent builds the public half, by content if you like      |
| `split_prompt()`  | how the raw prompt is cut into spans for the risk map                  |

The denylist (`~/.config/killchain/denylist.txt`) adds literal and regex rules
without touching any code. It carries two things: secrets that must never
leave, and terms that tip the frontier off (product names, licence names,
codenames).

The shipped dimensions are a starting taxonomy, not a law: attribution and
clone framing, reverse engineering, offensive security, extraction and ToS,
regulated domains, another vendor's model or SDK internals, hedge wording, and
scope an agent will negotiate away. Add a dimension when you find a refusal
that none of them predict.

### Checking that the routing works

Changing a dimension's wording is a claim about how a model behaves, and it is
easy to make that claim badly. `killchain/eval.py` measures it against labelled
corpora instead of trusting the wording:

```bash
python -m killchain.eval --rows 100 --out results.json
```

That scores `mlabonne/harmful_behaviors` and `mlabonne/harmless_alpaca` through
the same detector path a run uses, then prints how far apart the two score
distributions sit, the accuracy at each routing threshold, and the bar needed
for 90% recall. Each span costs a fraction of a cent. `tests/test_routing_accuracy.py`
asserts floors on the same measurements and is skipped without a key, so it is
run by hand rather than in CI.

**[EVALS.md](EVALS.md)** holds a recorded run with the resolved model build and
corpus hashes, so the numbers can be checked rather than taken on faith.

One caveat worth keeping in mind: those corpora are harmful *user prompts*, while
routing predicts whether a frontier agent would hedge on a *sentence of a
specification*. Separation there proves the detector reads harmful text as
harmful. It does not prove it flags a benign-looking spec sentence that happens
to mention a licence.

## Trust boundaries

To the **orchestrator (refuseless)**: the full prompt, the contract, and the
private implementation loop. This is the half that is supposed to know
everything.

To the **frontier agent**: only `contract.public.json`, the public brief,
interface signatures and descriptions, and decoy fixtures. It is told the
private functions are a black box it should not infer, reimplement, or ask
about.

To **OpenRouter/TypeSafe (Jev)**: the public view span by span, plus, when
`probe_raw_prompt = true` (default), raw prompt spans. The denylist terms ride
along in every criteria block either way. Turn scoring off entirely with
`--no-risk` if OpenRouter is out of bounds.

The raw prompt, `private_notes`, `private_plan`, and `private_impl/` live under
`paths.private_root` (default `~/killchain-private/`), never the frontier's cwd or
on its PYTHONPATH, and the frontier subprocess gets an environment scrubbed of
anything pointing there. The frontier runs with permissions skipped, so treat
that boundary as a convention, not a jail.

## Layers of protection on the outbound view

1. **Orchestrator partition** (semantic): told the rule is refusal risk, shown
   the dimensions, told to rewrite the framing rather than only move content.
2. **Regex gate** (deterministic): denylist literals and regexes plus built-in
   secret patterns. Any hit blocks the send.
3. **Risk scorer** (probabilistic): `typesafe/jev-1.14` via the OpenRouter
   Decisions API, one `noul` question per dimension per span, criteria from
   routing.py. Then a whole-view pass, because a refusal is triggered by the
   shape of a task and every individual span can be innocent.
4. **Human review**: the outbound view, the decoy fixtures, and the span
   scores are printed and you confirm, unless `--yes`.

Rejections from 2 and 3 are fed back to the orchestrator with the exact spans,
so it re-partitions and re-frames.

**Probes.** Before it partitions, the harness scores the raw prompt span by
span and puts the resulting risk map in the partition prompt. The orchestrator
can also reply `{"probes": ["exact text", ...]}` up to `max_probe_rounds`
times to score any phrasing it is unsure about before committing.

## Run

```bash
killchain "build X using our proprietary Y"
```

```bash
killchain -f prompt.md --frontier codex --yes
```

Flags: `--frontier claude|codex|fake`, `--orch-url`, `--risk-url` (alias
`--detector-url`), `--no-risk`/`--no-detector`, `--run-id`, `--skip-frontier`
(public already built, redo private), `--skip-private` (stop after frontier),
`--private-root`, `--workspace`.

Offline end-to-end with canned backends (exercises both feedback loops and the
whole-view pass):

```bash
killchain "rank leads" --orch-url fake --frontier fake --detector-url fake --yes
```

Then run the result with both halves together:

```bash
cd <workspace>/<run>/public && HARNESS_MODE=real PYTHONPATH=~/killchain-private/<run> python -m app.<entry>
```

## Config

Edit `~/.config/killchain/config.toml` (or pass `-c FILE`). `[orchestrator]` is
any OpenAI-compatible chat endpoint; default
is refuseless with the key read from `$REFUSELESS_API_KEY` or from
`~/.hermes/.env` at runtime (never stored in the repo). `[frontier]` picks
`claude` or `codex` and their flags (overridable per run by
`routing.pick_frontier`). `[risk]` holds the Jev endpoint and model and needs
`OPENROUTER_API_KEY`; dimensions and thresholds are in routing.py. `[paths]`
sets the workspace, the private root and an optional denylist location.

## Contract shape

Each interface is one Python function in `contracts/<module>.py`, generated
from the contract and never edited by either side. In stub mode it answers from
`_fixtures.json` (exact input match, else first fixture). In real mode it
delegates to `private_impl.<module>.<function>`.

Two fixture sets per interface. **Public fixtures** are decoys: right shape,
wrong values, never computed with the real logic, because a few correct
input/output points would let the frontier vendor fit the rule. They drive stub
mode. **Private fixtures** are computed with the real logic, live only in the
private run dir (`private_fixtures.json`), and drive `tests/test_contracts.py`
in real mode via `HARNESS_FIXTURES`. Passing there is the integration proof.
The harness rejects a public fixture that equals a private one.

## Developing

```bash
uv run --extra dev pytest
uv run --extra dev ruff check killchain tests
```

Tests never call a model. The CLI tests pass `--orch-url fake --no-detector`
and a fake frontier agent, so the whole pipeline runs offline in about two
seconds. Publishing is gated on them: the workflow runs the suite on Python
3.11, 3.12 and 3.13, builds the wheel, then installs that wheel into a clean
venv and does a partition with it. If the built artifact cannot do a run, it
does not reach PyPI.

## Limits

Jev predicts refusal; it does not measure it. Refusals are stochastic and
version-dependent, and a model can still balk at something no dimension
predicted. Two things mitigate that: the framing rules in `prompts/partition.md`
(describe behaviour, not lineage) and `NOTES.md` from the frontier, which
tells the private side the edge cases it expects. Re-run a partition when the
frontier ships a new model.

v1 limits: Python interfaces only; the orchestrator writes files by returning
JSON (no tool use needed on its side); one contract round, no renegotiation
mid-build.

## Why this exists

I keep handing the good model work it will not do. The build comes back with a
polite paragraph about what it cannot help with and a function that returns a
placeholder. So I drop to something weaker and looser, which writes the hard
part badly and the easy part badly too, and now I am debugging two models
instead of one.

The thing I actually wanted was to hand the boring 90% to the model that is
good at boring, and hand the part it flinches at to one that does not flinch.
That is all this does. No jailbreak, no prompt poem, no arguing with anything.
You know which half of your own project is the risky half. Say so once, and
each model gets the half it is best suited to.

Frame a task by what it does and Claude Code will build it. Frame it by what it
resembles and you get disclaimers and made-up numbers. That difference is not a
bug in the model. It is a difference in how you asked.

I write about building with these things, and about the parts of the industry
that deserve a harder look, over at
**[youtube.com/@drjoshcsimmons](https://youtube.com/@drjoshcsimmons)**. Issues
and war stories are welcome in the
[repo](https://github.com/jcpsimmons/killchain).

MIT licensed.

- Dr. J
