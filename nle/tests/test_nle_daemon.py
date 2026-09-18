# Copyright (c) Facebook, Inc. and its affiliates.
import os
import shutil
import signal
import tempfile
import time

import pytest

from nle.scripts.nle_daemon import NLEDaemon


@pytest.fixture
def pipe_dir():
    d = tempfile.mkdtemp(prefix="nle_daemon_test_")
    try:
        yield d
    finally:
        NLEDaemon(d).stop()
        shutil.rmtree(d, ignore_errors=True)


class TestNLEDaemon:
    def test_step_and_reset(self, pipe_dir):
        d = NLEDaemon(pipe_dir)
        assert not d.is_alive()
        d.start()
        assert d.is_alive()

        d.reset()
        assert "glyphs" in d.obs

        d.step(0)
        assert "glyphs" in d.obs
        assert "misc" in d.obs
        assert isinstance(d.done, (bool,))

        d.reset()
        assert "glyphs" in d.obs

        d.stop()
        assert not d.is_alive()

    def test_malformed_action_survives(self, pipe_dir):
        """A bad action must not crash the daemon or hang the caller --
        the episode stays alive for the next, valid call."""
        d = NLEDaemon(pipe_dir).start()
        d.reset()

        with pytest.raises(RuntimeError):
            d.step(999999)
        assert d.is_alive()

        with pytest.raises(RuntimeError):
            d.step(-1)
        assert d.is_alive()

        d.step(0)
        assert "glyphs" in d.obs

    def test_start_twice_raises(self, pipe_dir):
        """Idempotency: a second start() against a live daemon must not
        spawn a duplicate process or corrupt the PID file -- it raises."""
        d = NLEDaemon(pipe_dir).start()
        with pytest.raises(RuntimeError):
            NLEDaemon(pipe_dir).start()
        assert d.is_alive()

    def test_lifecycle_and_attributes(self, pipe_dir):
        d = NLEDaemon(pipe_dir)
        assert d.obs is None
        assert not d.is_alive()

        d.start()
        assert d.is_alive()

        d.reset()
        assert "glyphs" in d.obs
        assert d.done is False

        d.step(0)
        assert "glyphs" in d.obs

        d.stop()
        assert not d.is_alive()

    def test_construction_is_not_idempotent(self, pipe_dir):
        """Two NLEDaemon(pipe_dir) calls are independent client objects
        against the same daemon -- not the same instance, and their
        cached attributes don't sync with each other."""
        d1 = NLEDaemon(pipe_dir).start()
        d2 = NLEDaemon(pipe_dir)
        assert d1 is not d2

        d1.reset()
        assert d1.obs is not None
        assert d2.obs is None  # d2 never called anything itself

        d2.step(0)  # both clients drive the same underlying daemon/episode
        assert d2.obs is not None

    def test_save_and_resume_across_restart(self, pipe_dir):
        """REQ-001: the daemon reuses its own pipe_dir as NetHack's
        vardir, so stop(save=True) followed by a fresh NLEDaemon on the
        same pipe_dir resumes the episode -- simulating the daemon
        process itself being killed and restarted."""
        d1 = NLEDaemon(pipe_dir).start()
        d1.reset()
        d1.step(1)
        pos_before = (d1.obs["blstats"][0], d1.obs["blstats"][1])
        d1.stop(save=True)
        assert not d1.is_alive()

        d2 = NLEDaemon(pipe_dir).start()
        d2.reset()
        pos_after = (d2.obs["blstats"][0], d2.obs["blstats"][1])
        assert pos_after == pos_before
        d2.stop()

    def test_stop_without_save_does_not_leave_a_resumable_file(self, pipe_dir):
        """A plain stop() (no save=True) must not accidentally leave
        behind a save file for a later daemon on the same pipe_dir to
        stumble into -- resuming should only ever be intentional."""
        d1 = NLEDaemon(pipe_dir).start()
        d1.reset()
        d1.step(1)
        d1.stop()

        save_dir = os.path.join(pipe_dir, "save")
        assert os.listdir(save_dir) == []

    def test_start_after_hard_kill_clears_stale_pid_file(self, pipe_dir):
        """A SIGKILL (crash/OOM-kill) skips _run()'s own cleanup,
        leaving a stale daemon.pid behind. start() must detect and clear
        it, not let the readiness wait mistake the old file for the new
        daemon already being up."""
        d1 = NLEDaemon(pipe_dir).start()
        d1.reset()

        pid_file = os.path.join(pipe_dir, "daemon.pid")
        pid = int(open(pid_file).read().strip())
        os.kill(pid, signal.SIGKILL)
        # Reap the zombie so is_alive()'s os.kill(pid, 0) check reflects
        # a truly dead process, not an unreaped process-table artifact.
        deadline = time.time() + 10
        while True:
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                break
            if time.time() > deadline:
                raise TimeoutError("killed daemon process never reaped")
            time.sleep(0.05)
        assert not d1.is_alive()
        assert os.path.exists(pid_file)  # the kill skipped _run()'s cleanup

        d2 = NLEDaemon(pipe_dir).start()
        assert d2.is_alive()
        d2.reset()
        assert "glyphs" in d2.obs
        d2.stop()
