"""Joker effects for the scoring pipeline.

Each joker can hook three moments: when a played card scores (``on_scored``),
when a held card triggers (``on_held``), and its own turn in the joker row
(``on_main``). Scaling jokers (Ride the Bus, Hologram, Campfire, ...) read
their current value from the live description the game sends ("Currently X2.5
Mult"), so the engine stays right as they grow.

Jokers that only affect money, shop or deck changes don't touch scoring and are
handled by the strategy layer instead. Any joker not listed here still works
if its description has a "Currently ..." value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Sequence, Tuple

from .cards import RANK_CHIPS, Card, Joker

SUIT_NAMES = {"Hearts": "H", "Diamonds": "D", "Clubs": "C", "Spades": "S"}
RANK_NAMES = {"Ace": "A", "King": "K", "Queen": "Q", "Jack": "J", "10": "T",
              **{str(n): str(n) for n in range(2, 10)}}


@dataclass
class Env:
    played: Sequence[Card]
    held: Sequence[Card]
    jokers: Sequence[Joker]
    eff: List[Tuple[Joker, Joker]]
    ctx: Any            # RoundContext
    ev: Any             # HandEval
    rules: Any          # Rules
    prob: float         # probability multiplier (Oops! All 6s)
    photo_used: bool = False


def _p(env: Env, num: float, den: float) -> float:
    return min(1.0, num * env.prob / den) if env.ctx.expected else 0.0


def _suit_in(text: str):
    for name, s in SUIT_NAMES.items():
        if name in text:
            return s
    return None


def _rank_in(text: str):
    m = re.search(r"\b(Ace|King|Queen|Jack|10|[2-9])\b", text)
    return RANK_NAMES[m.group(1)] if m else None


# Hand-type jokers: key -> (hand it needs, +chips, +mult, xmult)
CONTAINS = {
    "j_jolly": ("Pair", 0, 8, 1), "j_zany": ("Three of a Kind", 0, 12, 1),
    "j_mad": ("Two Pair", 0, 10, 1), "j_crazy": ("Straight", 0, 12, 1),
    "j_droll": ("Flush", 0, 10, 1),
    "j_sly": ("Pair", 50, 0, 1), "j_wily": ("Three of a Kind", 100, 0, 1),
    "j_clever": ("Two Pair", 80, 0, 1), "j_devious": ("Straight", 100, 0, 1),
    "j_crafty": ("Flush", 80, 0, 1),
    "j_duo": ("Pair", 0, 0, 2), "j_trio": ("Three of a Kind", 0, 0, 3),
    "j_family": ("Four of a Kind", 0, 0, 4), "j_order": ("Straight", 0, 0, 3),
    "j_tribe": ("Flush", 0, 0, 2),
}

# Suit jokers: key -> (suit, +chips, +mult)
SUIT_SCORED = {
    "j_greedy_joker": ("D", 0, 3), "j_lusty_joker": ("H", 0, 3),
    "j_wrathful_joker": ("S", 0, 3), "j_gluttenous_joker": ("C", 0, 3),
    "j_arrowhead": ("S", 50, 0), "j_onyx_agate": ("C", 0, 7),
}

# Jokers whose whole effect is the "Currently ..." value in their description.
LIVE_VALUE = {
    "j_ride_the_bus", "j_ice_cream", "j_green_joker", "j_red_card", "j_madness", "j_square",
    "j_vampire", "j_hologram", "j_obelisk", "j_lucky_cat", "j_popcorn", "j_trousers",
    "j_ramen", "j_castle", "j_campfire", "j_swashbuckler", "j_throwback", "j_hit_the_road",
    "j_glass", "j_fortune_teller", "j_flash", "j_erosion", "j_steel_joker", "j_stone",
    "j_constellation", "j_caino", "j_yorick", "j_ceremonial", "j_wee", "j_runner",
    "j_stencil", "j_abstract", "j_bull", "j_bootstraps", "j_blue_joker", "j_supernova",
}


def retriggers_played(card: Card, first: bool, env: Env) -> int:
    n = 1 if card.seal == "RED" else 0
    for logic, _ in env.eff:
        k = logic.key
        if k == "j_hack" and card.rank in ("2", "3", "4", "5") and not card.is_stone:
            n += 1
        elif k == "j_sock_and_buskin" and card.is_face(env.rules.pareidolia):
            n += 1
        elif k == "j_hanging_chad" and first:
            n += 2
        elif k == "j_dusk" and env.ctx.hands_left == 1:
            n += 1
        elif k == "j_selzer":
            n += 1
    return n


def retriggers_held(card: Card, env: Env) -> int:
    n = 1 if card.seal == "RED" else 0
    return n + sum(1 for logic, _ in env.eff if logic.key == "j_mime")


def on_scored(j: Joker, card: Card, t, env: Env) -> None:
    k = j.key
    sm = env.rules.smeared
    face = card.is_face(env.rules.pareidolia)
    if k in SUIT_SCORED:
        suit, c, m = SUIT_SCORED[k]
        if card.has_suit(suit, sm):
            t.chips += c
            t.mult += m
    elif k == "j_fibonacci" and card.rank in ("A", "2", "3", "5", "8") and not card.is_stone:
        t.mult += 8
    elif k == "j_scary_face" and face:
        t.chips += 30
    elif k == "j_smiley" and face:
        t.mult += 5
    elif k == "j_even_steven" and card.rank in ("2", "4", "6", "8", "T") and not card.is_stone:
        t.mult += 4
    elif k == "j_odd_todd" and card.rank in ("A", "3", "5", "7", "9") and not card.is_stone:
        t.chips += 31
    elif k == "j_scholar" and card.rank == "A" and not card.is_stone:
        t.chips += 20
        t.mult += 4
    elif k == "j_walkie_talkie" and card.rank in ("T", "4") and not card.is_stone:
        t.chips += 10
        t.mult += 4
    elif k == "j_bloodstone" and card.has_suit("H", sm):
        t.mult *= 1 + 0.5 * _p(env, 1, 2)
    elif k == "j_photograph" and face and not env.photo_used:
        env.photo_used = True
        t.mult *= 2
    elif k == "j_triboulet" and card.rank in ("K", "Q") and not card.is_stone:
        t.mult *= 2
    elif k == "j_ancient":
        suit = _suit_in(j.effect)
        if suit and card.has_suit(suit, sm):
            t.mult *= 1.5
    elif k == "j_idol":
        suit, rank = _suit_in(j.effect), _rank_in(j.effect)
        if suit and rank and card.rank == rank and card.has_suit(suit, sm):
            t.mult *= 2


def on_held(j: Joker, card: Card, t, env: Env) -> None:
    k = j.key
    if card.is_stone:
        return
    if k == "j_baron" and card.rank == "K":
        t.mult *= 1.5
    elif k == "j_shoot_the_moon" and card.rank == "Q":
        t.mult += 13
    elif k == "j_raised_fist":
        ranked = [c for c in env.held if c.rank and not c.is_stone and not c.debuff]
        if ranked:
            lowest = min(ranked, key=lambda c: c.rank_value)
            if card is lowest:
                t.mult += 2 * RANK_CHIPS[card.rank]


def _live(j: Joker, t) -> bool:
    """Apply a joker's 'Currently ...' value. Returns False if it has none."""
    x, m, c = j.current_xmult, j.current_mult, j.current_chips
    if c is not None:
        t.chips += c
    if m is not None:
        t.mult += m
    if x is not None:
        t.mult *= x
    return x is not None or m is not None or c is not None


def on_main(j: Joker, t, env: Env) -> None:
    k = j.key
    ctx, ev = env.ctx, env.ev
    played = env.played
    if k in CONTAINS:
        hand, c, m, x = CONTAINS[k]
        if hand in ev.contains:
            t.chips += c
            t.mult += m
            t.mult *= x
        return
    if k == "j_joker":
        t.mult += 4
    elif k == "j_half":
        if len(played) <= 3:
            t.mult += 20
    elif k == "j_banner":
        t.chips += 30 * ctx.discards_left
    elif k == "j_mystic_summit":
        if ctx.discards_left == 0:
            t.mult += 15
    elif k == "j_misprint":
        if ctx.expected:
            t.mult += 11.5
    elif k == "j_gros_michel":
        t.mult += 15
    elif k == "j_cavendish":
        t.mult *= 3
    elif k == "j_card_sharp":
        if ctx.played_this_round.get(ev.name, 0) > 0:
            t.mult *= 3
    elif k == "j_acrobat":
        if ctx.hands_left == 1:
            t.mult *= 3
    elif k == "j_blackboard":
        held = [c for c in env.held]
        if all(c.has_suit("S", env.rules.smeared) or c.has_suit("C", env.rules.smeared) for c in held):
            t.mult *= 3
    elif k == "j_flower_pot":
        scoring = [played[i] for i in ev.scoring]
        if _all_suits(scoring, env.rules.smeared):
            t.mult *= 3
    elif k == "j_seeing_double":
        scoring = [played[i] for i in ev.scoring if not played[i].debuff]
        has_club = any(c.has_suit("C", env.rules.smeared) for c in scoring)
        other = any(c.suit not in (None, "C") and not c.is_stone for c in scoring)
        if has_club and other:
            t.mult *= 2
    elif k == "j_loyalty_card":
        m = re.search(r"(\d+)\s*remaining", j.effect)
        if m and int(m.group(1)) == 0 or "Active" in j.effect:
            t.mult *= 4
    elif k == "j_drivers_license":
        m = re.search(r"Currently\s*(\d+)", j.effect)
        if m and int(m.group(1)) >= 16:
            t.mult *= 3
    elif k == "j_green_joker":
        if not _live(j, t):
            return
        t.mult += 1                                  # gains +1 before scoring this hand
    elif k == "j_ride_the_bus":
        base = j.current_mult or 0
        no_face = not any(played[i].is_face(env.rules.pareidolia) for i in ev.scoring)
        t.mult += base + 1 if no_face else 0
    elif k == "j_runner":
        t.chips += (j.current_chips or 0) + (15 if "Straight" in ev.contains else 0)
    elif k == "j_square":
        t.chips += (j.current_chips or 0) + (4 if len(played) == 4 else 0)
    elif k == "j_trousers":
        t.mult += (j.current_mult or 0) + (2 if "Two Pair" in ev.contains else 0)
    elif k == "j_supernova":
        t.mult += ctx.hands_played_run.get(ev.name, 0) + 1
    elif k == "j_abstract":
        t.mult += 3 * len(env.jokers)
    elif k == "j_blue_joker":
        t.chips += 2 * ctx.deck_remaining
    elif k == "j_bull":
        t.chips += 2 * max(0, ctx.money)
    elif k == "j_bootstraps":
        t.mult += 2 * (max(0, ctx.money) // 5)
    elif k == "j_stencil":
        if not _live(j, t):
            empty = ctx.joker_slots - len(env.jokers)
            stencils = sum(1 for x in env.jokers if x.key == "j_stencil")
            t.mult *= max(1, empty + stencils)
    else:
        _live(j, t)


def _all_suits(cards: Sequence[Card], smeared: bool) -> bool:
    """Can the scoring cards cover all four suits (Wild cards fill any gap)?"""
    natural = {c.suit for c in cards if c.suit and not c.is_stone and c.enhancement != "WILD" and not c.debuff}
    wilds = sum(1 for c in cards if c.enhancement == "WILD" and not c.debuff)
    return len(natural) + wilds >= 4
