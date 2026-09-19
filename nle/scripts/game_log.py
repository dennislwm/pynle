# Copyright (c) Facebook, Inc. and its affiliates.
"""Per-game jsonl record for claude-play, written from the caller's side.

One JSON object per line, one top-level key per line: "game" (once, first
line), "session" (one per daemon start or resume), "logs" (one per step) and
"event" (kind: death, note or level). A resumed game appends to the same file.

Plain functions over a duck-typed `NLEDaemon` (only `pipe_dir`, `obs`, `done`,
`reset()`, `has_save()` and `pid()` are used), with no state of their own, so
moving the writer into the daemon later only moves the call sites.

See 13pynle.wiki/decisions/adr-02-per-game-jsonl-record.md (Option 2).
"""
import json
import os
import re

from nle import nethack

# Anchored to this file, not the CWD (two levels up from nle/scripts/): a
# checkout or an editable install. The directory is untracked (.gitignore).
STATE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "game_state")
)
POINTER = "game.log"

_GAME_FILE = re.compile(r"^(\d+)_.*\.jsonl$")

_CREATION = (
    ("hp", nethack.NLE_BL_HP),
    ("hpmax", nethack.NLE_BL_HPMAX),
    ("pw", nethack.NLE_BL_ENE),
    ("pwmax", nethack.NLE_BL_ENEMAX),
    ("ac", nethack.NLE_BL_AC),
    ("str25", nethack.NLE_BL_STR25),
    ("dex", nethack.NLE_BL_DEX),
    ("con", nethack.NLE_BL_CON),
    ("int", nethack.NLE_BL_INT),
    ("wis", nethack.NLE_BL_WIS),
    ("cha", nethack.NLE_BL_CHA),
)


def _bl(obs, index):
    return int(obs["blstats"][index])


def screen_line(obs, row):
    return bytes(obs["tty_chars"][row]).decode("ascii", "replace").strip()


def screen(obs):
    return "\n".join(
        bytes(row).decode("ascii", "replace").rstrip() for row in obs["tty_chars"]
    )


def append(path, key, payload):
    """One line, opened and closed per call, so every line is flushed."""
    with open(path, "a") as f:
        f.write(json.dumps({key: payload}) + "\n")


def allocate_game_file(state_dir=STATE_DIR, name="nle_daemon"):
    """Creates game_state/NNN_<name>.jsonl, NNN one above the highest existing
    number (counting files would reuse a number after a deletion). Exclusive
    create: a collision fails instead of appending to someone else's game."""
    os.makedirs(state_dir, exist_ok=True)
    numbers = [
        int(m.group(1)) for f in os.listdir(state_dir) if (m := _GAME_FILE.match(f))
    ]
    path = os.path.join(state_dir, f"{max(numbers, default=0) + 1:03d}_{name}.jsonl")
    open(path, "x").close()
    return path


def _pointer(pipe_dir):
    return os.path.join(pipe_dir, POINTER)


def current_game(pipe_dir):
    """The game file this pipe_dir is recording to, or None if the pointer is
    missing or names a file that no longer exists."""
    try:
        with open(_pointer(pipe_dir)) as f:
            path = f.read().strip()
    except FileNotFoundError:
        return None
    return path if os.path.isfile(path) else None


def _new_game(daemon, state_dir, character, fresh):
    path = allocate_game_file(state_dir)
    with open(_pointer(daemon.pipe_dir), "w") as f:
        f.write(path + "\n")
    metadata = {"schema": 1, "character": character, "vardir": daemon.pipe_dir}
    if fresh:
        metadata["creation"] = {name: _bl(daemon.obs, i) for name, i in _CREATION}
    name = os.path.basename(path)[: -len(".jsonl")]
    append(path, "game", {"name": name, "metadata": metadata})
    return path


def _session(daemon, path, resumed, pointer_lost=False):
    session = {
        "pid": daemon.pid(),
        "resumed": resumed,
        "start_turn": _bl(daemon.obs, nethack.NLE_BL_TIME),
    }
    if pointer_lost:
        session["pointer_lost"] = True
    append(path, "session", session)


def reset_game(daemon, character=None, state_dir=STATE_DIR):
    """daemon.reset() plus the record. A save that was in the save directory
    before the reset and is gone after it was consumed by a successful
    restore (src/restore.c:901-902), so that reset resumed a game; anything
    else is a fresh game. Assumes one `character` per pipe_dir: a save left
    unconsumed under another character is treated as fresh."""
    had_save = daemon.has_save()
    daemon.reset()
    resumed = had_save and not daemon.has_save()
    path = current_game(daemon.pipe_dir) if resumed else None
    pointer_lost = resumed and path is None
    if path is None:
        path = _new_game(daemon, state_dir, character, fresh=not resumed)
    _session(daemon, path, resumed, pointer_lost)
    return path


def _attach(daemon, state_dir, character=None):
    """A game is running but its start was never recorded (the daemon was
    reset without reset_game): start a new file and say so."""
    path = _new_game(daemon, state_dir, character, fresh=False)
    _session(daemon, path, resumed=False, pointer_lost=True)
    return path


def _event(daemon, path, kind, **fields):
    obs = daemon.obs
    payload = {
        "kind": kind,
        "t": _bl(obs, nethack.NLE_BL_TIME),
        "dlvl": _bl(obs, nethack.NLE_BL_DEPTH),
    }
    append(path, "event", {**payload, **fields})


def log_step(daemon, action, prev_obs, state_dir=STATE_DIR):
    """Call right after daemon.step(action). `prev_obs` is the observation
    before the step, so a change of dungeon level can be seen."""
    path = current_game(daemon.pipe_dir) or _attach(daemon, state_dir)
    obs = daemon.obs
    misc = obs["misc"]
    append(
        path,
        "logs",
        {
            "t": _bl(obs, nethack.NLE_BL_TIME),
            "dlvl": _bl(obs, nethack.NLE_BL_DEPTH),
            "dnum": _bl(obs, nethack.NLE_BL_DNUM),
            "hp": _bl(obs, nethack.NLE_BL_HP),
            "hpmax": _bl(obs, nethack.NLE_BL_HPMAX),
            "pw": _bl(obs, nethack.NLE_BL_ENE),
            "pwmax": _bl(obs, nethack.NLE_BL_ENEMAX),
            "ac": _bl(obs, nethack.NLE_BL_AC),
            "xp": _bl(obs, nethack.NLE_BL_XP),
            "exp": _bl(obs, nethack.NLE_BL_EXP),
            "score": _bl(obs, nethack.NLE_BL_SCORE),
            "gold": _bl(obs, nethack.NLE_BL_GOLD),
            "x": _bl(obs, nethack.NLE_BL_X),
            "y": _bl(obs, nethack.NLE_BL_Y),
            "action": int(action),
            "msg": screen_line(obs, 0),
            "prompt": int(bool(misc[0] or misc[1])),
        },
    )
    if _bl(obs, nethack.NLE_BL_DEPTH) != _bl(prev_obs, nethack.NLE_BL_DEPTH):
        _event(daemon, path, "level", screen=screen(obs))
    if daemon.done:
        end = daemon.info.get("end_status")
        _event(
            daemon,
            path,
            "death",
            end_status=getattr(end, "name", end),
            screen=screen(obs),
        )
    return path


def note(daemon, text, tag=None, state_dir=STATE_DIR, **fields):
    """A player's mistake or lesson, or a batch problem the driver saw."""
    path = current_game(daemon.pipe_dir) or _attach(daemon, state_dir)
    if tag is not None:
        fields["tag"] = tag
    _event(daemon, path, "note", text=text, **fields)
    return path
