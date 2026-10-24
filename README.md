# balatro-player

A local AI that plays [Balatro](https://www.playbalatro.com/), entirely on your Mac. It knows the rules from the
first run, plays hands well right away, and gets better at building runs the more it plays.

Designed for an Apple silicon Mac mini with 32 GB of memory. No cloud APIs.

## How it works

Balatro is really two games, so the bot has two brains:

- **Playing hands is exact maths.** An exact scoring engine plus a solver handle it: no AI needed, and it's
  instant.
- **Building a run is long-term strategy.** Shop, jokers, packs and skips are handled by a local LLM, with numbers
  from the engine and lessons from past runs.

```
 Balatro (Steam) + BalatroBot mod ── JSON-RPC ──┐
                                                ▼
                                    ┌──────────────────────┐
                                    │   agent (game loop)  │──► data/balatro.db
                                    └──┬────────────────┬──┘     (every decision)
                  play / discard       │                │  shop / packs / blinds
                                       ▼                ▼
                         ┌───────────────────┐  ┌─────────────────────────────┐
                         │ scoring engine    │◄─│ strategist                  │
                         │ + hand solver     │  │  heuristics (engine-scored) │
                         └───────────────────┘  │  + value model (learned)    │
                                                │  + lessons (learned)        │
                                                │  + local LLM (Qwen3 on MLX) │
                                                └─────────────────────────────┘
```

**Scoring engine** ([`engine/`](balatro_player/engine)). The engine reimplements Balatro's scoring pipeline:
- poker hands, including Stone and Wild cards, and the Four Fingers, Shortcut, Smeared and Splash jokers;
- enhancements, editions and seals, plus retriggers (Red Seal, Hack, Sock and Buskin, Hanging Chad, Dusk, Mime);
- held-in-hand effects;
- 100 jokers, including Blueprint and Brainstorm copies and joker order.

Scaling jokers read their live value from the game ("Currently X2.5 Mult"), so they stay accurate as they grow. After
every hand the bot compares its predicted score with the real one and logs any mismatch. `balatro-player report`
shows the engine's accuracy and the hands it got wrong, so the engine converges on the real rules.

**Hand solver** ([`solver.py`](balatro_player/solver.py)).
- **Plays:** it tries every play of up to five cards and scores each exactly. If a play clears the blind, it takes
  it. It respects boss rules too: The Psychic, The Eye, The Mouth and The Flint.
- **Discards:** it compares discard plans (keep the best hand, chase a flush, chase a straight, keep pairs) with a
  Monte Carlo simulation. Each plan is tested by drawing from the real remaining deck many times.

**Strategist** ([`strategy/`](balatro_player/strategy)). For each shop, pack and blind decision:
1. **Heuristics, scored by the engine.** The engine measures each option: "buying Blackboard multiplies your average
   best hand by x2.4", tested on the same sampled hands, plus economy and utility values.
2. **The value model** adjusts those scores once it has learned from enough runs.
3. **The local LLM** sees the state, the scored options, a baseline plan and relevant lessons, and makes the call.
4. **Validation:** every LLM step is checked against the game. If anything is invalid, the bot falls back to the
   heuristics.

### How it learns

| Loop | When | What |
|---|---|---|
| Lessons | after every run | The LLM reviews the run and writes 1–3 tagged lessons. Matching lessons are recalled into future prompts. Each lesson tracks how runs went when it was used; ones linked to bad runs are retired. |
| Value model | every 25 runs (automatic) | A small neural network learns to predict how far a run gets from its state. It then corrects the shop choices with real experience. |
| Fine-tuning | when you choose | `export-finetune` turns the LLM's decisions from your best runs into training data for a LoRA fine-tune with `mlx_lm.lora` (expert iteration). |

## Setup (one time)

You need Balatro on Steam, an Apple silicon Mac and about 25 GB of free disk.

```bash
git clone https://github.com/CristoXD73/balatro-player.git
cd balatro-player
./scripts/setup_mac.sh
```

The script does the following:
- installs [uv](https://docs.astral.sh/uv/);
- installs [Lovely Injector](https://github.com/ethangreen-dev/lovely-injector), [Steamodded](https://github.com/Steamodded/smods) and the [BalatroBot](https://github.com/coder/balatrobot) mod;
- creates the Python environment;
- downloads the LLM;
- runs the tests.

## Play

```bash
./scripts/start.sh -n 50        # start everything and play 50 runs
```

Or run each part in its own terminal:

```bash
.venv/bin/mlx_lm.server --model mlx-community/Qwen3-30B-A3B-4bit --port 8080
uvx balatrobot serve --fast                # or --headless --fast to skip rendering
.venv/bin/balatro-player play -n 50
```

| Command | What it does |
|---|---|
| `balatro-player doctor` | Checks that the game API and the LLM are reachable |
| `balatro-player play -n 20 [--deck RED --stake WHITE]` | Plays runs (learning as it goes) |
| `balatro-player play -n 30 --seeds seeds/eval.txt` | Plays fixed seeds, for fair before/after comparisons |
| `balatro-player play --no-llm` | Heuristics only: faster, and the baseline to beat |
| `balatro-player report` | Shows win rate, antes, what it loses to, engine accuracy and top lessons |
| `balatro-player train` | Retrains the value model now |
| `balatro-player export-finetune` | Writes LoRA data from the best runs and prints the `mlx_lm.lora` command |
| `balatro-player demo` | Shows the solver on a random hand, with no game needed |

### Memory budget (32 GB)

| | |
|---|---|
| macOS + Balatro | ~5 GB |
| Qwen3-30B-A3B 4-bit (mixture of experts, ~3B active, fast) | ~17 GB |
| Bot, engine, value model | < 1 GB |

On a 16 GB Mac, use a smaller model. Pass it to the setup script, and set `BALATRO_MODEL` when starting:

```bash
./scripts/setup_mac.sh --model mlx-community/Qwen3-8B-4bit
BALATRO_MODEL=mlx-community/Qwen3-8B-4bit ./scripts/start.sh -n 20
```

## Measuring progress

```bash
balatro-player play -n 30 --seeds seeds/eval.txt --no-llm   # baseline
balatro-player play -n 30 --seeds seeds/eval.txt            # full bot
balatro-player report
```

Run the seeded evaluation again every few hundred runs. Lessons and the value model accumulate in `data/`. Keep that
folder to keep what the bot has learned.

## Limitations and next steps

- **Engine coverage:** some rare joker interactions are approximated, and random effects use expected values.
  `report` lists every hand the engine mispredicted; fix those in `engine/jokers.py`.
- **Boss blinds:** boss effects that change the rules mid-hand (The Hook, The Serpent, The Ox, ...) aren't modelled
  in the solver yet. Debuffs and face-down cards come from the game state and are handled.
- **Consumables:** the bot uses Tarot and Spectral cards with simple targeting rules. It could do better.
- **Tests:** they run against a fake game that has the same API with simplified rules (`tests/fake_game.py`).

## Credits

- Game control: [BalatroBot](https://github.com/coder/balatrobot) (MIT).
- Mod loading: [Lovely Injector](https://github.com/ethangreen-dev/lovely-injector) and
  [Steamodded](https://github.com/Steamodded/smods).
- Balatro is by LocalThunk and published by Playstack. This project is not affiliated with them; you need your own
  copy of the game.
