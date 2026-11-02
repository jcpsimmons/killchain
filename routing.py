"""
###############################################################################
#                                                                             #
#   ROUTING.PY: THE ONE FILE TO EDIT FOR WHAT STAYS PRIVATE AND WHAT GOES OUT  #
#                                                                             #
###############################################################################

Everything else in skoal is plumbing. This file decides:

  1. POLICY          what counts as private. Shown verbatim to the orchestrator
                     when it partitions, and used as Jev's "yes, this leaks"
                     criteria.
  2. PUBLIC_OK       what is fine to send. Jev's "no, this is fine" criteria.
  3. HARD_RULES      regexes that block a send outright, no model involved.
                     The denylist file in the private root adds to these.
  4. THRESHOLDS      Jev leak probability needed to flag a span, per span kind.
  5. classify()      the final per-span decision. Override for custom logic
                     (combine kinds, whitelist phrases, require two signals...).
  6. pick_frontier() which coding agent gets the public half.
  7. split_prompt()  how the raw prompt is cut into spans for the leak map.

Span kinds, from the label the harness attaches to each span:
  prompt    a paragraph/sentence of the raw user prompt (leak map, pre-partition)
  probe     a subset the orchestrator asked to have scored
  brief     a paragraph of the public brief
  desc      an interface description
  sig       an interface signature with param descriptions
  fixture   one public (decoy) fixture, as JSON
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------- 1
POLICY = """\
Private means any of:
- unreleased product names, internal project codenames, roadmap items
- customer, partner, vendor or employee identities and anything that implies them
- proprietary algorithms, formulas, weights, thresholds, heuristics, prompts, or
  tuning constants, and any set of example values that would let a reader fit them
- internal hostnames, paths, schemas, infrastructure and deployment details
- credentials, tokens, keys, connection strings
- export-controlled technical data
- business metrics, pricing, margins, contract terms, legal matters
"""

# --------------------------------------------------------------------------- 2
PUBLIC_OK = """\
Public means generic engineering content: product behaviour described at the
interface level, invented example values that only show shape, ordinary CLI,
API, file and data plumbing, public technology and library names, error
handling and test expectations. Nothing proprietary can be recovered from it.
"""

# --------------------------------------------------------------------------- 3
# (name, compiled regex). Any match anywhere in outbound text blocks the send.
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

# --------------------------------------------------------------------------- 4
# Jev returns p(leak) in [0, 1]. A span is flagged when p >= its kind's threshold.
# Fixtures are numbers in JSON and Jev scores them near 0.5 even when they are
# decoys, so they get a higher bar. Lower a number to be stricter.
THRESHOLDS: dict[str, float] = {
    "prompt": 0.50,
    "probe": 0.50,
    "brief": 0.50,
    "desc": 0.50,
    "sig": 0.50,
    "fixture": 0.65,
    "default": 0.50,
}


def span_kind(label: str) -> str:
    if label.startswith("prompt["):
        return "prompt"
    if label.startswith("probe["):
        return "probe"
    if label.startswith("brief["):
        return "brief"
    if label.startswith("iface "):
        if label.endswith(" desc"):
            return "desc"
        if label.endswith(" sig"):
            return "sig"
        if " fixture[" in label:
            return "fixture"
    return "default"


# --------------------------------------------------------------------------- 5
def classify(label: str, text: str, p_leak: float) -> bool:
    """Return True to treat the span as PRIVATE (block the send / mark in leak map).

    Default: compare p_leak to the threshold for the span's kind. Examples of
    things you might add here:
      - return True if any denylist-like phrase you want to special-case is in text
      - return False for spans shorter than N chars (numbers alone tell nothing)
      - require p_leak >= 0.9 for fixtures but 0.4 for prose
    """
    return p_leak >= THRESHOLDS.get(span_kind(label), THRESHOLDS["default"])


# --------------------------------------------------------------------------- 6
def pick_frontier(public_view: dict, default: str) -> str:
    """Which coding agent builds the public half: "claude" | "codex" | "fake".

    `default` is [frontier].backend from config.toml. `public_view` is the
    brief and interfaces that will be sent, so you can route by content, e.g.
    send anything mentioning a UI to one agent and data plumbing to another.
    """
    return default


# --------------------------------------------------------------------------- 7
def split_prompt(prompt: str) -> list[tuple[str, str]]:
    """Cut the raw prompt into (label, text) spans for the pre-partition leak
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
