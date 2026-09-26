"""PC Brain sorter: DRY RUN ONLY.

Scans the folders you list in PC-Brain/sorter.json and writes a plan of
suggested moves into PC-Brain/sorter-plans/. It never moves, copies,
renames or deletes anything. Nothing is ever deleted by KastoBrain:
anything suspect is only ever SUGGESTED for the "FOR REVIEW – TO DELETE"
folder, for you to look at.

What this version suggests:
  - exact duplicates (same size and same SHA-256): keep the oldest copy,
    suggest the others for review
  - empty (0 byte) files
  - temporary / junk files (~$ Office lock files, .tmp, Thumbs.db, .DS_Store)

Usage:
    python pc_sorter.py H:\\KastoBrain
"""

import csv
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

REVIEW = "FOR REVIEW – TO DELETE"
JUNK_NAMES = {"thumbs.db", ".ds_store"}
JUNK_SUFFIXES = {".tmp", ".temp"}


def settings_file(root):
    return Path(root) / "PC-Brain" / "sorter.json"


def load_settings(root):
    f = settings_file(root)
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {"scan_folders": [], "skip_folders": []}


def save_settings(root, data):
    s = {"scan_folders": [str(x) for x in data.get("scan_folders", []) if str(x).strip()],
         "skip_folders": [str(x) for x in data.get("skip_folders", []) if str(x).strip()]}
    f = settings_file(root)
    f.parent.mkdir(parents=True, exist_ok=True)
    if f.exists():
        (f.parent / "sorter.previous.json").write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    f.write_text(json.dumps(s, indent=2), encoding="utf-8")
    return s


def list_plans(root):
    d = Path(root) / "PC-Brain" / "sorter-plans"
    out = []
    for f in sorted(d.glob("*.json"), reverse=True)[:10]:
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
            out.append({k: p[k] for k in ("id", "time", "scanned", "suggestions", "by_reason", "folders", "errors")}
                       | {"items": p["items"][:200]})
        except Exception:
            continue
    return out


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def skipped(path, skips):
    p = str(path).lower()
    return any(p == s.lower().rstrip("\\/") or p.startswith(s.lower().rstrip("\\/") + ("\\" if "\\" in s else "/"))
               for s in skips)


def dry_run(root, log=print):
    root = Path(root)
    s = load_settings(root)
    skips = s["skip_folders"] + [str(root)]          # never scan KastoBrain's own folder
    run = datetime.now().strftime("%Y%m%d-%H%M%S")
    log(f"=== PC sorter DRY RUN {run}: nothing will be moved ===")
    files, errors = [], []
    for folder in s["scan_folders"]:
        base = Path(folder)
        if not base.is_dir():
            errors.append(f"folder not found: {folder}")
            continue
        for p in base.rglob("*"):
            try:
                if p.is_file() and not skipped(p, skips):
                    st = p.stat()
                    files.append((p, st.st_size, st.st_mtime))
            except OSError as e:
                errors.append(f"{p}: {e}")
    log(f"  files scanned: {len(files)}")

    items = []

    def suggest(path, reason, keep=""):
        items.append({"file": str(path), "reason": reason, "keep": keep,
                      "suggested_move_to": str(root / REVIEW / Path(path).name)})

    by_size = {}
    for p, size, mtime in files:
        name = p.name.lower()
        if size == 0:
            suggest(p, "empty file")
        elif name.startswith("~$") or name in JUNK_NAMES or p.suffix.lower() in JUNK_SUFFIXES:
            suggest(p, "temporary / junk file")
        else:
            by_size.setdefault(size, []).append((p, mtime))
    hashed = 0
    for size, group in by_size.items():
        if len(group) < 2:
            continue
        by_hash = {}
        for p, mtime in group:
            try:
                by_hash.setdefault(sha256(p), []).append((p, mtime))
                hashed += 1
            except OSError as e:
                errors.append(f"{p}: {e}")
        for same in by_hash.values():
            if len(same) > 1:
                same.sort(key=lambda x: x[1])
                for p, _ in same[1:]:
                    suggest(p, "exact duplicate", keep=str(same[0][0]))
    log(f"  files hashed for duplicate check: {hashed}")

    by_reason = {}
    for i in items:
        by_reason[i["reason"]] = by_reason.get(i["reason"], 0) + 1
    plan = {"id": run, "time": datetime.now().isoformat(timespec="seconds"), "folders": s["scan_folders"],
            "scanned": len(files), "suggestions": len(items), "by_reason": by_reason,
            "errors": errors[:200], "items": items}
    out = root / "PC-Brain" / "sorter-plans"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{run}.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    with open(out / f"{run}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["file", "reason", "keep", "suggested_move_to"])
        w.writeheader()
        w.writerows(items)
    log(f"  suggestions: {len(items)} {by_reason}")
    log(f"  plan saved: {out / (run + '.csv')}")
    log("  NOTHING WAS MOVED. This is a list of suggestions only.")
    return plan


if __name__ == "__main__":
    dry_run(sys.argv[1])
