"""The play log: every move, timestamped against the video, with the bot's temperament.

Written for an AI video editor rather than a person. One JSON object per line in
``playlog/run_NNNN.jsonl``. Each event carries:
  - ``video_t`` / ``timelapse_t``: seconds into the run's video and its timelapse,
  - what happened (cards, scores, money, choices) and why,
  - ``mood`` and ``inner_voice``: how the bot "feels" given the real pressure it's under,
  - ``drama`` tags and a ``highlight`` score (0-1) for picking the moments worth keeping.
Each ante ends with an ``ante_summary`` event, and each run with ``run_end``.
See docs/PLAYLOG.md for the full schema.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .journal import humanize_reason, nice_key

MOODS = ["desperate", "nervous", "focused", "confident", "cocky"]

VOICE = {
    # Plays that beat the blind.
    ("win", "cocky"): ["{hand} for {score}. Easy.", "This blind never stood a chance: {score}.",
                       "{hand}, {score} points. Next."],
    ("win", "confident"): ["{hand} for {score}. That should do it.", "Playing the {hand}: {score}. Done.",
                           "Good hand. {hand}, {score}, and that's the blind."],
    ("win", "clutch"): ["LAST HAND. {hand} for {score}... and it's enough!", "Down to the wire: {hand}, {score}. Made it."],
    # Plays that chip away at it.
    ("play", "confident"): ["{hand} for {score}. {left} to go, plenty of hands left.",
                            "Chipping away: {hand}, {score}."],
    ("play", "focused"): ["{hand} for {score}. Still {left} to go, stay steady.",
                          "{score} from a {hand}. Need {left} more over {hands} hands."],
    ("play", "nervous"): ["Need {left} more with {hands} hands left... {hand} for {score} is all I've got.",
                          "Come on. {hand}, {score}. It's getting tight."],
    ("play", "desperate"): ["{hand} for {score}. I need a miracle.", "Last chance. {hand}. Please be enough.",
                            "This is it: {hand} for {score} against {left}."],
    ("discard", "cocky"): ["Not good enough for me. Tossing {n}.", "I can do better. Redrawing {n}."],
    ("discard", "confident"): ["{plan}. Throwing away {n}.", "Patience. Discarding {n} to set up the {goal}."],
    ("discard", "focused"): ["{plan}. Out go {n} cards.", "Building toward the {goal}. Discard {n}."],
    ("discard", "nervous"): ["This hand won't cut it. {plan}, fingers crossed.",
                             "Tossing {n} and hoping for the {goal}."],
    ("discard", "desperate"): ["Nothing here. Throwing {n} and praying.", "All in on the {goal}. Discard {n}."],
}


def mood_from_pressure(achievable: float, need: float) -> str:
    """How comfortable the bot is: what it can likely score vs. what it still needs."""
    if need <= 0:
        return "cocky"
    r = achievable / need
    return "cocky" if r >= 3 else "confident" if r >= 1.5 else "focused" if r >= 1 else \
        "nervous" if r >= 0.6 else "desperate"


def play_mood(score: float, need: float, hands_left: int) -> str:
    """Mood for a play: winning plays by margin, others by what's still reachable."""
    if need <= 0 or score >= need:
        return "cocky" if need <= 0 or score >= 2 * need else "confident"
    mood = mood_from_pressure(score * max(1, hands_left), need)
    return "confident" if mood == "cocky" else mood


def fmt(n: float) -> str:
    return f"{int(round(n)):,}"


class PlayLog:
    def __init__(self, out_dir: str = "playlog", speedup: float = 0,
                 recap: Optional[Callable[[Dict[str, Any]], str]] = None, seed: Optional[int] = None):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.speedup = speedup
        self.recap = recap
        self.rng = random.Random(seed)
        self.file = None
        self.t0 = time.time()
        self.run_id = 0
        self.seq = 0
        self.best_score_ever = 0
        self.ante: Optional[Dict[str, Any]] = None

    # Plumbing -------------------------------------------------------------------

    def start_run(self, run_id: int, t0: Optional[float], meta: Dict[str, Any]) -> None:
        if self.file:
            self.file.close()
        self.run_id = run_id
        self.seq = 0
        self.t0 = t0 or time.time()
        self.file = open(self.out_dir / f"run_{run_id:04d}.jsonl", "w", encoding="utf-8")
        self.ante = None
        self.emit("run_start", highlight=0.4, **meta)

    def emit(self, kind: str, highlight: float = 0.1, drama: Optional[List[str]] = None, **fields: Any) -> Dict[str, Any]:
        t = time.time() - self.t0
        ev = {"run": self.run_id, "seq": self.seq, "event": kind, "video_t": round(t, 2)}
        if self.speedup and self.speedup > 1:
            ev["timelapse_t"] = round(t / self.speedup, 2)
        ev.update({k: v for k, v in fields.items() if v is not None})
        ev["drama"] = drama or []
        ev["highlight"] = round(min(1.0, highlight), 2)
        self.seq += 1
        if self.file:
            self.file.write(json.dumps(ev, ensure_ascii=False) + "\n")
            self.file.flush()
        return ev

    def _voice(self, action: str, mood: str, **kw: Any) -> str:
        options = VOICE.get((action, mood)) or VOICE.get((action, "focused"), ["..."])
        try:
            return self.rng.choice(options).format(**kw)
        except (KeyError, IndexError):
            return options[0]

    # Ante bookkeeping --------------------------------------------------------------

    def _ante(self, ante: int, money: int) -> Dict[str, Any]:
        if self.ante is None or self.ante["ante"] != ante:
            self.ante = {"ante": ante, "money_start": money, "blinds": [], "plays": 0, "discards": 0,
                         "best_hand": None, "bought": [], "sold": [], "rerolls": 0, "moods": [],
                         "moments": []}
        return self.ante

    def _note_moment(self, ev: Dict[str, Any]) -> None:
        if self.ante is not None and ev["highlight"] >= 0.6:
            self.ante["moments"].append({"seq": ev["seq"], "video_t": ev["video_t"], "what": ev.get("inner_voice")
                                         or ev.get("event"), "drama": ev["drama"]})

    # Events -------------------------------------------------------------------------

    def blind(self, ante: int, money: int, blind: Dict[str, Any], action: str, reason: str) -> None:
        self._ante(ante, money)
        skip = action == "skip"
        self.emit("blind_skip" if skip else "blind_select", ante=ante, blind=blind.get("name"),
                  blind_type=blind.get("type"), target=blind.get("score"),
                  tag=blind.get("tag_name") if skip else None, reason=reason,
                  inner_voice=(f"Skipping {blind.get('name')} for the {blind.get('tag_name')}. Feeling bold."
                               if skip else f"{blind.get('name')}: {fmt(blind.get('score') or 0)} to beat. Let's go."),
                  highlight=0.5 if skip or blind.get("type") == "BOSS" else 0.15,
                  drama=["boss_intro"] if blind.get("type") == "BOSS" else (["risky_skip"] if skip else []))

    def play(self, ante: int, money: int, blind: str, target: int, chips_before: int, hand: List[str],
             played: List[str], hand_type: str, predicted: int, actual: Optional[int], hands_left: int,
             discards_left: int, reason: str, alternatives: List[Dict[str, Any]], cleared: bool,
             run_over: bool) -> None:
        a = self._ante(ante, money)
        a["plays"] += 1
        need = max(0, target - chips_before)
        score = actual if actual is not None else predicted
        mood = play_mood(score, need, hands_left)
        a["moods"].append(mood)
        drama: List[str] = []
        highlight = 0.15
        if cleared and chips_before == 0:
            drama.append("one_shot"); highlight = 0.7
        if cleared and hands_left == 1:
            drama.append("clutch"); highlight = 0.9
        if cleared and need and score >= need * 4:
            drama.append("overkill"); highlight = max(highlight, 0.6)
        if run_over and not cleared:
            drama.append("run_lost")
            if chips_before + score >= target * 0.8:
                drama.append("near_miss"); highlight = 0.95
            else:
                highlight = 0.8
        if hand_type in ("Straight Flush", "Four of a Kind", "Five of a Kind", "Flush House", "Flush Five"):
            drama.append("rare_hand"); highlight = max(highlight, 0.6)
        if self.best_score_ever and score >= self.best_score_ever * 1.25:
            drama.append("personal_best_hand"); highlight = max(highlight, 0.75)
        self.best_score_ever = max(self.best_score_ever, score)
        if a["best_hand"] is None or score > a["best_hand"]["score"]:
            a["best_hand"] = {"hand": hand_type, "score": score, "cards": played}
        line = ("win", "clutch") if "clutch" in drama else ("win", mood) if cleared else ("play", mood)
        voice = self._voice(line[0], line[1], hand=hand_type, score=fmt(score), left=fmt(max(0, need - score)),
                            hands=max(0, hands_left - 1))
        ev = self.emit("play", ante=ante, blind=blind, target=target, chips_before=chips_before,
                       chips_after=chips_before + score, hand_cards=hand, played=played, hand_type=hand_type,
                       predicted=predicted, actual=actual, hands_left=hands_left - 1, discards_left=discards_left,
                       cleared=cleared, mood=mood, inner_voice=voice, reason=reason,
                       considered=alternatives[:3], highlight=highlight, drama=drama)
        self._note_moment(ev)

    def discard(self, ante: int, money: int, blind: str, target: int, chips_before: int, hand: List[str],
                discarded: List[str], expected_next: float, best_now: int, hands_left: int,
                discards_left: int, reason: str) -> None:
        a = self._ante(ante, money)
        a["discards"] += 1
        need = max(0, target - chips_before)
        mood = mood_from_pressure(max(expected_next, best_now) * hands_left, need)
        a["moods"].append(mood)
        plan = reason.split(":")[0] if ":" in reason else "Redrawing"
        goal = plan.replace("chase ", "").replace("keep ", "")
        if not goal or goal in ("High Card", "pairs") or goal == plan:
            goal = "better hand" if goal != "pairs" else "pairs"
        voice = self._voice("discard", mood, n=len(discarded), plan=plan.capitalize(), goal=goal)
        self.emit("discard", ante=ante, blind=blind, target=target, chips_before=chips_before, hand_cards=hand,
                  discarded=discarded, plan=plan, expected_next=round(expected_next), best_now=best_now,
                  hands_left=hands_left, discards_left=discards_left - 1, mood=mood, inner_voice=voice,
                  reason=reason, highlight=0.25 if mood in ("nervous", "desperate") else 0.1,
                  drama=["gamble"] if mood == "desperate" else [])

    def blind_result(self, ante: int, money_before: int, money_after: int, blind: str, target: int,
                     scored: int, hands_used: int) -> None:
        a = self._ante(ante, money_before)
        margin = scored / target if target else 0
        a["blinds"].append({"blind": blind, "target": target, "scored": scored, "hands_used": hands_used,
                            "earned": money_after - money_before})
        is_boss = blind.startswith("The ") or "Boss" in blind
        drama = ["boss_beaten"] if is_boss else []
        if margin < 1.1:
            drama.append("close_call")
        self.emit("blind_cleared", ante=ante, blind=blind, target=target, scored=scored, margin=round(margin, 2),
                  hands_used=hands_used, money=money_after, earned=money_after - money_before,
                  inner_voice=("Phew. That was way too close." if margin < 1.1 else
                               f"{blind} down. +${money_after - money_before}."),
                  highlight=0.6 if is_boss else 0.3 if margin < 1.1 else 0.2, drama=drama)

    def shop(self, ante: int, money_before: int, money_after: int, kind: str, item: str, reason: str,
             source: str) -> None:
        a = self._ante(ante, money_before)
        name = nice_key(item)
        if kind.startswith("buy"):
            a["bought"].append(item)
        elif kind == "sell_joker":
            a["sold"].append(item)
        elif kind == "reroll":
            a["rerolls"] += 1
        if kind in ("leave", "rearrange_jokers"):
            return
        temper = "greedy" if kind == "reroll" and a["rerolls"] >= 2 else \
            "thrifty" if kind == "leave" else "decisive"
        why = reason if source == "llm" else humanize_reason(reason)
        voice = {"reroll": "Nothing I like. Rerolling." if a["rerolls"] < 2 else "One more reroll. Just one more.",
                 "sell_joker": f"Sorry {name}, I need the slot.",
                 "use_consumable": f"Using {name}.",
                 "buy_pack": f"Buying a {name}. Let's see what's inside.",
                 "buy_voucher": f"Investing in {name}. It pays off over the whole run."}.get(
            kind, f"Taking {name}: {why}." if why else f"Taking {name}.")
        self.emit("shop_" + kind.replace("_card", "").replace("_joker", ""), ante=ante, item=item, item_name=name,
                  money_before=money_before, money_after=money_after, reason=reason, decided_by=source,
                  temperament=temper, inner_voice=voice, highlight=0.35 if item.startswith("j_") else 0.15)

    def pack(self, ante: int, money: int, picked: Optional[str], options: List[str], reason: str) -> None:
        self._ante(ante, money)
        self.emit("pack_open", ante=ante, options=options, picked=picked, reason=reason,
                  picked_name=nice_key(picked) if picked else None,
                  inner_voice=(f"Ooh, {nice_key(picked)}. Mine." if picked else "Nothing good in here."),
                  highlight=0.35 if picked and picked.startswith("j_") else 0.15)

    def consumable(self, ante: int, money: int, key: str, targets: List[str]) -> None:
        self._ante(ante, money)
        self.emit("use_consumable", ante=ante, card=key, targets=targets, highlight=0.15,
                  inner_voice=f"Using {nice_key(key)}." + (f" On {', '.join(targets)}." if targets else ""))

    def end_ante(self, ante: int, money_end: int, jokers: List[str], outcome: str) -> None:
        """Summarise the ante (call when the boss is beaten or the run ends)."""
        a = self.ante
        if a is None or a["ante"] != ante:
            return
        moods = a["moods"]
        arc = [m for i, m in enumerate(moods) if i == 0 or m != moods[i - 1]]
        summary = {
            "ante": ante, "outcome": outcome, "money_start": a["money_start"], "money_end": money_end,
            "blinds": a["blinds"], "plays": a["plays"], "discards": a["discards"], "best_hand": a["best_hand"],
            "bought": a["bought"], "sold": a["sold"], "rerolls": a["rerolls"], "jokers": jokers,
            "mood_arc": arc,
            "dominant_mood": max(set(moods), key=moods.count) if moods else None,
            "key_moments": a["moments"][:6],
        }
        recap = ""
        if self.recap:
            try:
                recap = self.recap(summary)
            except Exception:                                          # noqa: BLE001
                recap = ""
        if not recap:
            recap = self._template_recap(summary)
        highlight = 0.7 if outcome != "cleared" else 0.5
        self.emit("ante_summary", recap=recap, highlight=highlight,
                  drama=["run_lost"] if outcome == "lost" else ["run_won"] if outcome == "won" else [],
                  **summary)
        self.ante = None

    @staticmethod
    def _template_recap(s: Dict[str, Any]) -> str:
        best = s["best_hand"]
        best_txt = f" My best hand was a {best['hand']} for {fmt(best['score'])}." if best else ""
        buys = f" Picked up {', '.join(nice_key(b) for b in s['bought'])}." if s["bought"] else ""
        mood = s.get("dominant_mood") or "focused"
        if s["outcome"] == "lost":
            return f"Ante {s['ante']} is where it ended. I was mostly {mood}.{best_txt}{buys}"
        if s["outcome"] == "won":
            return f"Ante {s['ante']} done and the run is WON.{best_txt}"
        return f"Ante {s['ante']} cleared with ${s['money_end']} in the bank. Mostly {mood}.{best_txt}{buys}"

    def end_run(self, result: Dict[str, Any], diary: str = "", video: Optional[str] = None,
                timelapse: Optional[str] = None) -> None:
        self.emit("run_end", highlight=1.0 if result.get("won") else 0.8,
                  drama=["run_won"] if result.get("won") else ["run_lost"],
                  result="won" if result.get("won") else "lost", ante=result.get("ante"),
                  lost_to=result.get("lost_to") or None, jokers=result.get("jokers"), minutes=result.get("minutes"),
                  diary=diary or None, video=video, timelapse=timelapse,
                  milestones=result.get("milestones") or None)
        if self.file:
            self.file.close()
            self.file = None
