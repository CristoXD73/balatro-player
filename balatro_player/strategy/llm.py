"""Minimal client for a local OpenAI-compatible chat server.

Works with ``mlx_lm.server`` (default), LM Studio and Ollama. Qwen3 models
think out loud inside <think> tags; that part is stripped before parsing.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_URL = "http://127.0.0.1:8080/v1"
DEFAULT_MODEL = "mlx-community/Qwen3-30B-A3B-4bit"


class LLM:
    def __init__(self, url: str = DEFAULT_URL, model: str = DEFAULT_MODEL, timeout: float = 120.0,
                 temperature: float = 0.3, max_tokens: int = 1200, think: bool = False):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.think = think
        self.calls = 0
        self.seconds = 0.0

    def available(self) -> bool:
        try:
            with urllib.request.urlopen(self.url + "/models", timeout=5) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def chat(self, system: str, user: str) -> str:
        if not self.think:
            user = user + "\n/no_think"        # Qwen3: skip long reasoning for speed
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        req = urllib.request.Request(self.url + "/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        t = time.time()
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            reply = json.loads(r.read())
        self.calls += 1
        self.seconds += time.time() - t
        return reply["choices"][0]["message"]["content"] or ""

    def chat_json(self, system: str, user: str) -> Tuple[Optional[Any], str]:
        """Returns (parsed JSON or None, raw text)."""
        raw = self.chat(system, user)
        return extract_json(raw), raw


_THINK = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Optional[Any]:
    text = _THINK.sub("", text).strip()
    candidates: List[str] = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    for c in candidates:
        c = c.strip()
        for opener, closer in (("{", "}"), ("[", "]")):
            start, end = c.find(opener), c.rfind(closer)
            if start != -1 and end > start:
                try:
                    return json.loads(c[start:end + 1])
                except json.JSONDecodeError:
                    continue
    return None
