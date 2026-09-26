"""Checks on a Build Brain run before you approve it.

1. quote_check(): plain code, no AI. Every document citation must carry the
   exact words it relies on. This opens the cited file and confirms those
   words are really there. Code cannot invent anything, so this is the
   strongest guard against made-up facts.
2. checker_review(): a second, different AI reads the changed pages against
   their sources and reports anything unsupported, wrong or missing.

Both only read. Neither changes the wiki or any document.
"""

import re
import zipfile
from pathlib import Path

FOOTNOTE = re.compile(r'^\[\^([^\]]+)\]:\s*(file|web):\s*(.*)$')
QUOTED = re.compile(r'^(.*?)\s*(?:[—–-]+\s*)?["“](.+)["”]\s*$')
TEXT_TYPES = {".txt", ".md", ".csv", ".htm", ".html", ".eml", ".rtf", ".json", ".xml"}


def normalise(text):
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def extract_text(path):
    """Return the document's text, or None if this file type cannot be checked."""
    suffix = path.suffix.lower()
    if suffix in TEXT_TYPES:
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".docx":
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
        xml = re.sub(r"</w:p>", "\n", xml)
        return re.sub(r"<[^>]+>", "", xml)
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader          # optional: pip install pypdf
        except ImportError:
            return None
        return "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    return None


def inside(path, folders):
    try:
        p = Path(path).resolve()
    except OSError:
        return False
    return any(p == Path(f).resolve() or Path(f).resolve() in p.parents for f in folders)


def quote_check(wiki_dir, pages, folders):
    """pages: list of 'Category/File.md' (added or changed). Returns a summary dict."""
    cache, results = {}, []
    for rel in pages:
        text = (Path(wiki_dir) / rel).read_text(encoding="utf-8", errors="replace")
        lines = []                        # join footnotes that wrap onto following lines
        for raw in text.splitlines():
            line = raw.strip()
            if lines and lines[-1].startswith("[^") and line and not line.startswith(("[^", "#", "- ", "---")):
                lines[-1] += " " + line
            else:
                lines.append(line)
        for line in lines:
            m = FOOTNOTE.match(line)
            if not m:
                continue
            ref, kind, rest = m.groups()
            item = {"page": rel, "ref": ref, "kind": kind}
            if kind == "web":
                item.update(status="web", source=rest.strip())
                results.append(item)
                continue
            q = QUOTED.match(rest.strip())
            src = (q.group(1) if q else rest).strip().rstrip("—–- ").strip()
            item["source"] = src
            if not q:
                item["status"] = "no quote"
            elif not inside(src, folders):
                item["status"] = "source not in this brain's folders"
            elif not Path(src).is_file():
                item["status"] = "source file not found"
            else:
                item["quote"] = q.group(2)
                if src not in cache:
                    try:
                        cache[src] = extract_text(Path(src))
                    except Exception:
                        cache[src] = None
                body = cache[src]
                if body is None:
                    item["status"] = "cannot check this file type"
                else:
                    item["status"] = "verified" if normalise(q.group(2)) in normalise(body) else "NOT FOUND"
            results.append(item)
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    problems = [r for r in results if r["status"] not in ("verified", "web")]
    return {"counts": counts, "problems": problems[:100], "total": len(results)}


CHECK_PROMPT = """You are the CHECKER for the KastoBrain brain "{name}". Another AI wrote the
changes below. Your job is to catch mistakes before the owner approves them.
You are READ-ONLY: never create, change, move or delete any file.

Changed or new pages (in ./wiki): {pages}
Automatic quote check found these problems: {problems}

For each changed page: open it, open the documents it cites, and check every fact.
Report ONLY real problems, each on one line starting with "- ":
  wrong facts, facts not supported by the cited source, invented details,
  wrong dates/amounts/names, missing important facts from the documents.
Do not rewrite anything.

Your FIRST line must be exactly "VERDICT: OK" (no real problems)
or "VERDICT: ISSUES" (one or more problems listed below it).
"""


def checker_review(run_ai, ai, rdir, name, pages, qc, folders):
    problems = "; ".join(f"{p['page']} [^{p['ref']}] {p['status']}" for p in qc["problems"][:30]) or "none"
    code, out = run_ai(ai, rdir, CHECK_PROMPT.format(name=name, pages=", ".join(pages) or "none",
                                                     problems=problems), folders, write=False)
    first = next((l.strip() for l in out.splitlines() if l.strip()), "")
    verdict = "OK" if first.upper().startswith("VERDICT: OK") else (
        "ISSUES" if first.upper().startswith("VERDICT: ISSUES") else "UNCLEAR")
    if code != 0:
        verdict = "NOT RUN"
    return {"ai": ai, "exit_code": code, "verdict": verdict, "report": out.strip()[:20000]}
