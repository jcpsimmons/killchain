"""Redaction gate: a deterministic check on everything that leaves the machine.

Two jobs now:

  * **Blocks** (secrets, keys, internal hostnames) against routing.HARD_RULES
    and the user denylist. Any hit stops the send.
  * **Hints** against routing.FRAMING_HINTS: clone/port wording, hedge words,
    licence talk, TODO-ish scope. These do not block, they are printed at
    review so you can see the phrasing that is most likely to make a frontier
    agent hedge or negotiate before it goes out.

The denylist lives in the private root because it names the terms that tip the
frontier off: product names it will recognise, licence names, codenames. Lines
are literals, or `re:<regex>`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from killchain.routing import FRAMING_HINTS, HARD_RULES as BUILTIN  # edit routing.py, not this file

DENYLIST_TEMPLATE = """# Risk denylist. One entry per line. Lines starting with # are ignored.
# Plain lines are case-insensitive literal matches.
# Lines starting with re: are Python regexes (case-insensitive).
#
# Two uses: things that must never reach a third-party API (keys, internal
# hostnames), and terms that reliably make a frontier model hedge: product
# names it will recognise, vendor names, licence names, project codenames.
#
# examples:
# nightjar
# re:\\bfrom memory\\b
# re:\\bgpl-?3\\b
"""


@dataclass
class Hit:
    rule: str
    match: str
    context: str


def load_denylist(path: Path) -> list[tuple[str, re.Pattern]]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DENYLIST_TEMPLATE)
        return []
    rules = []
    for n, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("re:"):
            try:
                rules.append((f"denylist regex {line[3:]!r}", re.compile(line[3:], re.I)))
            except re.error as e:
                raise SystemExit(f"denylist line {n}: bad regex {line[3:]!r}: {e}") from e
        else:
            rules.append((f"denylist literal {line!r}", re.compile(re.escape(line), re.I)))
    return rules


def _scan(text: str, rules: list[tuple[str, re.Pattern]]) -> list[Hit]:
    hits: list[Hit] = []
    for name, pat in rules:
        for m in pat.finditer(text):
            s, e = max(0, m.start() - 40), min(len(text), m.end() + 40)
            ctx = text[s:e].replace("\n", " ")
            hits.append(Hit(rule=name, match=m.group(0), context=ctx))
    return hits


def scan(text: str, denylist: list[tuple[str, re.Pattern]]) -> list[Hit]:
    """Blocking scan."""
    return _scan(text, BUILTIN + denylist)


def hints(text: str) -> list[Hit]:
    """Non-blocking framing warnings."""
    return _scan(text, FRAMING_HINTS)


def describe(hits: list[Hit]) -> str:
    lines = [f"REDACTION GATE BLOCKED SEND: {len(hits)} hit(s)"]
    for h in hits:
        lines.append(f"  [{h.rule}] {h.match!r}\n      ...{h.context}...")
    return "\n".join(lines)


def describe_hints(hits: list[Hit]) -> str:
    if not hits:
        return "framing hints: none"
    lines = [f"FRAMING HINTS: {len(hits)} phrase(s) that raise refusal odds"]
    for h in hits:
        lines.append(f"  [{h.rule}] {h.match!r}")
    return "\n".join(lines)
