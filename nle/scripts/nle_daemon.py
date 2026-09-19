# Copyright (c) Facebook, Inc. and its affiliates.
"""Persistent NLE daemon: holds one live NLE episode in a background
process, independent of any single caller, and exchanges one
action/observation pair per call over two POSIX FIFOs.

See 13pynle.wiki/decisions/adr-01-live-episode-turn-driver.md (Option 3).
"""
import fcntl
import os
import pickle
import select
import subprocess
import sys
import time

from nle import nethack
from nle.env.base import NLE

DEFAULT_OBSERVATION_KEYS = (
    "glyphs",
    "chars",
    "colors",
    "specials",
    "blstats",
    "message",
    "inv_glyphs",
    "inv_strs",
    "inv_letters",
    "inv_oclasses",
    "screen_descriptions",
    "tty_chars",
    "tty_colors",
    "tty_cursor",
    "misc",
)


def _action_fifo_path(pipe_dir):
    return os.path.join(pipe_dir, "action.fifo")


def _obs_fifo_path(pipe_dir):
    return os.path.join(pipe_dir, "obs.fifo")


def _pid_file_path(pipe_dir):
    return os.path.join(pipe_dir, "daemon.pid")


def _read_exact(fd, n):
    buf = b""
    while len(buf) < n:
        chunk = os.read(fd, n - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf


def _read_framed(fifo_path, timeout=None):
    """timeout bounds only the wait for a writer to connect -- once a
    message starts arriving it is read to completion."""
    flags = os.O_RDONLY if timeout is None else os.O_RDONLY | os.O_NONBLOCK
    fd = os.open(fifo_path, flags)
    try:
        if timeout is not None:
            ready, _, _ = select.select([fd], [], [], timeout)
            if not ready:
                raise TimeoutError(f"timed out waiting for a writer on {fifo_path}")
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
        header = _read_exact(fd, 4)
        if len(header) < 4:
            return None
        length = int.from_bytes(header, "big")
        data = _read_exact(fd, length)
    finally:
        os.close(fd)
    return pickle.loads(data)


def _write_framed(fifo_path, obj):
    data = pickle.dumps(obj)
    fd = os.open(fifo_path, os.O_WRONLY)
    try:
        os.write(fd, len(data).to_bytes(4, "big"))
        os.write(fd, data)
    finally:
        os.close(fd)


class NLEDaemon:
    """The only interface for driving a daemon (see Conventions.md,
    "Interact with a live daemon through NLEDaemon exclusively"). The wire
    protocol (PID-file liveness, FIFO framing, spawn/shutdown) lives
    directly in these methods -- there is no module-level function to
    reach this logic through instead.
    `pipe_dir` remains the daemon's real identity; this object caches the
    latest response as attributes and holds no other state. Multiple
    instances against the same `pipe_dir` are independent client handles
    to the same daemon -- they don't share their cached attributes."""

    def __init__(self, pipe_dir):
        self.pipe_dir = pipe_dir
        self.obs = self.reward = self.done = self.truncated = self.info = None

    def is_alive(self):
        """Stdlib equivalent of `tmux has-session`: PID-file liveness check."""
        pid_file = _pid_file_path(self.pipe_dir)
        if not os.path.exists(pid_file):
            return False
        try:
            pid = int(open(pid_file).read().strip())
            os.kill(pid, 0)
        except (OSError, ValueError):
            return False
        return True

    def start(self, character="mon-hum-neu-mal", max_episode_steps=5000, timeout=60):
        """Starts the daemon as a standalone background process, detached
        from this caller's session, and waits for it to report ready."""
        if self.is_alive():
            raise RuntimeError(f"daemon already running (pipe_dir={self.pipe_dir})")
        # is_alive() already confirmed the process behind any existing
        # pid_file is dead (e.g. a SIGKILL/OOM-kill that skipped _run()'s
        # own cleanup) -- clear it now, or the readiness wait below would
        # see the stale file and return immediately, before the new
        # subprocess has actually started.
        pid_file = _pid_file_path(self.pipe_dir)
        if os.path.exists(pid_file):
            os.remove(pid_file)
        os.makedirs(self.pipe_dir, exist_ok=True)
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "nle.scripts.nle_daemon",
                "_run",
                self.pipe_dir,
                character,
                str(max_episode_steps),
            ],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + timeout
        while not os.path.exists(pid_file):
            if time.time() > deadline:
                raise TimeoutError(f"daemon failed to start within {timeout}s")
            time.sleep(0.05)
        return self

    def stop(self, save=False, timeout=30):
        """Asks a live daemon to shut down and waits for it to exit.
        No-op if it isn't running.

        save=True mirrors NetHack's own save-and-quit: dosave0()
        (see REQ-001) only ever succeeds once per episode -- normal play
        never re-arms it -- so this is the daemon's one designated
        moment to use it. Resuming later means starting a new NLEDaemon
        on this same pipe_dir; NetHack's own startup finds the save file
        left in it and resumes automatically, no separate call needed.
        The daemon always stops either way; a failed save is
        raised here, after shutdown, as information rather than
        something that blocks the stop.
        """
        if not self.is_alive():
            return
        msg = {"cmd": "stop"}
        if save:
            msg["save"] = True
        _write_framed(_action_fifo_path(self.pipe_dir), msg)
        save_error = None
        if save:
            response = _read_framed(_obs_fifo_path(self.pipe_dir), timeout=timeout)
            if response is not None and not response.get("ok", True):
                save_error = response["error"]
        pid_file = _pid_file_path(self.pipe_dir)
        deadline = time.time() + timeout
        while os.path.exists(pid_file):
            if time.time() > deadline:
                raise TimeoutError(f"daemon failed to stop within {timeout}s")
            time.sleep(0.05)
        if save_error:
            raise RuntimeError(f"save failed during stop: {save_error}")

    def step(self, action, **kwargs):
        return self._update(self._send("step", action=action, **kwargs))

    def reset(self, **kwargs):
        return self._update(self._send("reset", **kwargs))

    def status(self):
        """Re-reads the last observation without advancing the episode --
        for a caller that lost its own handle and must not step/reset."""
        return self._update(self._send("status"))

    def _send(self, cmd, action=None, timeout=30):
        """Sends one request and returns the daemon's one response.

        cmd is "step" (requires `action`, an int index into
        `nle.nethack.ACTIONS`), "reset" or "status". Raises RuntimeError if the
        daemon rejected the request (e.g. an out-of-range action) or
        TimeoutError if no response arrives within `timeout` seconds --
        the daemon stays alive and reusable in both cases.
        """
        if not self.is_alive():
            raise RuntimeError(f"daemon not running (pipe_dir={self.pipe_dir})")
        msg = {"cmd": cmd}
        if action is not None:
            msg["action"] = action
        _write_framed(_action_fifo_path(self.pipe_dir), msg)
        response = _read_framed(_obs_fifo_path(self.pipe_dir), timeout=timeout)
        if response is not None and not response.get("ok", True):
            raise RuntimeError(f"daemon rejected request: {response['error']}")
        return response

    def _update(self, response):
        self.obs = response["obs"]
        self.reward = response["reward"]
        self.done = response["done"]
        self.truncated = response["truncated"]
        self.info = response["info"]
        return self.obs


def _run(pipe_dir, character, max_episode_steps):
    action_fifo = _action_fifo_path(pipe_dir)
    obs_fifo = _obs_fifo_path(pipe_dir)
    for path in (action_fifo, obs_fifo):
        if not os.path.exists(path):
            os.mkfifo(path)

    env = NLE(
        character=character,
        max_episode_steps=max_episode_steps,
        actions=nethack.ACTIONS,
        allow_all_modes=True,
        observation_keys=DEFAULT_OBSERVATION_KEYS,
        vardir=pipe_dir,
    )
    # No reset() here: the caller's own first "reset" message is what
    # resumes a leftover save file (or starts fresh if there is none) --
    # matches every existing NLEDaemon usage, which already calls
    # reset() first. Resetting here too would silently discard whatever
    # this reset() just resumed, with no way for the caller to see it.

    pid_file = _pid_file_path(pipe_dir)
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))

    last_response = None  # replayed by "status"
    try:
        while True:
            msg = _read_framed(action_fifo)
            if msg is None:
                continue
            cmd = msg.get("cmd")
            if cmd == "stop":
                if msg.get("save"):
                    try:
                        env.save()
                        response = {"ok": True}
                    except Exception as exc:
                        response = {"ok": False, "error": str(exc)}
                    _write_framed(obs_fifo, response)
                break
            try:
                if cmd == "status":
                    if last_response is None:
                        raise ValueError("no observation yet; call reset first")
                    response = last_response
                elif cmd == "reset":
                    obs, info = env.reset()
                    response = {"ok": True, "obs": obs, "reward": 0.0,
                                "done": False, "truncated": False, "info": info}
                else:
                    action = msg.get("action")
                    if not isinstance(action, int) or not (
                        0 <= action < len(env.actions)
                    ):
                        raise ValueError(
                            f"invalid action {action!r}; must be an int in "
                            f"0..{len(env.actions) - 1}"
                        )
                    obs, reward, done, truncated, info = env.step(action)
                    response = {"ok": True, "obs": obs, "reward": reward,
                                "done": done, "truncated": truncated,
                                "info": info}
                if cmd != "status":
                    last_response = response
            except Exception as exc:  # keep the daemon and episode alive
                response = {"ok": False, "error": str(exc)}
            _write_framed(obs_fifo, response)
    finally:
        env.close()
        os.remove(pid_file)


def _main():
    if len(sys.argv) != 5 or sys.argv[1] != "_run":
        raise SystemExit(
            "internal entry point: "
            "python -m nle.scripts.nle_daemon _run <pipe_dir> <character> <max_episode_steps>"
        )
    _, _, pipe_dir, character, max_episode_steps = sys.argv
    _run(pipe_dir, character, int(max_episode_steps))


if __name__ == "__main__":
    _main()
