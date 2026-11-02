"""Client for the local (on-box) model via an OpenAI-compatible chat endpoint.

llama.cpp server, vLLM, Ollama, LM Studio and TabbyAPI all speak this.
Stdlib only. `base_url = "fake"` selects a canned backend for pipeline tests.
"""
from __future__ import annotations

import json
import re
import urllib.request
import urllib.error


class LocalModelError(RuntimeError):
    pass


def chat(cfg: dict, messages: list[dict], temperature: float = 0.2,
         max_tokens: int = 8192) -> str:
    if cfg.get("base_url") == "fake":
        from fakes import fake_local
        return fake_local(messages)
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    body = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg.get('api_key', 'none')}"})
    try:
        with urllib.request.urlopen(req, timeout=cfg.get("timeout", 600)) as r:
            data = json.loads(r.read())
    except urllib.error.URLError as e:
        raise LocalModelError(f"local model unreachable at {url}: {e}") from e
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise LocalModelError(f"unexpected response shape: {json.dumps(data)[:500]}") from e


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict:
    """Pull one JSON object out of a chat reply, tolerating fences and prose."""
    m = _FENCE.search(text)
    cand = m.group(1) if m else text
    start, end = cand.find("{"), cand.rfind("}")
    if start < 0 or end < 0:
        raise LocalModelError("no JSON object in local model reply:\n" + text[:800])
    try:
        return json.loads(cand[start:end + 1])
    except json.JSONDecodeError as e:
        raise LocalModelError(f"local model reply is not valid JSON ({e}):\n" + cand[:800])
