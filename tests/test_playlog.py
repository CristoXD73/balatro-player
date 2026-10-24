import json

from balatro_player.playlog import PlayLog, mood_from_pressure, play_mood

from .fake_game import FakeClient, FakeLLM
from .test_agent import make_agent


def test_moods():
    assert play_mood(1000, 400, 3) == "cocky"
    assert play_mood(500, 400, 3) == "confident"
    assert play_mood(100, 1000, 1) == "desperate"
    assert mood_from_pressure(900, 1000) == "nervous"


def test_playlog_covers_a_whole_run(tmp_path):
    llm = FakeLLM()
    agent, log = make_agent(tmp_path, llm=llm, strength=0.6)
    recap = lambda s: llm.chat_json("", "ANTE SUMMARY")[0]["recap"]
    agent.playlog = PlayLog(str(tmp_path / "playlog"), speedup=8, recap=recap, seed=0)
    agent.play_run()
    events = [json.loads(l) for l in (tmp_path / "playlog" / "run_0001.jsonl").read_text().splitlines()]
    kinds = {e["event"] for e in events}
    assert {"run_start", "blind_select", "play", "blind_cleared", "ante_summary", "run_end"} <= kinds
    assert events[0]["event"] == "run_start" and events[-1]["event"] == "run_end"
    assert [e["seq"] for e in events] == list(range(len(events)))
    times = [e["video_t"] for e in events]
    assert times == sorted(times)
    for e in events:
        assert "timelapse_t" in e and 0 <= e["highlight"] <= 1 and isinstance(e["drama"], list)
    plays = [e for e in events if e["event"] == "play"]
    assert all(e["mood"] and e["inner_voice"] for e in plays)
    assert all(e["predicted"] == e["actual"] for e in plays if e.get("actual") is not None)
    summaries = [e for e in events if e["event"] == "ante_summary"]
    assert summaries and all("sweating" in s["recap"] for s in summaries)
    assert events[-1]["result"] in ("won", "lost")
    assert "run_lost" in events[-1]["drama"] or "run_won" in events[-1]["drama"]
