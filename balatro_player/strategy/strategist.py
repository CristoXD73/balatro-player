"""The strategist: heuristics + value model + lessons + local LLM.

For every run-level decision:
  1. the heuristic policy scores the options with the engine,
  2. the value model (once trained) nudges those scores with real experience,
  3. the LLM gets the state, the scored options, the baseline plan and the
     relevant lessons, and makes the call,
  4. the LLM's answer is validated; anything invalid falls back to step 1-2.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from ..logbook import Logbook
from ..memory import Memory
from ..state import GameView
from ..value_model import ValueModel, view_features
from . import prompts
from .evaluate import Evaluator
from .heuristics import Action, HeuristicPolicy
from .llm import LLM

log = logging.getLogger(__name__)


class Strategist:
    def __init__(self, logbook: Logbook, llm: Optional[LLM] = None, memory: Optional[Memory] = None,
                 value_model: Optional[ValueModel] = None, samples: int = 16):
        self.policy = HeuristicPolicy()
        self.llm = llm
        self.logbook = logbook
        self.memory = memory
        self.vm = value_model
        self.samples = samples
        self.plan: List[Dict[str, Any]] = []
        self.plan_from_llm = False
        self.plan_why = ""
        self._planned = False
        self.lessons_used: List[int] = []

    # Helpers ------------------------------------------------------------------

    def _lessons(self, view: GameView) -> List[str]:
        if not self.memory:
            return []
        recalled = self.memory.recall(view)
        self.lessons_used += [lid for lid, _ in recalled]
        return [text for _, text in recalled]

    def _vm_weight(self) -> float:
        if not self.vm or not self.vm.ready:
            return 0.0
        return min(1.0, self.vm.n_runs / 200)

    def _features(self, view: GameView, ev: Evaluator) -> List[float]:
        return view_features(view, ev.ratio())

    def _record(self, view: GameView, phase: str, action: Action, source: str, ev: Optional[Evaluator] = None,
                prompt: str = "", response: str = "") -> None:
        feats = self._features(view, ev) if ev is not None else None
        self.logbook.decision(view.ante, view.round_num, phase, action.to_dict(), source, feats,
                              prompt, response, self.lessons_used[-4:])

    def adjust_with_value_model(self, view: GameView, ev: Evaluator, cands: List[Action]) -> None:
        w = self._vm_weight()
        if not w:
            return
        now = self.vm.predict(view_features(view, ev.ratio()))
        for a in cands:
            if a.kind != "buy_card" or not a.key.startswith("j_"):
                continue
            item = view.shop[a.index]
            cost = int((item.get("cost") or {}).get("buy") or 0)
            v = ev.joker_value(item)
            after = self.vm.predict(view_features(view, ev.ratio(v["power_after"]), money=view.money - cost,
                                                  joker_keys=[j.key for j in view.jokers] + [a.key]))
            a.value += w * (after - now) / 4          # one predicted ante ~ 0.25 value
            a.reason += f"; value model {after - now:+.2f} antes"
        cands.sort(key=lambda a: -a.value)

    # Blind ------------------------------------------------------------------------

    def blind(self, view: GameView) -> Action:
        ev = Evaluator(view, samples=max(8, self.samples // 2))
        base = self.policy.blind(view, ev)
        b = view.blind("SELECT") or {}
        if self.llm and b.get("type") in ("SMALL", "BIG"):
            prompt = prompts.blind_prompt(view, ev, base, self._lessons(view))
            try:
                data, raw = self.llm.chat_json(prompts.SYSTEM, prompt)
                if isinstance(data, dict) and data.get("do") in ("select", "skip"):
                    a = Action(data["do"], reason=str(data.get("why", ""))[:200])
                    self._record(view, "blind", a, "llm", ev, prompt, raw)
                    return a
            except Exception as e:                                         # noqa: BLE001
                log.warning("LLM blind decision failed: %s", e)
        self._record(view, "blind", base, "heuristic", ev)
        return base

    # Shop -------------------------------------------------------------------------

    def new_shop(self) -> None:
        self.policy.new_shop()
        self.plan = []
        self.plan_from_llm = False
        self.plan_why = ""
        self._planned = False

    def shop(self, view: GameView) -> Action:
        ev = Evaluator(view, samples=self.samples)
        # Always use planets / useful consumables first.
        for a in self.policy.consumables(view):
            self._record(view, "shop", a, "heuristic", ev)
            return a
        if not self._planned:
            self._planned = True
            self._make_plan(view, ev)
        while self.plan:
            step = self.plan.pop(0)
            a = self._resolve(view, step)
            if a is not None:
                a.reason = self.plan_why or a.reason
                self._record(view, "shop", a, "llm" if self.plan_from_llm else "heuristic", ev)
                return a
        if self.plan_from_llm:
            # The LLM's plan is done: tidy up and leave.
            order = ev.best_joker_order(view.jokers)
            if order and order != list(range(len(order))):
                return Action("rearrange_jokers", order=order, reason="+mult before xMult")
            return Action("leave", reason="LLM plan complete")
        cands = self.policy.shop_candidates(view, ev)
        self.adjust_with_value_model(view, ev, cands)
        a = self.policy.shop(view, ev, cands)
        self._record(view, "shop", a, "heuristic", ev)
        return a

    def _make_plan(self, view: GameView, ev: Evaluator) -> None:
        if not self.llm:
            return
        cands = self.policy.shop_candidates(view, ev)
        self.adjust_with_value_model(view, ev, cands)
        baseline = [a for a in cands if a.value > 0.12][:3]
        prompt = prompts.shop_prompt(view, ev, cands, baseline, self._lessons(view))
        try:
            data, raw = self.llm.chat_json(prompts.SYSTEM, prompt)
        except Exception as e:                                             # noqa: BLE001
            log.warning("LLM shop plan failed: %s", e)
            return
        steps = data.get("plan") if isinstance(data, dict) else None
        if not isinstance(steps, list):
            log.info("LLM returned no usable plan; using heuristics")
            return
        self.plan = [s for s in steps if isinstance(s, dict) and s.get("do")][:8]
        self.plan_from_llm = True
        self.plan_why = str(data.get("why") or "")[:300]
        self.logbook.decision(view.ante, view.round_num, "shop_plan", {"plan": self.plan, "why": data.get("why")},
                              "llm", self._features(view, ev), prompt, raw, self.lessons_used[-4:])

    def _resolve(self, view: GameView, step: Dict[str, Any]) -> Optional[Action]:
        """Turn an LLM step (by key) into a concrete, affordable action (by index)."""
        do, key = step.get("do"), step.get("key", "")
        money = view.money

        def find(items, k):
            for i, c in enumerate(items):
                if c.get("key") == k:
                    return i, c
            return None, None

        if do == "reroll":
            return Action("reroll", reason="LLM") if money >= view.reroll_cost else None
        if do == "sell_joker":
            for i, j in enumerate(view.jokers):
                if j.key == key and not j.eternal:
                    return Action("sell_joker", i, key=key, reason="LLM")
            return None
        area = {"buy_card": view.shop, "buy_voucher": view.vouchers, "buy_pack": view.packs}.get(do)
        if area is None:
            return None
        i, c = find(area, key)
        if c is None:
            return None
        cost = int((c.get("cost") or {}).get("buy") or 0)
        if cost > money:
            return None
        if do == "buy_card" and c.get("set") == "JOKER" and len(view.jokers) >= view.joker_slots \
                and (c.get("modifier") or {}).get("edition") != "NEGATIVE":
            return None
        return Action(do, i, key=key, reason="LLM")

    # Packs --------------------------------------------------------------------------

    def pack(self, view: GameView) -> Action:
        ev = Evaluator(view, samples=self.samples)
        base = self.policy.pack(view, ev)
        kinds = {c.get("set") for c in view.pack}
        if self.llm and kinds & {"JOKER", "SPECTRAL"}:
            prompt = prompts.pack_prompt(view, ev, base, self._lessons(view))
            try:
                data, raw = self.llm.chat_json(prompts.SYSTEM, prompt)
                a = self._pack_action(view, data)
                if a is not None:
                    self._record(view, "pack", a, "llm", ev, prompt, raw)
                    return a
            except Exception as e:                                         # noqa: BLE001
                log.warning("LLM pack decision failed: %s", e)
        self._record(view, "pack", base, "heuristic", ev)
        return base

    def _pack_action(self, view: GameView, data: Any) -> Optional[Action]:
        if not isinstance(data, dict):
            return None
        if data.get("do") == "skip":
            return Action("pack_skip", reason=str(data.get("why", ""))[:200])
        if data.get("do") != "pick":
            return None
        try:
            i = int(data.get("index"))
        except (TypeError, ValueError):
            return None
        if not 0 <= i < len(view.pack):
            return None
        item = view.pack[i]
        if item.get("set") == "JOKER" and len(view.jokers) >= view.joker_slots:
            return None
        targets = [int(t) for t in (data.get("targets") or []) if str(t).isdigit() and int(t) < len(view.hand)]
        return Action("pack_pick", i, targets=targets, key=item.get("key", ""), reason=str(data.get("why", ""))[:200])

    # Consumables during a round --------------------------------------------------------

    def round_consumables(self, view: GameView) -> List[Action]:
        return self.policy.consumables(view)
