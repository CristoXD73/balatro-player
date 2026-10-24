"""Playing cards and jokers, built from BalatroBot's Card objects."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

RANKS = "23456789TJQKA"
RANK_ORDER = {r: i + 2 for i, r in enumerate(RANKS)}       # 2..14
RANK_CHIPS = {**{r: RANK_ORDER[r] for r in "23456789"}, "T": 10, "J": 10, "Q": 10, "K": 10, "A": 11}
SUITS = "HDCS"
FACES = set("JQK")


@dataclass(frozen=True)
class Card:
    rank: Optional[str]                 # "2".."A"; None for Stone cards or face-down cards
    suit: Optional[str]                 # "H", "D", "C", "S"
    enhancement: Optional[str] = None   # BONUS, MULT, WILD, GLASS, STEEL, STONE, GOLD, LUCKY
    edition: Optional[str] = None       # FOIL, HOLO, POLYCHROME
    seal: Optional[str] = None          # RED, BLUE, GOLD, PURPLE
    debuff: bool = False
    hidden: bool = False
    bonus_chips: int = 0                # permanent chips from Hiker etc. (when known)

    @property
    def is_stone(self) -> bool:
        return self.enhancement == "STONE"

    @property
    def rank_value(self) -> int:
        return RANK_ORDER.get(self.rank, 0) if self.rank and not self.is_stone else 0

    @property
    def chips(self) -> int:
        if self.is_stone:
            return 50 + self.bonus_chips
        base = RANK_CHIPS.get(self.rank, 0) if self.rank else 0
        if self.enhancement == "BONUS":
            base += 30
        return base + self.bonus_chips

    def is_face(self, pareidolia: bool = False) -> bool:
        if self.is_stone:
            return False
        return pareidolia or (self.rank in FACES)

    def has_suit(self, suit: str, smeared: bool = False) -> bool:
        if self.is_stone or self.suit is None:
            return False
        if self.enhancement == "WILD":
            return True
        if smeared:
            red = {"H", "D"}
            return (self.suit in red) == (suit in red)
        return self.suit == suit

    def short(self) -> str:
        if self.hidden:
            return "??"
        if self.is_stone:
            return "Stone"
        tag = "".join(x[0] for x in (self.enhancement, self.edition, self.seal) if x)
        return f"{self.rank}{self.suit}" + (f"[{tag}]" if tag else "")


def parse_card(text: str, **kw: Any) -> Card:
    """Shorthand for tests and docs: ``parse_card("AH")``, ``parse_card("TS", enhancement="GLASS")``."""
    return Card(rank=text[0].upper(), suit=text[1].upper(), **kw)


def card_from_api(c: Dict[str, Any]) -> Card:
    value = c.get("value") or {}
    mod = c.get("modifier") or {}
    state = c.get("state") or {}
    return Card(
        rank=value.get("rank"),
        suit=value.get("suit"),
        enhancement=mod.get("enhancement"),
        edition=mod.get("edition"),
        seal=mod.get("seal"),
        debuff=bool(state.get("debuff")),
        hidden=bool(state.get("hidden")),
    )


# Values parsed out of a joker's live description, e.g. "(Currently X2.5 Mult)".
_CUR_XMULT = re.compile(r"Currently\s*X\s*([\d.]+)", re.I)
_CUR_MULT = re.compile(r"Currently\s*\+?\s*(-?\d+)\s*Mult", re.I)
_CUR_CHIPS = re.compile(r"Currently\s*\+?\s*(-?\d+)\s*Chips", re.I)


@dataclass
class Joker:
    key: str                         # e.g. "j_blueprint"
    edition: Optional[str] = None
    effect: str = ""                 # live description text from the game
    sell: int = 0
    buy: int = 0
    debuff: bool = False
    eternal: bool = False
    rental: bool = False
    perishable: Optional[int] = None
    label: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def current_xmult(self) -> Optional[float]:
        m = _CUR_XMULT.search(self.effect)
        return float(m.group(1)) if m else None

    @property
    def current_mult(self) -> Optional[int]:
        m = _CUR_MULT.search(self.effect)
        return int(m.group(1)) if m else None

    @property
    def current_chips(self) -> Optional[int]:
        m = _CUR_CHIPS.search(self.effect)
        return int(m.group(1)) if m else None


def joker_from_api(c: Dict[str, Any]) -> Joker:
    mod = c.get("modifier") or {}
    state = c.get("state") or {}
    cost = c.get("cost") or {}
    return Joker(
        key=c.get("key", ""),
        edition=mod.get("edition"),
        effect=(c.get("value") or {}).get("effect", "") or "",
        sell=int(cost.get("sell") or 0),
        buy=int(cost.get("buy") or 0),
        debuff=bool(state.get("debuff")),
        eternal=bool(mod.get("eternal")),
        rental=bool(mod.get("rental")),
        perishable=mod.get("perishable"),
        label=c.get("label", ""),
    )
