"""Shared KastoBrain core, used by the web app and the MCP connector.

Everything here only reads, except:
  - create_project(): makes one new brain's folders and settings.json
  - ask():            saves the question and answer as a session file
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path

import dream
import setup_layout

NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-&().,']{0,60}$")

ASK_PROMPT = """You are {agent}, answering questions from the KastoBrain project brain "{name}".
You are READ-ONLY: never create, change, move or delete any file.

How to answer (Perplexity's Brain method):
1. Read ./wiki/index.md, then the pages relevant to the question in ./wiki.
2. Follow [[links]] to related pages when useful.
3. When proof matters, open the original document cited on the page.
4. Only if the wiki and documents do not cover it, research online.
Answer in plain English. Cite every fact: [page: Title], [file: full path] or [web: URL].
If you do not know, say so. Never guess.

Standing instructions for this brain:
{instructions}

Style (tone only; it never changes the rules above): {style}

Question:
{question}
"""


def projects_root(root):
    return Path(root) / "Projects"


def list_projects(root):
    p = projects_root(root)
    if not p.is_dir():
        return []
    return [d.name for d in sorted(p.iterdir()) if (d / "settings.json").is_file()]


def project_dir(root, name):
    projects = projects_root(root).resolve()
    path = (projects / str(name)).resolve()
    if path.parent != projects or not (path / "settings.json").is_file():
        return None
    return path


def settings(pdir):
    return json.loads((pdir / "settings.json").read_text(encoding="utf-8"))


def parse_page(path, category):
    text = path.read_text(encoding="utf-8")
    meta = {}
    if text.startswith("---"):
        for line in text.split("---", 2)[1].splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.split("#")[0].strip()
    return {"category": category, "file": path.name, "title": meta.get("title", path.stem),
            "summary": meta.get("summary", ""), "updated": meta.get("updated", ""), "text": text}


def wiki_pages(pdir):
    pages = []
    for cat in dream.CATEGORIES:
        d = pdir / "wiki" / cat
        if d.is_dir():
            pages += [parse_page(p, cat) for p in sorted(d.glob("*.md"))]
    return pages


def read_index(pdir):
    f = pdir / "wiki" / "index.md"
    return f.read_text(encoding="utf-8") if f.exists() else ""


def read_page(pdir, title):
    t = title.lower().removesuffix(".md")
    for p in wiki_pages(pdir):
        if p["title"].lower() == t or p["file"].lower().removesuffix(".md") == t:
            return p
    return None


def search(pdir, query, limit=20):
    """Plain text search over the wiki (like grep). Returns matching lines."""
    words = [w.lower() for w in query.split() if w.strip()]
    hits = []
    for p in wiki_pages(pdir):
        for n, line in enumerate(p["text"].splitlines(), 1):
            low = line.lower()
            if words and all(w in low for w in words):
                hits.append({"page": p["title"], "category": p["category"], "line": n, "text": line.strip()})
                if len(hits) >= limit:
                    return hits
    return hits


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


def create_project(root, name):
    """Make a new brain; it starts with the app-wide defaults (AI team, personality)."""
    made = _create_project(root, name)
    import appconf
    app = appconf.load_settings(root)
    p = projects_root(root) / made / "settings.json"
    s = json.loads(p.read_text(encoding="utf-8"))
    s.update(ai=app["ai"], ai_ask=app["ai_ask"], ai_check=app["ai_check"], personality=dict(app["personality"]))
    p.write_text(json.dumps(s, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (projects_root(root) / made / "Files").mkdir(exist_ok=True)
    return made


def _create_project(root, name):
    name = (name or "").strip()
    if not NAME_OK.match(name) or ".." in name:
        raise ValueError("Use letters, numbers, spaces and - _ & ( ) only (max 60).")
    if (projects_root(root) / name).exists():
        raise ValueError("A brain with that name already exists.")
    folders, files = setup_layout.plan(Path(root), name)
    base = projects_root(root) / name
    for f in folders:
        if base in f.parents or f == base:
            f.mkdir(parents=True, exist_ok=True)
    for path, text in files:
        if base in path.parents and not path.exists():
            path.write_text(text, encoding="utf-8")
    return name


TONES = {
    "professional": "clear, formal and precise",
    "friendly": "warm, plain and encouraging",
    "bubbly": "upbeat, cheerful and lively (still accurate)",
    "blunt": "direct and brief, no padding",
}
LENGTHS = {"short": "Keep answers short: a few sentences.", "normal": "",
           "detailed": "Give detailed, thorough answers."}


def app_defaults(pdir):
    import appconf
    return appconf.load_settings(Path(pdir).parent.parent)


def style_text(s, pdir=None):
    p = s.get("personality") or (app_defaults(pdir)["personality"] if pdir else {})
    tone = TONES.get(p.get("tone", "professional"), TONES["professional"])
    return f"Write in a {tone} tone. {LENGTHS.get(p.get('length', 'normal'), '')}".strip()


def safe_child(base, rel):
    """Resolve rel inside base; None if it would escape base."""
    base = Path(base).resolve()
    target = (base / (rel or "")).resolve()
    return target if target == base or base in target.parents else None


def brain_mcp(pdir, s):
    """Custom MCP connectors switched on for this brain."""
    wanted = set(s.get("connectors") or [])
    return [m for m in app_defaults(pdir).get("mcp_connectors", []) if m.get("name") in wanted]


SKILL_PROMPT = """You are {agent}, running the skill "{skill}" on the KastoBrain brain "{name}".
You are READ-ONLY: never create, change, move or delete any file.
Use ./wiki (start with ./wiki/index.md) and the original documents it cites.
Cite every fact: [page: Title], [file: full path] or [web: URL]. Never guess; say what is missing.
Style: {style}

Task:
{task}
"""


def run_skill(pdir, skill):
    """Run a skill read-only and save the result as a report (brain reports/ and the Downloads folder)."""
    import appconf
    s = settings(pdir)
    app = app_defaults(pdir)
    ai = s.get("ai_ask") or s.get("ai", "chatgpt")
    started = time.time()
    code, out = dream.run_ai(ai, pdir, SKILL_PROMPT.format(agent=app["agent_name"], skill=skill["name"], name=pdir.name,
                                                           style=style_text(s, pdir), task=skill["prompt"]),
                             dream.brain_folders(s, pdir), write=False, mcp=brain_mcp(pdir, s))
    stamp = datetime.now().strftime("%Y-%m-%d %H%M")
    text = f"# {skill['name']} — {pdir.name}\n\n_{stamp} · {ai} · {round(time.time() - started)}s_\n\n{out.strip()}\n"
    (pdir / "reports").mkdir(exist_ok=True)
    rep = pdir / "reports" / f"{stamp} - {skill['id']}.md"
    rep.write_text(text, encoding="utf-8")
    dl = appconf.downloads_dir(pdir.parent.parent) / f"{pdir.name} - {skill['name']} - {stamp}.md"
    dl.write_text(text, encoding="utf-8")
    return {"ok": code == 0, "report": str(rep), "download": str(dl), "text": text}


def list_reports(pdir):
    d = pdir / "reports"
    return [{"name": f.name, "path": str(f), "mtime": int(f.stat().st_mtime)}
            for f in sorted(d.glob("*.md"), reverse=True)] if d.is_dir() else []


def list_sessions(pdir, limit=50):
    out = []
    for f in sorted((pdir / "sessions").glob("*.json"), reverse=True)[:limit]:
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def ask(pdir, question):
    s = settings(pdir)
    ai = s.get("ai_ask") or s.get("ai", "chatgpt")
    app = app_defaults(pdir)
    started = time.time()
    code, output = dream.run_ai(
        ai, pdir, ASK_PROMPT.format(agent=app["agent_name"], name=pdir.name,
                                    instructions=s.get("instructions", "") or "(none)",
                                    style=style_text(s, pdir), question=question),
        dream.brain_folders(s, pdir), write=False, mcp=brain_mcp(pdir, s))
    sid = datetime.now().strftime("%Y%m%d-%H%M%S")
    session = {"id": sid, "question": question, "answer": output.strip(), "ai": ai,
               "ok": code == 0, "seconds": round(time.time() - started, 1),
               "time": datetime.now().isoformat(timespec="seconds")}
    (pdir / "sessions").mkdir(exist_ok=True)
    n, f = 1, pdir / "sessions" / f"{sid}.json"
    while f.exists():
        n += 1
        f = pdir / "sessions" / f"{sid}-{n}.json"
    session["id"] = f.stem
    f.write_text(json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8")
    return session
