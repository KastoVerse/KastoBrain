"""KastoBrain launcher: what the desktop / Start menu icon runs.

- If KastoBrain is already running, it just opens the app window.
- Otherwise it starts the engine hidden (no black window), waits until it
  is ready, then opens the app in its own window (Edge app mode).
- The engine stops by itself a few minutes after the last app window closes.
- Engine messages go to Logs\\engine.log (current) and engine.previous.log.
"""

import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

PORT = 8765
URL = f"http://127.0.0.1:{PORT}"
APP = Path(__file__).resolve().parent
ROOT = APP.parent
IDLE_SECONDS = 180


def running():
    try:
        with urllib.request.urlopen(URL + "/api/projects", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def start_engine():
    logs = ROOT / "Logs"
    logs.mkdir(exist_ok=True)
    log = logs / "engine.log"
    if log.exists():                                   # keep exactly two generations
        prev = logs / "engine.previous.log"
        if prev.exists():
            prev.unlink()
        log.rename(prev)
    python = Path(sys.executable)
    if python.name.lower() == "pythonw.exe":           # engine needs python.exe; window stays hidden
        python = python.with_name("python.exe")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    with open(log, "w", encoding="utf-8") as out:
        subprocess.Popen([str(python), str(APP / "server.py"), str(ROOT), "--port", str(PORT),
                          "--auto-exit", str(IDLE_SECONDS)],
                         cwd=str(APP), stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                         creationflags=flags, close_fds=True)


def open_window():
    candidates = [shutil.which("msedge"),
                  os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                  os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
                  shutil.which("chrome"),
                  os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe")]
    for exe in candidates:
        if exe and Path(exe).is_file():
            subprocess.Popen([exe, f"--app={URL}", "--window-size=1400,900"],
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            return
    webbrowser.open(URL)                               # fallback: normal browser tab


def main():
    if not (ROOT / "Projects").is_dir():
        webbrowser.open("file:///" + str(ROOT / "README.md"))
        return
    if not running():
        start_engine()
        for _ in range(60):                            # wait up to 30 seconds
            if running():
                break
            time.sleep(0.5)
    open_window()


if __name__ == "__main__":
    main()
