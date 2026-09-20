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
within 2 squares), G3 (F at a tame or peaceful monster, or a move into a peaceful
one), G4 (rest or search with a hostile in view, or more than 10 turns), G5 (rest
or search while Hungry, Weak or Fainting), G7 (a move or F into a gas spore or a
floating eye) or G8 (more than one action with a hostile adjacent; F and its
direction count as one). G4 and G5 apply only to a batch of digits, s and . ;
G8 only to a batch of moves, F, s, . and digits, so a cast, throw or quaff is fine.
"""
import os
import sys

import numpy as np

from nle import nethack
from nle.scripts import game_log
from nle.scripts.nle_daemon import NLEDaemon

PIPE_DIR = "/tmp/nle-daemon"
VIEW_PATH = os.path.join(game_log.STATE_DIR, "game_view.html")
CHARACTER = "mon-hum-neu-mal"
HUNGER = ("Hungry", "Weak", "Fainting")
REST_CAP = 10  # turns of rest or search per call (ADR-03 open point 1)
DIRECTIONS = {  # (dy, dx)
    "h": (0, -1), "j": (1, 0), "k": (-1, 0), "l": (0, 1),
    "y": (-1, -1), "u": (-1, 1), "b": (1, -1), "n": (1, 1),
}
TURN_KEYS = set(DIRECTIONS) | set("Fs.")  # keys that spend a turn on their own
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


def _hostile(descr):
    return not descr.startswith(("tame ", "peaceful "))


def _first_target(pairs, monsters):
    """(fight, description) of the monster the batch's first move or F lands
    on, else None. Later keys land on squares that depend on the earlier ones."""
    codes = [c for _, c in pairs[:2]]
    fight = len(codes) > 1 and codes[0] == ord("F")
    key = chr(codes[1] if fight else codes[0]) if codes else ""
    if key not in DIRECTIONS:
        return None
    for dy, dx, descr in monsters:
        if (dy, dx) == DIRECTIONS[key]:
            return fight, descr
    return None


def check_batch(pairs, obs):
    """A gate id and message if the batch must not be sent (ADR-03), else None.
    Reads monster hostility from description prefixes, so it can miss while
    hallucinating. Rest gates (G4, G5) look only at a batch made of digits, s
    and ., because a . or s elsewhere may answer a prompt (the travel confirm
    in `_<.`); G8 looks only at a batch of turn keys, so a move mixed with a
    command (hZal) is not refused. The driver keeps no state between calls, so
    a lone . that confirms a prompt opened by an earlier call still counts as
    a rest."""
    monsters = _monsters(obs)
    if len(pairs) > 1:
        for dy, dx, descr in monsters:
            if max(abs(dy), abs(dx)) <= 2 and descr.startswith("peaceful "):
                return "G2", f"{len(pairs)} keys with a peaceful ({descr}) within 2 squares: send one key per call"
    target = _first_target(pairs, monsters)
    if target:
        fight, descr = target
        if descr.startswith("peaceful ") or (fight and descr.startswith("tame ")):
            return "G3", f"a {'fight' if fight else 'move'} into a {descr}"
    total, digits = 0, ""
    if all(chr(c).isdigit() or chr(c) in "s." for _, c in pairs):
        for _, code in pairs:
            if chr(code).isdigit():
                digits += chr(code)
            else:
                total += int(digits or 1)
                digits = ""
    if total:
        if any(w in game_log.screen_line(obs, 23) for w in HUNGER):
            return "G5", "rest or search while Hungry, Weak or Fainting"
        hostile = [(max(abs(dy), abs(dx)), descr) for dy, dx, descr in monsters if _hostile(descr)]
        if hostile:
            distance, descr = min(hostile)
            return "G4", f"rest or search with a hostile monster in view ({descr or '?'}, {distance} away)"
        if total > REST_CAP:
            return "G4", f"rest or search {total} turns in one call (cap {REST_CAP})"
    if target and any(name in target[1] for name in ("gas spore", "floating eye")):
        return "G7", f"a move or F into a {target[1]}"
    # G8: only a batch of turn keys. A command that takes prompt answers (a cast
    # Zah, a throw tah, a quaff qa) is one action of several keys.
    actions = sum(1 for _, c in pairs if c != ord("F"))  # F is a prefix: Fh is one attack
    if actions > 1 and all(chr(c) in TURN_KEYS or chr(c).isdigit() for _, c in pairs):
        for dy, dx, descr in monsters:
            if max(abs(dy), abs(dx)) <= 1 and _hostile(descr):
                return "G8", f"{actions} actions with a hostile ({descr or '?'}) adjacent: send one action per call"
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
    game_log.write_view(obs, VIEW_PATH)


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
