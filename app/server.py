"""KastoBrain local web app.

Serves the browser page and a small JSON API on this PC only (127.0.0.1).
Reads brains from the KastoBrain root. The only thing it writes is a
project's settings.json, and only when you press Save; the previous
version is kept as settings.previous.json.

Usage:
    python server.py H:\\KastoBrain
    then open http://127.0.0.1:8765
"""

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

WEB = Path(__file__).resolve().parent / "web"
ROOT = None

EDITABLE = {"description", "instructions", "links", "ai", "memory", "never_send", "folders"}


def project_dir(name):
    projects = (ROOT / "Projects").resolve()
    path = (projects / name).resolve()
    if path.parent != projects or not (path / "settings.json").is_file():
        return None
    return path


def list_projects():
    projects = ROOT / "Projects"
    if not projects.is_dir():
        return []
    out = []
    for p in sorted(projects.iterdir()):
        if (p / "settings.json").is_file():
            out.append(p.name)
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = unquote(self.path.split("?")[0])
        if path == "/" or path == "/index.html":
            body = (WEB / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/projects":
            self.send_json({"root": str(ROOT), "projects": list_projects()})
        elif path.startswith("/api/project/"):
            pdir = project_dir(path[len("/api/project/"):])
            if not pdir:
                return self.send_json({"error": "project not found"}, 404)
            settings = json.loads((pdir / "settings.json").read_text(encoding="utf-8"))
            self.send_json({"name": pdir.name, "settings": settings})
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = unquote(self.path.split("?")[0])
        if not (path.startswith("/api/project/") and path.endswith("/settings")):
            return self.send_json({"error": "not found"}, 404)
        pdir = project_dir(path[len("/api/project/"):-len("/settings")])
        if not pdir:
            return self.send_json({"error": "project not found"}, 404)
        length = int(self.headers.get("Content-Length", 0))
        try:
            changes = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self.send_json({"error": "bad JSON"}, 400)
        target = pdir / "settings.json"
        old_text = target.read_text(encoding="utf-8")
        settings = json.loads(old_text)
        for key, value in changes.items():
            if key in EDITABLE:
                settings[key] = value
        (pdir / "settings.previous.json").write_text(old_text, encoding="utf-8")
        target.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self.send_json({"saved": True, "settings": settings})


def main():
    global ROOT
    parser = argparse.ArgumentParser(description="Run the KastoBrain app on this PC.")
    parser.add_argument("root", help="KastoBrain root, e.g. H:\\KastoBrain")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    ROOT = Path(args.root)
    if not (ROOT / "Projects").is_dir():
        print(f"No Projects folder in {ROOT}. Run setup_layout.py first.")
        return 1
    print(f"=== KastoBrain running ===\nRoot: {ROOT}\nOpen: http://127.0.0.1:{args.port}\nStop: Ctrl+C")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
