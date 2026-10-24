"""End-to-end: the agent plays full runs against the fake game."""

import json

from balatro_player.agent import Agent
from balatro_player.cli import _train
from balatro_player.logbook import Logbook
from balatro_player.memory import Memory
from balatro_player.strategy.strategist import Strategist
from balatro_player.value_model import ValueModel

from .fake_game import FakeClient, FakeLLM


def make_agent(tmp_path, llm=None, seed=0, strength=1.0):
    log = Logbook(str(tmp_path / "test.db"))
    memory = Memory(log)
    strategist = Strategist(log, llm=llm, memory=memory if llm else None, samples=4)
    client = FakeClient(seed=seed, strength=strength)
    return Agent(client, strategist, log, memory, llm, samples=4), log


def test_heuristic_runs_complete(tmp_path):
    agent, log = make_agent(tmp_path, strength=0.5)
    for _ in range(2):
        s = agent.play_run()
        assert s["ante"] >= 2                         # clears ante 1 on easy blinds
    runs = log.finished_runs()
    assert len(runs) == 2
    phases = {r["phase"] for r in log.db.execute("SELECT phase FROM decisions")}
    assert {"hand", "shop", "blind"} <= phases
    checks = list(log.db.execute("SELECT exact FROM score_checks"))
    assert checks and all(c["exact"] for c in checks)    # fake game scores with the same engine


def test_bot_buys_jokers(tmp_path):
    agent, log = make_agent(tmp_path, strength=0.5, seed=3)
    s = agent.play_run()
    assert s["jokers"], "expected the bot to buy at least one joker"


def test_llm_strategist_and_reflection(tmp_path):
    llm = FakeLLM()
    agent, log = make_agent(tmp_path, llm=llm, strength=0.5)
    agent.play_run()
    sources = {r["source"] for r in log.db.execute("SELECT source FROM decisions")}
    assert "llm" in sources
    lessons = log.lessons()
    assert len(lessons) == 1 and "Mercury" in lessons[0]["text"]
    # Second run recalls the lesson and credits it.
    agent.play_run()
    assert log.lessons()[0]["uses"] >= 1


def test_value_model_trains_from_runs(tmp_path):
    agent, log = make_agent(tmp_path, strength=0.6)
    for i in range(10):
        agent.client.rng.seed(i)
        agent.play_run()
    msg = _train(str(tmp_path / "test.db"), str(tmp_path / "vm.npz"), quiet=True)
    assert msg and "trained" in msg
    vm = ValueModel.load(str(tmp_path / "vm.npz"))
    assert vm.ready and vm.n_runs == 10
    feats = json.loads(next(iter(log.db.execute("SELECT features FROM decisions WHERE features IS NOT NULL")))[0])
    assert 0 < vm.predict(feats) < 12
