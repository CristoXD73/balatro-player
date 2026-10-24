"""Hand tactics: which cards to play or discard.

Plays are found by trying every subset of up to five cards and scoring each
one exactly. Discards are chosen by Monte Carlo: for a few "keep" plans (keep
the best hand, chase a flush, chase a straight, keep pairs), draw replacement
cards from the real remaining deck many times and measure how good the next
hand gets.
"""

from __future__ import annotations

import itertools
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .engine.cards import Card, Joker
from .engine.hands import evaluate_hand
from .engine.score import RoundContext, ScoreResult, rules_from_jokers, score_play


@dataclass
class Play:
    cards: Tuple[int, ...]
    result: ScoreResult


@dataclass
class Decision:
    action: str                      # "play" or "discard"
    cards: List[int]
    expected: float                  # predicted score (play) or expected next-hand score (discard)
    reason: str = ""
    alternatives: List[Dict] = field(default_factory=list)
    hand: str = ""                   # poker hand of a play


@dataclass
class Constraints:
    """Boss blind restrictions on what may be played."""
    exact_size: Optional[int] = None          # The Psychic: must play 5 cards
    banned_hands: Set[str] = field(default_factory=set)    # The Eye
    only_hand: Optional[str] = None           # The Mouth

    @classmethod
    def from_context(cls, ctx: RoundContext) -> "Constraints":
        c = cls()
        if ctx.blind_name == "The Psychic":
            c.exact_size = 5
        elif ctx.blind_name == "The Eye":
            c.banned_hands = {h for h, n in ctx.played_this_round.items() if n > 0}
        elif ctx.blind_name == "The Mouth":
            played = [h for h, n in ctx.played_this_round.items() if n > 0]
            c.only_hand = played[0] if played else None
        return c

    def allows(self, size: int, hand: str) -> bool:
        if self.exact_size and size != self.exact_size:
            return False
        if hand in self.banned_hands:
            return False
        if self.only_hand and hand != self.only_hand:
            return False
        return True


def best_plays(hand: Sequence[Card], jokers: Sequence[Joker], ctx: RoundContext,
               top: int = 5, constraints: Optional[Constraints] = None) -> List[Play]:
    constraints = constraints or Constraints.from_context(ctx)
    rules = rules_from_jokers(jokers)
    n = len(hand)
    plays: List[Play] = []
    sizes = [constraints.exact_size] if constraints.exact_size else range(1, min(5, n) + 1)
    for size in sizes:
        if size > n:
            continue
        for combo in itertools.combinations(range(n), size):
            played = [hand[i] for i in combo]
            ev = evaluate_hand(played, rules)
            if not constraints.allows(size, ev.name):
                continue
            held = [hand[i] for i in range(n) if i not in combo]
            plays.append(Play(combo, score_play(played, held, jokers, ctx, ev)))
    plays.sort(key=lambda p: (-p.result.score, len(p.cards)))
    return plays[:top]


def _keep_plans(hand: Sequence[Card], top_plays: List[Play]) -> List[Tuple[str, Set[int]]]:
    plans: List[Tuple[str, Set[int]]] = []
    for p in top_plays[:3]:
        plans.append((f"keep {p.result.hand}", set(p.cards)))
    suits = Counter(c.suit for c in hand if c.suit and not c.is_stone)
    for suit, cnt in suits.most_common(2):
        if cnt >= 3:
            plans.append((f"chase {suit} flush", {i for i, c in enumerate(hand) if c.has_suit(suit)}))
    values = sorted({c.rank_value for c in hand if c.rank_value})
    best_window: Set[int] = set()
    for lo in range(1, 11):
        window = set(range(lo, lo + 5))
        idx = {i for i, c in enumerate(hand)
               if c.rank_value in window or (c.rank_value == 14 and 1 in window)}
        uniq = {hand[i].rank_value for i in idx}
        if len(uniq) >= 3 and len(uniq) > len({hand[i].rank_value for i in best_window}):
            # one card per rank
            seen: Dict[int, int] = {}
            for i in sorted(idx):
                seen.setdefault(hand[i].rank_value, i)
            best_window = set(seen.values())
    if best_window:
        plans.append(("chase straight", best_window))
    ranks = Counter(c.rank for c in hand if c.rank and not c.is_stone)
    groups = {i for i, c in enumerate(hand) if c.rank and ranks[c.rank] >= 2}
    if groups:
        plans.append(("keep pairs", groups))
    # Deduplicate.
    out, seen_sets = [], set()
    for name, keep in plans:
        key = frozenset(keep)
        if key not in seen_sets and len(keep) < len(hand):
            seen_sets.add(key)
            out.append((name, keep))
    return out


def _discard_for(hand: Sequence[Card], keep: Set[int], limit: int = 5) -> List[int]:
    others = [i for i in range(len(hand)) if i not in keep]
    # Throw away the weakest cards first; never more than the limit.
    others.sort(key=lambda i: (hand[i].enhancement is not None, hand[i].edition is not None, hand[i].chips))
    return sorted(others[:limit])


def evaluate_discard(hand: Sequence[Card], discard: List[int], deck: Sequence[Card],
                     jokers: Sequence[Joker], ctx: RoundContext, need: float,
                     samples: int = 24, rng: Optional[random.Random] = None) -> Tuple[float, float]:
    """(expected best next-hand score, probability that hand reaches ``need``)."""
    rng = rng or random.Random(0)
    kept = [c for i, c in enumerate(hand) if i not in discard]
    if not deck:
        return 0.0, 0.0
    after = RoundContext(**{**ctx.__dict__, "discards_left": max(0, ctx.discards_left - 1)})
    constraints = Constraints.from_context(after)
    total, hits = 0.0, 0
    draw = min(len(discard), len(deck))
    for _ in range(samples):
        new_hand = kept + rng.sample(list(deck), draw)
        best = best_plays(new_hand, jokers, after, top=1, constraints=constraints)
        s = best[0].result.score if best else 0
        total += s
        hits += s >= need
    return total / samples, hits / samples


def decide(hand: Sequence[Card], deck: Sequence[Card], jokers: Sequence[Joker], ctx: RoundContext,
           target: int, chips_so_far: int, samples: int = 24, rng: Optional[random.Random] = None) -> Decision:
    constraints = Constraints.from_context(ctx)
    plays = best_plays(hand, jokers, ctx, top=6, constraints=constraints)
    if not plays:   # e.g. The Psychic with fewer than 5 cards: play everything
        return Decision("play", list(range(min(5, len(hand)))), 0, "no legal play found")
    best = plays[0]
    need = max(0, target - chips_so_far)
    alts = [{"cards": list(p.cards), "hand": p.result.hand, "score": p.result.score} for p in plays[:3]]

    if best.result.score >= need:
        # Several plays may win; prefer the one that keeps strong cards (fewest cards).
        winners = [p for p in plays if p.result.score >= need]
        pick = min(winners, key=lambda p: (len(p.cards), -p.result.score))
        return Decision("play", list(pick.cards), pick.result.score,
                        f"{pick.result.hand} clears the blind ({pick.result.score} >= {need})", alts,
                        pick.result.hand)

    if ctx.discards_left <= 0 or not deck:
        return Decision("play", list(best.cards), best.result.score, "no discards left", alts, best.result.hand)

    per_hand = need / max(1, ctx.hands_left)
    best_plan = None
    for name, keep in _keep_plans(hand, plays):
        discard = _discard_for(hand, keep)
        if not discard:
            continue
        ev, p_hit = evaluate_discard(hand, discard, deck, jokers, ctx, need if ctx.hands_left == 1 else per_hand,
                                     samples=samples, rng=rng)
        if best_plan is None or ev > best_plan[2]:
            best_plan = (name, discard, ev, p_hit)

    if best_plan:
        name, discard, ev, p_hit = best_plan
        last_hand = ctx.hands_left == 1
        if (last_hand and p_hit > 0) or ev > best.result.score * 1.1:
            return Decision("discard", discard, ev,
                            f"{name}: expected next hand {ev:.0f} vs {best.result.score} now (hit {p_hit:.0%})", alts)
    return Decision("play", list(best.cards), best.result.score,
                    f"{best.result.hand} for {best.result.score} (need {need} over {ctx.hands_left} hands)", alts,
                    best.result.hand)


def estimate_power(full_deck: Sequence[Card], jokers: Sequence[Joker], ctx: RoundContext,
                   hand_size: int = 8, samples: int = 20, rng: Optional[random.Random] = None,
                   draws: Optional[List[List[int]]] = None) -> float:
    """Average best single-hand score from a fresh draw: how strong the build is.

    Pass the same ``draws`` (index lists into ``full_deck``) to compare builds
    on identical hands (common random numbers make comparisons much less noisy).
    """
    rng = rng or random.Random(0)
    if not full_deck:
        return 0.0
    if draws is None:
        k = min(hand_size, len(full_deck))
        draws = [rng.sample(range(len(full_deck)), k) for _ in range(samples)]
    total = 0.0
    for idx in draws:
        hand = [full_deck[i] for i in idx]
        best = best_plays(hand, jokers, ctx, top=1)
        total += best[0].result.score if best else 0
    return total / len(draws)


def make_draws(deck_size: int, hand_size: int, samples: int, seed: int = 0) -> List[List[int]]:
    rng = random.Random(seed)
    k = min(hand_size, deck_size)
    return [rng.sample(range(deck_size), k) for _ in range(samples)]
