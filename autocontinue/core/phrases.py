"""推进词池：轮换、不连续重复、支持变量。"""
from __future__ import annotations

import random
from collections import defaultdict
from typing import Optional


class PhrasePool:
    def __init__(self, phrases: list[str], templates: dict[str, list[str]], seed: Optional[int] = None):
        self.phrases = [p for p in phrases if p.strip()] or ["请继续。"]
        self.templates = templates
        self.rng = random.Random(seed)
        self._last: Optional[str] = None

    def _choose(self, options: list[str]) -> str:
        pool = [o for o in options if o != self._last] or options
        choice = self.rng.choice(pool)
        self._last = choice
        return choice

    def pick(self, kind: str = "default", **variables) -> str:
        options = self.templates.get(kind) if kind != "default" else None
        text = self._choose(options or self.phrases)
        vals = defaultdict(str, {k: v for k, v in variables.items() if v is not None})
        try:
            return text.format_map(vals)
        except (ValueError, IndexError):
            return text
