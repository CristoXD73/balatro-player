"""Rule-based run strategy, driven by the scoring engine.

Works on its own (``--no-llm``) and is the fallback whenever the LLM is slow,
unavailable or returns something invalid. It also produces the candidate list
and numbers the LLM sees.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..engine.cards import Card
from ..state import GameView
from . import catalog
from .evaluate import Evaluator, money_weight


@dataclass
class Action:
    kind: str                        # buy_card, buy_voucher, buy_pack, sell_joker, sell_consumable,
                                     # use_consumable, reroll, rearrange_jokers, leave,
                                     # select, skip, pack_pick, pack_skip
    index: Optional[int] = None
    targets: List[int] = field(default_factory=list)
    order: List[int] = field(default_factory=list)
    reason: str = ""
    value: float = 0.0
    key: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = {"kind": self.kind, "index": self.index, "key": self.key, "reason": self.reason,
             "value": round(self.value, 3)}
        if self.targets:
            d["targets"] = self.targets
        if self.order:
            d["order"] = self.order
        return d


def interest_floor(view: GameView) -> int:
    """Bank to protect for interest ($1 per $5 held). Early on, power matters more;
    from ante 3 keep $25 (the default interest cap); stop saving near the end."""
    target = {1: 0, 2: 10, 3: 20, 4: 25, 5: 25, 6: 25, 7: 10}.get(view.ante, 0)
    used = view.raw.get("used_vouchers") or {}
    if target and "v_money_tree" in used:
        target *= 2
    elif target and "v_seed_money" in used:
        target = int(target * 1.5)
    return target


def dominant_suit(cards: List[Card]) -> Optional[str]:
    suits = Counter(c.suit for c in cards if c.suit and not c.is_stone)
    return suits.most_common(1)[0][0] if suits else None


def card_targets(key: str, hand: List[Card], view: GameView) -> Optional[List[int]]:
    """Hand indices a Tarot/Spectral should target, [] for no targets, None to not use it."""
    if not hand:
        return [] if key in catalog.SAFE_SPECTRALS and catalog.SAFE_SPECTRALS[key] == 0 else None
    order = sorted(range(len(hand)), key=lambda i: -hand[i].chips)       # best cards first
    plain = [i for i in order if not hand[i].enhancement and not hand[i].is_stone]
    if key in catalog.TAROT_ENHANCE:
        enh, n = catalog.TAROT_ENHANCE[key]
        if enh in ("STONE", "GOLD"):
            pool = list(reversed(plain))          # sacrifice weak cards
        else:
            pool = plain
        return pool[:n] if pool else None
    if key in catalog.TAROT_SUIT:
        suit = catalog.TAROT_SUIT[key]
        main = dominant_suit(view.deck + hand)
        if suit != main:
            return None
        off = [i for i in plain if hand[i].suit != suit]
        return off[:3] if off else None
    if key == "c_strength":
        return [i for i in plain if hand[i].rank not in ("A", "K")][:2] or None
    if key == "c_hanged_man":
        weak = sorted(plain, key=lambda i: hand[i].chips)
        return weak[:2] if len(weak) >= 2 else None
    if key == "c_death":
        if len(order) >= 2:
            best, worst = order[0], order[-1]
            return sorted([worst, best])        # left card becomes the right card
        return None
    if key in ("c_hermit", "c_temperance", "c_judgement", "c_high_priestess", "c_emperor",
               "c_wheel_of_fortune", "c_fool"):
        return []
    if key in catalog.SAFE_SPECTRALS:
        n = catalog.SAFE_SPECTRALS[key]
        if n == 0:
            return []
        return order[:n]
    return None


class HeuristicPolicy:
    def __init__(self, max_rerolls: int = 2):
        self.max_rerolls = max_rerolls
        self.rerolls_this_shop = 0

    def new_shop(self) -> None:
        self.rerolls_this_shop = 0

    # Blinds -------------------------------------------------------------------

    def blind(self, view: GameView, ev: Optional[Evaluator] = None) -> Action:
        b = view.blind("SELECT") or {}
        if b.get("type") == "BOSS" or not b:
            return Action("select", reason="boss blind must be played")
        tag = b.get("tag_name", "")
        tag_value = catalog.GOOD_TAGS.get(tag, 0)
        ev = ev or Evaluator(view, samples=10)
        ratio = ev.ratio()
        # Skip only with a strong build and a tag worth more than the round's cash.
        if tag_value >= 1.5 and ratio >= 2.5:
            return Action("skip", reason=f"strong build (ratio {ratio:.1f}) and {tag}", value=tag_value)
        return Action("select", reason=f"ratio {ratio:.1f}; tag {tag or 'none'} not worth it")

    # Shop -----------------------------------------------------------------------

    def shop_candidates(self, view: GameView, ev: Evaluator) -> List[Action]:
        out: List[Action] = []
        money = view.money
        floor = interest_floor(view)
        jokers = view.jokers
        free_slots = view.joker_slots - len(jokers)
        losses = ev.removal_losses() if jokers else []

        for i, c in enumerate(view.shop):
            cost = int((c.get("cost") or {}).get("buy") or 0)
            if cost > money:
                continue
            kind = c.get("set")
            key = c.get("key", "")
            spend_penalty = max(0, floor - (money - cost)) * 0.02
            if kind == "JOKER":
                negative = (c.get("modifier") or {}).get("edition") == "NEGATIVE"
                if free_slots > 0 or negative:
                    v = ev.joker_value(c)
                    out.append(Action("buy_card", i, key=key, value=v["total"] - spend_penalty,
                                      reason=f"power x{2 ** v['gain']:.2f}, econ {v['econ']:.2f}, bonus {v['bonus']:.2f}"))
                elif losses:
                    worst = min(range(len(losses)), key=lambda k: losses[k])
                    v = ev.joker_value(c, replacing=worst)
                    net = v["total"] - losses[worst] - spend_penalty
                    out.append(Action("buy_card", i, key=key, value=net,
                                      reason=f"replace {jokers[worst].key} (sell it first): net {net:.2f}"))
            elif kind == "PLANET":
                v = ev.planet_value(key)
                out.append(Action("buy_card", i, key=key, value=v - spend_penalty, reason=f"planet: power +{v:.2f}"))
            elif kind in ("TAROT", "SPECTRAL"):
                if len(view.consumables) < view.consumable_slots and key in ("c_hermit", "c_temperance", "c_judgement",
                                                                             "c_black_hole", "c_soul", "c_cryptid"):
                    out.append(Action("buy_card", i, key=key, value=0.3 - spend_penalty, reason="strong consumable"))

        for i, c in enumerate(view.vouchers):
            cost = int((c.get("cost") or {}).get("buy") or 10)
            key = c.get("key", "")
            if cost <= money and key in catalog.GOOD_VOUCHERS:
                v = catalog.GOOD_VOUCHERS[key] * 0.15 - max(0, floor - (money - cost)) * 0.02
                out.append(Action("buy_voucher", i, key=key, value=v, reason="good voucher"))

        for i, c in enumerate(view.packs):
            cost = int((c.get("cost") or {}).get("buy") or 4)
            key = c.get("key", "")
            if cost > money:
                continue
            penalty = max(0, floor - (money - cost)) * 0.02
            if "buffoon" in key and free_slots > 0:
                out.append(Action("buy_pack", i, key=key, value=0.35 - penalty, reason="buffoon pack: new joker"))
            elif "celestial" in key:
                out.append(Action("buy_pack", i, key=key, value=0.2 - penalty, reason="celestial: level hands"))
            elif "spectral" in key:
                out.append(Action("buy_pack", i, key=key, value=0.15 - penalty, reason="spectral pack"))
            elif "arcana" in key:
                out.append(Action("buy_pack", i, key=key, value=0.1 - penalty, reason="arcana pack"))
        out.sort(key=lambda a: -a.value)
        return out

    def shop(self, view: GameView, ev: Optional[Evaluator] = None,
             cands: Optional[List[Action]] = None) -> Action:
        ev = ev or Evaluator(view)
        for a in self.consumables(view):
            return a
        if cands is None:
            cands = self.shop_candidates(view, ev)
        good = [a for a in cands if a.value > 0.12]
        if good:
            best = good[0]
            if best.kind == "buy_card" and "replace" in best.reason:
                losses = ev.removal_losses()
                worst = min(range(len(losses)), key=lambda k: losses[k])
                return Action("sell_joker", worst, key=view.jokers[worst].key,
                              reason=f"make room for {best.key}", value=best.value)
            return best
        # Nothing worth buying: reroll if the build is weak and money is spare.
        ratio = ev.ratio()
        spare = view.money - interest_floor(view) - view.reroll_cost
        if ratio < 1.2 and spare >= 0 and self.rerolls_this_shop < self.max_rerolls and view.ante >= 2:
            self.rerolls_this_shop += 1
            return Action("reroll", reason=f"build weak (ratio {ratio:.2f}), nothing good")
        order = ev.best_joker_order(view.jokers)
        if order and order != list(range(len(order))):
            return Action("rearrange_jokers", order=order, reason="+mult before xMult")
        return Action("leave", reason=f"done (ratio {ratio:.2f})")

    # Consumables ------------------------------------------------------------------

    def consumables(self, view: GameView) -> List[Action]:
        """Consumables worth using right now (planets always; tarots when they have targets)."""
        out = []
        hand = view.hand
        for i, c in enumerate(view.consumables):
            key = c.get("key", "")
            kind = c.get("set")
            if kind == "PLANET":
                out.append(Action("use_consumable", i, key=key, reason="level up hand"))
            elif kind in ("TAROT", "SPECTRAL"):
                if key in ("c_judgement", "c_soul") and len(view.jokers) >= view.joker_slots:
                    continue
                if key == "c_hermit" and view.money < 6:
                    continue
                targets = card_targets(key, hand, view)
                if targets is not None and (targets or key not in catalog.TAROT_ENHANCE):
                    out.append(Action("use_consumable", i, key=key, targets=targets, reason="useful now"))
        return out

    # Packs --------------------------------------------------------------------------

    def pack(self, view: GameView, ev: Optional[Evaluator] = None) -> Action:
        items = view.pack
        hand = view.hand
        if not items:
            return Action("pack_skip", reason="empty pack")
        kinds = {c.get("set") for c in items}
        if "JOKER" in kinds:
            if len(view.jokers) >= view.joker_slots:
                return Action("pack_skip", reason="no joker slot")
            ev = ev or Evaluator(view)
            scored = [(ev.joker_value(c)["total"], i) for i, c in enumerate(items) if c.get("set") == "JOKER"]
            v, i = max(scored)
            return Action("pack_pick", i, key=items[i].get("key", ""), value=v, reason="best joker for build")
        if "PLANET" in kinds:
            ev = ev or Evaluator(view)
            scored = [(ev.planet_value(c.get("key", "")), i) for i, c in enumerate(items)]
            v, i = max(scored)
            return Action("pack_pick", i, key=items[i].get("key", ""), value=v, reason="planet for our hands")
        if kinds & {"TAROT", "SPECTRAL"}:
            best = None
            for i, c in enumerate(items):
                key = c.get("key", "")
                if c.get("set") == "SPECTRAL" and key not in catalog.SAFE_SPECTRALS:
                    continue
                if key in ("c_judgement", "c_soul") and len(view.jokers) >= view.joker_slots:
                    continue
                targets = card_targets(key, hand, view)
                if targets is None:
                    continue
                rank = 3 if key in ("c_soul", "c_black_hole", "c_cryptid") else \
                    2 if key in catalog.TAROT_ENHANCE or key in ("c_hermit", "c_judgement") else 1
                if best is None or rank > best[0]:
                    best = (rank, i, targets, key)
            if best:
                return Action("pack_pick", best[1], targets=best[2], key=best[3], reason="useful consumable")
            return Action("pack_skip", reason="nothing useful")
        # Standard pack: take an enhanced/sealed card in our main suit.
        main = dominant_suit(view.deck)
        for i, c in enumerate(items):
            mod = c.get("modifier") or {}
            suit = (c.get("value") or {}).get("suit")
            if (mod.get("enhancement") or mod.get("seal") or mod.get("edition")) and suit == main:
                return Action("pack_pick", i, key=c.get("key", ""), reason="enhanced card in main suit")
        return Action("pack_skip", reason="no card worth adding")
