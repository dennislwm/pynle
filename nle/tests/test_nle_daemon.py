# Copyright (c) Facebook, Inc. and its affiliates.
import shutil
import tempfile

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
