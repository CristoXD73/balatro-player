# Play log format

`playlog/run_NNNN.jsonl`: one file per run, one JSON object per line, in the order things happened.
It's written for an AI video editor: give it the play logs, `journal.txt` and the videos, and ask it to cut a
"zero to hero" video.

## Fields on every event

| Field | Meaning |
|---|---|
| `run` | Run number. It matches `recordings/run_NNNN_*.mov` and `Run #N` in `journal.txt`. |
| `seq` | Order of the event within the run. |
| `event` | The event type (see below). |
| `video_t` | Seconds from the start of this run's video (`recordings/run_NNNN_….mov`). |
| `timelapse_t` | Seconds into the timelapse copy (`…_xN.mov`), present when `--speedup` is used. |
| `highlight` | 0–1: how worth keeping this moment is. 0.1 is routine; 0.6+ is a highlight; 0.9+ is a must-show. |
| `drama` | Tags for the story (see below). |
| `inner_voice` | A one-line first-person thought, usable as a caption or voice-over. |
| `mood` | On plays and discards: `cocky`, `confident`, `focused`, `nervous` or `desperate`. It comes from the real pressure: what the bot can likely score against what it still needs. |

## Events

| `event` | When | Notable fields |
|---|---|---|
| `run_start` | The run begins | `deck`, `stake`, `seed`, `llm` |
| `blind_select` / `blind_skip` | It chooses to play or skip a blind | `blind`, `blind_type` (SMALL, BIG or BOSS), `target`, `tag`, `reason` |
| `discard` | It throws cards away | `hand_cards`, `discarded`, `plan` (e.g. "chase spades flush"), `expected_next`, `best_now`, `hands_left`, `discards_left` |
| `play` | It plays a hand | `hand_cards`, `played`, `hand_type`, `predicted`, `actual`, `chips_before`, `chips_after`, `target`, `cleared`, `considered` (the other plays it weighed) |
| `use_consumable` | It uses a Tarot, Planet or Spectral card | `card`, `targets` |
| `blind_cleared` | It beat the blind and cashed out | `scored`, `target`, `margin` (scored / target), `hands_used`, `earned`, `money` |
| `shop_buy`, `shop_buy_pack`, `shop_buy_voucher`, `shop_sell`, `shop_reroll`, `shop_use_consumable` | A shop action | `item`, `item_name`, `money_before`, `money_after`, `reason`, `decided_by` (llm or engine), `temperament` (decisive, thrifty or greedy) |
| `pack_open` | It picks from a booster pack | `options`, `picked`, `picked_name` |
| `ante_summary` | After each ante (boss beaten, or the run ended) | `recap` (1–2 sentences of first-person voice-over), `outcome`, `blinds` (per blind: target, scored, hands used, earned), `best_hand`, `bought`, `sold`, `rerolls`, `money_start`, `money_end`, `jokers`, `mood_arc`, `dominant_mood`, `key_moments` (the seq and video_t of its highlights) |
| `run_end` | The run is over | `result` (won or lost), `ante`, `lost_to`, `jokers`, `diary` (the journal entry), `milestones`, `video`, `timelapse` |

## Drama tags

| Tag | Meaning |
|---|---|
| `one_shot` | Beat the blind with the first hand |
| `clutch` | Beat the blind on the last hand |
| `overkill` | Scored 4x or more what it needed |
| `close_call` | Beat a blind by less than 10% |
| `near_miss` | Lost the run with at least 80% of the target |
| `run_lost` / `run_won` | The end of the run |
| `rare_hand` | Straight Flush, Four of a Kind, Five of a Kind, Flush House or Flush Five |
| `personal_best_hand` | Its highest-scoring hand so far in this session, by 25% or more |
| `boss_intro` / `boss_beaten` | A boss blind starts, or is beaten |
| `risky_skip` | Skipped a blind for a tag |
| `gamble` | A desperate discard |

## Suggested editing approach

1. Read `journal.txt` for the arc across runs: milestones (★ lines), diary entries and chapter retrospectives.
2. Pick the runs that mark turning points: the first run, the first new personal bests, the first boss beaten at
   each ante, the losses that taught a lesson, and the first win.
3. Within each chosen run, cut to events with `highlight >= 0.6` using `video_t`, or `timelapse_t` for montages.
   Use `inner_voice` for captions, and the `ante_summary.recap` and `run_end.diary` lines for narration.
4. Show the improvement with each run's `ante` from `run_end`, and the progress line under each journal entry.
