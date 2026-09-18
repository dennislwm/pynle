# Copyright (c) Facebook, Inc. and its affiliates.
import shutil
import tempfile

import pytest

from nle.scripts import nle_daemon


@pytest.fixture
def pipe_dir():
    d = tempfile.mkdtemp(prefix="nle_daemon_test_")
    try:
        yield d
    finally:
        nle_daemon.stop(d)
        shutil.rmtree(d, ignore_errors=True)


class TestNLEDaemon:
    def test_step_and_reset(self, pipe_dir):
        assert not nle_daemon.is_alive(pipe_dir)
        nle_daemon.start(pipe_dir)
        assert nle_daemon.is_alive(pipe_dir)

        response = nle_daemon.call(pipe_dir, "step", action=0)
        assert "glyphs" in response["obs"]
        assert "misc" in response["obs"]
        assert isinstance(response["done"], (bool,))

        response = nle_daemon.call(pipe_dir, "reset")
        assert "glyphs" in response["obs"]

        nle_daemon.stop(pipe_dir)
        assert not nle_daemon.is_alive(pipe_dir)

    def test_malformed_action_survives(self, pipe_dir):
        """A bad action must not crash the daemon or hang the caller --
        the episode stays alive for the next, valid call."""
        nle_daemon.start(pipe_dir)

        with pytest.raises(RuntimeError):
            nle_daemon.call(pipe_dir, "step", action=999999)
        assert nle_daemon.is_alive(pipe_dir)

        with pytest.raises(RuntimeError):
            nle_daemon.call(pipe_dir, "step", action=-1)
        assert nle_daemon.is_alive(pipe_dir)

        response = nle_daemon.call(pipe_dir, "step", action=0)
        assert "glyphs" in response["obs"]

    def test_start_twice_raises(self, pipe_dir):
        """Idempotency: a second start() against a live daemon must not
        spawn a duplicate process or corrupt the PID file -- it raises."""
        nle_daemon.start(pipe_dir)
        with pytest.raises(RuntimeError):
            nle_daemon.start(pipe_dir)
        assert nle_daemon.is_alive(pipe_dir)
