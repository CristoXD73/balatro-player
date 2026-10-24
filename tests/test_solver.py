import random
import time

from balatro_player.engine import Joker, RoundContext, parse_card
from balatro_player.solver import best_plays, decide, estimate_power


def cards(*names):
    return [parse_card(n) for n in names]


def full_deck():
    return [parse_card(r + s) for r in "23456789TJQKA" for s in "HDCS"]


def test_best_play_finds_flush_in_hand():
    hand = cards("2H", "5H", "9H", "JH", "KH", "KS", "3D", "7C")
    best = best_plays(hand, [], RoundContext(), top=1)[0]
    assert best.result.hand == "Flush"
    assert set(best.cards) == {0, 1, 2, 3, 4}


def test_play_when_it_clears_the_blind():
    hand = cards("AH", "AS", "AD", "KC", "2H", "3S", "7D", "9C")
    d = decide(hand, full_deck(), [], RoundContext(), target=300, chips_so_far=200)
    assert d.action == "play"


def test_discard_when_hand_is_weak_and_far_from_target():
    hand = cards("2H", "5H", "9H", "JH", "3S", "7D", "8C", "4S")   # four hearts
    deck = [c for c in full_deck() if c not in hand]
    ctx = RoundContext(hands_left=4, discards_left=3)
    d = decide(hand, deck, [], ctx, target=1200, chips_so_far=0, rng=random.Random(1))
    assert d.action == "discard"
    assert not {0, 1, 2, 3} & set(d.cards)        # keeps the hearts


def test_psychic_forces_five_cards():
    hand = cards("AH", "AS", "2D", "3C", "9H", "TS", "JD", "QC")
    ctx = RoundContext(blind_name="The Psychic")
    assert all(len(p.cards) == 5 for p in best_plays(hand, [], ctx, top=10))


def test_power_estimate_grows_with_jokers():
    deck = full_deck()
    base = estimate_power(deck, [], RoundContext(), samples=10)
    boosted = estimate_power(deck, [Joker("j_joker"), Joker("j_duo")], RoundContext(), samples=10)
    assert boosted > base * 1.5


def test_solver_is_fast_enough():
    hand = cards("2H", "5H", "9H", "JH", "3S", "7D", "8C", "4S")
    deck = [c for c in full_deck() if c not in hand]
    t = time.time()
    decide(hand, deck, [Joker("j_joker")], RoundContext(), target=2000, chips_so_far=0)
    assert time.time() - t < 10
