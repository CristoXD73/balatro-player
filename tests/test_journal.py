import os
import stat
import sys
import textwrap

from balatro_player.journal import Journal, humanize_reason, nice_key
from balatro_player.recording import Recorder

from .test_agent import make_agent
from .fake_game import FakeLLM


def test_journal_tells_the_story(tmp_path):
    llm = FakeLLM()
    agent, log = make_agent(tmp_path, llm=llm, strength=0.5)
    agent.journal = Journal(str(tmp_path / "journal.txt"), chapter_every=2)
    agent.play_run()
    agent.play_run()
    text = (tmp_path / "journal.txt").read_text()
    assert "BALATRO JOURNAL" in text
    assert "Run #1" in text and "Run #2" in text
    assert "★ My very first run." in text
    assert "Diary" in text and "all in on pairs" in text
    assert "What I learned" in text and "Mercury" in text
    assert "CHAPTER 1: LOOKING BACK" in text and "panic-rerolling" in text
    assert "Average Ante" in text or "average Ante" in text


def test_journal_without_llm_uses_plain_diary(tmp_path):
    agent, log = make_agent(tmp_path, strength=0.5)
    agent.journal = Journal(str(tmp_path / "journal.txt"))
    agent.play_run()
    text = (tmp_path / "journal.txt").read_text()
    assert "Diary" in text and ("I made it to Ante" in text or "I won" in text)


def test_milestones(tmp_path):
    from balatro_player.logbook import Logbook
    log = Logbook(str(tmp_path / "m.db"))
    for ante, won in ((2, False), (4, False), (3, False), (8, True)):
        log.start_run("RED", "WHITE", None, False)
        log.end_run(ante, ante * 3, won, "", [])
    assert Journal.milestones(log, 2) == ["New personal best: reached Ante 4 for the first time."]
    assert Journal.milestones(log, 3) == []
    assert Journal.milestones(log, 4) == ["I BEAT THE GAME. First win ever."]


def test_humanized_words():
    assert nice_key("j_ride_the_bus") == "Ride The Bus"
    assert nice_key("p_buffoon_normal_1") == "Buffoon Pack"
    assert "2.0x stronger" in humanize_reason("power x2.00, econ 0.00, bonus 0.00")


STUB = textwrap.dedent('''\
    #!{python}
    import shutil, signal, sys, time
    cmd = sys.argv[1]
    if cmd == "record":
        out = sys.argv[2]
        stop = []
        signal.signal(signal.SIGINT, lambda *a: stop.append(1))
        print("recording", flush=True)
        while not stop:
            time.sleep(0.05)
        open(out, "w").write("video")
    elif cmd == "speedup":
        shutil.copy(sys.argv[2], sys.argv[3])
''')


def test_recorder_names_and_timelapse(tmp_path):
    stub = tmp_path / "rec"
    stub.write_text(STUB.format(python=sys.executable))
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    r = Recorder(str(tmp_path / "videos"), speedup=8, binary=str(stub))
    assert r.start(7) is not None
    final = r.stop("ante5_lost")
    assert final.name == "run_0007_ante5_lost.mov" and final.exists()
    assert (tmp_path / "videos" / "run_0007_ante5_lost_x8.mov").exists()
