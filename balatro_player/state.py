"""A typed view over BalatroBot's game state."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .engine.cards import Card, Joker, card_from_api, joker_from_api
from .engine.score import BASE_HANDS, RoundContext

# Base blind score per ante on White stake (Small x1, Big x1.5, Boss x2).
ANTE_BASE = {1: 300, 2: 800, 3: 2000, 4: 5000, 5: 11000, 6: 20000, 7: 35000, 8: 50000}


def _cards(area: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not area:
        return []
    cards = area.get("cards") or []
    if isinstance(cards, dict):                         # Lua tables can arrive keyed "1", "2", ...
        cards = [cards[k] for k in sorted(cards, key=lambda k: int(k))]
    return list(cards)


@dataclass
class GameView:
    raw: Dict[str, Any]

    @property
    def state(self) -> str:
        return self.raw.get("state", "UNKNOWN")

    @property
    def ante(self) -> int:
        return int(self.raw.get("ante_num") or 0)

    @property
    def round_num(self) -> int:
        return int(self.raw.get("round_num") or 0)

    @property
    def money(self) -> int:
        return int(self.raw.get("money") or 0)

    @property
    def won(self) -> bool:
        return bool(self.raw.get("won"))

    # Areas --------------------------------------------------------------------

    @property
    def hand(self) -> List[Card]:
        return [card_from_api(c) for c in _cards(self.raw.get("hand"))]

    @property
    def hand_size(self) -> int:
        return int((self.raw.get("hand") or {}).get("limit") or 8)

    @property
    def deck(self) -> List[Card]:
        """Cards left in the draw pile (the full deck while in the shop)."""
        return [card_from_api(c) for c in _cards(self.raw.get("cards"))]

    @property
    def jokers(self) -> List[Joker]:
        return [joker_from_api(c) for c in _cards(self.raw.get("jokers"))]

    @property
    def joker_slots(self) -> int:
        return int((self.raw.get("jokers") or {}).get("limit") or 5)

    @property
    def consumables(self) -> List[Dict[str, Any]]:
        return _cards(self.raw.get("consumables"))

    @property
    def consumable_slots(self) -> int:
        return int((self.raw.get("consumables") or {}).get("limit") or 2)

    @property
    def shop(self) -> List[Dict[str, Any]]:
        return _cards(self.raw.get("shop"))

    @property
    def vouchers(self) -> List[Dict[str, Any]]:
        return _cards(self.raw.get("vouchers"))

    @property
    def packs(self) -> List[Dict[str, Any]]:
        return _cards(self.raw.get("packs"))

    @property
    def pack(self) -> List[Dict[str, Any]]:
        return _cards(self.raw.get("pack"))

    # Round ----------------------------------------------------------------------

    @property
    def round(self) -> Dict[str, Any]:
        return self.raw.get("round") or {}

    @property
    def hands_left(self) -> int:
        return int(self.round.get("hands_left") or 0)

    @property
    def discards_left(self) -> int:
        return int(self.round.get("discards_left") or 0)

    @property
    def round_chips(self) -> int:
        return int(self.round.get("chips") or 0)

    @property
    def reroll_cost(self) -> int:
        return int(self.round.get("reroll_cost") or 5)

    @property
    def blinds(self) -> Dict[str, Dict[str, Any]]:
        return self.raw.get("blinds") or {}

    def blind(self, status: str) -> Optional[Dict[str, Any]]:
        for kind in ("small", "big", "boss"):
            b = self.blinds.get(kind)
            if b and b.get("status") == status:
                return b
        return None

    @property
    def current_blind(self) -> Optional[Dict[str, Any]]:
        return self.blind("CURRENT")

    @property
    def target(self) -> int:
        b = self.current_blind or self.blind("SELECT") or {}
        return int(b.get("score") or 0)

    # Hands ----------------------------------------------------------------------

    @property
    def hand_levels(self) -> Dict[str, tuple]:
        levels = dict(BASE_HANDS)
        for name, h in (self.raw.get("hands") or {}).items():
            if name in levels:
                levels[name] = (int(h.get("chips") or 0), int(h.get("mult") or 0))
        return levels

    def hand_stat(self, field: str) -> Dict[str, int]:
        return {name: int(h.get(field) or 0) for name, h in (self.raw.get("hands") or {}).items()}

    def round_context(self) -> RoundContext:
        blind = self.current_blind or {}
        return RoundContext(
            hand_levels=self.hand_levels,
            hands_played_run=self.hand_stat("played"),
            played_this_round=self.hand_stat("played_this_round"),
            hands_left=max(1, self.hands_left),
            discards_left=self.discards_left,
            money=self.money,
            deck_remaining=len(_cards(self.raw.get("cards"))),
            joker_slots=self.joker_slots,
            blind_name=blind.get("name", ""),
        )

    # Summaries for prompts and features ---------------------------------------

    def deck_summary(self) -> Dict[str, Any]:
        cards = self.deck + self.hand
        suits = Counter(c.suit for c in cards if c.suit and not c.is_stone)
        ranks = Counter(c.rank for c in cards if c.rank and not c.is_stone)
        enh = Counter(c.enhancement for c in cards if c.enhancement)
        return {"size": len(cards), "suits": dict(suits), "ranks": dict(ranks), "enhancements": dict(enh)}
