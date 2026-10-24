"""Poker hand detection with Balatro's rules (Stone cards, Wild cards and the
rule-bending jokers Four Fingers, Shortcut, Smeared Joker and Splash)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import FrozenSet, List, Optional, Sequence

from .cards import SUITS, Card

# Best first.
HAND_NAMES = [
    "Flush Five", "Flush House", "Five of a Kind", "Straight Flush", "Four of a Kind",
    "Full House", "Flush", "Straight", "Three of a Kind", "Two Pair", "Pair", "High Card",
]


@dataclass(frozen=True)
class Rules:
    four_fingers: bool = False
    shortcut: bool = False
    smeared: bool = False
    splash: bool = False
    pareidolia: bool = False


@dataclass
class HandEval:
    name: str
    scoring: List[int]                       # indices into the played cards, in play order
    contains: FrozenSet[str] = field(default_factory=frozenset)


def _flush_cards(cards: Sequence[Card], need: int, rules: Rules) -> List[int]:
    best: List[int] = []
    for suit in SUITS:
        idx = [i for i, c in enumerate(cards) if c.has_suit(suit, rules.smeared)]
        if len(idx) >= need and len(idx) > len(best):
            best = idx
    return best


def _straight_cards(cards: Sequence[Card], need: int, rules: Rules) -> List[int]:
    by_value = {}
    for i, c in enumerate(cards):
        v = c.rank_value
        if v:
            by_value.setdefault(v, []).append(i)
            if v == 14:
                by_value.setdefault(1, []).append(i)   # Ace low
    values = sorted(by_value)
    max_gap = 2 if rules.shortcut else 1
    best: List[int] = []
    # Longest run of distinct values whose steps are within max_gap.
    for start in range(len(values)):
        run = [values[start]]
        for v in values[start + 1:]:
            if v - run[-1] <= max_gap:
                run.append(v)
            else:
                break
        if len(run) >= need:
            idx = sorted({i for v in run for i in by_value[v]})
            if len(idx) > len(best):
                best = idx
    return best


def evaluate_hand(cards: Sequence[Card], rules: Optional[Rules] = None) -> HandEval:
    """Classify a played hand (1-5 cards) and pick out the scoring cards."""
    rules = rules or Rules()
    need = 4 if rules.four_fingers else 5
    n = len(cards)

    ranked = [(i, c.rank) for i, c in enumerate(cards) if c.rank and not c.is_stone]
    counts = Counter(r for _, r in ranked)
    groups = sorted(counts.items(), key=lambda kv: (-kv[1], -Card(kv[0], None).rank_value))
    top = groups[0][1] if groups else 0
    second = groups[1][1] if len(groups) > 1 else 0

    flush = _flush_cards(cards, need, rules) if n >= need else []
    straight = _straight_cards(cards, need, rules) if n >= need else []

    contains = set()
    if top >= 2:
        contains.add("Pair")
    if top >= 2 and second >= 2:
        contains.add("Two Pair")
    if top >= 3:
        contains.add("Three of a Kind")
    if top >= 4:
        contains.add("Four of a Kind")
    if top >= 5:
        contains.add("Five of a Kind")
    if flush:
        contains.add("Flush")
    if straight:
        contains.add("Straight")
    full_house = top >= 3 and second >= 2
    if full_house:
        contains.add("Full House")

    def of_rank(*ranks: str) -> List[int]:
        return [i for i, r in ranked if r in ranks]

    if top >= 5 and flush:
        name, scoring = "Flush Five", sorted(set(of_rank(groups[0][0])) | set(flush))
    elif full_house and flush:
        name, scoring = "Flush House", sorted(set(of_rank(groups[0][0], groups[1][0])) | set(flush))
    elif top >= 5:
        name, scoring = "Five of a Kind", of_rank(groups[0][0])
    elif straight and flush:
        name, scoring = "Straight Flush", sorted(set(straight) | set(flush))
    elif top >= 4:
        name, scoring = "Four of a Kind", of_rank(groups[0][0])
    elif full_house:
        name, scoring = "Full House", of_rank(groups[0][0], groups[1][0])
    elif flush:
        name, scoring = "Flush", flush
    elif straight:
        name, scoring = "Straight", straight
    elif top >= 3:
        name, scoring = "Three of a Kind", of_rank(groups[0][0])
    elif top >= 2 and second >= 2:
        name, scoring = "Two Pair", of_rank(groups[0][0], groups[1][0])
    elif top >= 2:
        name, scoring = "Pair", of_rank(groups[0][0])
    else:
        name = "High Card"
        if ranked:
            best_i = max(ranked, key=lambda ir: cards[ir[0]].rank_value)[0]
            scoring = [best_i]
        else:
            scoring = []
    if name == "Straight Flush":
        contains.add("Straight Flush")
    contains.add(name)

    if rules.splash:
        scoring = list(range(n))
    else:
        # Stone cards always score.
        scoring = sorted(set(scoring) | {i for i, c in enumerate(cards) if c.is_stone})
    return HandEval(name=name, scoring=scoring, contains=frozenset(contains))
