"""Slow learning loop: a small value network trained on the bot's own runs.

It learns to predict how far a run will get (final ante) from the state at a
decision. In the shop, the strategist compares the predicted outcome *after*
each option with the outcome now, which corrects the hand-written heuristics
with real experience. It's a tiny MLP in numpy: training on thousands of
decisions takes seconds on any Mac.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np

STAKES = ["WHITE", "RED", "GREEN", "BLACK", "BLUE", "PURPLE", "ORANGE", "GOLD"]
XMULT_KEYS = {"j_duo", "j_trio", "j_family", "j_order", "j_tribe", "j_cavendish", "j_card_sharp",
              "j_acrobat", "j_blackboard", "j_flower_pot", "j_seeing_double", "j_baron", "j_triboulet",
              "j_photograph", "j_ancient", "j_idol", "j_bloodstone", "j_loyalty_card", "j_drivers_license"}
FEATURE_NAMES = ["ante", "round", "money", "log_ratio", "jokers", "free_slots", "hand_levels",
                 "deck_size", "enhanced", "consumables", "xmult_jokers", "stake"]


def features(ante: int, round_num: int, money: int, ratio: float, joker_keys: Sequence[str],
             joker_slots: int, total_levels: int, deck_size: int, enhanced: int, consumables: int,
             stake: str) -> List[float]:
    return [
        ante / 8, round_num / 24, min(money, 100) / 50, math.log2(max(ratio, 1e-3)) / 4,
        len(joker_keys) / 5, max(0, joker_slots - len(joker_keys)) / 2, total_levels / 24,
        deck_size / 52, enhanced / 10, consumables / 2,
        sum(1 for k in joker_keys if k in XMULT_KEYS) / 3,
        STAKES.index(stake) / 7 if stake in STAKES else 0.0,
    ]


def view_features(view, ratio: float, money: Optional[int] = None, joker_keys: Optional[List[str]] = None) -> List[float]:
    raw_hands = view.raw.get("hands") or {}
    levels = sum(int(h.get("level") or 1) for h in raw_hands.values()) if raw_hands else 12
    deck = view.deck + view.hand
    return features(
        ante=view.ante, round_num=view.round_num,
        money=view.money if money is None else money, ratio=ratio,
        joker_keys=[j.key for j in view.jokers] if joker_keys is None else joker_keys,
        joker_slots=view.joker_slots, total_levels=levels, deck_size=len(deck),
        enhanced=sum(1 for c in deck if c.enhancement), consumables=len(view.consumables),
        stake=view.raw.get("stake") or "WHITE",
    )


class ValueModel:
    def __init__(self, hidden: int = 32, seed: int = 0):
        self.hidden = hidden
        self.rng = np.random.default_rng(seed)
        self.params: Optional[dict] = None
        self.n_runs = 0

    @property
    def ready(self) -> bool:
        return self.params is not None

    def _init(self, n_in: int) -> None:
        h = self.hidden
        r = self.rng
        self.params = {
            "W1": r.normal(0, 1 / math.sqrt(n_in), (n_in, h)), "b1": np.zeros(h),
            "W2": r.normal(0, 1 / math.sqrt(h), (h, h)), "b2": np.zeros(h),
            "W3": r.normal(0, 1 / math.sqrt(h), (h, 1)), "b3": np.zeros(1),
        }

    def _forward(self, X: np.ndarray):
        # numpy's Accelerate BLAS on macOS can raise spurious FP warnings in matmul.
        with np.errstate(all="ignore"):
            return self._forward_raw(X)

    def _forward_raw(self, X: np.ndarray):
        p = self.params
        z1 = X @ p["W1"] + p["b1"]
        a1 = np.maximum(z1, 0)
        z2 = a1 @ p["W2"] + p["b2"]
        a2 = np.maximum(z2, 0)
        out = a2 @ p["W3"] + p["b3"]
        return out[:, 0], (X, z1, a1, z2, a2)

    def predict(self, x: Sequence[float]) -> float:
        """Predicted final ante (roughly 1-9; 9 means a win)."""
        if not self.ready:
            return 0.0
        y, _ = self._forward(np.asarray([x], dtype=float))
        return float(y[0] * 8)

    def fit(self, X: np.ndarray, y: np.ndarray, epochs: int = 300, lr: float = 3e-3,
            batch: int = 128, l2: float = 1e-4, val_frac: float = 0.15) -> Tuple[float, float]:
        """Train with Adam on (features, final_ante / 8). Returns (train MSE, validation MSE) in antes^2."""
        n = len(X)
        idx = self.rng.permutation(n)
        n_val = max(1, int(n * val_frac)) if n >= 20 else 0
        val, tr = idx[:n_val], idx[n_val:]
        self._init(X.shape[1])
        p = self.params
        m = {k: np.zeros_like(v) for k, v in p.items()}
        v = {k: np.zeros_like(v) for k, v in p.items()}
        best = (float("inf"), None)
        step = 0
        for _ in range(epochs):
            order = self.rng.permutation(tr)
            for s in range(0, len(order), batch):
                b = order[s:s + batch]
                pred, (Xb, z1, a1, z2, a2) = self._forward(X[b])
                g = 2 * (pred - y[b])[:, None] / len(b)
                with np.errstate(all="ignore"):
                    grads = {"W3": a2.T @ g, "b3": g.sum(0)}
                    d2 = (g @ p["W3"].T) * (z2 > 0)
                    grads.update({"W2": a1.T @ d2, "b2": d2.sum(0)})
                    d1 = (d2 @ p["W2"].T) * (z1 > 0)
                    grads.update({"W1": Xb.T @ d1, "b1": d1.sum(0)})
                step += 1
                for k in p:
                    gk = grads[k] + l2 * p[k]
                    m[k] = 0.9 * m[k] + 0.1 * gk
                    v[k] = 0.999 * v[k] + 0.001 * gk * gk
                    mh, vh = m[k] / (1 - 0.9 ** step), v[k] / (1 - 0.999 ** step)
                    p[k] = p[k] - lr * mh / (np.sqrt(vh) + 1e-8)
            if n_val:
                vl = float(np.mean((self._forward(X[val])[0] - y[val]) ** 2))
                if vl < best[0]:
                    best = (vl, {k: w.copy() for k, w in p.items()})
        if best[1] is not None:
            self.params = best[1]
        if not all(np.isfinite(w).all() for w in self.params.values()):
            raise FloatingPointError("value model training diverged")
        train_mse = float(np.mean((self._forward(X[tr])[0] - y[tr]) ** 2)) * 64
        return train_mse, (best[0] * 64 if n_val else float("nan"))

    def save(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        np.savez(path, n_runs=self.n_runs, **self.params)

    @classmethod
    def load(cls, path: str) -> "ValueModel":
        vm = cls()
        if Path(path).exists():
            data = np.load(path)
            vm.params = {k: data[k] for k in ("W1", "b1", "W2", "b2", "W3", "b3")}
            vm.n_runs = int(data["n_runs"])
        return vm


def dataset_from_logbook(log) -> Tuple[np.ndarray, np.ndarray, int]:
    """Every decision with features, labelled with its run's final ante (+1 for a win)."""
    X, y = [], []
    runs = {r["id"]: r for r in log.finished_runs()}
    for row in log.db.execute("SELECT run_id, features FROM decisions WHERE features IS NOT NULL"):
        run = runs.get(row["run_id"])
        if run is None:
            continue
        label = (run["ante_reached"] or 0) + (1 if run["won"] else 0)
        X.append(json.loads(row["features"]))
        y.append(label / 8)
    return np.asarray(X, dtype=float), np.asarray(y, dtype=float), len(runs)
