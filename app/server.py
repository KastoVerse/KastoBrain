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
from urllib.parse import parse_qs, unquote, urlsplit

import brain
import dream
import pc_sorter

WEB = Path(__file__).resolve().parent / "web"
ROOT = None

RUNNING = {}          # project -> list of log lines while a build runs
ASKING = set()        # projects with a question being answered

EDITABLE = {"description", "instructions", "links", "ai", "ai_ask", "ai_check", "memory", "never_send", "folders"}


def project_dir(name):
    return brain.project_dir(ROOT, name)


def list_projects():
    return brain.list_projects(ROOT)


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


def list_folder(pdir, folder):
    """Read-only listing of one folder inside this brain's folders. Never changes anything."""
    settings = json.loads((pdir / "settings.json").read_text(encoding="utf-8"))
    roots = [Path(f) for f in settings.get("folders", [])]
    if not folder:
        return {"path": "", "roots": True, "entries": [
            {"name": str(r), "path": str(r), "dir": True, "exists": r.is_dir(),
             "never_send": dream.under(r, settings.get("never_send", []))} for r in roots]}
    target = Path(folder).resolve()
    if not any(target == r.resolve() or r.resolve() in target.parents for r in roots):
        return {"error": "outside this brain's folders"}
    if not target.is_dir():
        return {"error": "folder not found"}
    seen_file = pdir / "processed.json"
    seen = set(json.loads(seen_file.read_text(encoding="utf-8"))) if seen_file.exists() else set()
    entries = []
    for e in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        try:
            st = e.stat()
        except OSError:
            continue
        item = {"name": e.name, "path": str(e), "dir": e.is_dir(),
                "never_send": dream.under(e, settings.get("never_send", []))}
        if not e.is_dir():
            item.update(size=st.st_size, mtime=int(st.st_mtime),
                        readable=e.suffix.lower() in dream.DOC_TYPES,
                        in_brain=dream.doc_key({"path": str(e), "size": st.st_size, "mtime": int(st.st_mtime)}) in seen)
        entries.append(item)
    parent = str(target.parent) if target not in [r.resolve() for r in roots] else ""
    return {"path": str(target), "parent": parent, "entries": entries}


def pending_runs(pdir):
    return brain.pending_runs(pdir)


def wiki_pages(pdir):
    return brain.wiki_pages(pdir)


def pc_brain_overview():
    rows = []
    for name in list_projects():
        pdir = project_dir(name)
        s = brain.settings(pdir)
        rows.append({"name": name, "description": s.get("description", ""), "ai": s.get("ai", ""),
                     "pages": len(wiki_pages(pdir)),
                     "pending": sum(1 for r in pending_runs(pdir) if r["status"] == "pending"),
                     "sessions": len(list(pdir.glob("sessions/*.json"))), "folders": s.get("folders", [])})
    return {"brains": rows, "sorter": pc_sorter.load_settings(ROOT), "plans": pc_sorter.list_plans(ROOT),
            "sorting": SORTING.get("log")}


SORTING = {}


def read_body(handler):
    length = int(handler.headers.get("Content-Length", 0))
    return json.loads(handler.rfile.read(length) or b"{}")


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
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        path = unquote(parts.path)
        if path == "/" or path == "/index.html":
            body = (WEB / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/projects":
            self.send_json({"root": str(ROOT), "projects": list_projects()})
        elif path == "/api/pcbrain":
            self.send_json(pc_brain_overview())
        elif path == "/api/search":
            q = query.get("q", [""])[0]
            self.send_json({"hits": [dict(h, brain=n) for n in list_projects()
                                     for h in brain.search(project_dir(n), q, 10)] if q.strip() else []})
        elif path.startswith("/api/project/") and path.count("/") == 4:
            name, what = path[len("/api/project/"):].split("/")
            pdir = project_dir(name)
            if not pdir:
                return self.send_json({"error": "project not found"}, 404)
            if what == "pages":
                return self.send_json({"pages": wiki_pages(pdir)})
            if what == "sessions":
                return self.send_json({"sessions": brain.list_sessions(pdir), "asking": name in ASKING})
            if what == "files":
                return self.send_json(list_folder(pdir, query.get("path", [""])[0]))
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
        try:
            if path == "/api/projects":
                return self.send_json({"created": brain.create_project(ROOT, read_body(self).get("name", ""))})
            if path == "/api/pcbrain/settings":
                return self.send_json({"saved": pc_sorter.save_settings(ROOT, read_body(self))})
            if path == "/api/pcbrain/dryrun":
                if "log" in SORTING:
                    return self.send_json({"started": False})
                SORTING["log"] = []

                def work():
                    try:
                        pc_sorter.dry_run(ROOT, log=SORTING["log"].append)
                    except Exception as e:
                        SORTING["log"].append(f"Dry run failed: {e}")
                    finally:
                        time.sleep(2)
                        SORTING.pop("log", None)
                threading.Thread(target=work, daemon=True).start()
                return self.send_json({"started": True})
            if path.startswith("/api/project/") and path.endswith("/ask"):
                pdir = project_dir(path[len("/api/project/"):-len("/ask")])
                question = read_body(self).get("question", "").strip()
                if not pdir or not question:
                    return self.send_json({"error": "brain or question missing"}, 400)
                if pdir.name in ASKING:
                    return self.send_json({"error": "already answering a question for this brain"}, 409)
                ASKING.add(pdir.name)
                try:
                    return self.send_json({"session": brain.ask(pdir, question)})
                finally:
                    ASKING.discard(pdir.name)
        except ValueError as e:
            return self.send_json({"error": str(e)}, 400)
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
