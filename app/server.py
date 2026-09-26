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
import shutil
import os
import json
import tempfile
import zipfile
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

import appconf
import brain
import dream
import pc_sorter

WEB = Path(__file__).resolve().parent / "web"
ROOT = None

RUNNING = {}          # project -> list of log lines while a build runs
LAST_PING = [time.time()]   # last time an open app window said "still here"
CODE = Path(__file__).resolve().parent.parent   # the KastoBrain program folder (a git clone)
ASKING = set()        # projects with a question being answered
SKILLS_RUNNING = {}   # project -> skill name while a skill runs
CACHE = {}            # slow checks (AI status, update check) cached for a few minutes

EDITABLE = {"description", "instructions", "links", "ai", "ai_ask", "ai_check", "memory", "never_send", "folders",
            "personality", "connectors"}


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
    (pdir / "Files").mkdir(exist_ok=True)
    roots = [pdir / "Files"] + [Path(f) for f in settings.get("folders", [])]
    if not folder:
        return {"path": "", "roots": True, "own": str(pdir / "Files"), "entries": [
            {"name": "This brain's files (uploads)" if i == 0 else str(r), "path": str(r), "dir": True,
             "exists": r.is_dir(), "own": i == 0,
             "never_send": dream.under(r, settings.get("never_send", []))} for i, r in enumerate(roots)]}
    target = Path(folder).resolve()
    if not any(target == r.resolve() or r.resolve() in target.parents for r in roots):
        return {"error": "outside this brain's folders"}
    if not target.is_dir():
        return {"error": "folder not found"}
    seen_file = pdir / "processed.json"
    seen = set(json.loads(seen_file.read_text(encoding="utf-8"))) if seen_file.exists() else set()
    di = pdir / "docinfo.json"
    docinfo = {k: {"title": v.get("name", ""), "description": v.get("description", ""), "original": v.get("original", "")}
               for k, v in (json.loads(di.read_text(encoding="utf-8")) if di.exists() else {}).items()}
    entries = []
    for e in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        try:
            st = e.stat()
        except OSError:
            continue
        item = {"name": e.name, "path": str(e), "dir": e.is_dir(), **docinfo.get(str(e), {}),
                "never_send": dream.under(e, settings.get("never_send", []))}
        if not e.is_dir():
            item.update(size=st.st_size, mtime=int(st.st_mtime),
                        readable=e.suffix.lower() in dream.DOC_TYPES,
                        in_brain=dream.doc_key({"path": str(e), "size": st.st_size, "mtime": int(st.st_mtime)}) in seen)
        entries.append(item)
    parent = str(target.parent) if target not in [r.resolve() for r in roots] else ""
    own = pdir / "Files"
    in_own = target == own.resolve() or own.resolve() in target.parents
    return {"path": str(target), "parent": parent, "entries": entries, "in_own": in_own,
            "own_rel": str(target.relative_to(own.resolve())) if in_own else ""}


def files_summary(pdir):
    s = brain.settings(pdir)
    docs, missing = dream.scan_documents(s, pdir)
    seen_file = pdir / "processed.json"
    seen = set(json.loads(seen_file.read_text(encoding="utf-8"))) if seen_file.exists() else set()
    in_brain = sum(1 for d in docs if dream.doc_key(d) in seen)
    return {"documents": len(docs), "in_brain": in_brain, "new": len(docs) - in_brain,
            "bytes": sum(d["size"] for d in docs), "missing_folders": missing,
            "pending": sum(1 for r in brain.pending_runs(pdir) if r["status"] == "pending")}


def unzip_into(pdir, rel_dir, zip_name, data_path):
    """Unpack an uploaded zip into the brain's Files folder. Never overwrites; refuses paths that escape."""
    base = brain.safe_child(pdir / "Files", rel_dir)
    if base is None:
        raise ValueError("bad folder")
    dest = base / Path(zip_name).stem
    n = 1
    while dest.exists():
        n += 1
        dest = base / f"{Path(zip_name).stem} ({n})"
    count = 0
    with zipfile.ZipFile(data_path) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            target = brain.safe_child(dest, info.filename.replace("\\", "/"))
            if target is None:
                continue                                  # skip anything trying to escape the folder
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            count += 1
    return {"folder": str(dest), "files": count}


def cached(key, seconds, fn):
    hit = CACHE.get(key)
    if hit and time.time() - hit[0] < seconds:
        return hit[1]
    val = fn()
    CACHE[key] = (time.time(), val)
    return val


def status():
    code, ver = git("rev-parse", "--short", "HEAD")
    app = appconf.load_settings(ROOT)
    upd = cached("update", 1800, update_status) if app.get("update_check", True) else {"available": False, "off": True}
    ais = CACHE.get("ai", (0, None))[1]
    return {"version": ver if not code else "unknown", "update": upd,
            "unread": sum(1 for n in appconf.list_notifications(ROOT) if not n.get("read")),
            "agents": None if ais is None else {"installed": sum(a["installed"] for a in ais),
                                                "connected": sum(1 for a in ais if a["mcp"])},
            "busy": {"building": list(RUNNING), "skills": SKILLS_RUNNING}}


def start_skill(name, skill_id):
    if name in SKILLS_RUNNING:
        return False
    sk = next((k for k in appconf.list_skills(ROOT) if k.get("id") == skill_id), None)
    if not sk:
        raise ValueError("skill not found")
    SKILLS_RUNNING[name] = sk["name"]

    def work():
        try:
            r = brain.run_skill(project_dir(name), sk)
            appconf.notify(ROOT, f"{sk['name']} finished for {name}" + ("" if r["ok"] else " (with problems)"),
                           "ok" if r["ok"] else "warn", name)
        except Exception as e:
            appconf.notify(ROOT, f"{sk['name']} failed for {name}: {e}", "warn", name)
        finally:
            SKILLS_RUNNING.pop(name, None)
    threading.Thread(target=work, daemon=True).start()
    return True


def run_workflow(wf, reason="manual"):
    """Run one workflow's steps in the background. Builds only ever go to Pending."""
    name = wf.get("brain")
    if not project_dir(name):
        appconf.notify(ROOT, f"Workflow '{wf['name']}': brain '{name}' not found", "warn")
        return False

    def work():
        done = []
        for step in wf.get("steps", []):
            try:
                if step == "build":
                    if name in RUNNING:
                        continue
                    RUNNING[name] = []
                    try:
                        c = dream.build(ROOT, name, log=RUNNING[name].append)
                        done.append(f"Build Brain: {c['status']}" + (f", checker {c['checker']['verdict']}" if c.get("checker") else ""))
                    finally:
                        RUNNING.pop(name, None)
                elif step.startswith("skill:"):
                    sk = next((k for k in appconf.list_skills(ROOT) if k.get("id") == step[6:]), None)
                    if sk:
                        SKILLS_RUNNING[name] = sk["name"]
                        try:
                            r = brain.run_skill(project_dir(name), sk)
                            done.append(f"{sk['name']}: report saved to Downloads" if r["ok"] else f"{sk['name']}: problems")
                        finally:
                            SKILLS_RUNNING.pop(name, None)
            except Exception as e:
                done.append(f"{step}: failed ({e})")
        items = appconf.list_workflows(ROOT)
        for w in items:
            if w["id"] == wf["id"]:
                w["last_run"] = time.strftime("%Y-%m-%d %H:%M")
        appconf.save_workflows(ROOT, items)
        if wf.get("notify", True):
            appconf.notify(ROOT, f"Workflow '{wf['name']}' ({reason}) on {name}: " + ("; ".join(done) or "nothing to do"),
                           "ok", name)
    threading.Thread(target=work, daemon=True).start()
    return True


def workflow_tick():
    """Called every minute by the scheduler."""
    now = time.time()
    for wf in appconf.list_workflows(ROOT):
        if not wf.get("enabled") or not project_dir(wf.get("brain", "")):
            continue
        last = wf.get("last_run") or ""
        try:
            last_t = time.mktime(time.strptime(last, "%Y-%m-%d %H:%M")) if last else 0
        except ValueError:
            last_t = 0
        if wf["trigger"] == "daily" and now - last_t >= 86400:
            run_workflow(wf, "daily")
        elif wf["trigger"] == "weekly" and now - last_t >= 7 * 86400:
            run_workflow(wf, "weekly")
        elif wf["trigger"] == "new_files" and now - last_t >= 600:
            pdir = project_dir(wf["brain"])
            if wf["brain"] not in RUNNING and files_summary(pdir)["new"] > 0 and not any(
                    r["status"] == "pending" for r in brain.pending_runs(pdir)):
                run_workflow(wf, "new files")


def allowed_download(pdir, path):
    """A file may be downloaded if it is in this brain's folder or one of its document folders."""
    settings = brain.settings(pdir)
    target = Path(path).resolve()
    roots = [pdir.resolve()] + [Path(f).resolve() for f in settings.get("folders", [])]
    return target.is_file() and any(target == r or r in target.parents for r in roots)


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


def git(*args, timeout=60):
    p = subprocess.run(["git", "-C", str(CODE), *args], capture_output=True, text=True, timeout=timeout,
                       stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return p.returncode, (p.stdout + p.stderr).strip()


def update_status():
    """Free check: is there newer approved code on GitHub? Changes nothing."""
    code, branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if code:
        return {"available": False, "error": "not a git download"}
    code, out = git("fetch", "--quiet", "origin", branch)
    if code:
        return {"available": False, "error": "could not reach GitHub", "detail": out[-300:]}
    code, log = git("log", "--oneline", "HEAD..FETCH_HEAD")      # what GitHub has that this PC doesn't
    if code:
        return {"available": False, "error": "could not compare versions", "detail": log[-300:]}
    changes = [l for l in log.splitlines() if l.strip()]
    return {"available": bool(changes), "branch": branch, "changes": changes[:20]}


def update_apply():
    """Only runs when you press Update now. Replaces program code only; your brains are untouched."""
    _, before = git("rev-parse", "HEAD")
    _, branch = git("rev-parse", "--abbrev-ref", "HEAD")
    code, out = git("pull", "--ff-only", "origin", branch, timeout=300)
    if code:
        return {"ok": False, "detail": out[-500:]}
    (ROOT / "Logs").mkdir(exist_ok=True)
    (ROOT / "Logs" / "update-previous-version.txt").write_text(before + "\n", encoding="utf-8")
    _, after = git("rev-parse", "HEAD")
    return {"ok": True, "from": before[:7], "to": after[:7],
            "note": "Close KastoBrain and open it again to use the new version."}


def update_rollback():
    f = ROOT / "Logs" / "update-previous-version.txt"
    if not f.exists():
        return {"ok": False, "detail": "no previous version recorded"}
    prev = f.read_text(encoding="utf-8").strip()
    _, branch = git("rev-parse", "--abbrev-ref", "HEAD")
    code, out = git("checkout", "-B", branch, prev)
    return {"ok": code == 0, "to": prev[:7], "detail": out[-300:],
            "note": "Close KastoBrain and open it again to use the previous version."}


def idle_watch(limit):
    """Stop the hidden engine when no app window has been open for `limit` seconds."""
    while True:
        time.sleep(15)
        if time.time() - LAST_PING[0] > limit and not RUNNING and not ASKING and "log" not in SORTING:
            os._exit(0)


def scheduler():
    """Hourly/daily auto-update. Results only ever go to Pending."""
    last = {}
    while True:
        time.sleep(60)
        try:
            workflow_tick()
        except Exception as e:
            appconf.notify(ROOT, f"Workflow check failed: {e}", "warn")
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

    def send_file(self, path, name, ctype="application/octet-stream"):
        size = Path(path).stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(name))
        self.end_headers()
        with open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                self.wfile.write(chunk)

    def do_GET(self):
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        path = unquote(parts.path)
        if path in ("/icon.png", "/favicon.ico"):
            body = (WEB / ("icon.png" if path == "/icon.png" else "kastobrain.ico")).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png" if path == "/icon.png" else "image/x-icon")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if path == "/api/ping":
            LAST_PING[0] = time.time()
            return self.send_json({"ok": True})
        if path == "/api/update":
            CACHE.pop("update", None)
            return self.send_json(cached("update", 1800, update_status))
        if path == "/api/status":
            return self.send_json(status())
        if path == "/api/app-settings":
            return self.send_json({"settings": appconf.load_settings(ROOT), "root": str(ROOT),
                                   "mcp_command": appconf.mcp_command(ROOT)})
        if path == "/api/ai-status":
            if query.get("refresh"):
                CACHE.pop("ai", None)
            return self.send_json({"ais": cached("ai", 600, appconf.ai_status)})
        if path == "/api/skills":
            return self.send_json({"skills": appconf.list_skills(ROOT)})
        if path == "/api/workflows":
            return self.send_json({"workflows": appconf.list_workflows(ROOT), "templates": appconf.WORKFLOW_TEMPLATES})
        if path == "/api/notifications":
            return self.send_json({"items": appconf.list_notifications(ROOT, mark_read=bool(query.get("read")))})
        if path == "/api/downloads":
            reports = [dict(r, brain=n) for n in list_projects() for r in brain.list_reports(project_dir(n))]
            return self.send_json({"files": appconf.list_downloads(ROOT), "folder": str(appconf.downloads_dir(ROOT)),
                                   "reports": reports})
        if path == "/api/downloads/file":
            f = appconf.downloads_dir(ROOT) / Path(query.get("name", [""])[0]).name
            if not f.is_file():
                return self.send_json({"error": "not found"}, 404)
            return self.send_file(f, f.name)
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
            if what == "download":
                if "session" in query:
                    f = pdir / "sessions" / (Path(query["session"][0]).name + ".json")
                    if not f.is_file():
                        return self.send_json({"error": "not found"}, 404)
                    x = json.loads(f.read_text(encoding="utf-8"))
                    md = f"# {x['question']}\n\n{x['answer']}\n\n_{x['time']} · {x['ai']}_\n"
                    tmp = Path(tempfile.gettempdir()) / f"kb-{f.stem}.md"
                    tmp.write_text(md, encoding="utf-8")
                    return self.send_file(tmp, f"{pdir.name} - answer {f.stem}.md", "text/markdown")
                target = query.get("path", [""])[0]
                if not allowed_download(pdir, target):
                    return self.send_json({"error": "not allowed"}, 403)
                return self.send_file(target, Path(target).name)
            if what == "summary":
                return self.send_json(files_summary(pdir))
            if what == "reports":
                return self.send_json({"reports": brain.list_reports(pdir), "running": SKILLS_RUNNING.get(name)})
            if what == "export":
                tmp = appconf.downloads_dir(ROOT) / f"{pdir.name} brain {time.strftime('%Y-%m-%d %H%M')}.zip"
                with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                    for f in pdir.rglob("*"):
                        if f.is_file():
                            z.write(f, f"{pdir.name}/{f.relative_to(pdir)}")
                return self.send_file(tmp, f"{pdir.name} brain.zip", "application/zip")
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
            if path == "/api/update/apply":
                return self.send_json(update_apply())
            if path == "/api/update/rollback":
                return self.send_json(update_rollback())
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
            if path == "/api/app-settings":
                return self.send_json({"settings": appconf.save_settings(ROOT, read_body(self))})
            if path == "/api/mcp/connect":
                b = read_body(self)
                ok, msg = appconf.mcp_connect(ROOT, b.get("key", ""), bool(b.get("connect", True)))
                CACHE.pop("ai", None)
                return self.send_json({"ok": ok, "message": msg})
            if path == "/api/skills":
                return self.send_json({"skill": appconf.save_skill(ROOT, read_body(self))})
            if path == "/api/skills/delete":
                return self.send_json({"ok": appconf.delete_skill(ROOT, read_body(self).get("id", ""))})
            if path == "/api/workflows":
                return self.send_json({"workflows": appconf.save_workflows(ROOT, read_body(self).get("workflows", []))})
            if path == "/api/workflows/run":
                wid = read_body(self).get("id")
                wf = next((w for w in appconf.list_workflows(ROOT) if w["id"] == wid), None)
                return self.send_json({"started": bool(wf) and run_workflow(wf)})
            if path.startswith("/api/project/") and "/skill/" in path:
                name, sid = path[len("/api/project/"):].split("/skill/", 1)
                if not project_dir(name):
                    return self.send_json({"error": "brain not found"}, 404)
                return self.send_json({"started": start_skill(project_dir(name).name, sid)})
            if path.startswith("/api/project/") and "/undo-names/" in path:
                name, run = path[len("/api/project/"):].split("/undo-names/", 1)
                if not project_dir(name):
                    return self.send_json({"error": "brain not found"}, 404)
                return self.send_json({"undone": dream.undo_names(ROOT, project_dir(name).name, Path(run).name,
                                                                  log=lambda *_: None)})
            if path.startswith("/api/project/") and path.endswith("/unzip"):
                pdir = project_dir(path[len("/api/project/"):-len("/unzip")])
                if not pdir:
                    return self.send_json({"error": "brain not found"}, 404)
                q = parse_qs(urlsplit(self.path).query)
                tmp = Path(tempfile.gettempdir()) / f"kb-upload-{time.time_ns()}.zip"
                length = int(self.headers.get("Content-Length", 0))
                with open(tmp, "wb") as out:
                    left = length
                    while left > 0:
                        chunk = self.rfile.read(min(left, 1 << 20))
                        if not chunk:
                            break
                        out.write(chunk)
                        left -= len(chunk)
                try:
                    return self.send_json(unzip_into(pdir, q.get("dir", [""])[0], q.get("name", ["upload.zip"])[0], tmp))
                except zipfile.BadZipFile:
                    return self.send_json({"error": "That file is not a valid zip."}, 400)
                finally:
                    tmp.unlink(missing_ok=True)
            if path.startswith("/api/project/") and (path.endswith("/upload") or path.endswith("/mkdir")):
                name, what = path[len("/api/project/"):].rsplit("/", 1)
                pdir = project_dir(name)
                if not pdir:
                    return self.send_json({"error": "brain not found"}, 404)
                q = parse_qs(urlsplit(self.path).query)
                files_root = pdir / "Files"
                files_root.mkdir(exist_ok=True)
                folder = brain.safe_child(files_root, q.get("dir", [""])[0])
                fname = Path(q.get("name", [""])[0].replace("\\", "/")).name.strip()
                if folder is None or not fname or fname in (".", ".."):
                    return self.send_json({"error": "bad name or folder"}, 400)
                if what == "mkdir":
                    (folder / fname).mkdir(parents=True, exist_ok=True)
                    return self.send_json({"created": str(folder / fname)})
                # sub-folders inside an uploaded folder come through the name as a relative path
                sub = brain.safe_child(folder, str(Path(q.get("rel", [""])[0]).parent)) if q.get("rel") else folder
                if sub is None:
                    return self.send_json({"error": "bad folder"}, 400)
                sub.mkdir(parents=True, exist_ok=True)
                target, n = sub / fname, 1
                while target.exists():                   # never overwrite: add (2), (3) ...
                    n += 1
                    target = sub / f"{Path(fname).stem} ({n}){Path(fname).suffix}"
                length = int(self.headers.get("Content-Length", 0))
                with open(target, "wb") as out:
                    left = length
                    while left > 0:
                        chunk = self.rfile.read(min(left, 1 << 20))
                        if not chunk:
                            break
                        out.write(chunk)
                        left -= len(chunk)
                return self.send_json({"saved": str(target), "bytes": length})
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
    parser.add_argument("--auto-exit", type=int, default=0, metavar="SECONDS",
                        help="stop by itself when no app window has been open this long")
    args = parser.parse_args()
    ROOT = Path(args.root)
    if not (ROOT / "Projects").is_dir():
        print(f"No Projects folder in {ROOT}. Run setup_layout.py first.")
        return 1
    threading.Thread(target=scheduler, daemon=True).start()
    if args.auto_exit:
        threading.Thread(target=idle_watch, args=(args.auto_exit,), daemon=True).start()
    print(f"=== KastoBrain running ===\nRoot: {ROOT}\nOpen: http://127.0.0.1:{args.port}\nStop: Ctrl+C")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
