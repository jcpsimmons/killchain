"""Model-based rule detector: Jev (TypeSafe) via the OpenRouter Decisions API.

Jev is a structured decision model: it returns a probability, not text. We
ask one `noul` (yes/no) question per span: "does this span reveal information
the confidentiality policy says must stay local?"

IMPORTANT: everything passed here is sent to OpenRouter and TypeSafe. The
harness therefore only ever scores the CANDIDATE PUBLIC VIEW (brief,
interface descriptions, fixtures), never the raw prompt or private notes.
Spans the detector flags are fed back to the local model so it can
re-partition. That is how the local model "tests subsets": via the harness,
on text that was already meant to go out.

`base_url = "fake"` selects a canned backend for pipeline tests.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.error
from dataclasses import dataclass

import routing


class DetectorError(RuntimeError):
    pass


@dataclass
class SpanVerdict:
    label: str      # where the span came from, e.g. "brief[2]" or "iface scoring.score_lead desc"
    text: str
    p_leak: float
    flagged: bool


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


def _question(denylist_terms: list[str]) -> dict:
    terms = ", ".join(denylist_terms) if denylist_terms else "(none listed)"
    return {
        "type": "noul",
        "instructions": ("Does this text reveal information that the confidentiality policy "
                         "says must not be sent to a third-party coding agent?"),
        "criteria": {
            "true": (routing.POLICY.strip() + "\nDenylisted terms or obvious variants of them: "
                     + terms + "."),
            "false": routing.PUBLIC_OK.strip(),
        },
    }


def _call(cfg: dict, state: str, questions: dict) -> dict:
    key = os.environ.get(cfg.get("api_key_env", "OPENROUTER_API_KEY"), "")
    if not key:
        raise DetectorError(f"env {cfg.get('api_key_env', 'OPENROUTER_API_KEY')} is not set")
    body = {"model": cfg.get("model", "typesafe/jev-1.14"), "state": state, "questions": questions}
    req = urllib.request.Request(
        cfg.get("base_url", "https://openrouter.ai/api/alpha/decisions"),
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}",
                 "HTTP-Referer": "https://github.com/local/harness", "X-Title": "harness-detector"})
    try:
        with urllib.request.urlopen(req, timeout=cfg.get("timeout", 60)) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise DetectorError(f"detector HTTP {e.code}: {e.read()[:500]!r}") from e
    except urllib.error.URLError as e:
        raise DetectorError(f"detector unreachable: {e}") from e


def score_spans(cfg: dict, spans: list[tuple[str, str]],
                denylist_terms: list[str]) -> list[SpanVerdict]:
    """Score each span with Jev, then let routing.classify() make the call."""
    if cfg.get("base_url") == "fake":
        from fakes import fake_detector
        return [SpanVerdict(l, t, p, routing.classify(l, t, p))
                for (l, t), p in zip(spans, fake_detector(spans))]
    q = _question(denylist_terms)
    out: list[SpanVerdict] = []
    for label, text in spans:
        resp = _call(cfg, text, {"leak": q})
        try:
            p = float(resp["answers"]["leak"]["noul"])
        except (KeyError, TypeError, ValueError) as e:
            raise DetectorError(f"unexpected detector response: {json.dumps(resp)[:500]}") from e
        out.append(SpanVerdict(label, text, p, routing.classify(label, text, p)))
    return out


def describe(verdicts: list[SpanVerdict], only_flagged: bool = True) -> str:
    rows = [v for v in verdicts if v.flagged] if only_flagged else verdicts
    if not rows:
        return "detector: no spans flagged"
    lines = [f"DETECTOR FLAGGED {len(rows)} span(s):"]
    for v in rows:
        lines.append(f"  p={v.p_leak:.2f} {v.label}: {v.text[:160]!r}")
    return "\n".join(lines)


def spans_from_prompt(prompt: str) -> list[tuple[str, str]]:
    """Delegates to routing.split_prompt(). Only used when probe_raw_prompt is on."""
    return routing.split_prompt(prompt)


def leak_map(verdicts: list[SpanVerdict]) -> str:
    """Human/LLM-readable table of span scores for the partition prompt."""
    lines = []
    for v in verdicts:
        tag = "PRIVATE" if v.flagged else "public "
        lines.append(f"- [{tag} p={v.p_leak:.2f}] {v.text}")
    return "\n".join(lines) or "- (no spans)"
