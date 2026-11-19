# Mad Pod Racing viewer

A local game viewer for CodinGame's **Mad Pod Racing** (Coders Strike Back): enter two bot commands, play a game
through a referee, and watch the replay in the browser, with each bot's stdin, stdout and stderr shown turn by turn.

- Any bot that speaks the CodinGame protocol works: `python bot.py`, `./bot`, `bot.exe`, `node bot.js`, `java -jar bot.jar` ...
- Replays are saved as JSON in `replays/` and can be reopened from the page.
- Playback: play/pause, step, scrub, speed, zoom and pan, fullscreen; overlays for trails, thrust, hitboxes, the
  bots' targets, race positions, messages and collision effects; a chart of each bot's response time per turn.
- The page re-simulates every turn with a port of the referee physics, to animate collisions within a turn and to show
  the final turn. It reports if its physics ever disagrees with the referee's log.

## Requirements

- Python 3.8+ (standard library only)
- [Go](https://go.dev/dl/) 1.18+ to build the referee (once)
- A modern browser

## Setup

Build the referee:

```sh
cd referee
go build -o csbref csbref.go        # Windows: go build -o csbref.exe csbref.go
```

The server looks for `referee/csbref` (or `csbref.exe`); set the environment variable `CSBREF` to use a referee
somewhere else.

## Usage

```sh
python server.py                    # then open http://localhost:8770
```

Enter a command for each bot and press **Play game**. Try it with the included example bot,
`python examples/simple_bot.py`, on both sides.

Options:

| Option | Meaning |
|---|---|
| `port` (first argument) | port to listen on, default 8770 |
| `--dir <folder>` | folder the bot commands run in; default the folder the server was started from |
| `--timeout <ms>` | a bot that takes longer than this for one turn loses; default 5000 |

The viewer shows each turn's response time and marks turns over CodinGame's limits (1000 ms for the first turn,
75 ms after that). It doesn't enforce those limits, so slow debug builds can still finish a game.

Games can also be played without the page:

```sh
python play.py "python examples/simple_bot.py" "./mybot" --seed 42
```

## The referee

`referee/csbref.go` is [robostac/coders-strike-back-referee](https://github.com/robostac/coders-strike-back-referee)
(MIT license, see `referee/LICENSE`), with one change so that maps are generated the way the CodinGame server
generates them today:

- one of the 13 base maps, with its checkpoint order **rotated** (any checkpoint can be the start, but the sequence
  is kept), where the original shuffled the order;
- each checkpoint moved by up to ±30 in x and y;
- always 3 laps.

The protocol it speaks is the one of CodinGame's multiplayer game runners (`###Start`, `###Input`, `###Output`, `###End`).
A seed makes a game reproducible.

## Replay format

See the docstring of `play.py`. In short: seed, players (name, command), laps, checkpoints, winner, and per turn
each bot's input lines, output lines, response time and stderr.

## Security

The server runs whatever commands are entered on the page. It only listens on `127.0.0.1` and refuses requests
that don't come from its own page, but don't expose it to a network.

## Assets

`web/assets/spritesheet.png` and `web/assets/background.jpg` are the game's graphics from CodinGame. They belong to
CodinGame and are not covered by this project's license.
