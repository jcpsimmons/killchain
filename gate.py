"""Redaction gate: deterministic check on everything that leaves the machine.

The local model does the semantic partition; this gate is the hard backstop.
It scans outbound text against:
  * HARD_RULES from routing.py (keys, tokens, private-key blocks, ...)
  * a user denylist (literal strings, or `re:<regex>` lines) kept in the
    private root, because the denylist itself names proprietary things.
Any hit blocks the send.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from routing import HARD_RULES as BUILTIN  # edit routing.py, not this file

DENYLIST_TEMPLATE = """# Redaction denylist. One entry per line. Lines starting with # are ignored.
# Plain lines are case-insensitive literal matches.
# Lines starting with re: are Python regexes (case-insensitive).
# Put here: internal project codenames, customer names, product names you
# have not announced, internal hostnames, employee names, anything that
# must never reach a third-party API.
#
# examples:
# project-nightjar
# re:\\bacme\\s*corp\\b
# re:\\b10\\.0\\.\\d+\\.\\d+\\b
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
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("re:"):
            rules.append((f"denylist regex {line[3:]!r}", re.compile(line[3:], re.I)))
        else:
            rules.append((f"denylist literal {line!r}", re.compile(re.escape(line), re.I)))
    return rules


def scan(text: str, denylist: list[tuple[str, re.Pattern]]) -> list[Hit]:
    hits: list[Hit] = []
    for name, pat in BUILTIN + denylist:
        for m in pat.finditer(text):
            s, e = max(0, m.start() - 40), min(len(text), m.end() + 40)
            ctx = text[s:e].replace("\n", " ")
            hits.append(Hit(rule=name, match=m.group(0), context=ctx))
    return hits


def describe(hits: list[Hit]) -> str:
    lines = [f"REDACTION GATE BLOCKED SEND: {len(hits)} hit(s)"]
    for h in hits:
        lines.append(f"  [{h.rule}] {h.match!r}\n      ...{h.context}...")
    return "\n".join(lines)
