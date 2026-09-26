# KastoBrain

A private "second brain" for your PC, built on Perplexity's Brain method, with your rules added.
You give a brain your documents and press **Build Brain**. It reads them, researches online, and
builds a linked wiki of **Concepts, Entities and Workstreams** by itself, with a source on every fact.
**Nothing goes into the wiki until you approve it.**

Only this code lives on GitHub. Your documents, brains, wiki and history stay on your PC
(`.gitignore` blocks them from ever being uploaded).

---

## What's in it

| Part | What it does |
|---|---|
| **Build Brain** | Dream run: orient, summarise, attach facts to subjects, update the wiki. Result goes to **Pending**. |
| **Pending approval** | Approve puts it into the wiki (previous wiki kept as `wiki.previous`). Reject leaves the wiki untouched. |
| **Auto-update** | Hourly or daily, in Settings → Memory. Runs while the app is open. Still goes to Pending. |
| **Ask** (Overview tab) | Ask a brain a question. Answers come from the wiki with sources. Read-only. Every question is kept and Build Brain learns from it. |
| **Files** tab | Your PC folders, read in place. Never moves or changes anything. |
| **Brain** tab | Page cards, the gold/silver/purple globe, Pending approval. |
| **PC Brain** | Overview of every brain, search across all brains, and the **PC sorter (dry run only)**. |
| **MCP connector** | Use your brains from **Codex, Claude Code and Gemini CLI**. |

**AI used:** **ChatGPT / Codex by default.** Each brain can be switched to Claude Code or Gemini CLI
in Settings → Context. It runs on the plans you already have. No API keys, no extra charges.

**Private folders:** list them under **Never send** in a brain's settings. They are left out of the document list the AI is given, and it is told never to open them.
**Honest limit:** that is an instruction, not a lock. The AI can technically still see folders inside a brain's folders. For real protection, keep private files **outside** the folders you give a brain.

---

## Install on your PC (Windows 11, about 5 minutes)

You already have: Python 3.11, Git, Codex. Drive H: has space.

### 1. Download KastoBrain to H:

Open **PowerShell** and paste (this only downloads the code into a new folder `H:\KastoBrain`):

```powershell
git clone -b claude/hj-sbzrri https://github.com/KastoVerse/KastoBrain.git H:\KastoBrain
```

It will ask you to sign in to GitHub the first time (the repo is private).
Once this work is merged, you can drop `-b claude/hj-sbzrri`.

### 2. First-time setup

Double-click **`H:\KastoBrain\setup.bat`**.
It first shows a **dry run** list of the folders it will create (inside `H:\KastoBrain` only),
then waits. Press a key to create them, or close the window to cancel.

### 3. Start the app

Double-click **`H:\KastoBrain\start-kastobrain.bat`**.
Your browser opens **http://127.0.0.1:8765**. Keep the black window open while you use it; close it to stop.

### 4. Make your first brain

1. Click **+ New brain** and give it a name.
2. In **Settings → Context**, add a **folder** of documents (start small, about 5 files), any **Never send** folders, and your instructions.
3. Press **Build Brain**. When it finishes, open **Brain → Pending approval** and Approve or Reject.

### 5. Make sure the AI command works

KastoBrain runs the AI in the background with its command-line tool:

| AI | Needed | Check in PowerShell |
|---|---|---|
| ChatGPT / Codex (default) | Already installed | `codex --version` |
| Claude Code | The `claude` command (same account as your Claude app) | `claude --version` |
| Gemini CLI | Optional | `gemini --version` |

To add the `claude` command (only if you want to use Claude for a brain), in PowerShell:

```powershell
irm https://claude.ai/install.ps1 | iex
```

Open a new PowerShell window, run `claude` once and sign in with your normal Claude account.

---

## Connect your AI apps (MCP connector)

This lets Codex, Claude Code or Gemini CLI read your brains and start a Build Brain run.
It **cannot approve, delete or move** anything. Approving is only done by you, in the app.

**Codex:**
```powershell
codex mcp add kastobrain -- python H:\KastoBrain\app\mcp_server.py H:\KastoBrain
```
(or add this to `%USERPROFILE%\.codex\config.toml`):
```toml
[mcp_servers.kastobrain]
command = "python"
args = ["H:\\KastoBrain\\app\\mcp_server.py", "H:\\KastoBrain"]
```

**Claude Code:**
```powershell
claude mcp add kastobrain --scope user -- python H:\KastoBrain\app\mcp_server.py H:\KastoBrain
```

**Gemini CLI:** add to `%USERPROFILE%\.gemini\settings.json`:
```json
{ "mcpServers": { "kastobrain": { "command": "python",
  "args": ["H:\\KastoBrain\\app\\mcp_server.py", "H:\\KastoBrain"] } } }
```

Then ask, for example: *"Using kastobrain, list my brains and summarise the Insurance brain."*

Tools it offers: `list_brains`, `brain_index`, `read_page`, `search`, `list_pending`, `build_brain`.

---

## PC sorter (dry run only)

In **PC Brain**, list folders to scan (and folders to skip), then press **Dry run**.
It suggests **exact duplicates** (keeps the oldest copy), **empty files** and **temporary/junk files**
for the `FOR REVIEW – TO DELETE` folder. **It never moves, renames or deletes anything.**
The full list is saved as a CSV in `H:\KastoBrain\PC-Brain\sorter-plans\`.

---

## Folder layout on H:

```
H:\KastoBrain\
  app\  templates\  README.md  setup.bat  start-kastobrain.bat   <- code (on GitHub)
  PC-Brain\                       <- PC Brain data, sorter settings and dry-run plans
  Projects\<brain>\               <- settings.json, wiki\, sessions\, pending\
  FOR REVIEW – TO DELETE\         <- only ever filled by you
  Logs\
```

---

## Tested / not yet tested

**Tested (in the cloud, on made-up test documents):** folder setup, the app screens, Build Brain
with Claude (made its own pages, researched online, skipped the private folder, waited for approval),
approve/reject, Ask with Claude (read-only, nothing changed), New Brain, cross-brain search,
Files tab (refuses folders outside the brain), PC sorter dry run (nothing moved),
MCP connector used from Claude Code.

**Not yet tested:** Codex and Gemini CLI (not installed on the test machine), the `.bat` files
(Windows only), real PDFs / Word files / scans, and Codex's web research inside Build Brain.
Run the first brain on a small folder and check the Pending result before trusting it with more.
