"""The game loop: drives one full run through BalatroBot, then reflects on it."""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Dict, List, Optional

from .client import BalatroClient, BalatroError
from .logbook import Logbook
from .memory import Memory
from .solver import decide
from .state import GameView
from .strategy import prompts
from .strategy.heuristics import Action
from .strategy.llm import LLM
from .strategy.strategist import Strategist

log = logging.getLogger(__name__)

MAX_STEPS = 4000
MAX_SHOP_ACTIONS = 25


class Agent:
    def __init__(self, client: BalatroClient, strategist: Strategist, logbook: Logbook,
                 memory: Optional[Memory] = None, llm: Optional[LLM] = None,
                 deck: str = "RED", stake: str = "WHITE", samples: int = 24, reflect: bool = True):
        self.client = client
        self.s = strategist
        self.log = logbook
        self.memory = memory
        self.llm = llm
        self.deck = deck
        self.stake = stake
        self.samples = samples
        self.reflect = reflect and llm is not None
        self.rng = random.Random()

    # One run -----------------------------------------------------------------

    def play_run(self, seed: Optional[str] = None) -> Dict[str, Any]:
        gs = self.client.gamestate()
        if gs.get("state") != "MENU":
            gs = self.client.menu()
        gs = self.client.start(self.deck, self.stake, seed)
        run_id = self.log.start_run(self.deck, self.stake, seed or gs.get("seed"), self.llm is not None)
        self.s.lessons_used = []
        self.s.new_shop()
        shop_actions = 0
        failed_consumables: set = set()
        errors = 0
        lost_to = ""
        started = time.time()

        for _ in range(MAX_STEPS):
            view = GameView(gs)
            st = view.state
            try:
                if view.won:
                    break
                if st == "BLIND_SELECT":
                    a = self.s.blind(view)
                    gs = self.client.skip() if a.kind == "skip" else self.client.select()
                    failed_consumables.clear()
                elif st == "SELECTING_HAND":
                    lost_to = (view.current_blind or {}).get("name", lost_to)
                    gs = self._hand_turn(view, failed_consumables)
                elif st == "ROUND_EVAL":
                    gs = self.client.cash_out()
                    self.s.new_shop()
                    shop_actions = 0
                elif st == "SHOP":
                    shop_actions += 1
                    if shop_actions > MAX_SHOP_ACTIONS:
                        gs = self.client.next_round()
                        continue
                    a = self.s.shop(view)
                    if a.kind == "use_consumable" and a.key in failed_consumables:
                        a = Action("leave")
                    gs = self._do_shop(view, a, failed_consumables)
                elif st == "SMODS_BOOSTER_OPENED":
                    a = self.s.pack(view)
                    try:
                        gs = self._do_pack(a)
                    except BalatroError:
                        gs = self.client.pack(skip=True)
                elif st == "GAME_OVER":
                    break
                elif st == "MENU":
                    break
                else:
                    time.sleep(0.2)
                    gs = self.client.gamestate()
                errors = 0
            except BalatroError as e:
                errors += 1
                log.warning("action failed (%s); refreshing state", e)
                if errors >= 20:
                    log.error("too many errors, abandoning run")
                    break
                time.sleep(0.2)
                gs = self.client.gamestate()

        view = GameView(gs)
        won = view.won
        jokers = [j.key for j in view.jokers]
        self.log.end_run(view.ante, view.round_num, won, "" if won else lost_to, jokers, gs.get("seed"))
        self.log.credit_lessons(self.s.lessons_used, view.ante + (1 if won else 0))
        summary = {"run_id": run_id, "seed": gs.get("seed"), "ante": view.ante, "round": view.round_num,
                   "won": won, "lost_to": "" if won else lost_to, "jokers": jokers,
                   "minutes": round((time.time() - started) / 60, 1)}
        if self.reflect:
            self._reflect(run_id, view, summary)
        if self.memory:
            self.memory.prune()
        return summary

    # Hands ---------------------------------------------------------------------------

    def _hand_turn(self, view: GameView, failed: set) -> Dict[str, Any]:
        for a in self.s.round_consumables(view):
            if a.key in failed:
                continue
            try:
                return self.client.use(a.index, a.targets or None)
            except BalatroError:
                failed.add(a.key)
        ctx = view.round_context()
        d = decide(view.hand, view.deck, view.jokers, ctx, view.target, view.round_chips,
                   samples=self.samples, rng=self.rng)
        self.log.decision(view.ante, view.round_num, "hand",
                          {"kind": d.action, "cards": d.cards, "expected": round(d.expected, 1), "reason": d.reason},
                          "solver")
        if d.action == "discard":
            return self.client.discard(d.cards)
        before = view.round_chips
        gs = self.client.play(d.cards)
        after = GameView(gs)
        if after.state in ("SELECTING_HAND", "ROUND_EVAL") and after.round_chips >= before:
            played = [view.hand[i] for i in d.cards]
            self.log.score_check(int(d.expected), after.round_chips - before,
                                 d.hand, [c.short() for c in played],
                                 [j.key for j in view.jokers], ctx.blind_name)
        return gs

    # Shop and packs -----------------------------------------------------------------

    def _do_shop(self, view: GameView, a: Action, failed: set) -> Dict[str, Any]:
        c = self.client
        try:
            if a.kind == "buy_card":
                return c.buy(card=a.index)
            if a.kind == "buy_voucher":
                return c.buy(voucher=a.index)
            if a.kind == "buy_pack":
                return c.buy(pack=a.index)
            if a.kind == "sell_joker":
                return c.sell(joker=a.index)
            if a.kind == "sell_consumable":
                return c.sell(consumable=a.index)
            if a.kind == "use_consumable":
                return c.use(a.index, a.targets or None)
            if a.kind == "reroll":
                return c.reroll()
            if a.kind == "rearrange_jokers":
                return c.rearrange(jokers=a.order)
        except BalatroError as e:
            log.info("shop action %s failed: %s", a.kind, e)
            if a.kind == "use_consumable":
                failed.add(a.key)
            return c.gamestate()
        return c.next_round()

    def _do_pack(self, a: Action) -> Dict[str, Any]:
        if a.kind == "pack_pick" and a.index is not None:
            return self.client.pack(card=a.index, targets=a.targets or None)
        return self.client.pack(skip=True)

    # Reflection ------------------------------------------------------------------------

    def _reflect(self, run_id: int, view: GameView, summary: Dict[str, Any]) -> None:
        decisions = self.log.run_decisions(run_id)
        timeline: List[Dict[str, Any]] = []
        for d in decisions:
            if d["phase"] in ("shop", "shop_plan", "blind", "pack"):
                timeline.append({"ante": d["ante"], "phase": d["phase"], "source": d["source"],
                                 "action": d["action"][:220]})
        played = view.hand_stat("played")
        boss = view.current_blind or {}
        report = {
            "deck": self.deck, "stake": self.stake, "won": summary["won"], "ante_reached": summary["ante"],
            "lost_to": summary["lost_to"], "lost_to_effect": boss.get("effect", ""),
            "final_jokers": [{"key": j.key, "effect": j.effect[:120]} for j in view.jokers],
            "hands_played": {h: n for h, n in played.items() if n},
            "money_at_end": view.money,
            "decisions": timeline[-40:],
        }
        try:
            data, _ = self.llm.chat_json(prompts.REFLECT_SYSTEM, prompts.reflect_prompt(report))
            lessons = data.get("lessons") if isinstance(data, dict) else None
            if isinstance(lessons, list) and self.memory:
                n = self.memory.add([l for l in lessons if isinstance(l, dict)], run_id)
                log.info("stored %d new lessons", n)
        except Exception as e:                                         # noqa: BLE001
            log.warning("reflection failed: %s", e)
