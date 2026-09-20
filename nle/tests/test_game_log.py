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


def make_obs(t=1, depth=1, hp=13, top="", misc=(0, 0, 0), exp=0, score=0, dnum=0):
    blstats = np.zeros(27, dtype=np.int64)
    blstats[nethack.NLE_BL_EXP] = exp
    blstats[nethack.NLE_BL_SCORE] = score
    blstats[nethack.NLE_BL_DNUM] = dnum
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
        "tty_colors": np.zeros((24, 80), dtype=np.int8),
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


@pytest.fixture(autouse=True)
def view_in_tmp(tmp_path, monkeypatch):
    """Keep tests from writing the real game_state/game_view.html."""
    monkeypatch.setattr(claude_play, "VIEW_PATH", str(tmp_path / "game_view.html"))


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
        assert [r for r in lines(second) if "session" in r][-1]["session"]["resumed"] is False
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


class TestAbandonedGame:
    """REQ-018: a fresh game while the previous one never ended in death."""

    def test_a_fresh_game_after_an_unfinished_one_writes_a_violation_note(self, dirs):
        pipe_dir, state_dir = dirs
        first = game_log.reset_game(FakeDaemon(pipe_dir), None, state_dir)
        second = game_log.reset_game(FakeDaemon(pipe_dir), None, state_dir)

        note = lines(second)[-1]["event"]
        assert note["kind"] == "note" and note["tag"] == "violation" and note["gate"] == "G12"
        assert note["previous"] == os.path.basename(first)[: -len(".jsonl")]

    def test_no_note_after_a_game_that_ended_in_death(self, dirs):
        pipe_dir, state_dir = dirs
        first = game_log.reset_game(FakeDaemon(pipe_dir), None, state_dir)
        game_log.append(first, "event", {"kind": "death", "t": 9, "dlvl": 1})
        second = game_log.reset_game(FakeDaemon(pipe_dir), None, state_dir)
        assert keys_of(second) == ["game", "session"]

    def test_a_resume_is_not_an_abandoned_game(self, dirs):
        pipe_dir, state_dir = dirs
        path = game_log.reset_game(FakeDaemon(pipe_dir), None, state_dir)
        resumed = FakeDaemon(pipe_dir, save_before=True, consumes_save=True)
        assert game_log.reset_game(resumed, None, state_dir) == path
        assert keys_of(path) == ["game", "session", "session"]


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

    def test_logs_line_records_the_dungeon_number(self, dirs):
        pipe_dir, state_dir = dirs
        d = FakeDaemon(pipe_dir, script=[(make_obs(dnum=2), False)])
        path = game_log.reset_game(d, None, state_dir)
        prev = d.obs
        d.step(12)
        game_log.log_step(d, 12, prev, state_dir)
        assert lines(path)[-1]["logs"]["dnum"] == 2

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


class TestWriteView:
    def test_colors_and_escapes(self, tmp_path):
        obs = make_obs(top="a<b")
        obs["tty_colors"][0, 0] = 1  # red
        path = str(tmp_path / "v.html")
        game_log.write_view(obs, path)
        page = open(path).read()
        assert '<span style="color:#cd0000">a</span>' in page
        assert "&lt;" in page

    def test_a_second_write_replaces_the_page(self, tmp_path):
        path = str(tmp_path / "v.html")
        game_log.write_view(make_obs(top="first"), path)
        game_log.write_view(make_obs(top="second"), path)
        page = open(path).read()
        assert "second" in page and "first" not in page and page.count("<pre>") == 1

    def test_a_failed_rename_leaves_no_temp_file(self, tmp_path, monkeypatch):
        def boom(*args):
            raise OSError("rename failed")

        monkeypatch.setattr(os, "replace", boom)
        game_log.write_view(make_obs(), str(tmp_path / "v.html"))
        assert os.listdir(tmp_path) == []

    def test_a_failed_write_leaves_the_player_output_unchanged(self, tmp_path, capsys, monkeypatch):
        d = FakeDaemon(str(tmp_path))
        monkeypatch.setattr(claude_play, "VIEW_PATH", str(tmp_path / "no" / "such" / "v.html"))
        claude_play.print_screen(d)
        with_failure = capsys.readouterr().out
        monkeypatch.setattr(claude_play, "VIEW_PATH", str(tmp_path / "v.html"))
        claude_play.print_screen(d)
        assert capsys.readouterr().out == with_failure
        assert os.path.exists(tmp_path / "v.html")


def gate_obs(monsters, hero=(5, 5)):
    """An observation with the hero at (y, x) and {(y, x): description} monsters."""
    obs = make_obs()
    obs["blstats"][nethack.NLE_BL_Y], obs["blstats"][nethack.NLE_BL_X] = hero
    obs["glyphs"] = np.full((21, 79), 2359, dtype=np.int32)  # a non-monster glyph
    obs["screen_descriptions"] = np.zeros((21, 79, 80), dtype=np.uint8)
    for (y, x), text in {hero: "human monk called Agent", **monsters}.items():
        obs["glyphs"][y, x] = nethack.GLYPH_MON_OFF
        obs["screen_descriptions"][y, x, : len(text)] = list(text.encode())
    return obs


def status_obs(monsters, status):
    """gate_obs with `status` on the bottom screen line."""
    obs = gate_obs(monsters)
    for i, ch in enumerate(status):
        obs["tty_chars"][23, i] = ord(ch)
    return obs


class TestGates:
    def gate(self, keys, monsters):
        return claude_play.check_batch(claude_play.parse_keys(keys), gate_obs(monsters))

    def test_g2_batch_next_to_a_peaceful(self):
        assert self.gate("sy", {(5, 7): "peaceful watchman"})[0] == "G2"

    def test_g2_allows_one_key_a_far_peaceful_and_a_pet(self):
        assert self.gate("s", {(5, 6): "peaceful watchman"}) is None
        assert self.gate("hh", {(5, 8): "peaceful watchman"}) is None
        assert self.gate("hh", {(5, 6): "tame little dog"}) is None

    def test_g10_refuses_a_quit(self):
        """&q is meta-q: NetHack's quit, which ends the game and is recorded as a death."""
        assert self.gate("&q", {})[0] == "G10"
        assert self.gate("h&q", {})[0] == "G10"
        assert self.gate("q", {}) is None  # quaff
        assert self.gate("&l", {}) is None  # #loot

    def test_g4_rest_with_a_hostile_in_view(self):
        assert self.gate("5s", {(1, 1): "jackal"})[0] == "G4"
        assert self.gate("s", {(1, 1): "peaceful watchman"}) is None

    def test_g4_only_counts_a_batch_of_rest_keys(self):
        """Game 002, t=1473: the travel call `_<.` was refused as a rest. A `.`
        or `s` that confirms or answers a prompt is not a rest."""
        jackal = {(1, 1): "jackal"}
        assert self.gate("_<.", jackal) is None
        assert self.gate(";l.", jackal) is None  # a farlook confirm
        assert self.gate("ws", jackal) is None  # an item letter
        assert self.gate("5s", jackal)[0] == "G4"
        assert self.gate(".", jackal)[0] == "G4"

    def test_g4_names_the_nearest_hostile_and_its_distance(self):
        message = self.gate("5s", {(1, 1): "jackal", (2, 3): "newt"})[1]
        assert "newt" in message and "3 away" in message

    def test_g5_rest_while_hungry_weak_or_fainting(self):
        for status in ("Hungry", "Weak", "Fainting"):
            assert claude_play.check_batch(claude_play.parse_keys("5s"), status_obs({}, status))[0] == "G5"
        assert claude_play.check_batch(claude_play.parse_keys("h"), status_obs({}, "Hungry")) is None
        assert self.gate("5s", {}) is None

    def test_g8_batch_with_a_hostile_adjacent(self):
        assert self.gate("hh", {(5, 4): "jackal"})[0] == "G8"
        assert self.gate("h", {(5, 4): "jackal"}) is None  # one key per call is the point
        assert self.gate("hh", {(5, 3): "jackal"}) is None  # 2 squares away
        assert self.gate("hh", {(5, 4): "tame little dog"}) is None
        assert self.gate("hh", {(5, 4): "peaceful watchman"})[0] == "G2"
        assert self.gate("Fh", {(5, 4): "jackal"}) is None  # F plus a direction is one attack
        assert self.gate("FhFh", {(5, 4): "jackal"})[0] == "G8"

    def test_g8_leaves_a_command_that_takes_prompt_answers(self):
        """A cast, a throw, a fire or a quaff is one action of several keys."""
        adjacent = {(5, 4): "jackal"}
        for keys in ("Zah", "fh", "tah", "qa"):
            assert self.gate(keys, adjacent) is None, keys
        assert self.gate("hs", adjacent)[0] == "G8"

    def test_g8_does_not_hide_the_rest_gates(self):
        adjacent = {(5, 4): "jackal"}
        assert self.gate("5s", adjacent)[0] == "G4"
        hungry = status_obs(adjacent, "Hungry")
        assert claude_play.check_batch(claude_play.parse_keys("5s"), hungry)[0] == "G5"

    def test_g3_fight_a_pet_or_walk_into_a_peaceful(self):
        assert self.gate("Fh", {(5, 4): "tame little dog"})[0] == "G3"
        assert self.gate("h", {(5, 4): "peaceful watchman"})[0] == "G3"
        assert self.gate("h", {(5, 4): "tame little dog"}) is None  # a swap
        assert self.gate("Fh", {(5, 4): "jackal"}) is None

    def test_g7_also_refuses_a_floating_eye(self):
        assert self.gate("l", {(5, 6): "floating eye"})[0] == "G7"
        assert self.gate("Fl", {(5, 6): "floating eye"})[0] == "G7"
        assert self.gate("h", {(5, 6): "floating eye"}) is None

    def test_g4_reads_the_count_prefix_and_caps_the_total(self):
        assert self.gate("300s", {})[0] == "G4"
        assert self.gate("6s6.", {})[0] == "G4"
        assert self.gate("10s", {}) is None
        assert self.gate("2h20", {}) is None  # digits not followed by s or .

    def test_g7_move_or_fight_into_a_gas_spore(self):
        assert self.gate("l", {(5, 6): "gas spore"})[0] == "G7"
        assert self.gate("Fl", {(5, 6): "gas spore"})[0] == "G7"
        assert self.gate("h", {(5, 6): "gas spore"}) is None

    def test_own_cell_is_never_a_monster(self):
        assert self.gate("hh", {}) is None  # the hero cell has a monster glyph and no prefix

    def test_refusal_sends_nothing_and_logs_a_violation(self, dirs):
        pipe_dir, state_dir = dirs
        d = FakeDaemon(pipe_dir)
        path = game_log.reset_game(d, None, state_dir)
        d.obs = gate_obs({(5, 6): "peaceful watchman"})

        assert claude_play.run_keys(d, "sy", {ord("s"): 0, ord("y"): 1}, state_dir) == "G2"

        assert keys_of(path) == ["game", "session", "event"]  # no step was taken or logged
        note = lines(path)[-1]["event"]
        assert note["tag"] == "violation" and note["gate"] == "G2" and note["unsent"] == "sy"


class TestRealDaemon:
    def test_monsters_reads_the_pet_from_a_real_observation(self, pipe_dir, tmp_path):
        """REQ-011: _monsters indexes glyphs/screen_descriptions by blstats Y, X
        correctly. A fresh game starts with the pet next to the hero."""
        d = NLEDaemon(pipe_dir).start()
        game_log.reset_game(d, "mon-hum-neu-mal", str(tmp_path))

        found = claude_play._monsters(d.obs)

        assert found, "no monster read: a silent skip or an off-by-one"
        assert (0, 0) not in [(dy, dx) for dy, dx, _ in found]  # the hero's own cell
        assert any(descr.startswith("tame ") and max(abs(dy), abs(dx)) <= 1 for dy, dx, descr in found)

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

    def test_a_plain_stop_saves_and_the_game_resumes(self, pipe_dir, tmp_path, monkeypatch, capsys):
        """REQ-017: --stop with no word saves; the next start resumes the same file."""
        monkeypatch.setattr(claude_play, "PIPE_DIR", pipe_dir)
        d1 = NLEDaemon(pipe_dir).start()
        path = game_log.reset_game(d1, "mon-hum-neu-mal", str(tmp_path))

        claude_play.main(["--stop"])

        assert "game saved" in capsys.readouterr().out
        d2 = NLEDaemon(pipe_dir).start()
        assert game_log.reset_game(d2, "mon-hum-neu-mal", str(tmp_path)) == path
        assert lines(path)[-1]["session"]["resumed"] is True

    def test_stop_save_still_saves_and_discard_does_not(self, pipe_dir, tmp_path, monkeypatch):
        monkeypatch.setattr(claude_play, "PIPE_DIR", pipe_dir)
        d = NLEDaemon(pipe_dir).start()
        game_log.reset_game(d, "mon-hum-neu-mal", str(tmp_path))
        claude_play.main(["--stop", "save"])
        assert d.has_save()

        d = NLEDaemon(pipe_dir).start()
        game_log.reset_game(d, "mon-hum-neu-mal", str(tmp_path))  # consumes the save
        claude_play.main(["--stop", "discard"])
        assert not d.has_save()

    def test_a_stop_that_cannot_save_reports_it_and_still_stops(self, pipe_dir, monkeypatch, capsys):
        """No reset yet, so there is nothing to save: NLEDaemon.stop raises after shutdown."""
        monkeypatch.setattr(claude_play, "PIPE_DIR", pipe_dir)
        d = NLEDaemon(pipe_dir).start()

        claude_play.main(["--stop"])  # must not raise

        assert "not saved" in capsys.readouterr().out
        assert not d.is_alive()

    def test_a_stop_on_a_finished_game_reports_it_and_still_stops(self, pipe_dir, monkeypatch, capsys):
        monkeypatch.setattr(claude_play, "PIPE_DIR", pipe_dir)
        d = NLEDaemon(pipe_dir).start(max_episode_steps=3)
        d.reset()
        while not d.done:
            d.step(1)

        claude_play.main(["--stop"])  # must not raise

        assert "not saved" in capsys.readouterr().out
        assert not d.is_alive() and not d.has_save()

    def test_g9_refuses_a_reset_over_a_running_game(self, pipe_dir, tmp_path, monkeypatch, capsys):
        """REQ-018: --reset would abandon a running game."""
        monkeypatch.setattr(claude_play, "PIPE_DIR", pipe_dir)
        notes = []
        monkeypatch.setattr(game_log, "note", lambda daemon, text, **kw: notes.append((text, kw)))
        d = NLEDaemon(pipe_dir).start()
        game_log.reset_game(d, "mon-hum-neu-mal", str(tmp_path))
        for _ in range(3):
            d.step(claude_play.ACTION_INDEX[ord("s")])
        turn = int(d.obs["blstats"][nethack.NLE_BL_TIME])

        claude_play.main(["--reset"])

        assert "REFUSED G9" in capsys.readouterr().out
        assert notes and notes[0][1]["tag"] == "violation" and notes[0][1]["gate"] == "G9"
        d.status()
        assert int(d.obs["blstats"][nethack.NLE_BL_TIME]) == turn  # the game was not reset

    def test_g9_refuses_only_a_running_game(self, pipe_dir, tmp_path):
        d = NLEDaemon(pipe_dir)
        assert claude_play.reset_refusal(d) is None  # no daemon
        d.start()
        assert claude_play.reset_refusal(d) is None  # started, never reset
        game_log.reset_game(d, "mon-hum-neu-mal", str(tmp_path))
        assert claude_play.reset_refusal(d)[0] == "G9"  # a fresh game at turn 1 is refused too

    def test_g9_lets_a_reset_through_after_the_game_is_over(self, pipe_dir):
        d = NLEDaemon(pipe_dir).start(max_episode_steps=3)
        d.reset()
        while not d.done:
            d.step(claude_play.ACTION_INDEX[ord("s")])
        assert claude_play.reset_refusal(d) is None

    def test_g11_logs_a_discard_of_a_running_game(self, pipe_dir, tmp_path, monkeypatch):
        monkeypatch.setattr(claude_play, "PIPE_DIR", pipe_dir)
        notes = []
        monkeypatch.setattr(game_log, "note", lambda daemon, text, **kw: notes.append(kw))
        d = NLEDaemon(pipe_dir).start()
        game_log.reset_game(d, "mon-hum-neu-mal", str(tmp_path))

        claude_play.main(["--stop", "discard"])

        assert [n["gate"] for n in notes] == ["G11"] and notes[0]["tag"] == "violation"
        assert not d.is_alive() and not d.has_save()

    def test_g11_is_silent_for_a_plain_stop_and_for_no_game(self, pipe_dir, tmp_path, monkeypatch):
        monkeypatch.setattr(claude_play, "PIPE_DIR", pipe_dir)
        notes = []
        monkeypatch.setattr(game_log, "note", lambda daemon, text, **kw: notes.append(kw))
        d = NLEDaemon(pipe_dir).start()
        claude_play.main(["--stop", "discard"])  # never reset: no game to discard
        d = NLEDaemon(pipe_dir).start()
        game_log.reset_game(d, "mon-hum-neu-mal", str(tmp_path))
        claude_play.main(["--stop"])  # saves
        assert notes == []
