"""Canned backends so the whole pipeline can be exercised with no models.

Toy task: rank inbound leads from a CSV; the lead-scoring formula is the
'proprietary' part. The fake local model deliberately trips the regex gate on
attempt 1 and the detector on attempt 2 so both feedback loops are covered.
"""
from __future__ import annotations

import json
from pathlib import Path

CONTRACT = {
    "public_brief": (
        "Build `leadrank`, a stdlib-only Python CLI that reads a CSV of inbound leads "
        "and prints them ranked by score, highest first.\n\n"
        "Input CSV columns: lead_id, company, company_size (int), industry (str), visits (int). "
        "Scoring is provided by the private function contracts.scoring.score_lead, which takes "
        "a dict of the non-id columns and returns a float in [0, 1].\n\n"
        "CLI: `python -m app.rank <csv> [--top N]`. Output one line per lead: "
        "`<rank>. <lead_id> <company> <score:.2f>`. Malformed rows are skipped with a warning "
        "on stderr. Add unittest tests under tests/ covering ranking order and --top."
    ),
    "interfaces": [{
        "module": "scoring",
        "function": "score_lead",
        "description": "Score one lead. Higher means more likely to convert.",
        "params": [{"name": "lead", "type": "dict",
                    "description": "keys: company_size (int), industry (str), visits (int)"}],
        "returns": "float",
        "fixtures": [
            {"inputs": {"lead": {"company_size": 50, "industry": "saas", "visits": 12}},
             "output": 0.42, "note": "decoy"},
            {"inputs": {"lead": {"company_size": 10, "industry": "retail", "visits": 0}},
             "output": 0.08, "note": "decoy"},
        ],
        "private_fixtures": [
            {"inputs": {"lead": {"company_size": 50, "industry": "saas", "visits": 12}},
             "output": 0.74, "note": "real"},
            {"inputs": {"lead": {"company_size": 10, "industry": "retail", "visits": 0}},
             "output": 0.1, "note": "real"},
        ],
    }],
    "private_notes": "Real formula: 0.01*company_size + 0.02*visits, capped at 1.0, rounded to 4dp.",
    "private_plan": "1. implement score_lead with the formula. 2. clamp. 3. round.",
}

IMPL = {
    "files": {
        "private_impl/__init__.py": "",
        "private_impl/scoring.py": (
            "def score_lead(lead: dict) -> float:\n"
            "    s = 0.01 * int(lead.get('company_size', 0)) + 0.02 * int(lead.get('visits', 0))\n"
            "    return round(min(1.0, max(0.0, s)), 4)\n"),
    },
    "notes": "fake implementation",
}


def fake_local(messages: list[dict]) -> str:
    system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
    user = messages[-1]["content"]
    if "orchestrator for a two-party build" in system:
        convo = "\n".join(m["content"] for m in messages if m["role"] == "user")
        if "PROBE RESULTS" not in convo:
            return json.dumps({"probes": ["rank leads from a CSV by score",
                                          "FLAGME the formula is 0.01 per employee"]})
        attempt = convo.count("PREVIOUS ATTEMPT REJECTED") + 1
        c = json.loads(json.dumps(CONTRACT))
        if attempt == 1:
            c["public_brief"] += "\n\nThis replaces the project nightjar scorer."
        elif attempt == 2:
            c["public_brief"] += "\n\nFLAGME the old weights were 0.01 and 0.02."
        return "```json\n" + json.dumps(c) + "\n```"
    if "implementing the private half" in user:
        return json.dumps(IMPL)
    raise RuntimeError("fake_local: unrecognised prompt")


def fake_detector(spans: list[tuple[str, str]]) -> list[float]:
    return [0.97 if "FLAGME" in t else 0.03 for _, t in spans]


def _contract_matches_canned(public_dir: Path) -> bool:
    try:
        pv = json.loads((public_dir / "contract.public.json").read_text())
        i = pv["interfaces"][0]
        return (len(pv["interfaces"]) == 1 and i["module"] == "scoring"
                and i["function"] == "score_lead" and [p["name"] for p in i["params"]] == ["lead"])
    except (OSError, KeyError, IndexError, ValueError):
        return False


def fake_frontier(prompt: str, public_dir: Path) -> str:
    if not _contract_matches_canned(public_dir):
        (public_dir / "NOTES.md").write_text(
            "# fake frontier\n\nContract does not match the canned toy signature "
            "(scoring.score_lead(lead)); wrote no app. Use a real frontier backend.\n")
        return "fake frontier: contract differs from canned toy; wrote NOTES.md only"
    app = public_dir / "app"
    app.mkdir(exist_ok=True)
    (app / "__init__.py").write_text("")
    (app / "rank.py").write_text('''"""leadrank CLI (built by fake frontier)."""
import argparse
import csv
import sys

from contracts import scoring


def load(path):
    rows = []
    with open(path, newline="") as f:
        for n, r in enumerate(csv.DictReader(f), 2):
            try:
                lead = {"company_size": int(r["company_size"]), "industry": r["industry"],
                        "visits": int(r["visits"])}
            except (KeyError, ValueError) as e:
                print(f"warning: skipping row {n}: {e}", file=sys.stderr)
                continue
            rows.append((r["lead_id"], r["company"], lead))
    return rows


def rank(rows, top=None):
    scored = [(lid, co, scoring.score_lead(lead=lead)) for lid, co, lead in rows]
    scored.sort(key=lambda t: t[2], reverse=True)
    return scored[:top] if top else scored


def main(argv=None):
    ap = argparse.ArgumentParser(prog="leadrank")
    ap.add_argument("csv")
    ap.add_argument("--top", type=int)
    a = ap.parse_args(argv)
    for n, (lid, co, s) in enumerate(rank(load(a.csv), a.top), 1):
        print(f"{n}. {lid} {co} {s:.2f}")


if __name__ == "__main__":
    main()
''')
    tests = public_dir / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_rank.py").write_text('''import unittest
from app.rank import rank


class RankTests(unittest.TestCase):
    ROWS = [
        ("L1", "Acme", {"company_size": 10, "industry": "retail", "visits": 0}),
        ("L2", "Beta", {"company_size": 50, "industry": "saas", "visits": 12}),
    ]

    def test_order(self):
        out = rank(self.ROWS)
        self.assertEqual([r[0] for r in out], ["L2", "L1"])

    def test_top(self):
        self.assertEqual(len(rank(self.ROWS, top=1)), 1)
''')
    (public_dir / "NOTES.md").write_text("# leadrank\n\nRun: `python -m app.rank leads.csv --top 5`\n")
    return "fake frontier: wrote app/rank.py, tests/test_rank.py, NOTES.md"
