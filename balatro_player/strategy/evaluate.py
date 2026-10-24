"""Measure shop options with the scoring engine.

"Power" is the average best single-hand score from a fresh draw of the current
deck. Comparing power with and without a joker, on the very same sampled
hands, tells you how much that joker is really worth to *this* build.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from ..engine.cards import Joker, joker_from_api
from ..engine.score import RoundContext
from ..solver import estimate_power, make_draws
from ..state import ANTE_BASE, GameView
from . import catalog


def money_weight(ante: int) -> float:
    """How much $1 per round is worth, in power-ratio units, at this ante."""
    return 0.10 if ante <= 2 else 0.06 if ante <= 4 else 0.03 if ante <= 6 else 0.01


class Evaluator:
    def __init__(self, view: GameView, samples: int = 16, seed: int = 0):
        self.view = view
        self.samples = samples
        self.seed = seed
        self.deck = view.deck + view.hand
        self.hand_size = view.hand_size or 8
        self.ctx = RoundContext(
            hand_levels=view.hand_levels,
            hands_played_run=view.hand_stat("played"),
            hands_left=4, discards_left=3,
            money=view.money,
            deck_remaining=max(0, len(self.deck) - self.hand_size),
            joker_slots=view.joker_slots,
        )
        self._draws: Dict[int, List[List[int]]] = {}
        self._power_now: Optional[float] = None

    def _draws_for(self, hand_size: int) -> List[List[int]]:
        if hand_size not in self._draws:
            self._draws[hand_size] = make_draws(len(self.deck), hand_size, self.samples, self.seed)
        return self._draws[hand_size]

    def power(self, jokers: List[Joker], hand_size: Optional[int] = None,
              levels: Optional[Dict[str, tuple]] = None) -> float:
        size = hand_size or self.hand_size
        ctx = self.ctx
        if levels is not None:
            ctx = RoundContext(**{**self.ctx.__dict__, "hand_levels": levels})
        return estimate_power(self.deck, jokers, ctx, draws=self._draws_for(size))

    @property
    def power_now(self) -> float:
        if self._power_now is None:
            self._power_now = self.power(self.view.jokers)
        return self._power_now

    def required_per_hand(self) -> float:
        """Score per hand needed for the upcoming boss blind (assuming ~3 good hands)."""
        boss = (self.view.blinds.get("boss") or {}).get("score")
        if not boss:
            boss = ANTE_BASE.get(self.view.ante, 50000 * 2 ** (self.view.ante - 8)) * 2
        return float(boss) / 3

    def ratio(self, power: Optional[float] = None) -> float:
        p = self.power_now if power is None else power
        return p / max(1.0, self.required_per_hand())

    def joker_value(self, card: Dict[str, Any], replacing: Optional[int] = None) -> Dict[str, Any]:
        """Value of adding a joker (optionally replacing the joker at ``replacing``)."""
        new = joker_from_api(card)
        jokers = list(self.view.jokers)
        if replacing is not None:
            jokers.pop(replacing)
        jokers.append(new)
        size = self.hand_size + catalog.HAND_SIZE_DELTA.get(new.key, 0)
        after = self.power(jokers, hand_size=size)
        gain = math.log2(max(after, 1.0) / max(self.power_now, 1.0))
        econ = catalog.ECONOMY_JOKERS.get(new.key, 0) * money_weight(self.view.ante)
        bonus = catalog.UTILITY_JOKERS.get(new.key, 0) + catalog.SCALING_JOKERS.get(new.key, 0)
        if new.edition == "NEGATIVE":
            bonus += 0.5
        return {"key": new.key, "power_after": after, "gain": gain, "econ": econ, "bonus": bonus,
                "total": gain + econ + bonus}

    def removal_losses(self) -> List[float]:
        """Value lost by selling each current joker (lower = better to sell)."""
        jokers = self.view.jokers
        out = []
        for i, j in enumerate(jokers):
            rest = jokers[:i] + jokers[i + 1:]
            size = self.hand_size - catalog.HAND_SIZE_DELTA.get(j.key, 0)
            p = self.power(rest, hand_size=size)
            loss = math.log2(max(self.power_now, 1.0) / max(p, 1.0))
            loss += catalog.ECONOMY_JOKERS.get(j.key, 0) * money_weight(self.view.ante)
            loss += catalog.UTILITY_JOKERS.get(j.key, 0) + catalog.SCALING_JOKERS.get(j.key, 0)
            if j.eternal:
                loss = float("inf")
            out.append(loss)
        return out

    def planet_value(self, key: str) -> float:
        hand = catalog.PLANET_HAND.get(key)
        if not hand:
            return 0.0
        levels = dict(self.ctx.hand_levels)
        chips, mult = levels[hand]
        inc = {"High Card": (10, 1), "Pair": (15, 1), "Two Pair": (20, 1), "Three of a Kind": (20, 2),
               "Straight": (30, 3), "Flush": (15, 2), "Full House": (25, 2), "Four of a Kind": (30, 3),
               "Straight Flush": (40, 4), "Five of a Kind": (35, 3), "Flush House": (40, 4),
               "Flush Five": (50, 3)}[hand]
        levels[hand] = (chips + inc[0], mult + inc[1])
        after = self.power(self.view.jokers, levels=levels)
        return math.log2(max(after, 1.0) / max(self.power_now, 1.0))

    def best_joker_order(self, jokers: List[Joker]) -> List[int]:
        """Order jokers so +chips/+mult come before xMult, and copiers sit left of the best joker."""
        def kind(j: Joker) -> int:
            if j.key in ("j_blueprint", "j_brainstorm"):
                return 3
            x = j.current_xmult
            if x or j.key in ("j_duo", "j_trio", "j_family", "j_order", "j_tribe", "j_cavendish",
                               "j_card_sharp", "j_acrobat", "j_blackboard", "j_flower_pot",
                               "j_seeing_double", "j_loyalty_card", "j_drivers_license") \
                    or j.edition == "POLYCHROME":
                return 2
            return 1
        order = sorted(range(len(jokers)), key=lambda i: (kind(jokers[i]) == 3, kind(jokers[i])))
        copiers = [i for i in order if jokers[i].key == "j_blueprint"]
        rest = [i for i in order if jokers[i].key != "j_blueprint"]
        if copiers and rest:
            # Blueprint copies its right neighbour: put it just before the strongest joker.
            best = max(rest, key=lambda i: self.power([jokers[i]]))
            pos = rest.index(best)
            rest[pos:pos] = copiers
            return rest
        return order
