"""The game loop: drives one full run through BalatroBot, then reflects on it."""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from .client import BalatroClient, BalatroError
from .journal import Journal, fallback_diary
from .logbook import Logbook
from .memory import Memory
from .playlog import PlayLog
from .recording import Recorder
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
                 deck: str = "RED", stake: str = "WHITE", samples: int = 24, reflect: bool = True,
                 recorder: Optional[Recorder] = None, journal: Optional[Journal] = None,
                 playlog: Optional[PlayLog] = None):
        self.client = client
        self.playlog = playlog
        self._blind: Dict[str, Any] = {}
        self._plays_this_blind = 0
        self.recorder = recorder
        self.journal = journal
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
        run_id = self.log.start_run(self.deck, self.stake, seed, self.llm is not None)
        if self.recorder:
            self.recorder.start(run_id)
        if self.playlog:
            t0 = self.recorder.started_at if self.recorder and self.recorder.current else None
            self.playlog.start_run(run_id, t0, {"deck": self.deck, "stake": self.stake, "seed": seed,
                                                "run_number": run_id, "llm": self.llm is not None,
                                                "inner_voice": f"Run {run_id}. Here we go."})
        gs = self.client.start(self.deck, self.stake, seed)
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
                    if self.playlog:
                        self.playlog.blind(view.ante, view.money, view.blind("SELECT") or {}, a.kind, a.reason)
                    gs = self.client.skip() if a.kind == "skip" else self.client.select()
                    self._plays_this_blind = 0
                    failed_consumables.clear()
                elif st == "SELECTING_HAND":
                    lost_to = (view.current_blind or {}).get("name", lost_to)
                    self._blind = view.current_blind or self._blind
                    gs = self._hand_turn(view, failed_consumables)
                elif st == "ROUND_EVAL":
                    money_before, scored = view.money, view.round_chips
                    gs = self.client.cash_out()
                    if self.playlog:
                        after = GameView(gs)
                        b = self._blind
                        self.playlog.blind_result(view.ante, money_before, after.money, b.get("name", "?"),
                                                  int(b.get("score") or 0), scored, self._plays_this_blind)
                        if b.get("type") == "BOSS":
                            self.playlog.end_ante(view.ante, after.money, [j.key for j in after.jokers], "cleared")
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
                    if self.playlog:
                        picked = a.key if a.kind == "pack_pick" else None
                        self.playlog.pack(view.ante, view.money, picked, [c.get("key", "") for c in view.pack], a.reason)
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
        video = None
        if self.recorder:
            path = self.recorder.stop(f"ante{view.ante}_{'won' if won else 'lost'}")
            video = str(path) if path else None
            summary["video"] = video
        if self.playlog:
            self.playlog.end_ante(view.ante, view.money, jokers, "won" if won else "lost")
        diary, lessons = "", []
        if self.reflect:
            diary, lessons = self._reflect(run_id, view, summary)
        diary = diary or fallback_diary(summary, view.money)
        if self.journal:
            self.journal.write_run(self.log, run_id, summary, diary, lessons, video)
        if self.playlog:
            summary["milestones"] = Journal.milestones(self.log, run_id)
            self.playlog.end_run(summary, diary, video,
                                 self.recorder.last_timelapse if self.recorder else None)
        if self.journal:
            chapter = self.journal.due_for_chapter(self.log)
            if chapter:
                self._chapter(chapter)
        if self.memory:
            self.memory.prune()
        return summary

    # Hands ---------------------------------------------------------------------------

    def _hand_turn(self, view: GameView, failed: set) -> Dict[str, Any]:
        for a in self.s.round_consumables(view):
            if a.key in failed:
                continue
            try:
                gs = self.client.use(a.index, a.targets or None)
                if self.playlog:
                    self.playlog.consumable(view.ante, view.money, a.key, [view.hand[i].short() for i in a.targets
                                                                           if i < len(view.hand)])
                return gs
            except BalatroError:
                failed.add(a.key)
        ctx = view.round_context()
        d = decide(view.hand, view.deck, view.jokers, ctx, view.target, view.round_chips,
                   samples=self.samples, rng=self.rng)
        self.log.decision(view.ante, view.round_num, "hand",
                          {"kind": d.action, "cards": d.cards, "expected": round(d.expected, 1), "reason": d.reason},
                          "solver")
        hand_txt = [c.short() for c in view.hand]
        picked = [hand_txt[i] for i in d.cards]
        blind_name = ctx.blind_name or (view.current_blind or {}).get("name", "")
        if d.action == "discard":
            if self.playlog:
                best_now = d.alternatives[0]["score"] if d.alternatives else 0
                self.playlog.discard(view.ante, view.money, blind_name, view.target, view.round_chips, hand_txt,
                                     picked, d.expected, best_now, view.hands_left, view.discards_left, d.reason)
            return self.client.discard(d.cards)
        before = view.round_chips
        gs = self.client.play(d.cards)
        self._plays_this_blind += 1
        after = GameView(gs)
        actual = None
        if after.state in ("SELECTING_HAND", "ROUND_EVAL") and after.round_chips >= before:
            actual = after.round_chips - before
            self.log.score_check(int(d.expected), actual, d.hand, picked,
                                 [j.key for j in view.jokers], ctx.blind_name)
        if self.playlog:
            self.playlog.play(view.ante, view.money, blind_name, view.target, before, hand_txt, picked, d.hand,
                              int(d.expected), actual, view.hands_left, view.discards_left, d.reason,
                              d.alternatives, cleared=after.state == "ROUND_EVAL",
                              run_over=after.state == "GAME_OVER")
        return gs

    # Shop and packs -----------------------------------------------------------------

    def _do_shop(self, view: GameView, a: Action, failed: set) -> Dict[str, Any]:
        gs = self._do_shop_action(view, a, failed)
        if self.playlog:
            source = "llm" if self.s.plan_from_llm and a.reason == self.s.plan_why else "engine"
            self.playlog.shop(view.ante, view.money, GameView(gs).money, a.kind, a.key, a.reason, source)
        return gs

    def _do_shop_action(self, view: GameView, a: Action, failed: set) -> Dict[str, Any]:
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

    def _reflect(self, run_id: int, view: GameView, summary: Dict[str, Any]) -> Tuple[str, List[str]]:
        """Ask the LLM for a diary entry and new lessons. Returns (diary, lesson texts)."""
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
        used = {r["id"]: r["text"] for r in self.log.lessons()}
        past = [used[i] for i in dict.fromkeys(self.s.lessons_used) if i in used]
        try:
            data, _ = self.llm.chat_json(prompts.REFLECT_SYSTEM, prompts.reflect_prompt(report, run_id, past))
        except Exception as e:                                         # noqa: BLE001
            log.warning("reflection failed: %s", e)
            return "", []
        if not isinstance(data, dict):
            return "", []
        diary = str(data.get("diary") or "").strip()
        lessons = [l for l in (data.get("lessons") or []) if isinstance(l, dict)]
        if lessons and self.memory:
            n = self.memory.add(lessons, run_id)
            log.info("stored %d new lessons", n)
        return diary, [str(l.get("text", "")).strip() for l in lessons if l.get("text")]

    def _chapter(self, chapter: int) -> None:
        runs = self.log.finished_runs()
        window = runs[-self.journal.chapter_every:]
        earlier = runs[:-self.journal.chapter_every]
        avg = lambda rs: round(sum((r["ante_reached"] or 0) for r in rs) / len(rs), 2) if rs else None
        losses: Dict[str, int] = {}
        for r in window:
            if not r["won"] and r["lost_to"]:
                losses[r["lost_to"]] = losses.get(r["lost_to"], 0) + 1
        stats = {"runs_this_chapter": len(window), "average_ante_this_chapter": avg(window),
                 "average_ante_before": avg(earlier), "wins_this_chapter": sum(r["won"] or 0 for r in window),
                 "total_wins": sum(r["won"] or 0 for r in runs), "most_common_killers": losses}
        lessons = sorted(self.log.lessons(), key=lambda r: -r["uses"])
        text = ""
        if self.llm:
            try:
                data, _ = self.llm.chat_json(prompts.CHAPTER_SYSTEM,
                                             prompts.chapter_prompt(chapter, stats, [l["text"] for l in lessons]))
                if isinstance(data, dict):
                    text = str(data.get("text") or "")
            except Exception as e:                                     # noqa: BLE001
                log.warning("chapter reflection failed: %s", e)
        if not text:
            text = (f"Over these {len(window)} runs I averaged Ante {stats['average_ante_this_chapter']}"
                    + (f", up from {stats['average_ante_before']} before" if earlier else "")
                    + f", with {stats['wins_this_chapter']} wins.")
        self.journal.write_chapter(chapter, text, self.log)
