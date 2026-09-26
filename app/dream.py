"""Build Brain / Dream for one project brain.

How a run works (Perplexity's Brain method, plus the approval gate):
  1. Orient:    write a brief with the scope, standing instructions,
                deletion log, document list and stopping conditions.
  2. Copy:      copy the live wiki into Projects/<name>/pending/<run>/wiki.
  3. Dream:     the AI (Claude Code or Codex, on your own plan) reads the
                documents, researches online, and edits ONLY that copy:
                summarise -> attach facts to subjects -> update the wiki.
  4. Pending:   the differences are listed in changes.json. Nothing in the
                live wiki changes until you approve.
  5. Approve:   the copy replaces the live wiki. The old wiki is kept as
                wiki.previous (one previous generation), so it can be undone.

Your documents are only ever read, never changed.

Usage:
    python dream.py H:\\KastoBrain "Project"                 build (to Pending)
    python dream.py H:\\KastoBrain "Project" --approve RUN   apply a pending run
    python dream.py H:\\KastoBrain "Project" --reject RUN    discard a pending run
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

DOC_TYPES = {".pdf", ".docx", ".doc", ".txt", ".md", ".rtf", ".eml", ".msg", ".xlsx", ".xls",
             ".csv", ".htm", ".html", ".png", ".jpg", ".jpeg"}
CATEGORIES = ("Concepts", "Entities", "Workstreams")

DREAM_PROMPT = """You are the Dream agent for the KastoBrain project brain "{name}".
Your working directory is a COPY of this brain's wiki. Edit only files inside ./wiki.
Never create, change, move or delete anything outside ./wiki. The documents are read-only.

Follow these phases in order.

1. ORIENT. Read ./BRIEF.md fully: scope, standing instructions, deletion log,
   the document list (NEW/CHANGED ones first) and the stopping conditions.
   Read ./wiki/index.md and any existing pages you will touch.

2. SUMMARISE. Read each session listed in the brief (a question and answer
   you gave before; corrections in them matter most). Read each NEW or CHANGED document. Understand what it says,
   who and what it involves, dates, amounts, reference numbers.

3. ATTACH FACTS TO SUBJECTS. Decide the subjects yourself:
   - Entities: people, companies, organisations, policies, accounts.
   - Workstreams: claims, cases, disputes, jobs, projects, matters.
   - Concepts: arguments, issues, rules, laws, themes.
   Put each significant fact on the right page. Research online to confirm
   and add proper details (company registration, licences, legislation,
   regulators, citations, rules) where it helps this brain. Prefer the
   websites listed in the brief.

4. UPDATE THE WIKI. Create or revise pages in ./wiki/Concepts, ./wiki/Entities
   or ./wiki/Workstreams using the page format in the brief. Reinforce what is
   still true, update what changed, mark stale facts as "(stale: reason)".
   Every fact needs a citation. For a document the footnote MUST be exactly:
   [^n]: file: FULL PATH AS LISTED IN THE BRIEF — "exact words copied from the document"
   Keep each footnote on ONE line. Copy the quoted words character for character (about 5 to 40 words) so they can be
   checked automatically. Never paraphrase inside the quotes. For the web use:
   [^n]: web: URL (checked YYYY-MM-DD)
   Link related pages with [[Page Title]].
   Never re-create anything listed in the deletion log. If nothing needs to
   change, change nothing.
   Finally rewrite ./wiki/index.md: one line per page,
   "- [[Title]] (Category): one-line summary".

Finish with a short plain-English report of what you changed and why.
"""


def now_id():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def load_settings(pdir):
    return json.loads((pdir / "settings.json").read_text(encoding="utf-8"))


def under(path, folders):
    p = str(path).lower()
    return any(p.startswith(str(Path(f)).lower().rstrip("\\/") + os.sep.lower()) or p == str(Path(f)).lower()
               for f in folders)


def scan_documents(settings):
    """List readable documents in the brain's folders, skipping never_send."""
    docs, missing = [], []
    for folder in settings.get("folders", []):
        root = Path(folder)
        if not root.is_dir():
            missing.append(folder)
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix.lower() in DOC_TYPES and not under(p, settings.get("never_send", [])):
                st = p.stat()
                docs.append({"path": str(p), "size": st.st_size, "mtime": int(st.st_mtime)})
    return docs, missing


def doc_key(d):
    return hashlib.sha1(f"{d['path']}|{d['size']}|{d['mtime']}".encode()).hexdigest()


def new_sessions(pdir):
    seen_file = pdir / "sessions_processed.json"
    seen = set(json.loads(seen_file.read_text(encoding="utf-8"))) if seen_file.exists() else set()
    return sorted(p.name for p in (pdir / "sessions").glob("*.json") if p.name not in seen)


def write_brief(pdir, settings, docs, seen):
    deletions = pdir / "deletions.md"
    deletion_text = deletions.read_text(encoding="utf-8") if deletions.exists() else "(none)"
    template = (Path(__file__).resolve().parent.parent / "templates" / "wiki-page.md").read_text(encoding="utf-8")
    def bullets(items):
        return [f"- {x}" for x in items] or ["(none)"]

    new = [d for d in docs if doc_key(d) not in seen]
    old = [d for d in docs if doc_key(d) in seen]
    lines = [
        f"# Brief for {settings['name']}",
        "", "## Scope", settings.get("description", ""),
        "", "## Standing instructions", settings.get("instructions", "") or "(none)",
        "", "## Websites to prioritise", *bullets(settings.get("links", [])),
        "", "## Never read (private)", *bullets(settings.get("never_send", [])),
        "", "## Deletion log (never re-create these)", deletion_text,
        "", f"## Documents: NEW or CHANGED ({len(new)})", *bullets(d["path"] for d in new),
        "", f"## Documents: already processed ({len(old)})", *bullets(d["path"] for d in old),
        "", "## Sessions to summarise (questions asked since the last approved run)",
        *bullets(str(pdir / "sessions" / n) for n in new_sessions(pdir)),
        "", "## Stopping conditions",
        "- Stop when every NEW or CHANGED document has been read and its facts filed.",
        "- Do not rewrite pages that are already correct.",
        "", "## Page format", "```", template, "```",
    ]
    return "\n".join(lines) + "\n"


def list_pages(wiki):
    out = {}
    for cat in CATEGORIES:
        d = wiki / cat
        if d.is_dir():
            for p in d.glob("*.md"):
                out[f"{cat}/{p.name}"] = p.read_text(encoding="utf-8")
    return out


def run_ai(ai, workdir, prompt, folders, write=True):
    """Run one AI job on your own plan. write=False means read-only (for Ask)."""
    if os.environ.get("KASTOBRAIN_TEST_AI"):
        cmd = [sys.executable, os.environ["KASTOBRAIN_TEST_AI"], prompt]
    elif ai == "chatgpt":
        last = Path(tempfile.gettempdir()) / f"kastobrain-codex-{os.getpid()}-{time.time_ns()}.txt"
        cmd = ["codex", "exec", "--skip-git-repo-check", "--output-last-message", str(last),
               "--sandbox", "workspace-write" if write else "read-only", prompt]
    elif ai == "grok":
        cmd = ["grok", "-p", prompt]
    elif ai == "gemini":
        cmd = ["gemini", "-p", prompt, "--approval-mode", "auto_edit" if write else "default"]
        for f in folders:
            cmd += ["--include-directories", f]
    else:
        tools = "Read,Glob,Grep,WebSearch,WebFetch" + (",Write,Edit" if write else "")
        cmd = ["claude", "-p", prompt, "--allowedTools", tools]
        if write:
            cmd += ["--permission-mode", "acceptEdits"]
        for f in folders:
            cmd += ["--add-dir", f]
    exe = shutil.which(cmd[0]) or cmd[0]
    try:
        proc = subprocess.run([exe] + cmd[1:], cwd=workdir, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return 127, f"{cmd[0]} is not installed or not on PATH."
    if ai == "chatgpt" and not os.environ.get("KASTOBRAIN_TEST_AI"):
        # Codex prints its whole working log; keep only its final answer.
        try:
            final = last.read_text(encoding="utf-8").strip()
            last.unlink()
        except OSError:
            final = ""
        if proc.returncode == 0 and final:
            return 0, final
    return proc.returncode, proc.stdout + ("\n" + proc.stderr if proc.stderr.strip() else "")


def build(root, name, log=print):
    pdir = root / "Projects" / name
    settings = load_settings(pdir)
    run = now_id()
    n = 1
    while (pdir / "pending" / run).exists():   # never reuse a run folder
        n += 1
        run = f"{now_id()}-{n}"
    rdir = pdir / "pending" / run
    log(f"=== Build Brain: {name}  run {run} ===")

    docs, missing = scan_documents(settings)
    for m in missing:
        log(f"  folder not found, skipped: {m}")
    seen_file = pdir / "processed.json"
    seen = set(json.loads(seen_file.read_text(encoding="utf-8"))) if seen_file.exists() else set()
    new_count = sum(1 for d in docs if doc_key(d) not in seen)
    log(f"  documents found: {len(docs)}   new or changed: {new_count}")

    wiki = pdir / "wiki"
    shutil.copytree(wiki, rdir / "wiki")
    (rdir / "BRIEF.md").write_text(write_brief(pdir, settings, docs, seen), encoding="utf-8")
    log("  brief written, wiki copied to pending; starting Dream …")

    code, output = run_ai(settings.get("ai", "chatgpt"), rdir, DREAM_PROMPT.format(name=name),
                          settings.get("folders", []) + [str(pdir / "sessions")])
    (rdir / "dream-output.txt").write_text(output, encoding="utf-8")

    before, after = list_pages(wiki), list_pages(rdir / "wiki")
    changes = {
        "run": run, "project": name, "ai": settings.get("ai", "chatgpt"), "exit_code": code,
        "documents": len(docs), "new_or_changed": new_count,
        "added": sorted(k for k in after if k not in before),
        "changed": sorted(k for k in after if k in before and after[k] != before[k]),
        "removed": sorted(k for k in before if k not in after),
        "doc_keys": [doc_key(d) for d in docs],
        "sessions": new_sessions(pdir),
        "status": "pending" if code == 0 else "failed",
    }
    if code == 0 and not (changes["added"] or changes["changed"] or changes["removed"]):
        changes["status"] = "no changes"
    touched = changes["added"] + changes["changed"]
    if changes["status"] == "pending" and touched:
        import verify
        folders = settings.get("folders", [])
        qc = verify.quote_check(rdir / "wiki", touched, folders)
        changes["quote_check"] = qc
        log(f"  Quote check (plain code): {qc['counts']}")
        checker = settings.get("ai_check", "")
        if checker and checker != "none":
            log(f"  Checker AI ({checker}) reviewing the changes …")
            changes["checker"] = verify.checker_review(run_ai, checker, rdir, name, touched, qc, folders)
            log(f"  Checker verdict: {changes['checker']['verdict']}")
    (rdir / "changes.json").write_text(json.dumps(changes, indent=2), encoding="utf-8")
    log(f"  Dream finished (exit {code}). Added {len(changes['added'])}, "
        f"changed {len(changes['changed'])}, removed {len(changes['removed'])}.")
    log(f"  Status: {changes['status'].upper()}. Nothing in the live wiki has changed.")
    return changes


def approve(root, name, run, log=print):
    pdir = root / "Projects" / name
    rdir = pdir / "pending" / run
    changes = json.loads((rdir / "changes.json").read_text(encoding="utf-8"))
    if changes["status"] != "pending":
        raise ValueError(f"run {run} is {changes['status']}, not pending")
    wiki, prev = pdir / "wiki", pdir / "wiki.previous"
    if prev.exists():
        shutil.rmtree(prev)          # only the one previous generation is kept
    wiki.rename(prev)
    shutil.copytree(rdir / "wiki", wiki)
    for p in changes["removed"]:      # removals go in the deletion log so Dream never re-creates them
        with open(pdir / "deletions.md", "a", encoding="utf-8") as f:
            f.write(f"- {p} (removed in run {run})\n")
    (pdir / "processed.json").write_text(json.dumps(changes["doc_keys"]), encoding="utf-8")
    sp = pdir / "sessions_processed.json"
    done = set(json.loads(sp.read_text(encoding="utf-8"))) if sp.exists() else set()
    sp.write_text(json.dumps(sorted(done | set(changes.get("sessions", [])))), encoding="utf-8")
    changes["status"] = "approved"
    (rdir / "changes.json").write_text(json.dumps(changes, indent=2), encoding="utf-8")
    log(f"Approved {run}: wiki updated. Previous wiki kept as wiki.previous.")
    return changes


def reject(root, name, run, log=print):
    rdir = root / "Projects" / name / "pending" / run
    changes = json.loads((rdir / "changes.json").read_text(encoding="utf-8"))
    changes["status"] = "rejected"
    (rdir / "changes.json").write_text(json.dumps(changes, indent=2), encoding="utf-8")
    log(f"Rejected {run}: live wiki untouched. The run stays in pending for reference.")
    return changes


def main():
    ap = argparse.ArgumentParser(description="Build Brain (Dream) for one project.")
    ap.add_argument("root")
    ap.add_argument("project")
    ap.add_argument("--approve", metavar="RUN")
    ap.add_argument("--reject", metavar="RUN")
    a = ap.parse_args()
    root = Path(a.root)
    if a.approve:
        approve(root, a.project, a.approve)
    elif a.reject:
        reject(root, a.project, a.reject)
    else:
        build(root, a.project)
    return 0


if __name__ == "__main__":
    sys.exit(main())
