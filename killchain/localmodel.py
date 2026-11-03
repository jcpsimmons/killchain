"""Client for the orchestrator (abliterated) model via an OpenAI-compatible
chat endpoint: refuseless by default, or llama.cpp / vLLM / Ollama / LM Studio
for an on-box model. Stdlib only. `base_url = "fake"` selects a canned backend.

The API key comes from the env var named by `api_key_env`, else from the
dotenv-style file at `env_file` (default: the Hermes .env). Never stored here.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import urllib.request
import urllib.error


class LocalModelError(RuntimeError):
    pass


def api_key(cfg: dict) -> str:
    name = cfg.get("api_key_env", "")
    if name and os.environ.get(name):
        return os.environ[name]
    env_file = Path(cfg.get("env_file", "~/.hermes/.env")).expanduser()
    if name and env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return cfg.get("api_key", "none")


def chat(cfg: dict, messages: list[dict], temperature: float = 0.2,
         max_tokens: int = 8192) -> str:
    if cfg.get("base_url") == "fake":
        from killchain.fakes import fake_local
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
                 "Authorization": f"Bearer {api_key(cfg)}"})
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
