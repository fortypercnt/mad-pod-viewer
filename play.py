#!/usr/bin/env python3
"""Plays one Mad Pod Racing game between two bots through the referee and saves a replay (JSON).

usage: python play.py "<command A>" "<command B>" [--seed S] [--timeout MS] [-o out.json]
example: python play.py "python examples/simple_bot.py" "./mybot"

A bot is any command that speaks the CodinGame protocol on stdin/stdout (stderr is recorded for the viewer).
Bot A is player 0. Commands run in the current directory. Without -o the replay goes to replays/.

Replay format (lists indexed by player 0 / 1):
  seed, date, players [{name, cmd}], laps, checkpoints [[x, y], ...], winner (-1 = none), note,
  turns: [{"in":  [[line, ...], [line, ...]],     # this turn's input lines (4 pods: own two first)
           "out": [[line, line], [line, line]],   # the two output lines
           "ms":  [t0, t1],                       # wall time from input sent to output read
           "err": [[line, ...], [line, ...]]}]    # stderr the bot printed during this turn
  init_err: stderr printed before the first turn's input.
"""
import argparse
import json
import os
import queue
import random
import re
import shlex
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPLAYS = os.path.join(HERE, "replays")
MAX_ERR_LINES = 200          # kept per bot per turn
DEFAULT_TIMEOUT_MS = 5000    # a bot that takes longer than this for one turn loses (it is probably stuck)
INTERPRETERS = {"python", "python3", "py", "pypy", "pypy3", "node", "java", "ruby", "php", "perl", "dotnet", "mono",
                "lua", "bash", "sh", "cmd", "go", "run", "-m", "-jar", "-u"}


def find_referee():
    """The compiled referee: referee/csbref(.exe), or the path in the environment variable CSBREF."""
    env = os.environ.get("CSBREF")
    if env:
        return env
    for name in ("csbref.exe", "csbref"):
        p = os.path.join(HERE, "referee", name)
        if os.path.isfile(p):
            return p
    raise FileNotFoundError("referee not found: build it with  cd referee && go build -o csbref" +
                            (".exe" if os.name == "nt" else "") + " csbref.go  (see README.md)")


def split_command(cmd, cwd=None):
    """A command line as Popen takes it: Windows parses the string itself, elsewhere it is split like a shell would.
    On Windows a relative program path ("./bot.exe", "..\\bots\\x.exe") would be looked up from this process's folder,
    not from cwd, so it is made absolute here."""
    if isinstance(cmd, list):
        return cmd
    if os.name != "nt":
        return shlex.split(cmd)
    if cwd:
        s = cmd.lstrip()
        if s.startswith('"'):
            end = s.find('"', 1)
            prog, rest = (s[1:end], s[end + 1:]) if end > 0 else (s[1:], "")
        else:
            prog, _, rest = s.partition(" ")
            rest = " " + rest if rest else ""
        if ("/" in prog or "\\" in prog) and not os.path.isabs(prog):
            return f'"{os.path.normpath(os.path.join(cwd, prog))}"{rest}'
    return cmd


def bot_name(cmd):
    """Short name for a command: the first word that is not an interpreter or an option, without folder and extension
    ("python bots/fast.py" -> "fast", "./mybot.exe" -> "mybot")."""
    words = cmd if isinstance(cmd, list) else shlex.split(cmd, posix=os.name != "nt")
    words = [w.strip('"\'') for w in words]
    for w in words:
        base = os.path.splitext(os.path.basename(w.replace("\\", "/")))[0]
        if base and base.lower() not in INTERPRETERS and not w.startswith("-"):
            return base
    return os.path.basename(words[0]) if words else "bot"


def _pump(stream, q):
    """Moves a bot's lines into a queue, each with the time it arrived."""
    for line in iter(stream.readline, ""):
        q.put((line.rstrip("\r\n"), time.perf_counter()))
    q.put((None, time.perf_counter()))   # end of stream


def play_logged(cmd0, cmd1, seed, names=None, timeout_ms=DEFAULT_TIMEOUT_MS, cwd=None):
    """Plays one game and returns the replay dict (see module docstring)."""
    ref = subprocess.Popen([find_referee()], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, text=True, bufsize=1)
    cmds = (cmd0, cmd1)
    log = {
        "seed": seed,
        "players": [{"name": (names or [None, None])[i] or bot_name(c), "cmd": c if isinstance(c, str) else " ".join(c)}
                    for i, c in enumerate(cmds)],
        "laps": None, "checkpoints": [], "winner": -1, "note": "", "turns": [], "init_err": [[], []],
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    bots = []
    try:
        for i, c in enumerate(cmds):
            try:
                bots.append(subprocess.Popen(split_command(c, cwd),stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE, text=True, bufsize=1, cwd=cwd))
            except OSError as e:
                raise RuntimeError(f"could not start bot {'AB'[i]} ({log['players'][i]['cmd']}): {e}")
    except Exception:
        for p in bots + [ref]:
            p.kill()
        raise
    outq = [queue.Queue(), queue.Queue()]
    errq = [queue.Queue(), queue.Queue()]
    for i, b in enumerate(bots):
        threading.Thread(target=_pump, args=(b.stdout, outq[i]), daemon=True).start()
        threading.Thread(target=_pump, args=(b.stderr, errq[i]), daemon=True).start()

    def take_err(p):
        lines = []
        while True:
            try:
                line, _ = errq[p].get_nowait()
            except queue.Empty:
                break
            if line is not None:
                lines.append(line)
        if len(lines) > MAX_ERR_LINES:
            lines = lines[:MAX_ERR_LINES] + [f"... ({len(lines) - MAX_ERR_LINES} more lines cut)"]
        return lines

    def read_output(p, n, deadline):
        """n lines of bot p, the time the last one arrived, and what went wrong (None, "timeout" or "exited")."""
        out, arrived = [], time.perf_counter()
        while len(out) < n:
            try:
                line, arrived = outq[p].get(timeout=max(0.0, deadline - time.perf_counter()))
            except queue.Empty:
                return out, time.perf_counter(), "timeout"
            if line is None:
                return out, arrived, "exited"
            out.append(line)
        return out, arrived, None

    init = [[], []]
    turn = None
    target = 0
    block = []
    first_block = [True, True]
    sent = [0.0, 0.0]   # when each bot was sent its last input line: its turn's clock starts there
    try:
        ref.stdin.write(f"###Seed {seed}\n###Start 2\n")
        ref.stdin.flush()
        while True:
            line = ref.stdout.readline()
            if not line:
                log["note"] = "referee closed"
                break
            if line.startswith("###Input"):
                target = int(line.split()[1])
                block = []
            elif line.startswith("###Output"):
                _, p, n = line.split()
                p, n = int(p), int(n)
                if p == 0:
                    turn = {"in": [None, None], "out": [None, None], "ms": [None, None], "err": [None, None]}
                    log["turns"].append(turn)
                first_block[p] = False
                if len(log["turns"]) == 1:
                    time.sleep(0.02)   # let the stderr reader catch up on start-up output
                    log["init_err"][p] = take_err(p)
                try:
                    bots[p].stdin.flush()
                except OSError:
                    pass
                t0 = sent[p] or time.perf_counter()
                out, arrived, problem = read_output(p, n, t0 + timeout_ms / 1000.0)
                dt = arrived - t0
                turn["in"][p] = block[-4:]
                turn["out"][p] = out + [""] * (n - len(out))
                turn["ms"][p] = round(1000 * dt, 2)
                time.sleep(0.002)
                turn["err"][p] = take_err(p)
                if problem:
                    log["winner"] = 1 - p
                    log["note"] = (f"bot {p} gave no answer within {timeout_ms} ms" if problem == "timeout"
                                   else f"bot {p} exited")
                    break
                ref.stdin.write("".join(o + "\n" for o in out))
                ref.stdin.flush()
            elif line.startswith("###End"):
                parts = line.split()
                log["winner"] = int(parts[1])
                rest = " ".join(parts[2:])
                log["note"] = "" if rest.replace(" ", "").isdigit() else rest
                break
            else:
                stripped = line.rstrip("\r\n")
                block.append(stripped)
                if first_block[target]:
                    init[target].append(stripped)
                try:
                    bots[target].stdin.write(line)
                    sent[target] = time.perf_counter()
                except OSError:
                    pass   # the bot is gone; noticed when its output is read
    except (BrokenPipeError, OSError) as e:
        log["note"] = f"pipe error {e}"
    finally:
        for pr in bots + [ref]:
            try:
                pr.kill()
            except OSError:
                pass
        for pr in bots + [ref]:
            try:
                pr.wait(timeout=5)
            except Exception:
                pass
    # init block: laps, checkpoint count, checkpoints, then the first turn's 4 pod lines
    ib = init[0]
    if len(ib) >= 2:
        log["laps"] = int(ib[0])
        ncp = int(ib[1])
        log["checkpoints"] = [[int(v) for v in ib[2 + k].split()] for k in range(ncp)]
    # a turn the game ended in may be incomplete (player 1 never asked)
    log["turns"] = [t for t in log["turns"] if t["out"][0] is not None]
    return log


def default_path(log):
    os.makedirs(REPLAYS, exist_ok=True)
    a, b = (re.sub(r"[^\w.-]+", "-", p["name"]) for p in log["players"])
    return os.path.join(REPLAYS, f"{time.strftime('%Y%m%d_%H%M%S')}_{a}_vs_{b}_{log['seed']}.json")


def main():
    ap = argparse.ArgumentParser(description="Plays one game between two bot commands and saves the replay.")
    ap.add_argument("a", help='command of bot A (player 0), e.g. "python bot.py"')
    ap.add_argument("b", help="command of bot B (player 1)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_MS, help="ms per turn before a bot loses as stuck")
    ap.add_argument("-o", default=None, help="replay file (default replays/<time>_<A>_vs_<B>_<seed>.json)")
    args = ap.parse_args()
    seed = args.seed if args.seed is not None else random.randrange(1, 2**31)
    log = play_logged(args.a, args.b, seed, timeout_ms=args.timeout)
    path = args.o or default_path(log)
    with open(path, "w") as f:
        json.dump(log, f, separators=(",", ":"))
    w = log["winner"]
    who = log["players"][w]["name"] if w >= 0 else "nobody"
    print(f"seed {seed}: {who} wins after {len(log['turns'])} turns ({log['note'] or 'finished'}) -> {path}")


if __name__ == "__main__":
    sys.exit(main())
