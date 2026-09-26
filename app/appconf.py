"""App-wide parts of KastoBrain: settings, AI status, connectors, skills,
workflows, notifications and the Downloads folder.

Everything is stored as plain files under the KastoBrain root:
  app-settings.json     app-wide settings and defaults for new brains
  Skills/*.json         skill library (built-in ones are created on first use)
  workflows.json        workflow list
  notifications.json    in-app notifications (newest first, last 200 kept)
  Downloads/            exports and reports, listed on the Downloads page
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

APP = Path(__file__).resolve().parent
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

DEFAULTS = {
    "agent_name": "KastoBrain",
    "personality": {"tone": "professional", "length": "normal", "voice": ""},
    "ai": "chatgpt", "ai_ask": "claude", "ai_check": "claude",
    "update_check": True,
    "mcp_connectors": [],
}

AIS = {
    "chatgpt": {"label": "ChatGPT / Codex", "cmd": "codex"},
    "claude": {"label": "Claude Code", "cmd": "claude"},
    "gemini": {"label": "Gemini CLI", "cmd": "gemini"},
    "grok": {"label": "Grok Build", "cmd": "grok"},
}


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():                                   # keep one previous version
        shutil.copyfile(path, path.with_suffix(".previous.json"))
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- settings
def load_settings(root):
    s = dict(DEFAULTS)
    s.update(_read(Path(root) / "app-settings.json", {}))
    s["personality"] = {**DEFAULTS["personality"], **(s.get("personality") or {})}
    return s


def save_settings(root, changes):
    s = load_settings(root)
    for k in ("agent_name", "personality", "ai", "ai_ask", "ai_check", "update_check", "mcp_connectors"):
        if k in changes:
            s[k] = changes[k]
    s["agent_name"] = (str(s.get("agent_name") or "KastoBrain")).strip()[:40] or "KastoBrain"
    _write(Path(root) / "app-settings.json", s)
    return s


# ---------------------------------------------------------------- AI status
def _run(cmd, timeout=15):
    exe = shutil.which(cmd[0])
    if not exe:
        return None
    try:
        p = subprocess.run([exe] + cmd[1:], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
        return (p.returncode, (p.stdout + p.stderr).strip())
    except Exception as e:
        return (1, str(e))


def mcp_connected(key):
    home = Path.home()
    try:
        if key == "chatgpt":
            return "[mcp_servers.kastobrain]" in (home / ".codex" / "config.toml").read_text(encoding="utf-8")
        if key == "claude":
            cfg = _read(home / ".claude.json", {})
            return "kastobrain" in (cfg.get("mcpServers") or {})
        if key == "gemini":
            return "kastobrain" in (_read(home / ".gemini" / "settings.json", {}).get("mcpServers") or {})
    except OSError:
        return False
    return None                                          # unknown for this app


def ai_status():
    out = []
    for key, info in AIS.items():
        v = _run([info["cmd"], "--version"])
        row = {"key": key, "label": info["label"], "installed": v is not None,
               "version": (v[1].splitlines() or [""])[0][:60] if v else "", "signed_in": None,
               "mcp": mcp_connected(key) if v else False}
        if key == "chatgpt" and v:
            s = _run(["codex", "login", "status"])
            row["signed_in"] = bool(s and s[0] == 0 and "logged in" in s[1].lower() and "not logged" not in s[1].lower())
        out.append(row)
    return out


def mcp_command(root):
    return [sys.executable.replace("pythonw.exe", "python.exe"), str(APP / "mcp_server.py"), str(root)]


def mcp_connect(root, key, connect=True):
    """Register (or remove) the KastoBrain connector with an AI app. Returns (ok, message)."""
    cmd = mcp_command(root)
    if key == "chatgpt":
        r = _run(["codex", "mcp", "add", "kastobrain", "--"] + cmd) if connect else _run(["codex", "mcp", "remove", "kastobrain"])
    elif key == "claude":
        r = _run(["claude", "mcp", "add", "kastobrain", "--scope", "user", "--"] + cmd, 60) if connect \
            else _run(["claude", "mcp", "remove", "kastobrain", "--scope", "user"], 60)
    elif key == "gemini":
        f = Path.home() / ".gemini" / "settings.json"
        cfg = _read(f, {})
        servers = cfg.setdefault("mcpServers", {})
        if connect:
            servers["kastobrain"] = {"command": cmd[0], "args": cmd[1:]}
        else:
            servers.pop("kastobrain", None)
        _write(f, cfg)
        return True, ("Connected" if connect else "Disconnected") + " (Gemini settings updated; previous copy kept)."
    else:
        return False, "This app has no automatic setup yet. Use the copy-paste setup below."
    if r is None:
        return False, "That AI app is not installed on this PC."
    return r[0] == 0, r[1][-400:] or ("Connected" if connect else "Disconnected")


# ---------------------------------------------------------------- skills
BUILTIN_SKILLS = [
    {"id": "timeline", "name": "Timeline", "icon": "🗓",
     "description": "Every dated event in this brain, in order, each with its source.",
     "prompt": "Build a complete chronological timeline of every dated event in this brain. One line per event: "
               "date — what happened — source citation. Include every date you can find in the wiki and documents. "
               "Mark uncertain dates as (approx.)."},
    {"id": "contradictions", "name": "Contradiction finder", "icon": "⚖",
     "description": "Statements that conflict with each other, side by side, with sources.",
     "prompt": "Find every contradiction or inconsistency between documents, statements, dates, amounts or names. "
               "For each: what A says (source) vs what B says (source), and why they conflict. "
               "If there are none, say so plainly."},
    {"id": "people", "name": "People & companies", "icon": "👥",
     "description": "Everyone and every organisation involved, their role and details.",
     "prompt": "List every person and organisation involved. For each: name, role in this matter, key details "
               "(ABN, reference numbers, contact details only if in the documents), and sources."},
    {"id": "letters", "name": "Letter summary", "icon": "✉",
     "description": "Each letter or email summarised in two lines, newest first.",
     "prompt": "Summarise every letter, email and notice in this brain, newest first: date, from, to, "
               "two-line summary, and any deadline or request it contains. Cite each."},
    {"id": "evidence", "name": "Evidence table", "icon": "📋",
     "description": "Each claim or issue with the evidence for and against it.",
     "prompt": "Make an evidence table as markdown: | Issue or claim | Evidence for (source) | Evidence against (source) "
               "| Gaps / missing documents |. Cover every issue in this brain."},
]


def skills_dir(root):
    d = Path(root) / "Skills"
    d.mkdir(parents=True, exist_ok=True)
    for sk in BUILTIN_SKILLS:
        f = d / f"{sk['id']}.json"
        if not f.exists():
            f.write_text(json.dumps({**sk, "builtin": True}, indent=2, ensure_ascii=False), encoding="utf-8")
    return d


def list_skills(root):
    return [_read(f, {}) for f in sorted(skills_dir(root).glob("*.json")) if not f.name.endswith(".previous.json")]


def save_skill(root, data):
    name = str(data.get("name", "")).strip()[:60]
    prompt = str(data.get("prompt", "")).strip()
    if not name or not prompt:
        raise ValueError("A skill needs a name and instructions.")
    sid = data.get("id") or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "skill"
    sk = {"id": sid, "name": name, "icon": str(data.get("icon") or "✦")[:2],
          "description": str(data.get("description", "")).strip()[:200], "prompt": prompt,
          "builtin": bool(_read(skills_dir(root) / f"{sid}.json", {}).get("builtin"))}
    _write(skills_dir(root) / f"{sid}.json", sk)
    return sk


def delete_skill(root, sid):
    f = skills_dir(root) / f"{Path(sid).name}.json"
    sk = _read(f, {})
    if sk.get("builtin"):
        raise ValueError("Built-in skills can be edited but not removed.")
    if f.exists():
        f.rename(f.with_suffix(".removed.json"))            # kept, not deleted
    return True


# ---------------------------------------------------------------- downloads & notifications
def downloads_dir(root):
    d = Path(root) / "Downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_downloads(root):
    d = downloads_dir(root)
    out = []
    for f in sorted(d.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_file():
            st = f.stat()
            out.append({"name": f.name, "path": str(f), "size": st.st_size, "mtime": int(st.st_mtime)})
    return out


def notify(root, text, kind="info", brain=""):
    f = Path(root) / "notifications.json"
    items = _read(f, [])
    items.insert(0, {"time": datetime.now().isoformat(timespec="seconds"), "text": text, "kind": kind,
                     "brain": brain, "read": False})
    f.write_text(json.dumps(items[:200], indent=2, ensure_ascii=False), encoding="utf-8")


def list_notifications(root, mark_read=False):
    f = Path(root) / "notifications.json"
    items = _read(f, [])
    if mark_read and any(not i.get("read") for i in items):
        for i in items:
            i["read"] = True
        f.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
    return items


# ---------------------------------------------------------------- workflows
WORKFLOW_TEMPLATES = [
    {"name": "New files → Build → Check → Notify", "trigger": "new_files",
     "steps": ["build"], "notify": True},
    {"name": "Weekly summary report", "trigger": "weekly",
     "steps": ["skill:timeline", "skill:letters"], "notify": True},
]


def list_workflows(root):
    return _read(Path(root) / "workflows.json", [])


def save_workflows(root, items):
    clean = []
    for w in items:
        clean.append({"id": w.get("id") or f"wf{int(time.time() * 1000)}{len(clean)}",
                      "name": str(w.get("name", "Workflow"))[:80], "brain": str(w.get("brain", "")),
                      "trigger": w.get("trigger") if w.get("trigger") in ("manual", "new_files", "daily", "weekly") else "manual",
                      "steps": [s for s in w.get("steps", []) if s == "build" or str(s).startswith("skill:")],
                      "notify": bool(w.get("notify", True)), "enabled": bool(w.get("enabled", True)),
                      "last_run": w.get("last_run", "")})
    _write(Path(root) / "workflows.json", clean)
    return clean
