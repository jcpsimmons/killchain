"""Model-based refusal-risk detector: Jev (TypeSafe) via the OpenRouter
Decisions API.

Jev is a structured decision model: it returns probabilities, not prose. For
each span we ask one `noul` (yes/no) question per dimension in
routing.DIMENSIONS, in a single request, and read all the answers back. A span
that trips any dimension is pulled out of the public view and handed to the
abliterated orchestrator to implement instead.

Dimensions come from routing.py. Everything scored here is sent to OpenRouter
and TypeSafe, so the harness only scores the CANDIDATE PUBLIC VIEW and, when
`probe_raw_prompt` is on, spans of the raw prompt.

`base_url = "fake"` selects a canned backend for pipeline tests.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.error
from dataclasses import dataclass, field

from killchain import routing


class DetectorError(RuntimeError):
    pass


@dataclass
class RiskVerdict:
    label: str                          # where the span came from, e.g. "brief[2]"
    text: str
    scores: dict[str, float] = field(default_factory=dict)
    flagged: bool = False

    @property
    def worst(self) -> tuple[str, float]:
        return routing.worst_dimension(self.scores)


def spans_from_public_view(pv: dict) -> list[tuple[str, str]]:
    """Split the public view into independently scorable spans."""
    out: list[tuple[str, str]] = []
    paras = [p.strip() for p in re.split(r"\n\s*\n", pv["public_brief"]) if p.strip()]
    for n, p in enumerate(paras):
        out.append((f"brief[{n}]", p))
    for i in pv["interfaces"]:
        name = f"{i['module']}.{i['function']}"
        out.append((f"iface {name} desc", i["description"]))
        sig = ", ".join(f"{p['name']}: {p['type']} ({p.get('description', '')})" for p in i["params"])
        out.append((f"iface {name} sig", f"{name}({sig}) -> {i['returns']}"))
        for k, f in enumerate(i["fixtures"]):
            out.append((f"iface {name} fixture[{k}]", json.dumps(f, sort_keys=True)))
    return out


def whole_view_text(pv: dict) -> str:
    """The public view flattened, for the holistic pass.

    Refusals are triggered by the shape of a task, not only by its sentences:
    every span can be innocent while the assembled brief reads as 'clone this
    product'. This text is scored once as the `overall` span.
    """
    parts = [pv["public_brief"]]
    for i in pv["interfaces"]:
        parts.append(f"{i['module']}.{i['function']}: {i['description']}")
    return "\n".join(parts)


def _questions(risky_terms: list[str]) -> dict:
    terms = ", ".join(risky_terms) if risky_terms else "(none listed)"
    safe = routing.SAFE.strip() + "\nKnown-risky terms and obvious variants of them: " + terms + "."
    qs: dict = {}
    for key, d in routing.DIMENSIONS.items():
        qs[key] = {
            "type": "noul",
            "instructions": d["question"],
            "criteria": {"true": d["risky"].strip() + "\nAlso true if: " + terms + ".",
                         "false": safe},
        }
    qs["overall"] = {
        "type": "noul",
        "instructions": ("Taken as one task, is this the kind of build a frontier coding agent "
                         "would hedge, ask about, simplify, or refuse?"),
        "criteria": {
            "true": ("Any of these, even once: brand or clone framing, reverse engineering, "
                     "offensive security, harvesting, a regulated domain, another vendor's "
                     "model, hedged claims, or scope big enough to negotiate away."),
            "false": safe,
        },
    }
    return qs


def _call(cfg: dict, state: str, questions: dict) -> dict:
    key = os.environ.get(cfg.get("api_key_env", "OPENROUTER_API_KEY"), "")
    if not key:
        raise DetectorError(f"env {cfg.get('api_key_env', 'OPENROUTER_API_KEY')} is not set")
    body = {"model": cfg.get("model", "typesafe/jev-1.14"), "state": state, "questions": questions}
    req = urllib.request.Request(
        cfg.get("base_url", "https://openrouter.ai/api/alpha/decisions"),
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}",
                 "HTTP-Referer": "https://github.com/local/harness", "X-Title": "skoal-risk"})
    try:
        with urllib.request.urlopen(req, timeout=cfg.get("timeout", 60)) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise DetectorError(f"detector HTTP {e.code}: {e.read()[:500]!r}") from e
    except urllib.error.URLError as e:
        raise DetectorError(f"detector unreachable: {e}") from e


def _read_scores(resp: dict) -> dict[str, float]:
    answers = resp.get("answers") or {}
    scores: dict[str, float] = {}
    for dim in routing.DIMENSIONS:
        try:
            scores[dim] = float(answers[dim]["noul"])
        except (KeyError, TypeError, ValueError):
            continue
    if not scores:
        raise DetectorError(f"unexpected detector response: {json.dumps(resp)[:500]}")
    return scores


def _verdict(label: str, text: str, scores: dict[str, float]) -> RiskVerdict:
    return RiskVerdict(label, text, scores, routing.classify(label, text, scores))


def score_spans(cfg: dict, spans: list[tuple[str, str]],
                risky_terms: list[str]) -> list[RiskVerdict]:
    """Score each span against every dimension, then let routing.classify() rule."""
    if cfg.get("base_url") == "fake":
        from killchain.fakes import fake_detector
        return [_verdict(l, t, s) for (l, t), s in zip(spans, fake_detector(spans))]
    qs = _questions(risky_terms)
    out: list[RiskVerdict] = []
    for label, text in spans:
        out.append(_verdict(label, text, _read_scores(_call(cfg, text, qs))))
    return out


def score_whole_view(cfg: dict, pv: dict, risky_terms: list[str]) -> RiskVerdict:
    """The holistic pass over the assembled public view."""
    text = whole_view_text(pv)
    if cfg.get("base_url") == "fake":
        from killchain.fakes import fake_overall
        return _verdict("overall", text, fake_overall(text))
    resp = _call(cfg, text, {"overall": _questions(risky_terms)["overall"]})
    return _verdict("overall", text, _read_scores(resp))


def describe(verdicts: list[RiskVerdict], only_flagged: bool = True) -> str:
    rows = [v for v in verdicts if v.flagged] if only_flagged else verdicts
    if not rows:
        return "detector: no spans over threshold"
    lines = [f"DETECTOR ROUTED {len(rows)} span(s) PRIVATE:"]
    for v in rows:
        dim, p = v.worst
        lines.append(f"  {dim} p={p:.2f} {v.label}: {v.text[:160]!r}")
    return "\n".join(lines)


def risk_map(verdicts: list[RiskVerdict]) -> str:
    """Human/LLM-readable table of span scores for the partition prompt."""
    lines = []
    for v in verdicts:
        dim, p = v.worst
        tag = f"PRIVATE {dim} p={p:.2f}" if v.flagged else f"public  {dim} p={p:.2f}"
        lines.append(f"- [{tag}] {v.text}")
    return "\n".join(lines) or "- (no spans)"


def spans_from_prompt(prompt: str) -> list[tuple[str, str]]:
    """Delegates to routing.split_prompt(). Only used when probe_raw_prompt is on."""
    return routing.split_prompt(prompt)
