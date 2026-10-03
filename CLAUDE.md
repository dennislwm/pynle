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
   `f=$(cat game_state/pipe/game.log); jq -e 'select(.event?.tag=="goal")' "$f" >/dev/null || jq -nc --arg text "<targets and aim>" '{event:{kind:"note",tag:"goal",text:$text}}' >> "$f"`

On every call:

1. Read the top line and the last status line before sending keys: `--More--`, `[yn]`, Hungry, Weak, Fainting, low HP.
2. Send one key per call while a peaceful (guard, shopkeeper, watchman) is within 2 squares, and never batch keys near a prompt: `y` and `n` are answers as well as moves.
3. A batch that stops early is already logged as a `violation` note. Treat it as a mistake to learn from. After an HP-loss stop, read the top line and name the cause before the next key; do not write it off as "probably a trap".
4. An `advice:` line (odds for what to do next, with a confidence) or a `hint:` line (which nav hint to read) may follow the nav hints. Confidence is how concentrated the odds are, not a promise of being right: a close split between the top two is a weak lean. A `REFUSED` gate and the gate table win. How they work: [Recording a claude-play game](README.md#recording-a-claude-play-game).

Constraints:

- `--reset` is refused (G9) while a game is running, whatever its turn. A game ends by death or by the operator. To leave a game, `--stop`: the next `--reset` resumes it.
- Run one `make play` call at a time: the pipes have one reader and one writer and no lock.
- `game_state/*.jsonl` is append-only. Never edit or delete a line or a file.

To end a session use `make play ARGS='--stop'`. It saves, resumes later, and can be repeated. Never use `--stop discard`: it ends the game with no save, and only the operator may run it. The driver logs it as a violation.

## Lessons (gate table)

Rules games taught, from this repo's own games or the reference's. Mechanical rows are enforced by the driver: it refuses the batch before any key is sent, prints `REFUSED <id>`, and records a `violation` note carrying the gate id. Soft and Discipline rows depend on you. Filter by Category (Combat, Loot, Navigation, Descent, Process) instead of rereading the table. Key syntax is in `make play ARGS='--help'`; rows name the command only. Evidence is `game_state/001_nle_daemon.jsonl` (001) or `game_state/002_nle_daemon.jsonl` (002), then the line number, or `ref:` plus a section of `../claude-code-nethack/.claude/skills/nethack-navigation/SKILL.md` at commit `9114caf` (prose there, not checked against its game logs).

Rows S7, S8, S9 and S11 moved into the advisor's rubrics in `nle/scripts/advice.json`, which cite the ids. Their text and evidence are in this file's git history (`git log -p -- CLAUDE.md`). Mechanical rows stay here.

| Id | Category | Trigger | Required action | Enforcement | Evidence |
|---|---|---|---|---|---|
| G2 | Combat | More than one key in a call while a peaceful is within 2 squares | One key per call. Never batch near a prompt (`y` answers yes): that half is Discipline, the driver checks only the peaceful | Mechanical | 001:10, 001:6 |
| G3 | Combat | `F` at a tame or peaceful monster, or a move into a peaceful one (a move into a tame pet is a swap) | Never attack them; route around | Mechanical | 001:3, 001:6, 001:10 |
| G4 | Combat | A call made only of digits, `s` and `.` (a rest or search, count prefix read) with a hostile in view, or more than 10 turns | Deal with the monster first; rest in calls of 10 or fewer, watching HP. The refusal names the nearest hostile and its distance. A `.` or `s` that answers a prompt inside a longer call (`_<.`) is not a rest | Mechanical | 001:5, 001:33, 002:918, 002:1377 |
| G5 | Combat | A rest or search call while the status line shows Hungry, Weak or Fainting | Eat first | Mechanical | 001:5 |
| G7 | Combat | A move or `F` into a gas spore or a floating eye | Never melee it; keep away or attack from range | Mechanical | 001:7, ref: Procedure 5 |
| G8 | Combat | A call of only moves, `F`, `s`, `.` and digits with more than one action while a hostile is adjacent (`F` and its direction count as one; a cast, throw or quaff is not refused) | One action per call, checking HP between them | Mechanical | ref: Threat ladder rung 3, jackal death |
| G9 | Process | `--reset` while a game is running, whatever its turn | Never abandon a game: `--stop` saves it, and the next `--reset` resumes it | Mechanical | 003 and 004: one daemon (pid 11413), 003 replaced by 004; neither has a `death` event |
| G10 | Process | A batch with `&q` (meta-q, quit) | Never quit: it ends the game with no save and the record shows a death | Mechanical | probe: `&q` then `y` gives `done` with end_status 1; no game incident, an operator decision |
| S1 | Loot | A corpse you did not kill, on a square you are about to enter | Treat it as a trap square: skip it, or enter only at HP above 70 percent and not fleeing. A corpse you just killed is a loot event (S9) | Soft | 001:8, 002:296 |
| S4 | Descent | Before `>`: HP at or below 70 percent, or a hunger or status warning | Rest or heal first, unless fleeing or using a trap door on purpose. Rest only on the upstairs or in a dead end | Soft | 001:37, 002:918 |
| S5 | Combat | A hostile adjacent and HP at or below twice its largest hit | Disengage first; if retreat will not open distance (a monster as fast as you), engrave Elbereth (humans `@` and minotaurs ignore it; against elves or archers, zap digging down: `z`, letter, `>`; monsters not adjacent do not follow). `pray` does not cancel attacks during the several turns it takes to resolve: last resort, and only while still above zero, not at it. Exception: a monster of speed 3 or less when one hit cannot kill and retreat is open | Discipline | ref: Threat ladder, prayer death; 002:1939, 006 nurse death; 008 small mimic |
| S6 | Combat | A heavy hitter or stealer in view, not adjacent | Fire or throw first; leave by an open route if HP falls fast. Drop carried gold first. Never let a mind flayer (message "wave of psychic energy") get adjacent: sleep resists about 93 percent, so avoid it. A leprechaun hall: skip it | Discipline | ref: Threat ladder, rothe and chameleon death; 002:1621, 002:2607; 008 Dlvl11 hall |
| S10 | Loot | An unidentified wand picked up, no hostile adjacent; or unknown boots or armor | Engrave-test the wand the same turn; identify boots and armor before wearing | Discipline | ref: Procedure 6, untested wand at death; 006 insights (cursed fumble boots) |
| S12 | Loot | Weapon or armor found, while `CHARACTER` is a Monk | Fight bare-handed and wear no armor by default | Discipline | ref: guidebook lines 137 and 3078 (only these two checked) |
| S13 | Loot | Pet-only food (tripe, egg) with a pet present | Throw it to the pet | Discipline | ref: Guidebook 6.2; no incident |
| S14 | Navigation | About to search a dead end | Step onto its unrevealed neighbours first | Discipline | ref: Fastest way rung 6, 20 wasted searches |
| S15 | Navigation | A branch seems to loop back | Walk it to its end before calling it a loop | Discipline | ref: Fastest way rung 6, missed stairs |
| S16 | Navigation | A boulder blocks the route | Push from every angle, then a wand, a pick-axe, then drop everything | Discipline | ref: Fastest way rung 6; no incident |
| S17 | Navigation | Travel (`_`) | Confirm only on an explored tile; if G4 refuses a lone `.`, confirm with `,` | Discipline | ref: Procedure 7, silent retarget |
| S18 | Descent | Arriving on a new level | Check the branch (Mines or main) with the attributes command | Discipline | ref: After descending, two Mines levels |
| S19 | Descent | No `>` on a fully explored level | Climb to the branch level and take the other `>` | Discipline | ref: No stairs down |
| S20 | Loot | A peaceful `@` among stacked items (a shop) | Check it is peaceful, buy food only if low, read every price; one key per call (G2) | Discipline | ref: Entering a shop |
| S21 | Loot | Unidentified items carried into a shop | Drop each, read the quote, pick it back up; judge retrieval against gold on hand. Find a heard shop (cash register) while shallow, and bring unused armor and weapons too: selling adds gold, a goal metric | Discipline | ref: Entering a shop, own stock; 002:2842 |
| S22 | Loot | A high-value price-ID with no identify path | Sell it, or use it under controlled conditions (full HP, cleared room, nothing adjacent), this visit; never sell worn gear or tools | Discipline | ref: Entering a shop, 12 logs with no identify or altar |
| S23 | Process | Ending a turn after ordinary progress | State the decision and go on; do not ask "keep going?" | Discipline | ref: Process row, confirmed 3 times |
| S24 | Process | A call returns REFUSED or STOPPED | Stop, read the screen and name the cause before the next key. Never repeat the same call or loop it | Discipline | 002:1542 to 002:1547 |
| S25 | Navigation | Ending a farlook (`;`) | Press Esc, not `.`. The name already shows on the top line as the cursor moves | Discipline | 002:1065, 002:2203 (9 of 10 lone-`.` refusals in 002) |
| S26 | Combat | A dangerous monster (rothe, ant, leprechaun, wererat, queen bee) adjacent or lined up, with 5 Pw | Cast sleep (`Za`, direction), then punch while it is frozen; recast if it wakes. Elves, vampire bats and lights resist. A leprechaun: drop gold first (S6) | Discipline | 006 insights: rothe, leprechaun, wererat, queen bee |
| S27 | Combat | A room of many monsters (beehive, zoo) | Fight from the doorway so 1 or 2 reach you; sleep the strongest first, kill awake weak ones next | Discipline | 006 insights: beehive at HP 16 of 47, zoo |
| S28 | Combat | Starting to dig a pit or hole downward | Never start it with a monster or sensed digit within 2 squares; a self-dug pit blocks fleeing | Discipline | 006 nurse death, Dlvl22 T2529 |
| S29 | Navigation | `least_explored:` names a quadrant at less coverage than the others | Take the room(s) lying in that quadrant (N/S split at map row 10, E/W split at map col 40) and run its perimeter (S14) before searching elsewhere | Discipline | ADR-05; 001, 004 hints (wall-search technique, 6 hidden doors found this way in 004) |
| S30 | Combat | About to rest or search below 70 percent HP, not already on the upstairs or a known dead end | Check `paths:` first; move to the nearest dead end or choke point it names before resting. Send one rest call per tool call and read HP each time | Discipline | ADR-05, S4; 002 rothe mistake (HP 17/40 to 6, 2 of 3 healing potions lost); 007 blind rest loop (27 to 18) |
| S31 | Navigation | A call of more than 4 moves into an unexplored corridor, door or dark room | Send at most 4 moves per call so a new room or letter is read before the next key | Discipline | 007 death, Dlvl7 T2312 |
| S32 | Combat | Crossing a crowded room (Big Room, zoo) with many awake monsters | On the stairs drop a scroll of scare monster (autopickup off with `@`), stack the protection spell to its cap, then move at once; the bonus decays 1 AC per 10 turns | Discipline | 008 Dlvl12 Big Room, 13-square dash with no damage |

Every time a game ends or you run `--stop`: take the `mistake` and `violation` notes, and any `death` event, after the last note whose text starts `Gate-table check:`. For each, save an `insight` note `Gate-table check: Mechanical / Soft / Discipline / no row, generic / pynle-specific -- <reason>` (add `-> <Id>` when you file a row) and print it. For a `death` event only, also ask whether an exit was in reach and whether an escape or defensive item went unused. Also name each tactic that worked since the last check (a kill, escape or recovery that cost little HP): save it as an `insight` note `Tactic that worked: <what>` with a `rule`, and file it by the Soft or Discipline bullet below unless a row already covers it.

- Soft or Discipline lesson: edit the row that already covers it, or the rubric in `nle/scripts/advice.json` if one does, else add a row with the next unused Id (Evidence cites the note's game file and line). Draft the text first, run `ponytail:ponytail-review` on the draft, apply its cuts to the draft, and only then edit this file; never edit first and review after. If the skill is unavailable, cut the draft to one trigger and one action. Skipping the review is a `mistake` note. Run no git commands: leave the edit uncommitted for the operator to review with `git diff`.
- Mechanical lesson, or a change to a Mechanical row: save a `hint` only, since it needs a driver gate.
- Generic lesson: fold a `Note for the operator:` line into the same `Gate-table check:` insight note, instead of a separate `hint` note. Never edit `../claude-code-nethack`.

Recording a lesson (needs a game started with `--reset`), until `make play` has a `--note` verb (tags: `mistake`, `insight`, `hint`, `item_id`, `goal`):
`jq -nc --arg tag <tag> --arg text "<what happened>" --arg rule "<the rule>" '{event:{kind:"note",tag:$tag,text:$text,rule:$rule}}' >> "$(cat game_state/pipe/game.log)"`
A hand note carries no turn or level; the driver's notes do.
