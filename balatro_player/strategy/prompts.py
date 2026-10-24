"""Prompts for the LLM strategist. Compact, numeric, and grounded in engine output."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from ..state import GameView, _cards
from .evaluate import Evaluator
from .heuristics import Action, interest_floor

SYSTEM = """You are an expert Balatro player making run-level decisions for a bot.
A separate exact solver plays the hands; you decide the build: what to buy, sell, skip and pick.

Balatro essentials:
- Score = chips x mult. +Chips and +Mult jokers should sit LEFT of xMult jokers; xMult scales best late.
- Antes 1-3: get reliable +Mult/+Chips quickly and grow money. Antes 4-8: you need xMult and scaling.
- Interest: $1 per $5 held at end of round, up to $5 at $25. Don't drop below the bank target without a strong reason.
- Commit to one or two poker hands; level them with Planet cards. Flush and pair-based builds are the most reliable.
- Joker slots are the scarcest resource: replace weak jokers rather than hoarding.
- "power xN" below is the engine's measured multiplier on your average best hand if you take that option,
  tested on the same sampled hands. "ratio" is power / score needed per hand for this ante's boss
  (ratio < 1 means you will probably lose the boss).

Reply with JSON only."""


def _card_line(c: Dict[str, Any]) -> str:
    mod = c.get("modifier") or {}
    extras = [x for x in (mod.get("edition"), "eternal" if mod.get("eternal") else None,
                          "rental" if mod.get("rental") else None) if x]
    eff = (c.get("value") or {}).get("effect", "")
    cost = (c.get("cost") or {}).get("buy")
    head = f"{c.get('key')} ({c.get('label', '')})"
    if extras:
        head += " [" + ", ".join(extras) + "]"
    if cost:
        head += f" ${cost}"
    return f"{head}: {eff}".strip()


def state_block(view: GameView, ev: Evaluator) -> str:
    levels = view.hand_levels
    played = view.hand_stat("played")
    top_hands = sorted(played, key=lambda h: -played[h])[:4]
    lines = [
        f"Ante {view.ante}, round {view.round_num}, money ${view.money} (bank target ${interest_floor(view)}), "
        f"deck {view.raw.get('deck')}/{view.raw.get('stake')}",
        f"Build power {ev.power_now:.0f} per hand; boss needs ~{ev.required_per_hand():.0f} per hand; ratio {ev.ratio():.2f}",
        f"Jokers ({len(view.jokers)}/{view.joker_slots}):",
    ]
    lines += [f"  {i}. " + _card_line(c) for i, c in enumerate(_cards(view.raw.get("jokers")))]
    if view.consumables:
        lines.append("Consumables: " + "; ".join(_card_line(c) for c in view.consumables))
    lines.append("Most played hands: " + ", ".join(
        f"{h} x{played[h]} (lvl chips {levels[h][0]}, mult {levels[h][1]})" for h in top_hands if h in levels))
    d = view.deck_summary()
    lines.append(f"Deck: {d['size']} cards, suits {d['suits']}, enhancements {d['enhancements']}")
    boss = (view.blinds.get("boss") or {})
    if boss.get("name"):
        lines.append(f"Upcoming boss: {boss.get('name')} - {boss.get('effect', '')}")
    return "\n".join(lines)


def shop_prompt(view: GameView, ev: Evaluator, candidates: List[Action], baseline: List[Action],
                lessons: List[str]) -> str:
    items = []
    for i, c in enumerate(view.shop):
        items.append(f"  card {c.get('key')}: " + _card_line(c))
    for c in view.vouchers:
        items.append(f"  voucher {c.get('key')}: " + _card_line(c))
    for c in view.packs:
        items.append(f"  pack {c.get('key')}: " + _card_line(c))
    cand = [f"  {a.kind} {a.key}: value {a.value:+.2f} ({a.reason})" for a in candidates[:10]]
    base = [f"{a.kind} {a.key}".strip() for a in baseline]
    parts = [
        "SHOP DECISION",
        state_block(view, ev),
        f"Reroll cost ${view.reroll_cost}.",
        "Shop items:", *(items or ["  (empty)"]),
        "Engine evaluation of options (value: higher is better; 0.1 ~ small upgrade, 1.0 ~ doubles power):",
        *(cand or ["  (nothing affordable)"]),
        f"Baseline bot plan: {base or ['leave']}",
    ]
    if lessons:
        parts += ["Lessons from your past runs (apply if relevant):", *[f"  - {l}" for l in lessons]]
    parts.append(
        'Return {"plan": [steps...], "why": "<one sentence>"} where each step is one of '
        '{"do":"buy_card","key":K}, {"do":"buy_voucher","key":K}, {"do":"buy_pack","key":K}, '
        '{"do":"sell_joker","key":K}, {"do":"reroll"}. Steps run in order; an empty plan means leave the shop. '
        "Only use keys listed above. Sell a joker before buying one when slots are full.")
    return "\n".join(parts)


def blind_prompt(view: GameView, ev: Evaluator, baseline: Action, lessons: List[str]) -> str:
    b = view.blind("SELECT") or {}
    parts = [
        "BLIND DECISION",
        state_block(view, ev),
        f"Next blind: {b.get('name')} ({b.get('type')}) needs {b.get('score')}. "
        f"Skipping gives: {b.get('tag_name')} - {b.get('tag_effect')}. Skipping also forfeits this round's cash reward.",
        f"Baseline bot: {baseline.kind} ({baseline.reason})",
    ]
    if lessons:
        parts += ["Lessons:", *[f"  - {l}" for l in lessons]]
    parts.append('Return {"do": "select" or "skip", "why": "<one sentence>"}.')
    return "\n".join(parts)


def pack_prompt(view: GameView, ev: Evaluator, baseline: Action, lessons: List[str]) -> str:
    items = [f"  {i}. " + _card_line(c) for i, c in enumerate(view.pack)]
    hand = ", ".join(c.short() for c in view.hand)
    parts = [
        "BOOSTER PACK DECISION",
        state_block(view, ev),
        "Pack contents:", *items,
        f"Hand cards available as targets (0-based): {hand or 'none'}",
        f"Baseline bot: {baseline.kind} {baseline.key} {baseline.targets or ''} ({baseline.reason})",
    ]
    if lessons:
        parts += ["Lessons:", *[f"  - {l}" for l in lessons]]
    parts.append('Return {"do": "pick", "index": I, "targets": [hand indices if the card needs them]} '
                 'or {"do": "skip"}, plus "why".')
    return "\n".join(parts)


REFLECT_SYSTEM = """You are coaching a Balatro bot after a run. Find the decisions that mattered most
and turn them into short, general, actionable lessons for future runs. Reply with JSON only."""


def reflect_prompt(summary: Dict[str, Any]) -> str:
    return ("RUN SUMMARY\n" + json.dumps(summary, indent=1)[:6000] +
            '\n\nReturn {"lessons": [{"text": "<one actionable sentence>", '
            '"tags": ["<joker keys, hand types, ante-early|ante-mid|ante-late, deck, boss names>"]}]} '
            "with 1-3 lessons. Prefer lessons about build direction, economy and joker choices. "
            "Do not restate generic rules; say what to do differently.")
