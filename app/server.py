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
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

import dream

WEB = Path(__file__).resolve().parent / "web"
ROOT = None

RUNNING = {}          # project -> list of log lines while a build runs

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


def start_build(name):
    if name in RUNNING:
        return False
    RUNNING[name] = []

    def work():
        try:
            dream.build(ROOT, name, log=RUNNING[name].append)
        except Exception as e:  # report, never crash the app
            RUNNING[name].append(f"Build failed: {e}")
        finally:
            time.sleep(2)
            RUNNING.pop(name, None)
    threading.Thread(target=work, daemon=True).start()
    return True


def pending_runs(pdir):
    out = []
    pend = pdir / "pending"
    if pend.is_dir():
        for r in sorted(pend.iterdir(), reverse=True):
            c = r / "changes.json"
            if c.is_file():
                data = json.loads(c.read_text(encoding="utf-8"))
                data.pop("doc_keys", None)
                out.append(data)
    return out


def wiki_pages(pdir):
    pages = []
    for cat in dream.CATEGORIES:
        d = pdir / "wiki" / cat
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            text = p.read_text(encoding="utf-8")
            meta = {}
            if text.startswith("---"):
                for line in text.split("---", 2)[1].splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.split("#")[0].strip()
            pages.append({"category": cat, "file": p.name, "title": meta.get("title", p.stem),
                          "summary": meta.get("summary", ""), "updated": meta.get("updated", ""), "text": text})
    return pages


def scheduler():
    """Hourly/daily auto-update. Results only ever go to Pending."""
    last = {}
    while True:
        time.sleep(60)
        for name in list_projects():
            try:
                sched = json.loads((ROOT / "Projects" / name / "settings.json").read_text(
                    encoding="utf-8")).get("memory", {}).get("update_schedule", "off")
            except Exception:
                continue
            gap = {"hourly": 3600, "daily": 86400}.get(sched)
            if not gap:
                last.pop(name, None)
                continue
            last.setdefault(name, time.time())   # first run one full interval after switching on
            if time.time() - last[name] >= gap and start_build(name):
                last[name] = time.time()


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
        elif path.startswith("/api/project/") and path.count("/") == 4:
            name, what = path[len("/api/project/"):].split("/")
            pdir = project_dir(name)
            if not pdir:
                return self.send_json({"error": "project not found"}, 404)
            if what == "pages":
                return self.send_json({"pages": wiki_pages(pdir)})
            if what == "pending":
                return self.send_json({"runs": pending_runs(pdir), "running": RUNNING.get(name)})
            self.send_json({"error": "not found"}, 404)
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
        parts = path[len("/api/project/"):].split("/") if path.startswith("/api/project/") else []
        if len(parts) >= 2 and parts[1] in ("build", "approve", "reject"):
            pdir = project_dir(parts[0])
            if not pdir:
                return self.send_json({"error": "project not found"}, 404)
            if parts[1] == "build":
                return self.send_json({"started": start_build(pdir.name)})
            if len(parts) != 3 or not (pdir / "pending" / parts[2] / "changes.json").is_file():
                return self.send_json({"error": "run not found"}, 404)
            try:
                fn = dream.approve if parts[1] == "approve" else dream.reject
                return self.send_json({"ok": True, "run": fn(ROOT, pdir.name, parts[2], log=lambda *_: None)})
            except ValueError as e:
                return self.send_json({"error": str(e)}, 400)
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
    threading.Thread(target=scheduler, daemon=True).start()
    print(f"=== KastoBrain running ===\nRoot: {ROOT}\nOpen: http://127.0.0.1:{args.port}\nStop: Ctrl+C")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
