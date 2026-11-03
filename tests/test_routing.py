"""routing.py decides what gets pulled out of the public view.

`apply()` mutates module-level globals, so every test here snapshots and
restores them. A leak would silently retune later tests.
"""
from __future__ import annotations

import copy

import pytest

from killchain import routing


@pytest.fixture(autouse=True)
def restore_routing():
    dims = copy.deepcopy(routing.DIMENSIONS)
    thr = dict(routing.THRESHOLDS)
    slack = dict(routing.KIND_SLACK)
    rules = list(routing.HARD_RULES)
    yield
    routing.DIMENSIONS.clear()
    routing.DIMENSIONS.update(dims)
    routing.THRESHOLDS.clear()
    routing.THRESHOLDS.update(thr)
    routing.KIND_SLACK.clear()
    routing.KIND_SLACK.update(slack)
    routing.HARD_RULES[:] = rules


# --------------------------------------------------------------- span_kind
@pytest.mark.parametrize("label,kind", [
    ("prompt[0]", "prompt"),
    ("prompt[1.2]", "prompt"),
    ("probe[3]", "probe"),
    ("brief[0]", "brief"),
    ("overall", "overall"),
    ("iface Engine.run desc", "desc"),
    ("iface Engine.run sig", "sig"),
    ("iface Engine.run fixture[0]", "fixture"),
    ("something else", "default"),
])
def test_span_kind(label, kind):
    assert routing.span_kind(label) == kind


# ------------------------------------------------------------- thresholds
def test_threshold_uses_default_for_unknown_dimension():
    assert routing.threshold_for("prompt[0]", "not-a-dimension") == routing.THRESHOLDS["default"]


def test_fixtures_get_more_slack_than_briefs():
    assert routing.threshold_for("iface M.f fixture[0]", "attribution") > \
        routing.threshold_for("brief[0]", "attribution")


def test_overall_is_stricter_than_default():
    assert routing.threshold_for("overall", "attribution") < \
        routing.threshold_for("prompt[0]", "attribution")


def test_threshold_never_exceeds_one():
    routing.KIND_SLACK["fixture"] = 0.9
    assert routing.threshold_for("iface M.f fixture[0]", "attribution") == 1.0


# --------------------------------------------------------------- classify
def test_classify_routes_when_over_threshold():
    assert routing.classify("brief[0]", "text", {"attribution": 0.99}) is True


def test_classify_keeps_span_when_under_threshold():
    assert routing.classify("brief[0]", "text", {"attribution": 0.01}) is False


def test_classify_with_no_scores_keeps_span():
    assert routing.classify("brief[0]", "text", {}) is False


def test_classify_routes_if_any_single_dimension_trips():
    scores = {"attribution": 0.0, "regulated": 1.0, "extraction": 0.0}
    assert routing.classify("brief[0]", "text", scores) is True


def test_classify_ignores_text_argument_by_default():
    # Default implementation is score-only; text is there for your overrides.
    assert routing.classify("brief[0]", "AKIA" + "A" * 16, {"attribution": 0.0}) is False


# ------------------------------------------------------- worst_dimension
def test_worst_dimension_picks_highest():
    assert routing.worst_dimension({"a": 0.2, "b": 0.9}) == ("b", 0.9)


def test_worst_dimension_empty_is_dash():
    assert routing.worst_dimension({}) == ("-", 0.0)


# ---------------------------------------------------------- pick_frontier
def test_pick_frontier_returns_default():
    assert routing.pick_frontier({"public_brief": "x"}, "claude") == "claude"


# ----------------------------------------------------------- split_prompt
def test_split_prompt_splits_paragraphs():
    spans = routing.split_prompt("first para\n\nsecond para")
    assert [t for _, t in spans] == ["first para", "second para"]
    assert [label for label, _ in spans] == ["prompt[0]", "prompt[1]"]


def test_split_prompt_drops_blank_paragraphs():
    spans = routing.split_prompt("a\n\n\n\n\nb")
    assert len(spans) == 2


def test_split_prompt_breaks_long_paragraph_into_sentences():
    long_para = ("This is a sentence. " * 30).strip()
    spans = routing.split_prompt(long_para)
    assert len(spans) > 1
    assert spans[0][0] == "prompt[0.0]"
    assert all(not t.endswith(".") or len(t) < 400 for _, t in spans)


def test_split_prompt_short_long_para_stays_whole_at_boundary():
    para = "x" * 400
    spans = routing.split_prompt(para)
    assert len(spans) == 1


def test_split_prompt_over_boundary_uses_sentence_labels():
    # Long paragraphs always take the sentence path, even with no sentence
    # breaks, so the label gains the .k suffix.
    para = "x" * 401
    spans = routing.split_prompt(para)
    assert spans[0][0] == "prompt[0.0]"


def test_split_prompt_short_para_has_no_sentence_suffix():
    assert routing.split_prompt("short para")[0][0] == "prompt[0]"


def test_split_prompt_empty_returns_empty():
    assert routing.split_prompt("   \n\n  ") == []


# ------------------------------------------------------------------ apply
def test_apply_with_empty_config_is_noop():
    before = copy.deepcopy(routing.THRESHOLDS)
    assert routing.apply({}) == []
    assert routing.THRESHOLDS == before


def test_apply_rejects_unknown_section_key():
    with pytest.raises(SystemExit, match="unknown key"):
        routing.apply({"routing": {"threshold": {"x": 0.5}}})


def test_apply_tunes_threshold():
    notes = routing.apply({"routing": {"thresholds": {"hedge_framing": 0.75}}})
    assert routing.THRESHOLDS["hedge_framing"] == 0.75
    assert any("threshold hedge_framing" in n for n in notes)


def test_apply_rejects_unknown_dimension_in_thresholds():
    with pytest.raises(SystemExit, match="no dimension"):
        routing.apply({"routing": {"thresholds": {"nope": 0.5}}})


def test_apply_rejects_out_of_range_threshold():
    with pytest.raises(SystemExit, match="not a probability"):
        routing.apply({"routing": {"thresholds": {"hedge_framing": 1.5}}})
    assert routing.THRESHOLDS["hedge_framing"] == 0.60  # unchanged


def test_apply_disables_dimension():
    routing.apply({"routing": {"disabled_dimensions": ["hedge_framing"]}})
    assert "hedge_framing" not in routing.DIMENSIONS
    assert "hedge_framing" not in routing.THRESHOLDS


def test_apply_rejects_unknown_disabled_dimension():
    with pytest.raises(SystemExit, match="no dimension"):
        routing.apply({"routing": {"disabled_dimensions": ["made_up"]}})


def test_apply_adds_literal_hard_rule():
    routing.apply({"routing": {"hard_rules": ["TICKER-SYMBOL"]}})
    name, rx = routing.HARD_RULES[-1]
    assert "TICKER-SYMBOL" in name
    assert rx.search("this mentions ticker-symbol inline")  # case-insensitive


def test_apply_escapes_literal_hard_rule():
    routing.apply({"routing": {"hard_rules": ["a.b"]}})
    _, rx = routing.HARD_RULES[-1]
    assert rx.search("a.b")
    assert not rx.search("axb"), "literal rule must not act as a regex"


def test_apply_prefix_re_treats_rule_as_regex():
    routing.apply({"routing": {"hard_rules": [r"re:\bsk-[a-z0-9]{20,}"]}})
    _, rx = routing.HARD_RULES[-1]
    assert rx.search("sk-" + "a" * 20)


def test_apply_rejects_bad_regex():
    with pytest.raises(SystemExit, match="bad regex"):
        routing.apply({"routing": {"hard_rules": ["re:[unclosed"]}})


def test_apply_accepts_dict_hard_rule_with_reason():
    routing.apply({"routing": {"hard_rules": [
        {"pattern": "CODENAME", "why": "internal codename"}]}})
    name, rx = routing.HARD_RULES[-1]
    assert name == "internal codename"
    assert rx.search("codename")


def test_apply_kind_slack():
    routing.apply({"routing": {"kind_slack": {"fixture": 0.0}}})
    assert routing.KIND_SLACK["fixture"] == 0.0
