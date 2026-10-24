"""JSON-RPC client for the BalatroBot mod (https://github.com/coder/balatrobot).

Every game action returns the full game state, so callers can chain
``state = client.play([0, 1])`` without a separate ``gamestate()`` call.
"""

from __future__ import annotations

import itertools
import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


class BalatroError(RuntimeError):
    """An error returned by the BalatroBot API (e.g. INVALID_STATE)."""

    def __init__(self, name: str, message: str, method: str):
        super().__init__(f"{method}: {name} - {message}")
        self.name = name
        self.method = method


class BalatroClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 12346, timeout: float = 60.0):
        self.url = f"http://{host}:{port}"
        self.timeout = timeout
        self._ids = itertools.count(1)

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": next(self._ids)}
        if params:
            body["params"] = params
        req = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            reply = json.loads(resp.read())
        if "error" in reply:
            err = reply["error"]
            name = (err.get("data") or {}).get("name", str(err.get("code")))
            raise BalatroError(name, err.get("message", ""), method)
        return reply["result"]

    def wait_until_ready(self, timeout: float = 120.0) -> None:
        deadline = time.time() + timeout
        while True:
            try:
                if self.call("health").get("status") == "ok":
                    return
            except (urllib.error.URLError, ConnectionError, OSError):
                pass
            if time.time() > deadline:
                raise TimeoutError(f"BalatroBot not reachable at {self.url}")
            time.sleep(1.0)

    # Game actions -----------------------------------------------------------

    def gamestate(self) -> Dict[str, Any]:
        return self.call("gamestate")

    def start(self, deck: str = "RED", stake: str = "WHITE", seed: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"deck": deck, "stake": stake}
        if seed:
            params["seed"] = seed
        return self.call("start", params)

    def menu(self) -> Dict[str, Any]:
        return self.call("menu")

    def select(self) -> Dict[str, Any]:
        return self.call("select")

    def skip(self) -> Dict[str, Any]:
        return self.call("skip")

    def play(self, cards: List[int]) -> Dict[str, Any]:
        return self.call("play", {"cards": sorted(cards)})

    def discard(self, cards: List[int]) -> Dict[str, Any]:
        return self.call("discard", {"cards": sorted(cards)})

    def cash_out(self) -> Dict[str, Any]:
        return self.call("cash_out")

    def next_round(self) -> Dict[str, Any]:
        return self.call("next_round")

    def reroll(self) -> Dict[str, Any]:
        return self.call("reroll")

    def buy(self, card: Optional[int] = None, voucher: Optional[int] = None, pack: Optional[int] = None) -> Dict[str, Any]:
        params = {k: v for k, v in (("card", card), ("voucher", voucher), ("pack", pack)) if v is not None}
        return self.call("buy", params)

    def sell(self, joker: Optional[int] = None, consumable: Optional[int] = None) -> Dict[str, Any]:
        params = {k: v for k, v in (("joker", joker), ("consumable", consumable)) if v is not None}
        return self.call("sell", params)

    def use(self, consumable: int, cards: Optional[List[int]] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"consumable": consumable}
        if cards:
            params["cards"] = sorted(cards)
        return self.call("use", params)

    def pack(self, card: Optional[int] = None, targets: Optional[List[int]] = None, skip: bool = False) -> Dict[str, Any]:
        if skip:
            return self.call("pack", {"skip": True})
        params: Dict[str, Any] = {"card": card}
        if targets:
            params["targets"] = sorted(targets)
        return self.call("pack", params)

    def rearrange(self, hand: Optional[List[int]] = None, jokers: Optional[List[int]] = None,
                  consumables: Optional[List[int]] = None) -> Dict[str, Any]:
        for name, order in (("hand", hand), ("jokers", jokers), ("consumables", consumables)):
            if order is not None:
                return self.call("rearrange", {name: order})
        raise ValueError("rearrange needs one of hand/jokers/consumables")
