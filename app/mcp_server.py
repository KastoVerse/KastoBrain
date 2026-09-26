"""KastoBrain MCP connector (stdio). Works with Claude Code, Codex and Gemini CLI.

Gives the AI read access to your brains, plus one action: start a Build
Brain run. A build only ever produces a Pending change; approving it is
done by you, in the KastoBrain app. There is no approve, delete or
move tool here on purpose.

Usage (the AI app starts this itself once configured, see README):
    python mcp_server.py H:\\KastoBrain
"""

import json
import subprocess
import sys
from pathlib import Path

import brain

ROOT = None
APP = Path(__file__).resolve().parent

TOOLS = [
    {"name": "list_brains", "description": "List all KastoBrain project brains with page and pending counts.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "brain_index", "description": "Read a brain's wiki index: one line per page. Start here.",
     "inputSchema": {"type": "object", "properties": {"brain": {"type": "string"}}, "required": ["brain"]}},
    {"name": "read_page", "description": "Read one wiki page in full, with its citations and [[links]].",
     "inputSchema": {"type": "object", "properties": {"brain": {"type": "string"}, "title": {"type": "string"}},
                     "required": ["brain", "title"]}},
    {"name": "search", "description": "Text search across wiki pages. Leave brain empty to search every brain.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "brain": {"type": "string"}},
                     "required": ["query"]}},
    {"name": "list_pending", "description": "List Build Brain runs and whether they are pending approval.",
     "inputSchema": {"type": "object", "properties": {"brain": {"type": "string"}}, "required": ["brain"]}},
    {"name": "build_brain", "description": "Start a Build Brain run in the background. The result goes to Pending; "
                                           "the owner approves it in the KastoBrain app. Nothing changes until then.",
     "inputSchema": {"type": "object", "properties": {"brain": {"type": "string"}}, "required": ["brain"]}},
]


for _t in TOOLS:   # read tools are safe: label them so AI apps can run them without asking
    _t["annotations"] = ({"readOnlyHint": True} if _t["name"] != "build_brain"
                         else {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False})


def need(name):
    pdir = brain.project_dir(ROOT, name)
    if not pdir:
        raise ValueError(f"No brain called '{name}'. Brains: {', '.join(brain.list_projects(ROOT)) or 'none'}")
    return pdir


def call(name, a):
    if name == "list_brains":
        rows = []
        for n in brain.list_projects(ROOT):
            p = brain.project_dir(ROOT, n)
            rows.append(f"- {n}: {len(brain.wiki_pages(p))} pages, "
                        f"{sum(1 for r in brain.pending_runs(p) if r['status'] == 'pending')} pending. "
                        f"{brain.settings(p).get('description', '')}")
        return "\n".join(rows) or "No brains yet."
    if name == "brain_index":
        return brain.read_index(need(a["brain"])) or "Index is empty. The brain has not been built yet."
    if name == "read_page":
        page = brain.read_page(need(a["brain"]), a["title"])
        return page["text"] if page else f"No page titled '{a['title']}'. Use brain_index to see titles."
    if name == "search":
        names = [a["brain"]] if a.get("brain") else brain.list_projects(ROOT)
        hits = [f"[{n} / {h['page']}] {h['text']}" for n in names for h in brain.search(need(n), a["query"], 15)]
        return "\n".join(hits) or "No matches."
    if name == "list_pending":
        runs = brain.pending_runs(need(a["brain"]))
        return "\n".join(f"- {r['run']}: {r['status']}, added {len(r['added'])}, changed {len(r['changed'])}, "
                         f"removed {len(r['removed'])}" for r in runs) or "No runs yet."
    if name == "build_brain":
        pdir = need(a["brain"])
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        subprocess.Popen([sys.executable, str(APP / "dream.py"), str(ROOT), pdir.name], cwd=str(APP),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=flags)
        return (f"Build Brain started for '{pdir.name}'. When it finishes, the changes wait in "
                f"Brain → Pending approval in the KastoBrain app. Nothing changes until the owner approves.")
    raise ValueError(f"Unknown tool {name}")


def reply(msg_id, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": msg_id}
    if error:
        out["error"] = {"code": -32603, "message": error}
    else:
        out["result"] = result
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    global ROOT
    if len(sys.argv) < 2:
        sys.stderr.write("usage: mcp_server.py <KastoBrain root>\n")
        return 1
    ROOT = Path(sys.argv[1])
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, msg_id = msg.get("method"), msg.get("id")
        if msg_id is None:
            continue                                    # notifications need no reply
        if method == "initialize":
            reply(msg_id, {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2024-11-05"),
                           "capabilities": {"tools": {}},
                           "serverInfo": {"name": "kastobrain", "version": "0.1"}})
        elif method == "tools/list":
            reply(msg_id, {"tools": TOOLS})
        elif method == "tools/call":
            p = msg.get("params", {})
            try:
                text = call(p.get("name"), p.get("arguments") or {})
                reply(msg_id, {"content": [{"type": "text", "text": text}]})
            except Exception as e:
                reply(msg_id, {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})
        elif method == "ping":
            reply(msg_id, {})
        else:
            reply(msg_id, error=f"method not supported: {method}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
