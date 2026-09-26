"""Create the KastoBrain folder layout.

Dry run by default: prints what it would create and changes nothing.
Only creates folders and starter files that do not already exist.
Never overwrites, moves or deletes anything.

Usage:
    python setup_layout.py H:\\KastoBrain            (dry run)
    python setup_layout.py H:\\KastoBrain --apply    (create)
    python setup_layout.py H:\\KastoBrain --apply --project "Insurance"
"""

import argparse
import json
import sys
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"

TOP_FOLDERS = [
    "PC-Brain/wiki/Concepts",
    "PC-Brain/wiki/Entities",
    "PC-Brain/wiki/Workstreams",
    "PC-Brain/sessions",
    "PC-Brain/pending",
    "Projects",
    "FOR REVIEW – TO DELETE",
    "Logs",
]

PROJECT_FOLDERS = [
    "wiki/Concepts",
    "wiki/Entities",
    "wiki/Workstreams",
    "sessions",
    "pending",
]


def plan(root, project):
    """Return (folders, files) to create. files is a list of (path, text)."""
    folders = [root / f for f in TOP_FOLDERS]
    files = [(root / "PC-Brain/wiki/index.md", "# PC Brain index\n")]
    if project:
        base = root / "Projects" / project
        folders += [base / f for f in PROJECT_FOLDERS]
        settings = json.loads((TEMPLATES / "settings.example.json").read_text(encoding="utf-8"))
        settings["name"] = project
        settings["folders"] = []
        settings["never_send"] = []
        settings["links"] = []
        files.append((base / "settings.json", json.dumps(settings, indent=2, ensure_ascii=False) + "\n"))
        files.append((base / "wiki/index.md", f"# {project} index\n"))
    return folders, files


def main():
    parser = argparse.ArgumentParser(description="Create the KastoBrain folder layout.")
    parser.add_argument("root", help="KastoBrain root, e.g. H:\\KastoBrain")
    parser.add_argument("--project", help="Also create one project brain with this name")
    parser.add_argument("--apply", action="store_true", help="Actually create (default is dry run)")
    args = parser.parse_args()

    root = Path(args.root)
    folders, files = plan(root, args.project)
    mode = "CREATING" if args.apply else "DRY RUN (nothing will be changed)"
    print(f"=== KastoBrain layout: {mode} ===")
    print(f"Root: {root}\n")

    created = skipped = 0
    for folder in folders:
        if folder.exists():
            print(f"  exists, skipped   {folder}")
            skipped += 1
            continue
        print(f"  new folder        {folder}")
        if args.apply:
            folder.mkdir(parents=True, exist_ok=True)
        created += 1
    for path, text in files:
        if path.exists():
            print(f"  exists, skipped   {path}")
            skipped += 1
            continue
        print(f"  new file          {path}")
        if args.apply:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        created += 1

    verb = "Created" if args.apply else "Would create"
    print(f"\n=== {verb}: {created}   Already there (untouched): {skipped} ===")
    if not args.apply:
        print("Nothing was changed. Add --apply to create.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
