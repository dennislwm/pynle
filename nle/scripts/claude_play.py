# Copyright (c) Facebook, Inc. and its affiliates.
"""Drives the live NLE daemon one call at a time and records each game to
game_state/ (see nle/scripts/game_log.py and ADR-02).

  --start           spawn the daemon
  --reset           start a new game, or resume a saved one
  --stop [save]     stop the daemon (save: write a save file first)
  --help            print this text
  <keys>            send keys, print the screen

Key syntax: ~ is ESC, | is Enter, ^x is ctrl-x, &x is meta-x (&l is #loot),
a backtick is a literal caret, anything else is sent as typed. A batch stops
early on HP loss, --More--, a [yn] prompt or a hunger warning; each early stop
is recorded as a note tagged "violation".
"""
import sys

from nle import nethack
from nle.scripts import game_log
from nle.scripts.nle_daemon import NLEDaemon

PIPE_DIR = "/tmp/nle-daemon"
CHARACTER = "mon-hum-neu-mal"
HUNGER = ("Hungry", "Weak", "Fainting")
# step() takes an index into nethack.ACTIONS, not a key code.
ACTION_INDEX = {int(a): i for i, a in enumerate(nethack.ACTIONS)}


def parse_keys(keys):
    """Splits a key string into (typed, key code) pairs."""
    pairs = []
    i = 0
    while i < len(keys):
        k = keys[i]
        if k in "^&":
            i += 1
            if i >= len(keys):
                raise SystemExit(f"{k!r} needs a character after it")
            code = ord(keys[i]) & 0x1F if k == "^" else ord(keys[i]) | 0x80
            pairs.append((k + keys[i], code))
        else:
            code = {"~": 27, "|": 13, "`": ord("^")}.get(k, ord(k))
            pairs.append((k, code))
        i += 1
    return pairs


def stop_reason(daemon, hp0):
    """Why a batch should stop after the step just taken, or None."""
    obs = daemon.obs
    top = game_log.screen_line(obs, 0)
    bottom = game_log.screen_line(obs, 23)
    if daemon.done:
        return "game over"
    if int(obs["blstats"][nethack.NLE_BL_HP]) < hp0:
        return "HP loss"
    if "--More--" in top:
        return "--More--"
    if "[yn" in top:
        return "[yn] prompt"
    for word in HUNGER:
        if word in bottom:
            return word
    return None


def run_keys(daemon, keys, action_index=ACTION_INDEX, state_dir=game_log.STATE_DIR):
    """Sends the keys one step at a time, recording each step, and stops at the
    first reason to. Returns that reason, or None if every key was sent."""
    pairs = parse_keys(keys)
    for typed, code in pairs:
        if code not in action_index:
            raise SystemExit(f"key {typed!r} (code {code}) is not in nethack.ACTIONS")
    hp0 = int(daemon.obs["blstats"][nethack.NLE_BL_HP])
    for i, (typed, code) in enumerate(pairs):
        prev_obs = daemon.obs
        daemon.step(action_index[code])
        game_log.log_step(daemon, action_index[code], prev_obs, state_dir)
        reason = stop_reason(daemon, hp0)
        if reason and i + 1 < len(pairs):
            unsent = "".join(t for t, _ in pairs[i + 1 :])
            if reason != "game over":
                game_log.note(
                    daemon,
                    f"stopped after key {i + 1} of {len(pairs)}: {reason}",
                    tag="violation",
                    state_dir=state_dir,
                    unsent=unsent,
                )
            print(f"STOPPED after key {i + 1} of {len(pairs)} ({reason}): unsent {unsent!r}")
            return reason
    return None


def print_screen(daemon):
    obs = daemon.obs
    cursor = tuple(int(x) for x in obs["tty_cursor"])
    print("DONE" if daemon.done else "", daemon.info.get("end_status"), "cursor(row,col)=", cursor)
    for row in game_log.screen(obs).split("\n"):
        if row:
            print(row)


def main(argv):
    if not argv:
        raise SystemExit(__doc__)
    daemon = NLEDaemon(PIPE_DIR)
    if argv[0].startswith("--"):
        verb, *rest = " ".join(argv).split()  # "make play ARGS" passes one string
        if verb == "--start":
            daemon.start(character=CHARACTER)
            print("daemon started")
        elif verb == "--stop":
            daemon.stop(save="save" in rest)
            print("daemon stopped")
        elif verb == "--reset":
            print("game file:", game_log.reset_game(daemon, CHARACTER))
            print_screen(daemon)
        elif verb == "--help":
            print(__doc__)
        else:
            raise SystemExit(__doc__)
    elif len(argv) > 1:
        # A space is a key too, so keys are never joined from several arguments.
        raise SystemExit("pass the keys as one quoted argument")
    else:
        daemon.status()  # this call is a new process: re-read the last obs first
        run_keys(daemon, argv[0])
        print_screen(daemon)


if __name__ == "__main__":
    main(sys.argv[1:])
