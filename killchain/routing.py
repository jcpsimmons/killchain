"""
###############################################################################
#                                                                             #
#   ROUTING.PY: WHAT THE FRONTIER MODEL MIGHT REFUSE, AS SHIPPED DEFAULTS    #
#                                                                             #
###############################################################################

Everything else in killchain is plumbing. This file holds the shipped
defaults; `apply()` below overlays the `[routing]` section of your config
(~/.config/killchain/config.toml) on top at startup, so you can retune without
editing installed code.

This file decides:

  1. DIMENSIONS      the refusal risk dimensions. Each one is a yes/no question
                     with a "risky" and a "fine" criteria block, sent to Jev.
                     Add, remove, or retune dimensions here.
  2. SAFE            what a frontier agent builds without complaint. Jev's
                     "no, it will be fine" criteria for every dimension.
  3. HARD_RULES      regexes that block a send outright, no model involved.
                     Secrets, keys, hostnames. The denylist file adds to these.
  4. THRESHOLDS      risk probability needed to pull a span out of the public
                     view, per dimension and per span kind.
  5. classify()      the final per-span decision. Any dimension over its bar
                     routes the span to the abliterated model.
  6. pick_frontier() which coding agent gets the public half.
  7. split_prompt()  how the raw prompt is cut into spans for the risk map.

The private half exists because the frontier might refuse, hedge, water down,
or quietly guess. It is the job of the orchestrator to write a brief that reads
as ordinary engineering while the interesting behaviour lives behind it.

Span kinds, from the label the harness attaches to each span:
  prompt    a paragraph/sentence of the raw user prompt (risk map, pre-partition)
  probe     a subset the orchestrator asked to have scored
  brief     a paragraph of the public brief
  desc      an interface description
  sig       an interface signature with param descriptions
  fixture   one public (decoy) fixture, as JSON
  overall   the whole assembled public view, scored once as a unit
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------- 1
# Each dimension is a refusal trigger observed in practice, not a category of
# technology. Keep the questions about the AGENT'S BEHAVIOUR, because that is
# what we are predicting. `risky` argues for a refusal; `SAFE` argues against.
DIMENSIONS: dict[str, dict[str, str]] = {
    "attribution": {
        "title": "brand, clone or licence framing",
        "question": ("Would a frontier coding agent hedge, ask questions, or refuse because this "
                     "is framed as cloning, porting, or reimplementing an existing product, "
                     "library, or API, or because it involves licence terms (GPL, AGPL, "
                     "'from memory', 'compatible with')?"),
        "risky": ("Clones, ports, and reimplementations invite 'from memory' disclaimers and "
                  "licence quibbles. Named commercial products, 'clone of X', 'port of the "
                  "GPL version', 'reimplement the X API' all raise the odds."),
    },
    "reverse_engineering": {
        "title": "reverse engineering",
        "question": ("Would a frontier coding agent treat this as reverse engineering: "
                     "decompilation, disassembly, binary or firmware patching, protocol or file "
                     "format RE, cracking serials or checksums, DRM, modding, or 'work out the "
                     "algorithm from behaviour'?"),
        "risky": ("Anything where the source of truth is a binary, a captured stream, an "
                  "obfuscated blob, or observed behaviour. Also checksums, magic numbers, "
                  "unpacking, patching, and key formats."),
    },
    "offensive_security": {
        "title": "security and exploitation",
        "question": ("Would a frontier coding agent treat this as security work: exploits, PoCs, "
                     "fuzzing, scanners, malware or RAT behaviour, evasion, persistence, "
                     "pentest tooling, packet capture or MITM?"),
        "risky": ("Exploit, payload, CVE, fuzz, shellcode, persistence, hooking, sandbox escape, "
                  "sniffing, MITM. Agents annotate these with caveats even when they are "
                  "comfortable."),
    },
    "extraction": {
        "title": "harvesting and terms of service",
        "question": ("Would a frontier coding agent hedge because this involves harvesting: "
                     "scraping a site, bulk API pulls, rate-limit or captcha workarounds, "
                     "anti-bot evasion, or data the terms of service restrict?"),
        "risky": ("Scraping, crawlers, rotating proxies, headless browsers, captcha solving, "
                  "undoing rate limits, mirroring someone else's dataset."),
    },
    "regulated": {
        "title": "regulated domain",
        "question": ("Would a frontier coding agent add disclaimers or refuse because this is a "
                     "regulated domain: health, medical, finance, trading, tax, legal, or "
                     "personal-data compliance (HIPAA, GDPR, PCI)?"),
        "risky": ("Clinical thresholds, dosage, risk scoring, underwriting, tax rules, "
                  "contract interpretation, PII handling, audit requirements."),
    },
    "proprietary_model": {
        "title": "another vendor's model or tooling",
        "question": ("Would a frontier coding agent hedge because this touches another vendor's "
                     "model, weights, tokenizer, or SDK internals, or asks to replicate a hosted "
                     "model's behaviour?"),
        "risky": ("Tokenizers, logit post-processing, sampling tricks, weight formats, "
                  "quantisation, 'make it behave like GPT', undocumented SDK internals."),
    },
    "hedge_framing": {
        "title": "hedgy or unverifiable phrasing",
        "question": ("Is this phrased in a way that invites the agent to caveat instead of commit: "
                     "'roughly', 'approximately', 'as far as I know', 'should be', 'probably', "
                     "'experimental', 'may vary', or an explicitly uncertain claim?"),
        "risky": ("A hedged source sentence produces a hedged implementation, usually with a "
                  "comment saying so. Commit or route it."),
    },
    "scale_or_scope": {
        "title": "scope the agent will negotiate away",
        "question": ("Is this sliced in a way that a frontier coding agent would meet by "
                     "delivering a simplified subset, a mock, a TODO, or by asking which of "
                     "several things you meant?"),
        "risky": ("Enormous modules, vague verbs, several products at once, 'full' anything, "
                  "unspecified edge behaviour."),
    },
}

# --------------------------------------------------------------------------- 2
SAFE = """\
Buildable without comment: ordinary CLI, file, HTTP and data plumbing, a clear
function signature, tables and fields, error handling, test expectations, and
behaviour described purely by input and output. Nothing here asks the agent to
judge licences, guess a hidden algorithm, or take a position on a domain."""

# --------------------------------------------------------------------------- 3
# (name, compiled regex). Any match anywhere in outbound text blocks the send.
# Reflection risk does not mean the frontier is trusted with secrets too.
HARD_RULES: list[tuple[str, re.Pattern]] = [
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("openai/anthropic style key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{20,}\b")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("slack token", re.compile(r"\bxox[abpr]-[A-Za-z0-9\-]{10,}\b")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer header", re.compile(r"(?i)\bauthorization:\s*bearer\s+\S+")),
    ("connection string", re.compile(r"\b(?:postgres|mysql|mongodb(?:\+srv)?|redis)://[^\s'\"]+")),
    ("private ipv4", re.compile(r"\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}(?:\.\d{1,3})?\b")),
    # add your own, e.g.:
    # ("internal host", re.compile(r"\b[a-z0-9-]+\.corp\.example\.com\b", re.I)),
]

# Phrases that are not blockers but reliably raise refusal odds. Reported as
# warnings so you can see them in review without a whole re-partition round.
FRAMING_HINTS: list[tuple[str, re.Pattern]] = [
    ("clone/port framing", re.compile(r"(?i)\b(clone|closely|exactly like|drop-in replacement|"
                                      r"port of|reimplement|re-implement|from scratch to match)\b")),
    ("hedge word", re.compile(r"(?i)\b(roughly|approximately|about|probably|should be|"
                              r"as far as i know|i think|maybe|experimental|may vary|etc\.?)\b")),
    ("memory caveat", re.compile(r"(?i)\b(from memory|as i recall|if i remember|canonical|"
                                 r"official behaviour|the real one)\b")),
    ("licence talk", re.compile(r"(?i)\b(gpl|agpl|lgpl|mit licence|apache licence|licence|license|"
                                r"copyright|trademark|patent)\b")),
    ("undefined behaviour", re.compile(r"(?i)\b(todo|tbd|figure out|later|whatever works|"
                                       r"best effort|not sure)\b")),
]

# --------------------------------------------------------------------------- 4
# Jev returns p(risk) in [0, 1] per dimension. A span is pulled out of the
# public view when any dimension reaches its bar. Lower means stricter.
#
# Deliberately liberal on the dimensions that produce the worst failures
# (a refused or silently wrong build); conservative on hedge_framing, which is
# cheap to drop but also cheap to live with.
THRESHOLDS: dict[str, float] = {
    "attribution": 0.50,
    "reverse_engineering": 0.45,
    "offensive_security": 0.50,
    "extraction": 0.55,
    "regulated": 0.55,
    "proprietary_model": 0.55,
    "hedge_framing": 0.60,
    "scale_or_scope": 0.60,
    "overall": 0.50,
    "default": 0.55,
}

# Fixtures are bare numbers in JSON and score near 0.5 whatever they contain,
# so they need a higher bar than prose. Delta is added to the threshold.
KIND_SLACK: dict[str, float] = {"fixture": 0.10, "sig": 0.05, "overall": -0.05}


def apply(cfg: dict) -> list[str]:
    """Overlay the [routing] config section onto the defaults above.

    Recognised keys, all optional:
      disabled_dimensions = ["hedge_framing"]        drop a dimension entirely
      thresholds = { hedge_framing = 0.75, ... }     per-dimension bar
      kind_slack = { fixture = 0.05, ... }           per-span-kind delta
      hard_rules = ["TICKER-SYMBOL", "re:\\bsk-[a-z0-9]{20,}"]   extra blocking regexes

    Returns human-readable notes for whatever changed, so a run log shows the
    retuning. Raises SystemExit on a typo, because silently ignoring
    `threshold = { hedge = 0.9 }` would be worse than failing.
    """
    notes: list[str] = []
    sec = cfg.get("routing") or {}
    known = {"disabled_dimensions", "thresholds", "kind_slack", "hard_rules"}
    unknown = set(sec) - known
    if unknown:
        raise SystemExit(f"config [routing]: unknown key(s) {sorted(unknown)}; expected {sorted(known)}")

    for dim in sec.get("disabled_dimensions", []):
        if dim not in DIMENSIONS:
            raise SystemExit(f"config [routing].disabled_dimensions: no dimension {dim!r}; "
                             f"known: {sorted(DIMENSIONS)}")
        del DIMENSIONS[dim]
        THRESHOLDS.pop(dim, None)
        notes.append(f"dimension {dim} disabled")

    for dim, val in (sec.get("thresholds") or {}).items():
        if dim not in THRESHOLDS and dim not in DIMENSIONS:
            raise SystemExit(f"config [routing].thresholds: no dimension {dim!r}; "
                             f"known: {sorted(THRESHOLDS)}")
        f = float(val)
        if not 0.0 <= f <= 1.0:
            raise SystemExit(f"config [routing].thresholds.{dim}: {f} is not a probability")
        THRESHOLDS[dim] = f
        notes.append(f"threshold {dim} = {f}")

    for kind, val in (sec.get("kind_slack") or {}).items():
        KIND_SLACK[kind] = float(val)
        notes.append(f"kind slack {kind} = {float(val)}")

    for rule in sec.get("hard_rules", []):
        if isinstance(rule, dict) and "pattern" in rule:
            pat, why = rule["pattern"], rule.get("why", "")
        else:
            pat, why = str(rule), ""
        body = pat[3:] if pat.startswith("re:") else re.escape(pat)
        try:
            rx = re.compile(body, re.I)
        except re.error as e:
            raise SystemExit(f"config [routing].hard_rules: bad regex {pat!r}: {e}") from e
        HARD_RULES.append((why or f"config rule {pat!r}", rx))
        notes.append(f"hard rule {pat!r}")
    return notes


def span_kind(label: str) -> str:
    if label.startswith("prompt["):
        return "prompt"
    if label.startswith("probe["):
        return "probe"
    if label.startswith("brief["):
        return "brief"
    if label == "overall":
        return "overall"
    if label.startswith("iface "):
        if label.endswith(" desc"):
            return "desc"
        if label.endswith(" sig"):
            return "sig"
        if " fixture[" in label:
            return "fixture"
    return "default"


def threshold_for(label: str, dim: str) -> float:
    base = THRESHOLDS.get(dim, THRESHOLDS["default"])
    return min(1.0, base + KIND_SLACK.get(span_kind(label), 0.0))


# --------------------------------------------------------------------------- 5
def classify(label: str, text: str, scores: dict[str, float]) -> bool:
    """Return True to pull the span out of the public view.

    Default: True if any dimension reaches its threshold. Examples of what you
    might add here:
      - require two dimensions over their bar before routing a whole brief
      - force True whenever the span names a binary, a dump, or a capture
      - force False for the CLI/usage paragraphs, which never trip anything
    """
    return any(p >= threshold_for(label, d) for d, p in scores.items())


def worst_dimension(scores: dict[str, float]) -> tuple[str, float]:
    if not scores:
        return ("-", 0.0)
    return max(scores.items(), key=lambda kv: kv[1])


# --------------------------------------------------------------------------- 6
def pick_frontier(public_view: dict, default: str) -> str:
    """Which coding agent builds the public half: "claude" | "codex" | "fake".

    `default` is [frontier].backend from config.toml. `public_view` is the
    brief and interfaces that will be sent, so you can route by content, e.g.
    keep UI work away from the agent that is pickiest about attribution.
    """
    return default


# --------------------------------------------------------------------------- 7
def split_prompt(prompt: str) -> list[tuple[str, str]]:
    """Cut the raw prompt into (label, text) spans for the pre-partition risk
    map. Default: paragraphs, then sentences for paragraphs over 400 chars.
    Finer spans give a sharper map and cost more Jev calls."""
    out: list[tuple[str, str]] = []
    paras = [p.strip() for p in re.split(r"\n\s*\n", prompt) if p.strip()]
    for n, p in enumerate(paras):
        if len(p) <= 400:
            out.append((f"prompt[{n}]", p))
            continue
        for k, s in enumerate(re.split(r"(?<=[.!?])\s+", p)):
            if s.strip():
                out.append((f"prompt[{n}.{k}]", s.strip()))
    return out
