![NetHack Learning Environment (NLE)](https://github.com/NetHack-LE/nle/raw/main/dat/nle/logo.png)

--------------------------------------------------------------------------------

<p align="center">
  <a href="https://github.com/NetHack-LE/nle/actions/workflows/test_and_deploy.yml"><img src="https://github.com/NetHack-LE/nle/actions/workflows/test_and_deploy.yml/badge.svg?branch=main" /></a>
  <a href="https://pypi.python.org/pypi/nle/"><img src="https://img.shields.io/pypi/v/nle.svg" /></a>
  <a href="https://pepy.tech/project/nle"><img src="https://static.pepy.tech/personalized-badge/nle?period=total&units=international_system&left_color=black&right_color=orange&left_text=Downloads" /></a>
  <a href="https://twitter.com/NetHack_LE"><img src="https://img.shields.io/twitter/follow/NetHack_LE?label=Twitter&style=social" alt="Twitter" /></a>
 </p>

The NetHack Learning Environment (NLE) is a Reinforcement Learning environment originally presented at [NeurIPS 2020](https://neurips.cc/Conferences/2020).
This version of NLE is based on [NetHack 3.6.7](https://github.com/NetHack/NetHack/releases/tag/NetHack-3.6.7_Released) and designed to provide a standard RL interface to the game, and comes with tasks that function as a first step to evaluate agents on this new environment.

NetHack is one of the oldest and arguably most impactful videogames in history,
as well as being one of the hardest roguelikes currently being played by humans.
It is procedurally generated, rich in entities and dynamics, and overall an
extremely challenging environment for current state-of-the-art RL agents, while
being much cheaper to run compared to other challenging testbeds. Through NLE,
we wish to establish NetHack as one of the next challenges for research in
decision making and machine learning.

You can read more about NLE in the [NeurIPS 2020 paper](https://arxiv.org/abs/2006.13760), and about NetHack in its [original
README](./README.nh), at [nethack.org](https://nethack.org/), and on the
[NetHack wiki](https://nethackwiki.com).

![Example of an agent running on NLE](https://github.com/NetHack-LE/nle/raw/main/dat/nle/example_run.gif)

This version of NLE uses the [Farama Organisation Gymnasium Environment](https://gymnasium.farama.org) APIs.


# Getting started

Starting with NLE environments is extremely simple, provided one is familiar
with other gym (or Gynmasium) / RL environments.


## Installation

NLE requires `python>=3.10`, `cmake>=3.28` to be installed and available both when building the
package, and at runtime.

On **MacOS**, one can use `Homebrew` as follows:

``` bash
$ brew install cmake
```

On a plain **Ubuntu 18.04** distribution, `cmake` and other dependencies
can be installed by doing:

```bash
# Python and most build deps
$ sudo apt-get install -y build-essential autoconf libtool pkg-config \
    python3-dev python3-pip python3-numpy git flex bison libbz2-dev

# recent cmake version
$ wget -O - https://apt.kitware.com/keys/kitware-archive-latest.asc 2>/dev/null | sudo apt-key add -
$ sudo apt-add-repository 'deb https://apt.kitware.com/ubuntu/ bionic main'
$ sudo apt-get update && apt-get --allow-unauthenticated install -y \
    cmake \
    kitware-archive-keyring
```

Afterwards it's a matter of setting up your environment. We advise using a conda
environment for this:

```bash
$ conda create -y -n nle python=3.10
$ conda activate nle
$ pip install nle
```


NOTE: If you want to extend / develop NLE, please install the package as follows:

``` bash
$ git clone https://github.com/NetHack-LE/nle
$ pip install -e ".[dev]"
$ pre-commit install
```


## Development workflow

Requires: [`cmake`, `uv`](#installation) already installed.

The steps above under "extend / develop NLE" are also available as `make`
targets from the repo root, for the `uv`-based workflow:

1. `make setup` -- one-time: checks `uv`/`cmake` are available, installs the
   `pre-commit` git hook.
2. `make test` -- day-to-day: builds the compiled extension
   (`uv sync --extra dev`, automatic since `test` depends on `build`), then
   runs `pytest`.
3. `make check-pins` / `make status` -- anytime, read-only checks.
4. `make play ARGS='--reset'` -- drive a live game and record it, see
   [Recording a claude-play game](#recording-a-claude-play-game).

Run `make help` for the full target list.


## Docker

We have provided some docker images. Please see the [relevant
README](https://github.com/NetHack-LE/nle/blob/main/docker/README.md). 


## Trying it out

After installation, one can try out any of the provided tasks as follows:

```python
>>> import gymnasium as gym
>>> import nle
>>> env = gym.make("NetHackScore-v0")
>>> env.reset()  # each reset generates a new dungeon
>>> env.step(1)  # move agent '@' north
>>> env.render()
```

NLE also comes with a few scripts that allow to get some environment rollouts,
and play with the action space:

```bash
# Play NetHackStaircase-v0 as a human
$ python -m nle.scripts.play

# Use a random agent
$ python -m nle.scripts.play --mode random

# Play the full game using directly the NetHack internal interface
# (Useful for debugging outside of the gymnasium environment)
$ python -m nle.scripts.play --env NetHackScore-v0 # works with random agent too

# See all the options
$ python -m nle.scripts.play --help
```

Note that `nle.scripts.play` can also be run with `nle-play`, if the package
has been properly installed.

### Driving a live episode across separate calls

`nle.scripts.nle_daemon` holds one live NLE episode in a background process
whose lifecycle is independent of any single caller, and exchanges one
action/observation pair per call over two POSIX FIFOs:

```python
>>> from nle.scripts.nle_daemon import NLEDaemon
>>> d = NLEDaemon("/tmp/nle-daemon").start()  # spawns the daemon, blocks until ready
>>> d.is_alive()
True
>>> d.step(0)
>>> d.obs["glyphs"]
...
>>> d.reset()
>>> d.status()  # re-reads the last observation without advancing a turn
>>> d.stop()  # blocks until the daemon has exited
```

By itself, this does not survive the daemon process dying (crash, OOM-kill,
host reboot) -- a caller reconnecting later against a still-alive daemon is
the failure mode this covers on its own. See the pynle wiki's
`decisions/adr-01-live-episode-turn-driver.md` for the full record.

### Surviving a killed or restarted daemon

`stop(save=True)` writes a native NetHack save file before shutting down,
mirroring NetHack's own save-and-quit -- and because the daemon reuses its
own identity directory as NetHack's working directory, a fresh `NLEDaemon`
started on that same directory resumes automatically:

```python
>>> from nle.scripts.nle_daemon import NLEDaemon
>>> d = NLEDaemon("/tmp/nle-daemon").start()
>>> d.reset()
>>> d.step(0)
>>> d.stop(save=True)  # saves, then shuts down -- always stops either way
>>> # ... daemon process is gone; later, in a new process: ...
>>> d = NLEDaemon("/tmp/nle-daemon").start()
>>> d.reset()  # resumes the saved episode, not a fresh game
```

Saving only ever succeeds once per episode -- NetHack's own save flag is
zeroed after a successful save and never re-armed during normal play, so
`save()` is wired into shutdown rather than offered as a repeatable
mid-episode action. A plain `stop()` (no `save=True`) leaves nothing behind
to resume. Each resume starts a new process, so a resumed game can be saved
again.

### Recording a claude-play game

See the pynle wiki's `decisions/adr-02-per-game-jsonl-record.md`. Claude
playing: see [CLAUDE.md](CLAUDE.md).

Requires: [`make build`](#development-workflow) run once.

`nle.scripts.claude_play` drives the daemon one call at a time and records
each game to one jsonl file in `game_state/` (untracked), one top-level key
per line:

1. `make play ARGS='--start'` spawns the daemon.
2. `make play ARGS='--reset'` starts a new game, or resumes a saved one, and
   prints the game file.
3. `make play ARGS='hjkl'` sends the keys and prints the screen. A batch stops
   early when:
   - HP drops.
   - The game shows `--More--` or a `[yn]` prompt.
   - A hunger warning appears.

   Each early stop is recorded as a note tagged `violation`.
4. `make play ARGS='--stop save'` saves and stops. `--start` then `--reset`
   resumes into the same file. A plain `--stop` ends the game, and the next
   `--reset` opens a new file.

Verbs and key syntax: `make play ARGS='--help'`. Pass keys as one quoted
argument, because a space is a key too. For a key the shell or `make` would eat
(such as `"`), call `uv run python -m nle.scripts.claude_play '<keys>'`
directly.

The file has four kinds of line: `game` (once, first), `session` (one per
start or resume), `logs` (one per step) and `event` (`kind` is `death`,
`note` or `level`). Query it with `jq`:

```bash
# HP by turn
jq -r 'select(has("logs")) | [.logs.t, .logs.hp, .logs.dlvl] | @tsv' game_state/002_nle_daemon.jsonl
# deaths and notes, violations included
jq -c 'select(.event.kind == "death" or .event.kind == "note") | .event' game_state/002_nle_daemon.jsonl
```

Guard every comparison with `has("logs")`: in `jq` a missing key sorts below
any number, so `select(.logs.hp < 10)` also matches the `game` line. After a
hard kill the last line can be cut off; `jq -cR 'fromjson? | ...'` skips it.

Additionally, a [TorchBeast](https://github.com/facebookresearch/torchbeast)
agent is bundled in `nle.agent` together with a simple model to provide a
starting point for experiments:

``` bash
$ pip install "nle[agent]"
$ python -m nle.agent.agent --num_actors 80 --batch_size 32 --unroll_length 80 --learning_rate 0.0001 --entropy_cost 0.0001 --use_lstm --total_steps 1000000000
```

Plot the mean return over the last 100 episodes:
```bash
$ python -m nle.scripts.plot
```
```
                              averaged episode return

  140 +---------------------------------------------------------------------+
      |             +             +            ++-+ ++++++++++++++++++++++++|
      |             :             :          ++++++++||||||||||||||||||||||||
  120 |-+...........:.............:...+-+.++++|||||||||||||||||||||||||||||||
      |             :        +++++++++++++++||||||||||AAAAAAAAAAAAAAAAAAAAAA|
      |            +++++++++++++||||||||||||||AAAAAAAAAAAA|||||||||||||||||||
  100 |-+......+++++|+|||||||||||||||||||||||AA||||||||||||||||||||||||||||||
      |       +++|||||||||||||||AAAAAAAAAAAAAA|||||||||||+++++++++++++++++++|
      |    ++++|||||AAAAAAAAAAAAAA||||||||||||++++++++++++++-+:             |
   80 |-++++|||||AAAAAA|||||||||||||||||||||+++++-+...........:...........+-|
      | ++|||||AAA|||||||||||||||++++++++++++-+ :             :             |
   60 |++||AAAAA|||||+++++++++++++-+............:.............:...........+-|
      |++|AA||||++++++-|-+        :             :             :             |
      |+|AA|||+++-+ :             :             :             :             |
   40 |+|A+++++-+...:.............:.............:.............:...........+-|
      |+AA+-+       :             :             :             :             |
      |AA-+         :             :             :             :             |
   20 |AA-+.........:.............:.............:.............:...........+-|
      |++-+         :             :             :             :             |
      |+-+          :             :             :             :             |
    0 |-+...........:.............:.............:.............:...........+-|
      |+            :             :             :             :             |
      |+            +             +             +             +             |
  -20 +---------------------------------------------------------------------+
      0           2e+08         4e+08         6e+08         8e+08         1e+09
                                       steps
```

### NLE Language Wrapper

We thank [ngoodger](https://github.com/ngoodger) for implementing the [NLE Language Wrapper](https://github.com/ngoodger/nle-language-wrapper) that translates the non-language observations from NetHack tasks into similar language representations. Actions can also be optionally provided in text form which are converted to the Discrete actions of the NLE.

### NetHack Learning Dataset

The NetHack Learning Dataset (NLD) code now ships with `NLE`, allowing users to the load large-scale datasets featured in [Dungeons and Data: A Large-Scale NetHack Dataset](https://papers.neurips.cc/paper_files/paper/2022/file/9d9258fd703057246cb341e615426e2d-Paper-Datasets_and_Benchmarks.pdf), while also generating and loading their own datasets.

```python
import nle.dataset as nld

if not nld.db.exists():
    nld.db.create()
    # NB: Different methods are used for data based on NLE and data from NAO.
    nld.add_nledata_directory("/path/to/nld-aa", "nld-aa-v0")
    nld.add_altorg_directory("/path/to/nld-nao", "nld-nao-v0")

dataset = nld.TtyrecDataset("nld-aa-v0", batch_size=128, ...)
for i, mb in enumerate(dataset):
    foo(mb) # etc...
```

For information on how to download NLD-AA and NLD-NAO, see the dataset doc [here](https://github.com/NetHack-LE/nle/blob/main/DATASET.md).

Otherwise checkout the tutorial Colab notebook [here](https://colab.research.google.com/drive/1GRP15SbOEDjbyhJGMDDb2rXAptRQztUD?usp=sharing).

# Papers using the NetHack Learning Environment
- Henaff et al. [Scalable Option Learning in High-Throughput Environments](https://arxiv.org/abs/2509.00338), arXiv:2509.00338 \[cs.LG\].
- Paglieri et al. [BALROG: Benchmarking Agentic LLM and VLM Reasoning On Games](https://arxiv.org/abs/2411.13543) (UCL, IDEAS NCBR, NYU, Oxford, Anthropic, ICLR 2025)
- Klissarov et al. [MaestroMotif: Skill Design from Artificial Intelligence Feedback](https://arxiv.org/abs/2412.08542) (Mila, FAIR, UT Austin, Alberta, Amii, ICLR 2025) 
- Klissarov et al. [Motif: Intrinsic Motivation from Artificial Intelligence Feedback](https://arxiv.org/abs/2310.00166) (Mila, FAIR, UT Austin, ICLR 2024) 
- Izumiya and Simo-Serra [Inventory Management with Attention-Based Meta Actions](https://esslab.jp/~ess/publications/IzumiyaCOG2021.pdf) (Waseda University, CoG 2021).
- Samvelyan et al. [MiniHack the Planet: A Sandbox for Open-Ended Reinforcement Learning Research](https://arxiv.org/abs/2109.13202) (FAIR, UCL, Oxford, NeurIPS 2021).
- Zhang et al. [BeBold: Exploration Beyond the Boundary of Explored Regions](https://arxiv.org/abs/2012.08621) (Berkley, FAIR, Dec 2020).
- Küttler et al. [The NetHack Learning Environment](https://arxiv.org/abs/2006.13760) (FAIR, Oxford, NYU, Imperial, UCL, NeurIPS 2020).

Open a [pull
request](https://github.com/NetHack-LE/nle/edit/main/README.md)
to add papers.



# Contributing

We welcome contributions to NLE. If you are interested in contributing please
see [this document](https://github.com/NetHack-LE/nle/blob/main/CONTRIBUTING.md).


# Architecture

NLE is direct fork of [NetHack](https://github.com/nethack/nethack) and
therefore contains code that operates on many different levels of abstraction.
This ranges from low-level game logic, to the higher-level administration of
repeated nethack games, and finally to binding of these games to Python 
`gymnasium` environment.

If you want to learn more about the architecture of `nle` and how it works
under the hood, checkout the [architecture document](https://github.com/NetHack-LE/nle/blob/main/doc/nle/ARCHITECTURE.md).
This may be a useful starting point for anyone looking to contribute to the
lower level elements of NLE.


# Related Environments
- [gym\_nethack](http://campbelljc.com/research/gym_nethack/)
- [rogueinabox](https://github.com/rogueinabox/rogueinabox)
- [rogue-gym](https://github.com/kngwyu/rogue-gym)
- [MiniGrid](https://github.com/maximecb/gym-minigrid)
- [CoinRun](https://github.com/openai/coinrun)
- [MineRL](http://minerl.io/docs)
- [Project Malmo](https://www.microsoft.com/en-us/research/project/project-malmo/)
- [OpenAI Procgen Benchmark](https://openai.com/blog/procgen-benchmark/)
- [Obstacle Tower](https://github.com/Unity-Technologies/obstacle-tower-env)

# Interview about the environment with Weights&Biases
[Facebook AI Research’s Tim & Heiner on democratizing reinforcement learning research.](https://www.youtube.com/watch?v=oYSNXTkeCtw)

[![Interview with Weigths&Biases](https://img.youtube.com/vi/oYSNXTkeCtw/0.jpg)](https://www.youtube.com/watch?v=oYSNXTkeCtw)

# Citation

If you use NLE in any of your work, please cite:

```
@inproceedings{kuettler2020nethack,
  author    = {Heinrich K{\"{u}}ttler and
               Nantas Nardelli and
               Alexander H. Miller and
               Roberta Raileanu and
               Marco Selvatici and
               Edward Grefenstette and
               Tim Rockt{\"{a}}schel},
  title     = {{The NetHack Learning Environment}},
  booktitle = {Proceedings of the Conference on Neural Information Processing Systems (NeurIPS)},
  year      = {2020},
}
```

If you use NLD or the datasets in any of your work, please cite:

```
@article{hambro2022dungeons,
  title={Dungeons and Data: A Large-Scale NetHack Dataset},
  author={Hambro, Eric and Raileanu, Roberta and Rothermel, Danielle and Mella, Vegard and Rockt{\"a}schel, Tim and K{\"u}ttler, Heinrich and Murray, Naila},
  journal={Advances in Neural Information Processing Systems},
  volume={35},
  pages={24864--24878},
  year={2022}
}
```
