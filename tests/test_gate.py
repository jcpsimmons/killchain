"""The redaction gate is the last thing between a secret and a third-party API.

Two behaviours under test: blocking (secrets, keys, internal hosts) must fire,
and hints (framing words) must NOT block, because a false block stops a build.
"""
from __future__ import annotations

import pytest

from killchain import gate


@pytest.fixture(autouse=True)
def restore_builtin_rules():
    """gate.scan imports HARD_RULES by reference; undo any mutation."""
    from killchain.routing import HARD_RULES
    rules = list(HARD_RULES)
    yield
    HARD_RULES[:] = rules


# ------------------------------------------------------------------ blocking
@pytest.mark.parametrize("text,label", [
    ("AKIA" + "A" * 16, "aws access key"),
    ("sk-ant-" + "a" * 30, "openai/anthropic style key"),
    ("sk-" + "a" * 25, "openai/anthropic style key"),
    ("ghp_" + "a" * 36, "github token"),
    ("gho_" + "1" * 36, "github token"),
    ("xoxb-" + "1" * 20, "slack token"),
    ("-----BEGIN RSA PRIVATE KEY-----", "private key block"),
    ("Authorization: Bearer abc123def", "bearer header"),
    ("postgres://user:pw@host/db", "connection string"),
    ("mongodb+srv://a:b@c/d", "connection string"),
    ("10.0.0.5", "private ipv4"),
    ("192.168.1.20", "private ipv4"),
    ("172.16.4.4", "private ipv4"),
])
def test_hard_rules_block(text, label):
    hits = gate.scan(text, [])
    assert hits, f"{label} should have blocked: {text!r}"
    assert any(label in h.rule for h in hits), [h.rule for h in hits]


@pytest.mark.parametrize("text", [
    "8.8.8.8",
    "172.32.0.1",          # outside 172.16-31
    "172.15.0.1",
    "this is ordinary text with no secrets",
    "sk-short",
])
def test_clean_text_does_not_block(text):
    assert gate.scan(text, []) == []


def test_partial_private_ip_still_blocks():
    """The last octet is optional by design: a truncated host is still a leak."""
    assert gate.scan("10.0.0", [])


def test_denylist_literal_blocks_case_insensitively(tmp_path):
    p = tmp_path / "denylist.txt"
    p.write_text("nightjar\n")
    rules = gate.load_denylist(p)
    assert gate.scan("we used NightJar for this", rules)


def test_denylist_regex_blocks(tmp_path):
    p = tmp_path / "denylist.txt"
    p.write_text(r"re:\bgpl-?3\b" + "\n")
    rules = gate.load_denylist(p)
    assert gate.scan("licensed under GPL3", rules)
    assert gate.scan("licensed under GPL-3", rules)
    assert not gate.scan("licensed under MIT", rules)


def test_denylist_literal_is_escaped(tmp_path):
    p = tmp_path / "denylist.txt"
    p.write_text("a.b\n")
    rules = gate.load_denylist(p)
    assert gate.scan("literally a.b here", rules)
    assert not gate.scan("axb here", rules)


def test_denylist_ignores_comments_and_blanks(tmp_path):
    p = tmp_path / "denylist.txt"
    p.write_text("# comment\n\n   \nrealterm\n")
    rules = gate.load_denylist(p)
    assert len(rules) == 1


def test_bad_denylist_regex_exits_with_line_number(tmp_path):
    p = tmp_path / "denylist.txt"
    p.write_text("fine\nre:[unclosed\n")
    with pytest.raises(SystemExit, match="line 2"):
        gate.load_denylist(p)


def test_missing_denylist_creates_template(tmp_path):
    p = tmp_path / "sub" / "denylist.txt"
    rules = gate.load_denylist(p)
    assert rules == []
    assert p.exists()
    assert "Risk denylist" in p.read_text()


def test_hit_context_is_bounded(tmp_path):
    p = tmp_path / "denylist.txt"
    p.write_text("needle\n")
    rules = gate.load_denylist(p)
    text = ("x" * 500) + "needle" + ("y" * 500)
    hits = gate.scan(text, rules)
    assert len(hits[0].context) <= 40 + len("needle") + 40


def test_hit_records_match_text(tmp_path):
    p = tmp_path / "denylist.txt"
    p.write_text("needle\n")
    rules = gate.load_denylist(p)
    hit = gate.scan("a needle b", rules)[0]
    assert hit.match == "needle"


# -------------------------------------------------------------------- hints
@pytest.mark.parametrize("text,label", [
    ("make it exactly like the original", "clone/port framing"),
    ("a drop-in replacement", "clone/port framing"),
    ("roughly 40% of them", "hedge word"),
    ("as far as i know this is true", "hedge word"),
    ("from memory, the parser does", "memory caveat"),
    ("it is under the GPL license", "licence talk"),
    ("TODO figure out later", "undefined behaviour"),
])
def test_framing_hints_are_detected(text, label):
    labels = [h.rule for h in gate.hints(text)]
    assert any(label in got for got in labels), labels


def test_hints_do_not_block():
    """A hint must never stop a send; that is gate.scan's job."""
    text = "exactly like the original, roughly speaking, from memory, GPL"
    assert gate.hints(text), "should produce hints"
    assert gate.scan(text, []) == [], "hints must not block"


def test_hints_on_clean_text_is_empty():
    assert gate.hints("write a function that adds two integers") == []


def test_clean_framing_is_not_flagged():
    """Ordinary engineering prose should not trip the hint regexes."""
    text = ("Implement a function that parses a CSV row and returns a list of "
            "fields. Raise ValueError on a malformed row. Include tests.")
    assert gate.hints(text) == []


# -------------------------------------------------------------- formatting
def test_describe_includes_count_and_rules(tmp_path):
    p = tmp_path / "denylist.txt"
    p.write_text("needle\n")
    msg = gate.describe(gate.scan("a needle b", gate.load_denylist(p)))
    assert "BLOCKED SEND" in msg
    assert "1 hit" in msg
    assert "needle" in msg


def test_describe_hints_none():
    assert gate.describe_hints([]) == "framing hints: none"


def test_describe_hints_lists_phrases():
    msg = gate.describe_hints(gate.hints("roughly speaking"))
    assert "FRAMING HINTS" in msg
