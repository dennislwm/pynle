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

Recording a lesson (needs a game started with `--reset`), until `make play` has a `--note` verb (tags: `mistake`, `insight`, `hint`, `item_id`, `goal`):
`jq -nc --arg tag mistake --arg text "<what happened, and the rule>" '{event:{kind:"note",tag:$tag,text:$text}}' >> "$(cat /tmp/nle-daemon/game.log)"`
