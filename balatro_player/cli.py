"""balatro-player command line.

  balatro-player doctor            check the game and the LLM are reachable
  balatro-player play -n 20        play 20 runs (learning as it goes)
  balatro-player report            win rate, antes, solver accuracy, lessons
  balatro-player train             retrain the value model from logged runs
  balatro-player export-finetune   write LoRA training data from the best runs
  balatro-player demo              try the solver offline, no game needed
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from pathlib import Path
from typing import List, Optional

DB = "data/balatro.db"
VALUE_MODEL = "data/value_model.npz"


def _llm(args):
    if args.no_llm:
        return None
    from .strategy.llm import LLM
    llm = LLM(url=args.llm_url, model=args.model)
    if not llm.available():
        print(f"! LLM server not reachable at {args.llm_url}; continuing with heuristics only.\n"
              f"  Start one with: mlx_lm.server --model {args.model} --port 8080", file=sys.stderr)
        return None
    return llm


def cmd_doctor(args) -> int:
    from .client import BalatroClient
    from .strategy.llm import LLM
    ok = True
    try:
        BalatroClient(args.host, args.port, timeout=5).call("health")
        print(f"✓ BalatroBot API at {args.host}:{args.port}")
    except Exception as e:                                                   # noqa: BLE001
        ok = False
        print(f"✗ BalatroBot API at {args.host}:{args.port}: {e}\n  Start it with: uvx balatrobot serve --fast")
    llm = LLM(url=args.llm_url, model=args.model)
    if llm.available():
        try:
            reply = llm.chat("Reply with JSON only.", 'Return {"ok": true}')
            print(f"✓ LLM at {args.llm_url} ({args.model}) replied: {reply.strip()[:60]}")
        except Exception as e:                                               # noqa: BLE001
            ok = False
            print(f"✗ LLM at {args.llm_url} reachable but chat failed: {e}")
    else:
        print(f"✗ LLM not reachable at {args.llm_url} (optional; use --no-llm to play without it)")
    return 0 if ok else 1


def cmd_play(args) -> int:
    from .agent import Agent
    from .client import BalatroClient
    from .journal import Journal
    from .logbook import Logbook
    from .memory import Memory
    from .playlog import PlayLog
    from .recording import Recorder
    from .strategy.strategist import Strategist
    from .value_model import ValueModel

    recorder = None
    if args.record:
        recorder = Recorder(args.recordings, speedup=args.speedup)
        if not recorder.available:
            print("! Recorder not built; run scripts/setup_mac.sh (or swiftc -parse-as-library -O "
                  "tools/recorder.swift -o tools/bin/balatro-recorder). Playing without recording.", file=sys.stderr)
            recorder = None
    journal = None if args.no_journal else Journal(args.journal)

    client = BalatroClient(args.host, args.port)
    client.wait_until_ready()
    logbook = Logbook(args.db)
    llm = _llm(args)
    memory = Memory(logbook)
    vm = ValueModel.load(args.value_model)
    strategist = Strategist(logbook, llm=llm, memory=memory if llm else None, value_model=vm, samples=args.samples)
    playlog = None
    if not args.no_playlog:
        recap = None
        if llm:
            from .strategy import prompts

            def recap(summary):
                data, _ = llm.chat_json(prompts.ANTE_SYSTEM, prompts.ante_prompt(summary))
                return str(data.get("recap") or "") if isinstance(data, dict) else ""
        playlog = PlayLog(args.playlog, speedup=args.speedup if recorder else 0, recap=recap)
    agent = Agent(client, strategist, logbook, memory, llm, deck=args.deck, stake=args.stake, samples=args.samples,
                  recorder=recorder, journal=journal, playlog=playlog)

    seeds: List[Optional[str]] = [None] * args.runs
    if args.seeds:
        listed = [s.strip() for s in Path(args.seeds).read_text().split() if s.strip()]
        seeds = [listed[i % len(listed)] for i in range(args.runs)]

    for i, seed in enumerate(seeds, 1):
        s = agent.play_run(seed)
        result = "WON" if s["won"] else f"lost at ante {s['ante']} to {s['lost_to'] or '?'}"
        video = f" video {s['video']}" if s.get("video") else ""
        print(f"run {i}/{args.runs} seed {s['seed']}: {result} ({s['minutes']} min) jokers {', '.join(s['jokers'])}{video}",
              flush=True)
        if args.train_every and i % args.train_every == 0:
            _train(args.db, args.value_model, quiet=True)
            strategist.vm = ValueModel.load(args.value_model)
    if llm:
        print(f"LLM: {llm.calls} calls, {llm.seconds / max(1, llm.calls):.1f}s average")
    return 0


def _train(db: str, path: str, quiet: bool = False) -> Optional[str]:
    from .logbook import Logbook
    from .value_model import ValueModel, dataset_from_logbook
    X, y, n_runs = dataset_from_logbook(Logbook(db))
    if n_runs < 10 or len(X) < 50:
        msg = f"not enough data yet ({n_runs} runs, {len(X)} decisions); need 10+ runs"
        if not quiet:
            print(msg)
        return None
    vm = ValueModel()
    vm.n_runs = n_runs
    train, val = vm.fit(X, y)
    vm.save(path)
    msg = f"value model trained on {len(X)} decisions from {n_runs} runs: error ±{train ** 0.5:.2f} antes (validation ±{val ** 0.5:.2f})"
    print(msg)
    return msg


def cmd_train(args) -> int:
    _train(args.db, args.value_model)
    return 0


def cmd_report(args) -> int:
    from .logbook import Logbook
    log = Logbook(args.db)
    runs = log.finished_runs()
    if not runs:
        print("No finished runs yet. Play some with: balatro-player play -n 10")
        return 0
    def stats(rs):
        antes = [r["ante_reached"] or 0 for r in rs]
        wins = sum(r["won"] or 0 for r in rs)
        return f"{len(rs)} runs, win rate {wins / len(rs):.0%}, average ante {sum(antes) / len(antes):.2f}"
    print("All:      ", stats(runs))
    if len(runs) > 20:
        print("Last 20:  ", stats(runs[-20:]))
        print("First 20: ", stats(runs[:20]))
    losses = {}
    for r in runs:
        if not r["won"] and r["lost_to"]:
            losses[r["lost_to"]] = losses.get(r["lost_to"], 0) + 1
    if losses:
        print("Lost to:  ", ", ".join(f"{k} x{v}" for k, v in sorted(losses.items(), key=lambda kv: -kv[1])[:6]))
    checks = list(log.db.execute("SELECT predicted, actual, exact, hand, cards, jokers FROM score_checks"))
    if checks:
        exact = sum(c["exact"] for c in checks)
        close = sum(1 for c in checks if c["actual"] and abs(c["predicted"] - c["actual"]) / c["actual"] < 0.05)
        print(f"Engine:    {exact}/{len(checks)} exact score predictions, {close / len(checks):.0%} within 5%")
        bad = [c for c in checks if c["actual"] and abs(c["predicted"] - c["actual"]) / c["actual"] >= 0.05][-5:]
        for c in bad:
            print(f"  off: {c['hand']} predicted {c['predicted']} actual {c['actual']} cards {c['cards']} jokers {c['jokers']}")
    lessons = log.lessons()
    if lessons:
        print(f"Lessons:   {len(lessons)} active")
        top = sorted(lessons, key=lambda r: -(r["ante_sum"] / r["uses"] if r["uses"] else 0))[:5]
        for l in top:
            avg = f"avg ante {l['ante_sum'] / l['uses']:.1f} over {l['uses']} uses" if l["uses"] else "unused"
            print(f"  - {l['text']} ({avg})")
    return 0


def cmd_export(args) -> int:
    """Chat-format JSONL of the LLM's decisions from the best runs, for `mlx_lm.lora`."""
    from .logbook import Logbook
    from .strategy.prompts import SYSTEM
    log = Logbook(args.db)
    runs = [r for r in log.finished_runs() if r["llm"]]
    if len(runs) < 10:
        print(f"Only {len(runs)} LLM runs logged; play more first.")
        return 1
    antes = sorted((r["ante_reached"] or 0) + (r["won"] or 0) for r in runs)
    cutoff = antes[int(len(antes) * (1 - args.top))]
    good = {r["id"] for r in runs if (r["ante_reached"] or 0) + (r["won"] or 0) >= cutoff}
    rows = [d for d in log.db.execute("SELECT run_id, prompt, response FROM decisions WHERE source='llm' "
                                      "AND prompt != '' AND response != ''") if d["run_id"] in good]
    random.Random(0).shuffle(rows)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    n_valid = max(1, len(rows) // 10)
    for name, part in (("valid", rows[:n_valid]), ("train", rows[n_valid:])):
        with open(out / f"{name}.jsonl", "w") as f:
            for d in part:
                f.write(json.dumps({"messages": [{"role": "system", "content": SYSTEM},
                                                 {"role": "user", "content": d["prompt"]},
                                                 {"role": "assistant", "content": d["response"]}]}) + "\n")
    print(f"Wrote {len(rows)} examples from {len(good)} runs (ante >= {cutoff}) to {out}/")
    print(f"Fine-tune with:\n  mlx_lm.lora --model {args.model} --train --data {out} --iters 600 "
          f"--batch-size 2 --adapter-path data/adapters\n"
          f"Then serve with:\n  mlx_lm.server --model {args.model} --adapter-path data/adapters --port 8080")
    return 0


def cmd_demo(args) -> int:
    from .engine.cards import Joker, parse_card
    from .engine.score import RoundContext
    from .solver import decide
    rng = random.Random(args.seed)
    deck = [parse_card(r + s) for r in "23456789TJQKA" for s in "HDCS"]
    rng.shuffle(deck)
    hand, rest = deck[:8], deck[8:]
    jokers = [Joker("j_joker"), Joker("j_droll")]
    print("Hand:   ", " ".join(c.short() for c in hand))
    print("Jokers:  Joker (+4 Mult), Droll Joker (+10 Mult on Flush)")
    d = decide(hand, rest, jokers, RoundContext(), target=600, chips_so_far=0, rng=rng)
    picked = " ".join(hand[i].short() for i in d.cards)
    print(f"Decision: {d.action} {picked}\n  {d.reason}")
    for a in d.alternatives:
        print(f"  option: {a['hand']} {' '.join(hand[i].short() for i in a['cards'])} = {a['score']}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="balatro-player", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=12346)
    p.add_argument("--llm-url", default="http://127.0.0.1:8080/v1")
    p.add_argument("--model", default="mlx-community/Qwen3-30B-A3B-4bit")
    p.add_argument("--db", default=DB)
    p.add_argument("--value-model", default=VALUE_MODEL)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor")
    play = sub.add_parser("play")
    play.add_argument("-n", "--runs", type=int, default=1)
    play.add_argument("--deck", default="RED")
    play.add_argument("--stake", default="WHITE")
    play.add_argument("--seeds", help="file with one seed per line (for fair evaluation)")
    play.add_argument("--no-llm", action="store_true", help="heuristics only")
    play.add_argument("--samples", type=int, default=24, help="Monte Carlo samples per decision")
    play.add_argument("--train-every", type=int, default=25, help="retrain the value model every N runs (0 = never)")
    play.add_argument("--record", action="store_true", help="screen-record every run to --recordings")
    play.add_argument("--recordings", default="recordings", help="folder for run videos")
    play.add_argument("--speedup", type=float, default=0, help="also save an N-times-faster timelapse of each run")
    play.add_argument("--journal", default="journal.txt", help="human-readable diary of the bot's learning")
    play.add_argument("--no-journal", action="store_true")
    play.add_argument("--playlog", default="playlog", help="folder for per-run play-by-play logs (JSONL, for video editing)")
    play.add_argument("--no-playlog", action="store_true")
    sub.add_parser("report")
    sub.add_parser("train")
    exp = sub.add_parser("export-finetune")
    exp.add_argument("--out", default="data/finetune")
    exp.add_argument("--top", type=float, default=0.25, help="fraction of best runs to learn from")
    demo = sub.add_parser("demo")
    demo.add_argument("--seed", type=int, default=7)

    args = p.parse_args(argv)
    if not hasattr(args, "no_llm"):
        args.no_llm = False
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return {"doctor": cmd_doctor, "play": cmd_play, "report": cmd_report, "train": cmd_train,
            "export-finetune": cmd_export, "demo": cmd_demo}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
