# CLAUDE.md

Claude's role in this repo is the player. How to run things is in [README.md](README.md); this file says when and why.

## Playing NetHack (the player role)

You play through a live daemon, one call at a time, and every game is recorded. Commands: [Recording a claude-play game](README.md#recording-a-claude-play-game). `make play ARGS='--help'` prints the verbs and the key syntax.

Before the first key of a session:

1. Read your past notes. "No such file" means there are none yet. Old notes may name `pipenv` or `play.py`; the commands here and in the README are current.
   `jq -cR 'fromjson? | .event? | select(.tag == "hint" or .tag == "mistake" or .tag == "goal") | {tag, when, text, rule} | with_entries(select(.value != null))' game_state/*.jsonl`
2. `make play ARGS='--start'` spawns the daemon. An "already running" error means it is.
3. `make play ARGS='--reset'` starts a new game or resumes a saved one.
4. Give the game one goal, only if its file has none (a resumed game keeps its goal). The targets are the best of each metric over all past games (`exp` is null until a game records it; skip a null):
   `jq -nRc '[inputs|fromjson?|.logs?|select(.)]|{dlvl:(map(.dlvl)|max),xl:(map(.xp)|max),exp:(map(.exp)|max),gold:(map(.gold)|max),hpmax:(map(.hpmax)|max),pwmax:(map(.pwmax)|max),ac:(map(.ac)|min)}' game_state/*.jsonl`
   The goal is to beat, strictly, more than half of the metrics that have a target (AC beats by going lower). With no past logs, the goal is to survive and reach Dlvl 2. Write it as a `goal` note, only when none exists:
   `f=$(cat /tmp/nle-daemon/game.log); jq -e 'select(.event?.tag=="goal")' "$f" >/dev/null || jq -nc --arg text "<targets and aim>" '{event:{kind:"note",tag:"goal",text:$text}}' >> "$f"`

On every call:

1. Read the top line and the last status line before sending keys: `--More--`, `[yn]`, Hungry, Weak, Fainting, low HP.
2. Send one key per call while a peaceful (guard, shopkeeper, watchman) is within 2 squares, and never batch keys near a prompt: `y` and `n` are answers as well as moves.
3. A batch that stops early is already logged as a `violation` note. Treat it as a mistake to learn from.

Constraints:

- `--reset` on a running game abandons it and opens a new game file. To keep a game, `--stop save` first.
- Run one `make play` call at a time: the pipes have one reader and one writer and no lock.
- `game_state/*.jsonl` is append-only. Never edit or delete a line or a file.

To end a session use `make play ARGS='--stop save'` (resumes later, and it can be repeated) or a plain `--stop` (ends the game).

## Lessons (gate table)

Rules your own games taught. Mechanical rows are enforced by the driver: it refuses the batch before any key is sent, prints `REFUSED <id>`, and records a `violation` note carrying the gate id. Soft and Discipline rows depend on you. Evidence is `game_state/001_nle_daemon.jsonl` (001) or `game_state/002_nle_daemon.jsonl` (002), then the line number.

| Id | Trigger | Required action | Enforcement | Evidence |
|---|---|---|---|---|
| G2 | More than one key in a call while a peaceful is within 2 squares | One key per call; never batch near a prompt (`y` answers yes) | Mechanical | 001:10, 001:6 |
| G4 | Rest or search (`s`, `.`, count prefix read) with a hostile in view, or more than 10 turns in one call | Deal with the monster first; rest in calls of 10 or fewer, watching HP | Mechanical | 001:5, 001:33, 002:918 |
| G7 | A move or `F` into a gas spore | Never melee it; keep away or attack from range | Mechanical | 001:7 |
| S1 | A corpse lying with items on the square you are about to enter | Do not step on it: it is a trap victim (the driver cannot see the items) | Soft | 001:8, 002:296 |
| S4 | Before `>`: HP at or below 70 percent, or a hunger or status warning | Rest or heal first, unless fleeing or using a trap door on purpose | Soft | 001:37 |

After a game ends (or you stop for good): for each `mistake` or `violation` note and the `death` event, print one line `Gate-table check: Mechanical / Soft / Discipline / no row, generic / pynle-specific -- <reason>`. Where a lesson needs a new or edited row, save the draft row (Id, Trigger, Required action, Enforcement, Evidence) as a `hint` note. Only propose: the operator files the change.

Recording a lesson (needs a game started with `--reset`), until `make play` has a `--note` verb (tags: `mistake`, `insight`, `hint`, `item_id`, `goal`):
`jq -nc --arg tag mistake --arg text "<what happened, and the rule>" '{event:{kind:"note",tag:$tag,text:$text}}' >> "$(cat /tmp/nle-daemon/game.log)"`
