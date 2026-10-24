"""The Balatro scoring pipeline.

Order of operations (as in the game):
  1. Poker hand base chips/mult (from the run's hand levels; The Flint halves them).
  2. Each scoring card, left to right, once per trigger: card chips, enhancement,
     edition, then every joker's "when scored" effect.
  3. Each card held in hand, once per trigger: Steel, then jokers' "held" effects.
  4. Each joker, left to right: edition (Foil/Holo), main effect, Polychrome.
  score = floor(chips * mult)

Random effects (Lucky cards, Misprint, Bloodstone, ...) use their expected
value by default, which is what you want for planning. Pass ``expected=False``
to ignore them instead.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .cards import Card, Joker
from .hands import HandEval, Rules, evaluate_hand
from . import jokers as J

# Level-1 values, used when the game hasn't told us the current levels.
BASE_HANDS: Dict[str, Tuple[int, int]] = {
    "High Card": (5, 1), "Pair": (10, 2), "Two Pair": (20, 2), "Three of a Kind": (30, 3),
    "Straight": (30, 4), "Flush": (35, 4), "Full House": (40, 4), "Four of a Kind": (60, 7),
    "Straight Flush": (100, 8), "Five of a Kind": (120, 12), "Flush House": (140, 14),
    "Flush Five": (160, 16),
}


@dataclass
class RoundContext:
    hand_levels: Dict[str, Tuple[int, int]] = field(default_factory=lambda: dict(BASE_HANDS))
    hands_played_run: Dict[str, int] = field(default_factory=dict)   # before this hand
    played_this_round: Dict[str, int] = field(default_factory=dict)  # before this hand
    hands_left: int = 4             # including the hand being played
    discards_left: int = 3
    money: int = 0
    deck_remaining: int = 44
    joker_slots: int = 5
    blind_name: str = ""
    expected: bool = True


@dataclass
class ScoreResult:
    hand: str
    chips: float
    mult: float
    score: int
    scoring: List[int]


class Tally:
    __slots__ = ("chips", "mult")

    def __init__(self, chips: float, mult: float):
        self.chips = chips
        self.mult = mult


def rules_from_jokers(jokers: Sequence[Joker]) -> Rules:
    keys = {j.key for j in jokers if not j.debuff}
    return Rules(
        four_fingers="j_four_fingers" in keys,
        shortcut="j_shortcut" in keys,
        smeared="j_smeared" in keys,
        splash="j_splash" in keys,
        pareidolia="j_pareidolia" in keys,
    )


def effective_jokers(jokers: Sequence[Joker]) -> List[Tuple[Joker, Joker]]:
    """(logic joker, slot joker) pairs, resolving Blueprint and Brainstorm copies."""
    def resolve(i: int, seen: frozenset) -> Optional[Joker]:
        if i < 0 or i >= len(jokers) or i in seen:
            return None
        j = jokers[i]
        if j.debuff:
            return None
        if j.key == "j_blueprint":
            return resolve(i + 1, seen | {i})
        if j.key == "j_brainstorm":
            return resolve(0, seen | {i})
        return j

    out = []
    for i, slot in enumerate(jokers):
        if slot.debuff:
            continue
        logic = resolve(i, frozenset())
        if logic is not None:
            out.append((logic, slot))
        elif slot.edition:
            out.append((Joker(key="", edition=slot.edition), slot))   # edition still applies
    return out


def score_play(played: Sequence[Card], held: Sequence[Card], jokers: Sequence[Joker],
               ctx: Optional[RoundContext] = None, hand_eval: Optional[HandEval] = None) -> ScoreResult:
    ctx = ctx or RoundContext()
    rules = rules_from_jokers(jokers)
    ev = hand_eval or evaluate_hand(played, rules)
    chips, mult = ctx.hand_levels.get(ev.name, BASE_HANDS[ev.name])
    if ctx.blind_name == "The Flint":
        chips, mult = max(math.floor(chips * 0.5 + 0.5), 0), max(math.floor(mult * 0.5 + 0.5), 1)
    t = Tally(chips, mult)
    eff = effective_jokers(jokers)
    prob = 2 ** sum(1 for j in jokers if j.key == "j_oops" and not j.debuff)
    env = J.Env(played=played, held=held, jokers=jokers, eff=eff, ctx=ctx, ev=ev, rules=rules, prob=prob)

    # 2. Scoring cards.
    first_scoring = ev.scoring[0] if ev.scoring else -1
    for i in ev.scoring:
        card = played[i]
        if card.debuff:
            continue
        triggers = 1 + J.retriggers_played(card, i == first_scoring, env)
        for _ in range(triggers):
            t.chips += card.chips
            if card.enhancement == "MULT":
                t.mult += 4
            elif card.enhancement == "GLASS":
                t.mult *= 2
            elif card.enhancement == "LUCKY" and ctx.expected:
                t.mult += 20 * min(1.0, prob / 5)
            if card.edition == "FOIL":
                t.chips += 50
            elif card.edition == "HOLO":
                t.mult += 10
            elif card.edition == "POLYCHROME":
                t.mult *= 1.5
            for logic, _slot in eff:
                J.on_scored(logic, card, t, env)

    # 3. Cards held in hand.
    for card in held:
        if card.debuff:
            continue
        triggers = 1 + J.retriggers_held(card, env)
        for _ in range(triggers):
            if card.enhancement == "STEEL":
                t.mult *= 1.5
            for logic, _slot in eff:
                J.on_held(logic, card, t, env)

    # 4. Jokers.
    for logic, slot in eff:
        if slot.edition == "FOIL":
            t.chips += 50
        elif slot.edition == "HOLO":
            t.mult += 10
        J.on_main(logic, t, env)
        if slot.edition == "POLYCHROME":
            t.mult *= 1.5

    return ScoreResult(hand=ev.name, chips=t.chips, mult=t.mult,
                       score=int(math.floor(t.chips * t.mult)), scoring=list(ev.scoring))
