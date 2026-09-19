# Copyright (c) Facebook, Inc. and its affiliates.
import json
import os
import shutil
import tempfile

import numpy as np
import pytest

from nle import nethack
from nle.env.base import NLE
from nle.scripts import claude_play, game_log
from nle.scripts.nle_daemon import NLEDaemon


def make_obs(t=1, depth=1, hp=13, top="", misc=(0, 0, 0), exp=0, score=0):
    blstats = np.zeros(27, dtype=np.int64)
    blstats[nethack.NLE_BL_EXP] = exp
    blstats[nethack.NLE_BL_SCORE] = score
    blstats[nethack.NLE_BL_TIME] = t
    blstats[nethack.NLE_BL_DEPTH] = depth
    blstats[nethack.NLE_BL_HP] = hp
    blstats[nethack.NLE_BL_HPMAX] = 13
    tty = np.full((24, 80), ord(" "), dtype=np.uint8)
    for i, ch in enumerate(top):
        tty[0, i] = ord(ch)
    return {
        "blstats": blstats,
        "tty_chars": tty,
        "tty_cursor": np.array([0, 0]),
        "misc": np.array(misc),
    }


class FakeDaemon:
    """Just what game_log and claude_play use of an NLEDaemon."""

    def __init__(self, pipe_dir, save_before=False, consumes_save=False, script=()):
        self.pipe_dir = pipe_dir
        self.obs = make_obs()
        self.done = False
        self.info = {}
        self._save = save_before
        self._consumes_save = consumes_save
        self._script = list(script)  # (obs, done) returned by successive step() calls

    def has_save(self):
        return self._save

    def pid(self):
        return 4242

    def reset(self):
        if self._consumes_save:
            self._save = False
        self.obs = make_obs()

    def step(self, action):
        self.obs, self.done = self._script.pop(0)


def lines(path):
    with open(path) as f:
        return [json.loads(line) for line in f]


def keys_of(path):
    return [next(iter(record)) for record in lines(path)]


@pytest.fixture
def dirs():
    pipe_dir = tempfile.mkdtemp(prefix="game_log_pipe_")
    state_dir = tempfile.mkdtemp(prefix="game_log_state_")
    try:
        yield pipe_dir, state_dir
    finally:
        shutil.rmtree(pipe_dir, ignore_errors=True)
        shutil.rmtree(state_dir, ignore_errors=True)


@pytest.fixture
def pipe_dir():
    d = tempfile.mkdtemp(prefix="nle_daemon_test_")
    try:
        yield d
    finally:
        NLEDaemon(d).stop()
        shutil.rmtree(d, ignore_errors=True)


class TestAllocate:
    def test_highest_number_plus_one_survives_a_deleted_middle_file(self, tmp_path):
        (tmp_path / "001_nle_daemon.jsonl").write_text("")  # the placeholder
        first = game_log.allocate_game_file(str(tmp_path))
        second = game_log.allocate_game_file(str(tmp_path))
        third = game_log.allocate_game_file(str(tmp_path))
        assert [os.path.basename(p)[:3] for p in (first, second, third)] == [
            "002",
            "003",
            "004",
        ]

        os.remove(second)  # counting files would now hand out 004 again
        fourth = game_log.allocate_game_file(str(tmp_path))
        assert os.path.basename(fourth)[:3] == "005"
        assert os.path.exists(third) and os.path.getsize(third) == 0

    def test_exclusive_create_fails_instead_of_appending(self, tmp_path, monkeypatch):
        existing = tmp_path / "001_nle_daemon.jsonl"
        existing.write_text("keep\n")
        # The allocator now predicts 001 again, as if two writers raced.
        monkeypatch.setattr(os, "listdir", lambda d: [])
        with pytest.raises(FileExistsError):
            game_log.allocate_game_file(str(tmp_path))
        assert existing.read_text() == "keep\n"


class TestResetGame:
    def test_fresh_game_writes_game_then_session(self, dirs):
        pipe_dir, state_dir = dirs
        d = FakeDaemon(pipe_dir)
        path = game_log.reset_game(d, "mon-hum-neu-mal", state_dir)

        assert keys_of(path) == ["game", "session"]
        game, session = lines(path)
        assert game["game"]["metadata"]["character"] == "mon-hum-neu-mal"
        assert "creation" in game["game"]["metadata"]
        assert session["session"] == {"pid": 4242, "resumed": False, "start_turn": 1}
        assert game_log.current_game(pipe_dir) == path

    def test_resume_appends_one_session_line_and_no_second_game_line(self, dirs):
        pipe_dir, state_dir = dirs
        path = game_log.reset_game(FakeDaemon(pipe_dir), None, state_dir)

        resumed = FakeDaemon(pipe_dir, save_before=True, consumes_save=True)
        assert game_log.reset_game(resumed, None, state_dir) == path

        assert keys_of(path) == ["game", "session", "session"]
        assert lines(path)[-1]["session"]["resumed"] is True
        assert os.listdir(state_dir) == [os.path.basename(path)]

    def test_a_save_that_was_not_consumed_is_a_fresh_game(self, dirs):
        pipe_dir, state_dir = dirs
        first = game_log.reset_game(FakeDaemon(pipe_dir), None, state_dir)

        stuck = FakeDaemon(pipe_dir, save_before=True, consumes_save=False)
        second = game_log.reset_game(stuck, None, state_dir)

        assert second != first
        assert lines(second)[-1]["session"]["resumed"] is False
        assert game_log.current_game(pipe_dir) == second

    def test_resume_with_no_pointer_starts_a_new_file(self, dirs):
        pipe_dir, state_dir = dirs
        d = FakeDaemon(pipe_dir, save_before=True, consumes_save=True)
        path = game_log.reset_game(d, None, state_dir)

        assert keys_of(path) == ["game", "session"]
        session = lines(path)[-1]["session"]
        assert session["resumed"] is True and session["pointer_lost"] is True

    def test_pointer_naming_a_missing_file_counts_as_lost(self, dirs):
        pipe_dir, state_dir = dirs
        with open(os.path.join(pipe_dir, game_log.POINTER), "w") as f:
            f.write(os.path.join(state_dir, "999_gone.jsonl") + "\n")
        assert game_log.current_game(pipe_dir) is None


class TestLogStep:
    def test_logs_line_then_level_and_death_events(self, dirs):
        pipe_dir, state_dir = dirs
        script = [
            (make_obs(t=2, depth=1, top="You hear a door open."), False),
            (make_obs(t=3, depth=2), False),
            (make_obs(t=4, depth=2, hp=0, top="You die..."), True),
        ]
        d = FakeDaemon(pipe_dir, script=script)
        d.info = {"end_status": NLE.StepStatus.DEATH}
        path = game_log.reset_game(d, None, state_dir)

        for action in (12, 13, 14):
            prev = d.obs
            d.step(action)
            game_log.log_step(d, action, prev, state_dir)

        records = lines(path)
        assert [next(iter(r)) for r in records] == [
            "game",
            "session",
            "logs",
            "logs",
            "event",  # level change to Dlvl 2
            "logs",
            "event",  # death
        ]
        first_log = records[2]["logs"]
        assert first_log["t"] == 2 and first_log["msg"] == "You hear a door open."
        assert first_log["action"] == 12 and first_log["prompt"] == 0
        assert records[4]["event"]["kind"] == "level"
        assert records[4]["event"]["dlvl"] == 2 and "screen" in records[4]["event"]
        assert records[6]["event"]["kind"] == "death"
        assert records[6]["event"]["end_status"] == "DEATH"
        assert "cause" not in records[6]["event"]

    def test_logs_line_records_the_goal_metrics(self, dirs):
        pipe_dir, state_dir = dirs
        d = FakeDaemon(pipe_dir, script=[(make_obs(exp=42, score=137), False)])
        path = game_log.reset_game(d, None, state_dir)
        prev = d.obs
        d.step(12)
        game_log.log_step(d, 12, prev, state_dir)
        logs = lines(path)[-1]["logs"]
        assert logs["exp"] == 42 and logs["score"] == 137
        for key in ("dlvl", "xp", "gold", "hpmax", "pwmax", "ac"):
            assert key in logs

    def test_prompt_flag_comes_from_misc(self, dirs):
        pipe_dir, state_dir = dirs
        d = FakeDaemon(
            pipe_dir,
            script=[(make_obs(t=2, top="Really attack the guard? [yn]", misc=(1, 0, 0)), False)],
        )
        path = game_log.reset_game(d, None, state_dir)
        prev = d.obs
        d.step(1)
        game_log.log_step(d, 1, prev, state_dir)
        assert lines(path)[-1]["logs"]["prompt"] == 1

    def test_a_game_with_no_recorded_start_is_attached_to_a_new_file(self, dirs):
        pipe_dir, state_dir = dirs
        d = FakeDaemon(pipe_dir, script=[(make_obs(t=2), False)])
        prev = d.obs
        d.step(1)
        path = game_log.log_step(d, 1, prev, state_dir)

        assert keys_of(path) == ["game", "session", "logs"]
        session = lines(path)[1]["session"]
        assert session["pointer_lost"] is True and session["resumed"] is False
        assert "creation" not in lines(path)[0]["game"]["metadata"]


class TestClaudePlay:
    def test_parse_keys(self):
        codes = [c for _, c in claude_play.parse_keys("~|^x&l`h")]
        assert codes == [27, 13, 24, ord("l") | 0x80, ord("^"), ord("h")]

    def test_a_trailing_modifier_is_an_error(self):
        with pytest.raises(SystemExit):
            claude_play.parse_keys("h^")

    def test_help_prints_usage_and_exits_zero(self, capsys):
        claude_play.main(["--help"])  # returns normally: exit status 0
        assert "Key syntax" in capsys.readouterr().out

    def test_unknown_key_is_rejected_before_any_step(self, dirs):
        pipe_dir, state_dir = dirs
        d = FakeDaemon(pipe_dir)
        with pytest.raises(SystemExit):
            claude_play.run_keys(d, "z", {ord("h"): 0}, state_dir)

    def test_early_stop_is_logged_as_a_violation_note(self, dirs):
        pipe_dir, state_dir = dirs
        script = [
            (make_obs(t=2), False),
            (make_obs(t=3, top="Really attack the guard? [yn]", misc=(1, 0, 0)), False),
        ]
        d = FakeDaemon(pipe_dir, script=script)
        path = game_log.reset_game(d, None, state_dir)
        index = {ord("h"): 0, ord("j"): 1, ord("k"): 2}

        reason = claude_play.run_keys(d, "hjk", index, state_dir)

        assert reason == "[yn] prompt"
        assert keys_of(path) == ["game", "session", "logs", "logs", "event"]
        note = lines(path)[-1]["event"]
        assert note["kind"] == "note" and note["tag"] == "violation"
        assert note["unsent"] == "k"
        assert note["text"] == "stopped after key 2 of 3: [yn] prompt"

    def test_a_full_batch_with_nothing_to_stop_on_logs_no_note(self, dirs):
        pipe_dir, state_dir = dirs
        script = [(make_obs(t=2), False), (make_obs(t=3), False)]
        d = FakeDaemon(pipe_dir, script=script)
        path = game_log.reset_game(d, None, state_dir)

        assert claude_play.run_keys(d, "hj", {ord("h"): 0, ord("j"): 1}, state_dir) is None
        assert keys_of(path) == ["game", "session", "logs", "logs"]


class TestRealDaemon:
    def test_a_resumed_game_appends_to_the_same_file(self, pipe_dir, tmp_path):
        """REQ-004 idempotency with a real save/resume: one game line, one
        session line per start, and a plain stop() begins a new game file."""
        state_dir = str(tmp_path)

        d1 = NLEDaemon(pipe_dir).start()
        path = game_log.reset_game(d1, "mon-hum-neu-mal", state_dir)
        prev = d1.obs
        d1.step(1)
        game_log.log_step(d1, 1, prev, state_dir)
        d1.stop(save=True)
        assert d1.has_save()

        d2 = NLEDaemon(pipe_dir).start()
        assert game_log.reset_game(d2, "mon-hum-neu-mal", state_dir) == path
        assert not d2.has_save()  # the restore consumed it

        assert keys_of(path) == ["game", "session", "logs", "session"]
        sessions = [r["session"] for r in lines(path) if "session" in r]
        assert [s["resumed"] for s in sessions] == [False, True]
        assert sessions[0]["pid"] != sessions[1]["pid"]

        d2.stop()  # no save: the next start is a fresh game
        d3 = NLEDaemon(pipe_dir).start()
        second = game_log.reset_game(d3, "mon-hum-neu-mal", state_dir)
        assert second != path
        assert os.path.basename(second)[:3] == "002"
