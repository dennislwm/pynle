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
3. A batch that stops early is already logged as a `violation` note. Treat it as a mistake to learn from. After an HP-loss stop, read the top line and name the cause before the next key; do not write it off as "probably a trap".

Constraints:

- `--reset` on a running game abandons it and opens a new game file. To keep a game, `--stop save` first.
- Run one `make play` call at a time: the pipes have one reader and one writer and no lock.
- `game_state/*.jsonl` is append-only. Never edit or delete a line or a file.

To end a session use `make play ARGS='--stop save'` (resumes later, and it can be repeated) or a plain `--stop` (ends the game).

## Lessons (gate table)

Rules games taught, from this repo's own games or the reference's. Mechanical rows are enforced by the driver: it refuses the batch before any key is sent, prints `REFUSED <id>`, and records a `violation` note carrying the gate id. Soft and Discipline rows depend on you. Filter by Category (Combat, Loot, Navigation, Descent, Process) instead of rereading the table. Key syntax is in `make play ARGS='--help'`; rows name the command only. Evidence is `game_state/001_nle_daemon.jsonl` (001) or `game_state/002_nle_daemon.jsonl` (002), then the line number, or `ref:` plus a section of `../claude-code-nethack/.claude/skills/nethack-navigation/SKILL.md` at commit `9114caf` (prose there, not checked against its game logs).

| Id | Category | Trigger | Required action | Enforcement | Evidence |
|---|---|---|---|---|---|
| G2 | Combat | More than one key in a call while a peaceful is within 2 squares | One key per call. Never batch near a prompt (`y` answers yes): that half is Discipline, the driver checks only the peaceful | Mechanical | 001:10, 001:6 |
| G4 | Combat | Rest or search (`s`, `.`, count prefix read) with a hostile in view, or more than 10 turns in one call | Deal with the monster first; rest in calls of 10 or fewer, watching HP | Mechanical | 001:5, 001:33, 002:918 |
| G7 | Combat | A move or `F` into a gas spore | Never melee it; keep away or attack from range | Mechanical | 001:7 |
| S1 | Loot | A corpse you did not kill, on a square you are about to enter | Treat it as a trap square: skip it, or enter only at HP above 70 percent and not fleeing. A corpse you just killed is a loot event (S9) | Soft | 001:8, 002:296 |
| S4 | Descent | Before `>`: HP at or below 70 percent, or a hunger or status warning | Rest or heal first, unless fleeing or using a trap door on purpose. Rest only on the upstairs or in a dead end | Soft | 001:37, 002:918 |
| S5 | Combat | HP critical with a hostile adjacent | Disengage first; if retreat will not open distance (a monster as fast as you), engrave Elbereth (humans `@` and minotaurs ignore it). `pray` does not cancel its next attack: last resort | Discipline | ref: Threat ladder, prayer death |
| S6 | Combat | A heavy hitter or stealer in view, not adjacent | Fire or throw first; leave by an open route if HP falls fast | Discipline | ref: Threat ladder, rothe and chameleon death |
| S7 | Combat | A floating eye, mold, lichen or acid blob adjacent | Never melee a floating eye (paralysis), no exception. Mold or lichen: route around or shoot. Acid blob only: melee when it is the sole way past and nothing ranged remains | Discipline | ref: Procedure 5, acid blob corridor |
| S8 | Combat | A hostile that S5 to S7 and G7 do not flag (never a peaceful) | Kill it: XP raises HP. Keep descending | Discipline | goal metrics; no incident |
| S9 | Loot | A kill you made leaves an item or a corpse | Step on it and read "Things that are here" this turn | Discipline | ref: Procedure 6, mummy drop |
| S10 | Loot | An unidentified wand picked up, no hostile adjacent | Engrave-test it the same turn | Discipline | ref: Procedure 6, untested wand at death |
| S11 | Loot | A chest or box | Loot it where it lies; force it only if locked. Take items from the list (`o`, then `a`), never with `A`, and never take a gray stone | Discipline | ref: Procedure 6, encumbrance; 002:1538 |
| S12 | Loot | Weapon or armor found, while `CHARACTER` is a Monk | Fight bare-handed and wear no armor by default | Discipline | ref: guidebook lines 137 and 3078 (only these two checked) |
| S13 | Loot | Pet-only food (tripe, egg) with a pet present | Throw it to the pet | Discipline | ref: Guidebook 6.2; no incident |
| S14 | Navigation | About to search a dead end | Step onto its unrevealed neighbours first | Discipline | ref: Fastest way rung 6, 20 wasted searches |
| S15 | Navigation | A branch seems to loop back | Walk it to its end before calling it a loop | Discipline | ref: Fastest way rung 6, missed stairs |
| S16 | Navigation | A boulder blocks the route | Push from every angle, then a wand, a pick-axe, then drop everything | Discipline | ref: Fastest way rung 6; no incident |
| S17 | Navigation | Travel (`_`) | Confirm only on an explored tile | Discipline | ref: Procedure 7, silent retarget |
| S18 | Descent | Arriving on a new level | Check the branch (Mines or main) with the attributes command | Discipline | ref: After descending, two Mines levels |
| S19 | Descent | No `>` on a fully explored level | Climb to the branch level and take the other `>` | Discipline | ref: No stairs down |
| S20 | Loot | A peaceful `@` among stacked items (a shop) | Check it is peaceful, buy food only if low, read every price; one key per call (G2) | Discipline | ref: Entering a shop |
| S21 | Loot | Unidentified items carried into a shop | Drop each, read the quote, pick it back up; judge retrieval against gold on hand | Discipline | ref: Entering a shop, own stock |
| S22 | Loot | A high-value price-ID with no identify path | Sell it, or use it under controlled conditions (full HP, cleared room, nothing adjacent), this visit; never sell worn gear or tools | Discipline | ref: Entering a shop, 12 logs with no identify or altar |
| S23 | Process | Ending a turn after ordinary progress | State the decision and go on; do not ask "keep going?" | Discipline | ref: Process row, confirmed 3 times |
| S24 | Process | A call returns REFUSED or STOPPED | Stop, read the screen and name the cause before the next key. Never repeat the same call or loop it | Discipline | 002:1542 to 002:1547 |

Every time a game ends or you run `--stop` (with or without `save`): for each `mistake` or `violation` note since the last check, and the `death` event, ask whether an exit was in reach and whether an escape or defensive item went unused, then print one line `Gate-table check: Mechanical / Soft / Discipline / no row, generic / pynle-specific -- <reason>`. Where a lesson needs a new or edited row, run `ponytail:ponytail-review` on the draft row (Id, Trigger, Required action, Enforcement, Evidence), apply its cuts, save it as a `hint` note, then file it in the gate table in the same check.

Recording a lesson (needs a game started with `--reset`), until `make play` has a `--note` verb (tags: `mistake`, `insight`, `hint`, `item_id`, `goal`):
`jq -nc --arg tag mistake --arg text "<what happened, and the rule>" '{event:{kind:"note",tag:$tag,text:$text}}' >> "$(cat /tmp/nle-daemon/game.log)"`
