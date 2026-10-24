from balatro_player.engine import Joker, RoundContext, evaluate_hand, parse_card, score_play
from balatro_player.engine.hands import Rules


def cards(*names, **kw):
    return [parse_card(n, **kw) for n in names]


# Hand detection ----------------------------------------------------------------

def test_pair_scores_only_pair_cards():
    ev = evaluate_hand(cards("KH", "KS", "2D", "7C", "9H"))
    assert ev.name == "Pair"
    assert ev.scoring == [0, 1]
    assert "Pair" in ev.contains and "Two Pair" not in ev.contains


def test_high_card_scores_highest():
    ev = evaluate_hand(cards("2H", "AS", "7D"))
    assert ev.name == "High Card"
    assert ev.scoring == [1]


def test_straights_with_ace_low_and_high():
    assert evaluate_hand(cards("AH", "2S", "3D", "4C", "5H")).name == "Straight"
    assert evaluate_hand(cards("TH", "JS", "QD", "KC", "AH")).name == "Straight"
    assert evaluate_hand(cards("QH", "KS", "AD", "2C", "3H")).name == "High Card"   # no wraparound


def test_flush_full_house_and_contains():
    assert evaluate_hand(cards("2H", "5H", "9H", "JH", "KH")).name == "Flush"
    fh = evaluate_hand(cards("KH", "KS", "KD", "2C", "2H"))
    assert fh.name == "Full House"
    assert {"Pair", "Two Pair", "Three of a Kind", "Full House"} <= fh.contains


def test_straight_flush_and_four_of_a_kind():
    assert evaluate_hand(cards("5S", "6S", "7S", "8S", "9S")).name == "Straight Flush"
    assert evaluate_hand(cards("9S", "9H", "9D", "9C", "2S")).name == "Four of a Kind"


def test_wild_and_stone_cards():
    hand = cards("2H", "5H", "9H", "JH") + [parse_card("KS", enhancement="WILD")]
    assert evaluate_hand(hand).name == "Flush"
    stone = [parse_card("AS", enhancement="STONE")]
    ev = evaluate_hand(cards("KH", "KS") + stone)
    assert ev.name == "Pair" and ev.scoring == [0, 1, 2]       # Stone always scores


def test_rule_jokers():
    four = cards("5H", "6S", "7D", "8C", "KH")
    assert evaluate_hand(four).name == "High Card"
    assert evaluate_hand(four, Rules(four_fingers=True)).name == "Straight"
    gap = cards("3H", "5S", "7D", "9C", "JH")
    assert evaluate_hand(gap, Rules(shortcut=True)).name == "Straight"
    red = cards("2H", "5D", "9H", "JD", "KH")
    assert evaluate_hand(red, Rules(smeared=True)).name == "Flush"
    splash = evaluate_hand(cards("KH", "KS", "2D"), Rules(splash=True))
    assert splash.scoring == [0, 1, 2]


# Scoring -----------------------------------------------------------------------

def test_plain_pair_of_kings():
    # Pair: 10 chips x 2 mult; Kings add 10 + 10 chips -> 30 x 2 = 60
    r = score_play(cards("KH", "KS"), [], [])
    assert (r.hand, r.score) == ("Pair", 60)


def test_flush_with_hand_level():
    ctx = RoundContext()
    ctx.hand_levels["Flush"] = (50, 6)                  # level 2 Flush
    r = score_play(cards("2H", "5H", "9H", "JH", "AH"), [], [], ctx)
    assert r.score == (50 + 2 + 5 + 9 + 10 + 11) * 6


def test_enhancements_editions_and_red_seal():
    played = [parse_card("AH", enhancement="GLASS", seal="RED"), parse_card("AS", edition="FOIL")]
    # Pair 10x2; AH glass: +11, x2, retriggered: +11, x2 ; AS: +11, +50 foil
    # chips = 10 + 11 + 11 + 11 + 50 = 93; mult = 2 * 2 * 2 = 8
    assert score_play(played, [], []).score == 93 * 8


def test_steel_held_and_baron_with_mime():
    played = cards("2H")
    held = [parse_card("KS", enhancement="STEEL")]
    jokers = [Joker("j_baron"), Joker("j_mime")]
    # High card 5+2=7 chips, 1 mult. Held K steel triggers twice (Mime): each x1.5 steel, x1.5 Baron
    assert score_play(played, held, jokers).score == int(7 * 1.5 ** 4)


def test_joker_order_matters():
    played = cards("KH", "KS")
    add_then_mult = [Joker("j_joker"), Joker("j_duo")]
    mult_then_add = [Joker("j_duo"), Joker("j_joker")]
    assert score_play(played, [], add_then_mult).score == 30 * (2 + 4) * 2
    assert score_play(played, [], mult_then_add).score == 30 * (2 * 2 + 4)


def test_blueprint_copies_right_neighbour():
    played = cards("KH", "KS")
    jokers = [Joker("j_blueprint"), Joker("j_duo")]
    assert score_play(played, [], jokers).score == 30 * 2 * 2 * 2


def test_live_value_jokers_read_description():
    played = cards("KH", "KS")
    j = Joker("j_campfire", effect="Gains X0.25 Mult for each card sold (Currently X1.75 Mult)")
    assert score_play(played, [], [j]).score == int(30 * 2 * 1.75)


def test_joker_editions():
    played = cards("KH", "KS")
    jokers = [Joker("j_joker", edition="POLYCHROME"), Joker("", edition="FOIL")]
    # chips 30 + 50 foil = 80; mult (2 + 4) * 1.5 = 9
    assert score_play(played, [], jokers).score == 80 * 9


def test_debuffed_cards_do_not_score():
    played = [parse_card("KH"), parse_card("KS", debuff=True)]
    r = score_play(played, [], [])
    assert r.hand == "Pair" and r.score == (10 + 10) * 2


def test_the_flint_halves_base():
    ctx = RoundContext(blind_name="The Flint")
    assert score_play(cards("KH", "KS"), [], [], ctx).score == (5 + 20) * 1
