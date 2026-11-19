#!/usr/bin/env python3
"""Mad Pod Racing game viewer: enter two bot commands, play a game through the referee, watch the replay.

usage: python server.py [--port 8770] [--dir <folder the bot commands run in>] [--timeout MS]
then open http://localhost:8770

Bot commands run in --dir (default: the folder the server was started from), so "python bot.py" or "./bot.exe"
work as they would in a terminal there. Replays are saved in replays/ and can be reopened from the page.
The server only listens on localhost: anyone who can reach it can run commands on this computer.
"""
import argparse
import glob
import json
import os
import random
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from play import DEFAULT_TIMEOUT_MS, REPLAYS, default_path, find_referee, play_logged

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".png": "image/png",
         ".jpg": "image/jpeg", ".json": "application/json", ".css": "text/css"}
play_lock = threading.Semaphore(4)   # games at the same time
config = {"dir": os.getcwd(), "timeout": DEFAULT_TIMEOUT_MS, "port": 8770}


def list_replays():
    out = []
    for p in sorted(glob.glob(os.path.join(REPLAYS, "*.json")), key=os.path.getmtime, reverse=True)[:300]:
        name = os.path.basename(p)
        try:
            with open(p) as f:
                d = json.load(f)
            out.append({"file": name, "date": d.get("date", ""), "seed": d.get("seed"),
                        "players": [x["name"] for x in d["players"]], "winner": d.get("winner", -1),
                        "turns": len(d.get("turns", []))})
        except Exception as e:   # unreadable file: still list it
            out.append({"file": name, "error": str(e)})
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def local_request(self):
        """Only pages served by this server may use it (stops other web sites and DNS rebinding from starting bots)."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ("localhost", "127.0.0.1", "::1")

    def send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, separators=(",", ":"))
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        ext = os.path.splitext(path)[1].lower()
        if not os.path.isfile(path):
            return self.send(404, {"error": "not found"})
        with open(path, "rb") as f:
            self.send(200, f.read(), TYPES.get(ext, "application/octet-stream"))

    def do_GET(self):
        if not self.local_request():
            return self.send(403, {"error": "forbidden"})
        u = urlparse(self.path)
        q = parse_qs(u.query)
        path = u.path
        if path in ("/", "/index.html"):
            return self.send_file(os.path.join(WEB, "index.html"))
        if path == "/sim.js" or path.startswith("/assets/"):
            p = os.path.abspath(os.path.join(WEB, path.lstrip("/")))
            if not p.startswith(WEB + os.sep):
                return self.send(403, {"error": "forbidden"})
            return self.send_file(p)
        if path == "/api/config":
            return self.send(200, {"dir": config["dir"], "timeout": config["timeout"]})
        if path == "/api/replays":
            return self.send(200, list_replays())
        if path == "/api/replay":
            name = os.path.basename(q.get("file", [""])[0])
            return self.send_file(os.path.join(REPLAYS, name))
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self.local_request() or not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.send(403, {"error": "forbidden"})   # a plain form post from another site cannot set this type
        if urlparse(self.path).path != "/api/play":
            return self.send(404, {"error": "not found"})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            a, b = str(req.get("a", "")).strip(), str(req.get("b", "")).strip()
            if not a or not b:
                raise ValueError("enter a command for both bots")
            seed = req.get("seed")
            seed = int(seed) if seed not in (None, "") else random.randrange(1, 2**31)
            with play_lock:
                log = play_logged(a, b, seed, timeout_ms=config["timeout"], cwd=config["dir"])
        except Exception as e:
            return self.send(400, {"error": str(e)})
        path = default_path(log)
        with open(path, "w") as f:
            json.dump(log, f, separators=(",", ":"))
        self.send(200, {"file": os.path.basename(path), "winner": log["winner"], "turns": len(log["turns"])})


def main():
    ap = argparse.ArgumentParser(description="Mad Pod Racing game viewer")
    ap.add_argument("port", nargs="?", type=int, default=8770)
    ap.add_argument("--port", dest="port_opt", type=int)
    ap.add_argument("--dir", default=os.getcwd(), help="folder the bot commands run in (default: current folder)")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_MS, help="ms per turn before a bot loses as stuck")
    args = ap.parse_args()
    config.update(dir=os.path.abspath(args.dir), timeout=args.timeout, port=args.port_opt or args.port)
    find_referee()   # fail early with build instructions
    os.makedirs(REPLAYS, exist_ok=True)
    print(f"viewer on http://localhost:{config['port']}  (bot commands run in {config['dir']})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", config["port"]), Handler).serve_forever()


if __name__ == "__main__":
    main()
