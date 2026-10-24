"""A tiny stand-in for BalatroBot: same methods and state shapes, simplified rules.

Good enough to drive the agent through full runs in tests (blinds, hands,
shop, packs, planets, game over, winning) without the real game.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

from balatro_player.client import BalatroError
from balatro_player.engine.cards import parse_card
from balatro_player.engine.score import BASE_HANDS, RoundContext, score_play
from balatro_player.engine.cards import Joker
from balatro_player.state import ANTE_BASE

JOKER_POOL = [
    ("j_joker", "+4 Mult", 2), ("j_jolly", "+8 Mult if played hand contains a Pair", 3),
    ("j_droll", "+10 Mult if played hand contains a Flush", 4), ("j_duo", "X2 Mult if hand contains a Pair", 7),
    ("j_tribe", "X2 Mult if hand contains a Flush", 8), ("j_golden", "Earn $4 at end of round", 6),
    ("j_sly", "+50 Chips if played hand contains a Pair", 3), ("j_half", "+20 Mult if 3 or fewer cards", 5),
]
PLANETS = [("c_mercury", "Pair", (15, 1)), ("c_jupiter", "Flush", (15, 2)), ("c_pluto", "High Card", (10, 1))]


def card_dict(rank: str, suit: str, i: int) -> Dict[str, Any]:
    return {"id": i, "key": f"{suit}_{rank}", "set": "DEFAULT", "label": f"{rank}{suit}",
            "value": {"suit": suit, "rank": rank, "effect": ""},
            "modifier": {"seal": None, "edition": None, "enhancement": None, "eternal": False,
                         "perishable": None, "rental": False},
            "state": {"debuff": False, "hidden": False, "highlight": False}, "cost": {"sell": 1, "buy": 0}}


def joker_dict(key: str, effect: str, cost: int, i: int) -> Dict[str, Any]:
    return {"id": i, "key": key, "set": "JOKER", "label": key, "value": {"effect": effect},
            "modifier": {"edition": None, "eternal": False, "rental": False}, "state": {},
            "cost": {"sell": max(1, cost // 2), "buy": cost}}


class FakeClient:
    def __init__(self, seed: int = 0, strength: float = 1.0):
        self.rng = random.Random(seed)
        self.strength = strength          # scales blind targets (lower = easier)
        self.reset()

    def reset(self) -> None:
        self.state = "MENU"
        self.ante, self.blind_idx, self.round_num, self.money = 1, 0, 0, 4
        self.jokers: List[Dict[str, Any]] = []
        self.consumables: List[Dict[str, Any]] = []
        self.levels = {k: list(v) for k, v in BASE_HANDS.items()}
        self.played = {k: 0 for k in BASE_HANDS}
        self.played_round = {k: 0 for k in BASE_HANDS}
        self.won = False
        self.shop: List[Dict[str, Any]] = []
        self.packs: List[Dict[str, Any]] = []
        self.pack_cards: List[Dict[str, Any]] = []
        self.hand: List[Dict[str, Any]] = []
        self.deck: List[Dict[str, Any]] = []
        self.chips = self.hands_left = self.discards_left = 0
        self.calls = 0

    # Helpers -----------------------------------------------------------------------

    def _target(self, idx: Optional[int] = None) -> int:
        idx = self.blind_idx if idx is None else idx
        base = ANTE_BASE.get(self.ante, 50000)
        return int(base * (1, 1.5, 2)[idx] * self.strength)

    def _blinds(self) -> Dict[str, Any]:
        out = {}
        for i, kind in enumerate(("small", "big", "boss")):
            if i < self.blind_idx:
                status = "DEFEATED"
            elif i == self.blind_idx:
                status = "CURRENT" if self.state == "SELECTING_HAND" else "SELECT"
            else:
                status = "UPCOMING"
            out[kind] = {"type": kind.upper(), "status": status, "name": ["Small Blind", "Big Blind", "The Wall"][i],
                         "effect": "", "score": self._target(i), "tag_name": "Uncommon Tag", "tag_effect": ""}
        return out

    def _area(self, cards, limit) -> Dict[str, Any]:
        return {"count": len(cards), "limit": limit, "cards": cards}

    def gamestate(self) -> Dict[str, Any]:
        self.calls += 1
        return {
            "state": self.state, "round_num": self.round_num, "ante_num": self.ante, "money": self.money,
            "deck": "RED", "stake": "WHITE", "seed": "FAKE", "won": self.won, "used_vouchers": {},
            "hands": {k: {"order": 0, "level": 1, "chips": v[0], "mult": v[1], "played": self.played[k],
                          "played_this_round": self.played_round[k], "example": []} for k, v in self.levels.items()},
            "round": {"hands_left": self.hands_left, "hands_played": 0, "discards_left": self.discards_left,
                      "discards_used": 0, "reroll_cost": 5, "chips": self.chips},
            "blinds": self._blinds(),
            "jokers": self._area(self.jokers, 5), "consumables": self._area(self.consumables, 2),
            "cards": self._area(self.deck, 52), "hand": self._area(self.hand, 8),
            "shop": self._area(self.shop, 2), "vouchers": self._area([], 1), "packs": self._area(self.packs, 2),
            "pack": self._area(self.pack_cards, 3) if self.state == "SMODS_BOOSTER_OPENED" else None,
        }

    def _need(self, *states: str, method: str) -> None:
        if self.state not in states:
            raise BalatroError("INVALID_STATE", f"{method} needs {states}, state is {self.state}", method)

    def _draw(self) -> None:
        while len(self.hand) < 8 and self.deck:
            self.hand.append(self.deck.pop())

    def _restock(self) -> None:
        picks = self.rng.sample(JOKER_POOL, 2)
        self.shop = [joker_dict(k, e, c, i) for i, (k, e, c) in enumerate(picks)]
        if self.rng.random() < 0.5:
            key, hand, _ = self.rng.choice(PLANETS)
            self.shop.append({"id": 9, "key": key, "set": "PLANET", "label": key, "value": {"effect": hand},
                              "modifier": {}, "state": {}, "cost": {"sell": 1, "buy": 3}})
        self.packs = [{"id": 20, "key": "p_buffoon_normal_1", "set": "BOOSTER", "label": "Buffoon Pack",
                       "value": {"effect": "Choose 1 of 2 Jokers"}, "modifier": {}, "state": {},
                       "cost": {"sell": 0, "buy": 4}}]

    # API ------------------------------------------------------------------------------

    def call(self, method: str, params=None):
        return getattr(self, method)(**(params or {}))

    def health(self):
        return {"status": "ok"}

    def menu(self):
        self.reset()
        return self.gamestate()

    def start(self, deck="RED", stake="WHITE", seed=None):
        self._need("MENU", method="start")
        self.state = "BLIND_SELECT"
        return self.gamestate()

    def select(self):
        self._need("BLIND_SELECT", method="select")
        self.state = "SELECTING_HAND"
        self.round_num += 1
        self.chips, self.hands_left, self.discards_left = 0, 4, 4
        self.played_round = {k: 0 for k in BASE_HANDS}
        self.deck = [card_dict(r, s, i) for i, (r, s) in enumerate((r, s) for r in "23456789TJQKA" for s in "HDCS")]
        self.rng.shuffle(self.deck)
        self.hand = []
        self._draw()
        return self.gamestate()

    def skip(self):
        self._need("BLIND_SELECT", method="skip")
        if self.blind_idx == 2:
            raise BalatroError("NOT_ALLOWED", "cannot skip boss", "skip")
        self.blind_idx += 1
        return self.gamestate()

    def _cards_of(self, idx):
        from balatro_player.engine.cards import card_from_api
        return [card_from_api(self.hand[i]) for i in idx]

    def play(self, cards):
        self._need("SELECTING_HAND", method="play")
        if not 1 <= len(cards) <= 5 or max(cards) >= len(self.hand):
            raise BalatroError("BAD_REQUEST", "bad cards", "play")
        held = [i for i in range(len(self.hand)) if i not in cards]
        from balatro_player.engine.cards import joker_from_api
        ctx = RoundContext(hand_levels={k: tuple(v) for k, v in self.levels.items()}, hands_left=self.hands_left,
                           discards_left=self.discards_left, played_this_round=dict(self.played_round),
                           hands_played_run=dict(self.played))
        r = score_play(self._cards_of(cards), self._cards_of(held), [joker_from_api(j) for j in self.jokers], ctx)
        self.chips += r.score
        self.played[r.hand] += 1
        self.played_round[r.hand] += 1
        self.hands_left -= 1
        self.hand = [self.hand[i] for i in held]
        if self.chips >= self._target():
            self.state = "ROUND_EVAL"
            self.hand = []
        elif self.hands_left == 0:
            self.state = "GAME_OVER"
        else:
            self._draw()
        return self.gamestate()

    def discard(self, cards):
        self._need("SELECTING_HAND", method="discard")
        if self.discards_left <= 0:
            raise BalatroError("NOT_ALLOWED", "no discards", "discard")
        self.hand = [c for i, c in enumerate(self.hand) if i not in cards]
        self.discards_left -= 1
        self._draw()
        return self.gamestate()

    def cash_out(self):
        self._need("ROUND_EVAL", method="cash_out")
        golden = sum(4 for j in self.jokers if j["key"] == "j_golden")
        self.money += 3 + self.hands_left + min(5, self.money // 5) + golden
        if self.blind_idx == 2 and self.ante == 8:
            self.won = True
        self.state = "SHOP"
        self._restock()
        return self.gamestate()

    def next_round(self):
        self._need("SHOP", method="next_round")
        self.blind_idx += 1
        if self.blind_idx == 3:
            self.blind_idx = 0
            self.ante += 1
        self.state = "BLIND_SELECT"
        return self.gamestate()

    def buy(self, card=None, voucher=None, pack=None):
        self._need("SHOP", method="buy")
        if card is not None:
            item = self.shop[card]
            cost = item["cost"]["buy"]
            if cost > self.money:
                raise BalatroError("NOT_ALLOWED", "too expensive", "buy")
            if item["set"] == "JOKER":
                if len(self.jokers) >= 5:
                    raise BalatroError("NOT_ALLOWED", "no slots", "buy")
                self.jokers.append(item)
            else:
                if len(self.consumables) >= 2:
                    raise BalatroError("NOT_ALLOWED", "no slots", "buy")
                self.consumables.append(item)
            self.money -= cost
            self.shop.pop(card)
        elif pack is not None:
            item = self.packs.pop(pack)
            if item["cost"]["buy"] > self.money:
                raise BalatroError("NOT_ALLOWED", "too expensive", "buy")
            self.money -= item["cost"]["buy"]
            self.pack_cards = [joker_dict(k, e, c, i) for i, (k, e, c) in enumerate(self.rng.sample(JOKER_POOL, 2))]
            self.state = "SMODS_BOOSTER_OPENED"
        else:
            raise BalatroError("BAD_REQUEST", "no vouchers in fake", "buy")
        return self.gamestate()

    def pack(self, card=None, targets=None, skip=False):
        self._need("SMODS_BOOSTER_OPENED", method="pack")
        if not skip:
            if len(self.jokers) >= 5:
                raise BalatroError("NOT_ALLOWED", "no slots", "pack")
            self.jokers.append(self.pack_cards[card])
        self.pack_cards = []
        self.state = "SHOP"
        return self.gamestate()

    def sell(self, joker=None, consumable=None):
        if joker is not None:
            self.money += self.jokers.pop(joker)["cost"]["sell"]
        elif consumable is not None:
            self.consumables.pop(consumable)
        return self.gamestate()

    def reroll(self):
        self._need("SHOP", method="reroll")
        if self.money < 5:
            raise BalatroError("NOT_ALLOWED", "no money", "reroll")
        self.money -= 5
        self._restock()
        return self.gamestate()

    def use(self, consumable, cards=None):
        item = self.consumables.pop(consumable)
        for key, hand, (c, m) in PLANETS:
            if key == item["key"]:
                self.levels[hand][0] += c
                self.levels[hand][1] += m
        return self.gamestate()

    def rearrange(self, hand=None, jokers=None, consumables=None):
        if jokers is not None:
            self.jokers = [self.jokers[i] for i in jokers]
        return self.gamestate()


class FakeLLM:
    """Returns plausible JSON for each prompt type."""

    def __init__(self):
        self.calls = 0
        self.seconds = 0.0

    def available(self):
        return True

    def chat(self, system, user):
        return ""

    def chat_json(self, system, user):
        self.calls += 1
        if "SHOP DECISION" in user:
            keys = [line.split()[2].rstrip(":") for line in user.splitlines() if line.startswith("  buy_card j_")]
            plan = [{"do": "buy_card", "key": keys[0]}] if keys else []
            return {"plan": plan, "why": "take the best joker"}, '{"plan": []}'
        if "BLIND DECISION" in user:
            return {"do": "select", "why": "safe"}, '{"do": "select"}'
        if "BOOSTER PACK" in user:
            return {"do": "pick", "index": 0, "why": "joker"}, '{"do": "pick", "index": 0}'
        if "RUN SUMMARY" in user:
            return {"lessons": [{"text": "Buy a pair-focused joker early and level Pair with Mercury.",
                                 "tags": ["pair", "ante-early"]}]}, "{}"
        return None, ""
