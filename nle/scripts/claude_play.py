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
is recorded as a note tagged "violation". Before any key is sent, a batch is
refused (and recorded the same way) by gate G2 (more than one key with a peaceful
within 2 squares), G4 (rest or search with a hostile in view, or more than 10
turns) or G7 (a move or F into a gas spore).
"""
import sys

import numpy as np

from nle import nethack
from nle.scripts import game_log
from nle.scripts.nle_daemon import NLEDaemon

PIPE_DIR = "/tmp/nle-daemon"
CHARACTER = "mon-hum-neu-mal"
HUNGER = ("Hungry", "Weak", "Fainting")
REST_CAP = 10  # turns of rest or search per call (ADR-03 open point 1)
DIRECTIONS = {  # (dy, dx)
    "h": (0, -1), "j": (1, 0), "k": (-1, 0), "l": (0, 1),
    "y": (-1, -1), "u": (-1, 1), "b": (1, -1), "n": (1, 1),
}
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


def _monsters(obs):
    """(dy, dx, description) of every monster cell except the hero's own, dy and
    dx relative to the hero. Empty if the observation has no descriptions."""
    if "screen_descriptions" not in obs:
        return []
    hy = int(obs["blstats"][nethack.NLE_BL_Y])
    hx = int(obs["blstats"][nethack.NLE_BL_X])
    found = []
    for y, x in zip(*np.nonzero(nethack.glyph_is_monster(obs["glyphs"]))):
        if (y, x) != (hy, hx):
            descr = bytes(obs["screen_descriptions"][y][x]).split(b"\0")[0].decode("ascii", "replace")
            found.append((int(y) - hy, int(x) - hx, descr))
    return found


def check_batch(pairs, obs):
    """A gate id and message if the batch must not be sent (ADR-03), else None.
    Reads monster hostility from description prefixes, so it can miss while
    hallucinating. G7 only looks at the batch's first move: later keys land
    on squares that depend on the earlier ones."""
    monsters = _monsters(obs)
    if len(pairs) > 1:
        for dy, dx, descr in monsters:
            if max(abs(dy), abs(dx)) <= 2 and descr.startswith("peaceful "):
                return "G2", f"{len(pairs)} keys with a peaceful ({descr}) within 2 squares: send one key per call"
    total, digits = 0, ""
    for _, code in pairs:
        if chr(code).isdigit():
            digits += chr(code)
        else:
            if chr(code) in "s.":
                total += int(digits or 1)
            digits = ""
    if total:
        if any(not d.startswith(("tame ", "peaceful ")) for _, _, d in monsters):
            return "G4", "rest or search with a hostile monster in view"
        if total > REST_CAP:
            return "G4", f"rest or search {total} turns in one call (cap {REST_CAP})"
    moves = [c for _, c in pairs[:2]]
    if moves and moves[0] == ord("F") and len(moves) > 1:
        moves = moves[1:]
    if moves and chr(moves[0]) in DIRECTIONS:
        dy, dx = DIRECTIONS[chr(moves[0])]
        for my, mx, descr in monsters:
            if (my, mx) == (dy, dx) and "gas spore" in descr:
                return "G7", "a move or F into a gas spore (it explodes)"
    return None


def run_keys(daemon, keys, action_index=ACTION_INDEX, state_dir=game_log.STATE_DIR):
    """Sends the keys one step at a time, recording each step, and stops at the
    first reason to. Returns that reason, or None if every key was sent."""
    pairs = parse_keys(keys)
    for typed, code in pairs:
        if code not in action_index:
            raise SystemExit(f"key {typed!r} (code {code}) is not in nethack.ACTIONS")
    refusal = check_batch(pairs, daemon.obs)
    if refusal:
        gate, message = refusal
        game_log.note(
            daemon, f"refused {gate}: {message}", tag="violation", state_dir=state_dir, gate=gate, unsent=keys
        )
        print(f"REFUSED {gate}: {message}. Nothing was sent.")
        return gate
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
