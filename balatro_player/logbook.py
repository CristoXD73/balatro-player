"""Everything the bot does, in one SQLite file: runs, decisions, score checks, lessons.

This is the training data for the learning loops, so it records the state
features at each decision and, at the end, how far the run got.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY, started REAL, ended REAL, deck TEXT, stake TEXT, seed TEXT,
    ante_reached INTEGER, round_reached INTEGER, won INTEGER, lost_to TEXT, final_jokers TEXT,
    llm INTEGER, notes TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY, run_id INTEGER, t REAL, ante INTEGER, round INTEGER, phase TEXT,
    action TEXT, source TEXT, features TEXT, prompt TEXT, response TEXT, lessons_used TEXT
);
CREATE TABLE IF NOT EXISTS score_checks (
    id INTEGER PRIMARY KEY, run_id INTEGER, predicted INTEGER, actual INTEGER, hand TEXT,
    cards TEXT, jokers TEXT, blind TEXT, exact INTEGER
);
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY, created REAL, text TEXT, tags TEXT, source_run INTEGER,
    uses INTEGER DEFAULT 0, ante_sum REAL DEFAULT 0, active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS decisions_run ON decisions(run_id);
"""


class Logbook:
    def __init__(self, path: str = "data/balatro.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.run_id: Optional[int] = None

    # Runs ---------------------------------------------------------------------

    def start_run(self, deck: str, stake: str, seed: Optional[str], llm: bool) -> int:
        cur = self.db.execute("INSERT INTO runs (started, deck, stake, seed, llm) VALUES (?,?,?,?,?)",
                              (time.time(), deck, stake, seed, int(llm)))
        self.db.commit()
        self.run_id = cur.lastrowid
        return self.run_id

    def end_run(self, ante: int, round_num: int, won: bool, lost_to: str, jokers: List[str],
                seed: Optional[str] = None) -> None:
        self.db.execute(
            "UPDATE runs SET ended=?, ante_reached=?, round_reached=?, won=?, lost_to=?, final_jokers=?, "
            "seed=COALESCE(seed, ?) WHERE id=?",
            (time.time(), ante, round_num, int(won), lost_to, json.dumps(jokers), seed, self.run_id))
        self.db.commit()

    # Decisions ------------------------------------------------------------------

    def decision(self, ante: int, round_num: int, phase: str, action: Dict[str, Any], source: str,
                 features: Optional[List[float]] = None, prompt: str = "", response: str = "",
                 lessons_used: Iterable[int] = ()) -> None:
        self.db.execute(
            "INSERT INTO decisions (run_id, t, ante, round, phase, action, source, features, prompt, response, "
            "lessons_used) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (self.run_id, time.time(), ante, round_num, phase, json.dumps(action), source,
             json.dumps(features) if features is not None else None, prompt, response,
             json.dumps(list(lessons_used))))
        self.db.commit()

    def score_check(self, predicted: int, actual: int, hand: str, cards: List[str], jokers: List[str],
                    blind: str) -> None:
        exact = int(predicted == actual)
        self.db.execute(
            "INSERT INTO score_checks (run_id, predicted, actual, hand, cards, jokers, blind, exact) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (self.run_id, predicted, actual, hand, json.dumps(cards), json.dumps(jokers), blind, exact))
        self.db.commit()

    def run_decisions(self, run_id: int) -> List[sqlite3.Row]:
        return list(self.db.execute("SELECT * FROM decisions WHERE run_id=? ORDER BY id", (run_id,)))

    def finished_runs(self) -> List[sqlite3.Row]:
        return list(self.db.execute("SELECT * FROM runs WHERE ended IS NOT NULL ORDER BY id"))

    # Lessons ----------------------------------------------------------------------

    def add_lesson(self, text: str, tags: List[str], source_run: Optional[int]) -> None:
        self.db.execute("INSERT INTO lessons (created, text, tags, source_run) VALUES (?,?,?,?)",
                        (time.time(), text.strip(), json.dumps([t.lower() for t in tags]), source_run))
        self.db.commit()

    def lessons(self, active_only: bool = True) -> List[sqlite3.Row]:
        q = "SELECT * FROM lessons" + (" WHERE active=1" if active_only else "")
        return list(self.db.execute(q))

    def credit_lessons(self, lesson_ids: Iterable[int], ante_reached: int) -> None:
        for lid in set(lesson_ids):
            self.db.execute("UPDATE lessons SET uses=uses+1, ante_sum=ante_sum+? WHERE id=?", (ante_reached, lid))
        self.db.commit()

    def retire_lesson(self, lesson_id: int) -> None:
        self.db.execute("UPDATE lessons SET active=0 WHERE id=?", (lesson_id,))
        self.db.commit()
