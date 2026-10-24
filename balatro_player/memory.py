"""Fast learning loop: lessons written after each run, recalled when relevant.

After every run the LLM writes 1-3 lessons tagged with what they're about
(jokers, hand types, ante stage, deck, boss). During play, lessons whose tags
match the current situation are put into the prompt. Each lesson tracks how
runs went when it was used; lessons that keep showing up in bad runs are
retired automatically.
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional, Set, Tuple

from .logbook import Logbook
from .state import GameView


def stage(ante: int) -> str:
    return "ante-early" if ante <= 2 else "ante-mid" if ante <= 5 else "ante-late"


def situation_tags(view: GameView) -> Set[str]:
    tags = {stage(view.ante), (view.raw.get("deck") or "").lower()}
    tags |= {j.key.lower() for j in view.jokers}
    for c in view.shop + view.pack:
        if c.get("key"):
            tags.add(c["key"].lower())
    played = view.hand_stat("played")
    for h in sorted(played, key=lambda h: -played[h])[:2]:
        if played[h]:
            tags.add(h.lower())
    boss = (view.blinds.get("boss") or {}).get("name")
    if boss:
        tags.add(boss.lower())
    return {t for t in tags if t}


class Memory:
    def __init__(self, log: Logbook, k: int = 4, min_uses_to_judge: int = 8):
        self.log = log
        self.k = k
        self.min_uses = min_uses_to_judge

    def recall(self, view: GameView) -> List[Tuple[int, str]]:
        tags = situation_tags(view)
        rows = self.log.lessons()
        if not rows:
            return []
        avg_all = self._average_ante()
        scored = []
        for r in rows:
            ltags = set(json.loads(r["tags"] or "[]"))
            overlap = len(tags & ltags)
            if overlap == 0 and ltags:
                continue
            quality = 0.0
            if r["uses"] >= 3:
                quality = (r["ante_sum"] / r["uses"] - avg_all) / 2
            explore = 1 / math.sqrt(1 + r["uses"])          # give new lessons a chance
            scored.append((overlap + quality + explore, r["id"], r["text"]))
        scored.sort(reverse=True)
        return [(lid, text) for _, lid, text in scored[: self.k]]

    def prune(self) -> int:
        """Retire lessons that are consistently used in below-average runs."""
        avg_all = self._average_ante()
        retired = 0
        for r in self.log.lessons():
            if r["uses"] >= self.min_uses and r["ante_sum"] / r["uses"] < avg_all - 0.75:
                self.log.retire_lesson(r["id"])
                retired += 1
        return retired

    def _average_ante(self) -> float:
        runs = [r["ante_reached"] or 0 for r in self.log.finished_runs()]
        return sum(runs) / len(runs) if runs else 0.0

    def add(self, lessons: List[Dict[str, Any]], run_id: Optional[int]) -> int:
        n = 0
        existing = {r["text"].lower() for r in self.log.lessons(active_only=False)}
        for l in lessons[:3]:
            text = str(l.get("text", "")).strip()
            if 15 <= len(text) <= 300 and text.lower() not in existing:
                self.log.add_lesson(text, [str(t) for t in l.get("tags", [])][:8], run_id)
                n += 1
        return n
