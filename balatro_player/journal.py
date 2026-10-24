"""journal.txt: the bot's diary, written for humans.

One entry per run: what happened, a first-person diary entry, what it was
thinking at the key moments, what it learned, milestones, and the link to the
video. Every 25 runs it looks back over the whole chapter. Read top to bottom,
it tells the story from novice to (eventually) beating the game.
"""

from __future__ import annotations

import json
import re
import textwrap
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .logbook import Logbook

WIDTH = 78
RULE = "═" * WIDTH


def _wrap(text: str, indent: str = "  ") -> str:
    paras = [p.strip() for p in text.strip().split("\n") if p.strip()]
    return "\n\n".join(textwrap.fill(p, WIDTH, initial_indent=indent, subsequent_indent=indent) for p in paras)


def _bullet(text: str) -> str:
    return textwrap.fill(text, WIDTH, initial_indent="  · ", subsequent_indent="    ")


def nice_key(key: str) -> str:
    """j_ride_the_bus -> Ride The Bus; c_jupiter -> Jupiter; p_buffoon_normal_1 -> Buffoon Pack."""
    if not key:
        return ""
    if key.startswith("p_"):
        return key[2:].split("_")[0].title() + " Pack"
    name = re.sub(r"^[jcv]_", "", key).replace("_", " ").title()
    return name


def humanize_reason(reason: str) -> str:
    """Turn the engine's terse reasons into plain words."""
    m = re.search(r"power x([\d.]+)", reason)
    if m:
        x = float(m.group(1))
        if x >= 1.05:
            return f"the numbers said it makes my hands about {x:.1f}x stronger"
        return "it doesn't add much power, but it pays for itself or grows over time"
    if reason.startswith("replace"):
        return "it's better than my weakest joker, so I swapped them"
    if "planet" in reason or "level up" in reason:
        return "it levels up a hand I actually play"
    if "ratio" in reason and "skip" not in reason:
        return reason.replace("ratio", "strength vs. the boss")
    return reason


class Journal:
    def __init__(self, path: str = "journal.txt", chapter_every: int = 25):
        self.path = Path(path)
        self.chapter_every = chapter_every
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                f"{RULE}\n  BALATRO JOURNAL\n  A local AI learning to play Balatro, one run at a time.\n"
                f"  Started {time.strftime('%A %d %B %Y, %H:%M')}\n{RULE}\n\n")

    def _append(self, text: str) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(text.rstrip() + "\n\n")

    # Milestones -----------------------------------------------------------------

    @staticmethod
    def milestones(log: Logbook, run_id: int) -> List[str]:
        runs = log.finished_runs()
        before = [r for r in runs if r["id"] < run_id]
        this = next((r for r in runs if r["id"] == run_id), None)
        if this is None:
            return []
        out = []
        ante = this["ante_reached"] or 0
        best_before = max((r["ante_reached"] or 0) for r in before) if before else 0
        if not before:
            out.append("My very first run.")
        if this["won"]:
            wins_before = [r for r in before if r["won"]]
            same_stake = [r for r in wins_before if r["stake"] == this["stake"]]
            if not wins_before:
                out.append("I BEAT THE GAME. First win ever.")
            elif not same_stake:
                out.append(f"First win on {this['stake'].title()} Stake.")
            streak = 1
            for r in reversed(before):
                if r["won"]:
                    streak += 1
                else:
                    break
            if streak >= 2:
                out.append(f"{streak} wins in a row.")
        elif before and ante > best_before:
            out.append(f"New personal best: reached Ante {ante} for the first time.")
        n = len(before) + 1
        if n in (10, 50, 100, 250, 500, 1000):
            out.append(f"Run number {n}.")
        return out

    @staticmethod
    def progress_line(log: Logbook) -> str:
        runs = log.finished_runs()
        if not runs:
            return ""
        last = runs[-10:]
        prev = runs[-20:-10]
        avg = lambda rs: sum((r["ante_reached"] or 0) for r in rs) / len(rs)
        wins = sum(r["won"] or 0 for r in runs)
        line = f"Last {len(last)} runs: average Ante {avg(last):.1f}"
        if prev:
            line += f" (the 10 before: {avg(prev):.1f})"
        return line + f" · Wins so far: {wins} of {len(runs)}"

    # Entries ----------------------------------------------------------------------

    @staticmethod
    def highlights(log: Logbook, run_id: int, limit: int = 6) -> List[str]:
        """The decisions worth remembering, in the bot's own words."""
        out = []
        for d in log.run_decisions(run_id):
            if d["phase"] == "hand":
                continue
            try:
                a = json.loads(d["action"])
            except (TypeError, json.JSONDecodeError):
                continue
            ante = d["ante"]
            if d["phase"] == "shop" and a.get("kind", "").startswith(("buy", "sell")):
                verb = "bought" if a["kind"].startswith("buy") else "sold"
                why = a.get("reason", "") if d["source"] == "llm" else humanize_reason(a.get("reason", ""))
                line = f"Ante {ante}: {verb} {nice_key(a.get('key', ''))}"
                out.append(f"{line}. {why}".strip() if d["source"] == "llm" else f"{line} because {why}.")
            elif d["phase"] == "blind" and a.get("kind") == "skip":
                out.append(f"Ante {ante}: skipped a blind for the tag. {humanize_reason(a.get('reason', ''))}")
            elif d["phase"] == "pack" and a.get("kind") == "pack_pick" and a.get("key"):
                why = a.get("reason", "")
                out.append(f"Ante {ante}: opened a pack and took {nice_key(a['key'])}. {humanize_reason(why)}".strip())
        out = list(dict.fromkeys(out))
        # Keep the story readable: first few and last few.
        if len(out) > limit:
            out = out[: limit // 2] + out[-(limit - limit // 2):]
        return out

    def write_run(self, log: Logbook, run_id: int, summary: Dict[str, Any], diary: str,
                  lessons: List[str], video: Optional[str] = None) -> None:
        run = next((r for r in log.finished_runs() if r["id"] == run_id), None)
        deck = (run["deck"] if run else "").title()
        stake = (run["stake"] if run else "").title()
        if summary.get("won"):
            result = "WON THE RUN"
        else:
            result = f"Lost on Ante {summary.get('ante')} to {summary.get('lost_to') or 'a blind'}"
        lines = [RULE,
                 f"Run #{run_id} · {time.strftime('%a %d %b %Y, %H:%M')} · {deck} Deck, {stake} Stake",
                 f"Result: {result}  ({summary.get('minutes', '?')} min)"]
        jokers = [nice_key(k) for k in summary.get("jokers", [])]
        lines.append("Jokers at the end: " + (", ".join(jokers) if jokers else "none"))
        if video:
            lines.append(f"Video: {video}")
        lines.append("")
        for m in self.milestones(log, run_id):
            lines.append(f"★ {m}")
        text = "\n".join(lines).rstrip() + "\n"
        if diary:
            text += "\nDiary\n" + _wrap(diary) + "\n"
        hl = self.highlights(log, run_id)
        if hl:
            text += "\nWhat I was thinking\n" + "\n".join(_bullet(h) for h in hl) + "\n"
        if lessons:
            text += "\nWhat I learned\n" + "\n".join(_bullet(l) for l in lessons) + "\n"
        progress = self.progress_line(log)
        if progress:
            text += "\n" + progress + "\n"
        self._append(text)

    def write_chapter(self, number: int, reflection: str, log: Logbook) -> None:
        text = (f"{RULE}\n  CHAPTER {number}: LOOKING BACK\n{RULE}\n" + _wrap(reflection) + "\n\n"
                + self.progress_line(log))
        self._append(text)

    def due_for_chapter(self, log: Logbook) -> Optional[int]:
        n = len(log.finished_runs())
        return n // self.chapter_every if n and n % self.chapter_every == 0 else None


def fallback_diary(summary: Dict[str, Any], view_money: int) -> str:
    """A plain diary entry when no LLM is available."""
    jokers = ", ".join(nice_key(k) for k in summary.get("jokers", [])) or "no jokers at all"
    if summary.get("won"):
        return f"I won. The build that got me there: {jokers}. I finished with ${view_money}."
    return (f"I made it to Ante {summary.get('ante')} before {summary.get('lost_to') or 'a blind'} stopped me. "
            f"My build at the end was {jokers}, with ${view_money} in the bank. "
            f"Not enough scoring for that point in the run; I need to find stronger multipliers earlier.")
