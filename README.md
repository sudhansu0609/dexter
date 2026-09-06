# Dexter — Personal AI Super Assistant

A local-first desktop assistant for Windows. Runs your apps and files, plans your
day, actually reminds you, controls audio/media/system, and runs the Buzzcaf
Studio for you — with the language model kept local (a llama.cpp server,
LM Studio, or Ollama). Text chat is the way in; voice was removed in v5.

---

## Quick start

```bash
pip install -r requirements.txt      # see "Optional extras" below
npm install && npm run build
Dexter.vbs                           # native WebView2 window, no console
start_dexter.bat                     # same, with a console for the log
```

`start_dexter.bat` runs a **preflight check** first and refuses to launch on a
real failure. Run it on its own any time you want to know whether Dexter can
actually work:

```bash
python backend/preflight.py          # deps, model, GPU, embeddings cache, reminders
npm run check                        # preflight + pytest + tsc + vitest
```

It reports the degradations that used to be silent — a missing APScheduler
(reminders stored but never fired), a model that has fallen back to the CPU
because something else holds the GPU, an embeddings model that is not cached.

Or run the pieces separately:

```bash
python backend/main.py               # API on http://127.0.0.1:8098
npm run dev                          # Vite dev server on :3006
npm run desktop                      # Electron shell
```

Copy `.env.example` to `.env` to configure. Everything works with defaults;
no API key is required.

### The model matters

Dexter is local-first. When codeBuzz's `llama-server` is up on port 8089 Dexter
uses it first (it already holds the GPU; see `LLAMACPP_URL`). Otherwise load a
model in LM Studio with solid **function calling** —
**Qwen2.5-7B-Instruct** or **Qwen3-8B** are the best quality-per-VRAM locally. A
3B model will misfire on multi-step requests. The loaded model is auto-detected
from `/models`, so there is nothing to configure after you swap it.

---

## The workbook — goals, commitments, and an honest week

Dexter's day planner used to be a flat list of today's `{time, title, done}`:
completed items were erased at midnight, and unfinished ones rolled forward
forever, unchanged and unremarked. There was nothing to plan against, nothing to
be held to, and no record of what actually happened.

The workbook (`backend/workbook.py`) is the model of your work that everything
else now reads from:

```
Goal         the outcome you want            "ship the channel relaunch"
 └─ Project  a body of work under a goal     "record 8 videos"
     └─ Commitment  something you said       "script ep.3 by Friday"
         └─ Entry   what actually happened   "worked 90m, blocked on b-roll"
```

```
"my goal this quarter is to ship the channel relaunch"
"I'm committing to scripting episode three by Friday"
"log that I worked ninety minutes on the edit"
"what did I say I'd do today"        "what's due this week"
"I finished scripting episode three" "push the edit to tomorrow"
"what am I avoiding"                 "close out my day"   "how was my week"
```

Three things it does that a to-do list cannot:

- **Rolling is counted.** Move something four times and Dexter says so, and asks
  whether it is still real. A task you have been avoiding for a week must not
  look like one written down this morning.
- **Completed work survives the day.** The weekly scorecard reports what you
  kept, what slipped, where the hours went, and — most usefully — which goals
  got no attention at all.
- **The day ends deliberately.** An evening check-in at 21:00 asks you to close
  out; 23:50 records the day regardless so nothing is lost silently; Sunday
  evening runs the review.

All of it answers at **Tier 1**, in tens of milliseconds, without touching the
language model.

## Architecture

The language model is the **last** resort, not the first. Most commands never
reach it, which is what makes Dexter both fast and predictable on local hardware.

```
text
     │
     ▼
┌─ Tier 0: fast path ──────────────────┐  ~5ms, no LLM
│  regex + greetings                   │  "open chrome", "my agenda", "remind me…"
└──────────── miss ────────────────────┘
     ▼
┌─ Tier 1: semantic intent ────────────┐  ~15ms, no LLM
│  embedding vs each tool's examples   │  "put on some music" → play_on_youtube
│  + deterministic slot extraction     │  "what am I looking at" → get_active_window
└──────────── miss / low confidence ───┘
     ▼
┌─ Tier 2: constrained agent loop ─────┐  local LLM with tool calling
│  ONE tool per iteration, max 5       │  "find the invoice, rename it,
│  top-8 tools only, args validated    │   and remind me at 5"
└──────────────────────────────────────┘
```

Measured on this machine: Tier 0/1 answer in **3–30ms**; Tier 2 depends on
which model server is up (see `ECOSYSTEM.md`) and streams its reply.

### Backend modules

| File | Role |
|---|---|
| `main.py` | FastAPI transport only — endpoints, lifecycle, SSE streaming |
| `desktop_app.py` | The window: preflight, in-process uvicorn, WebView2, remembered size, tray icon, `Ctrl+Alt+D` |
| `buzzcaf_client.py` | HTTP client for the Buzzcaf Studio (port 8000), launches its desktop app |
| `router.py` | The three tiers, slot extraction, agent loop, confirmations |
| `tool_registry.py` | `@tool` decorator → OpenAI schema + intent examples + validation |
| `tools_core.py` | Apps, files, agenda, notes, media |
| `tools_studio.py` | The Buzzcaf Studio: status, launch, projects, steps, approvals, topics, YouTube stats |
| `tools_knowledge.py` | `search_knowledge`, `reindex_knowledge`, profile tools |
| `tools_assistant.py` | Reminders, memory, time |
| `agenda.py` | Today, composed from the workbook + reminders + calendar |
| `knowledge_ingest.py` | Indexes your folders (md/txt/pdf/docx/csv, Studio JSON) into `memory.documents` with citations |
| `user_profile.py` | `data/profile.json`: who you are, your channels; injected into every prompt |
| `windows_tools.py` | Volume, brightness, clipboard, screenshots, windows, lock |
| `media_tools.py` | System-wide playback via Windows SMTC |
| `google_tools.py` | Gmail + Google Calendar |
| `scheduler.py` | APScheduler; fires reminders on 3 channels; maintenance jobs |
| `events.py` | SSE bus — the backend's only way to reach the user unprompted |
| `memory.py` | SQLite conversation history + durable facts |
| `hybrid_llm.py` | Provider routing, cached health, tool calling, streaming |
| `timeparse.py` | Natural-language times ("in 20 minutes", "every weekday at 9") |
| `app_index.py` | One-time scan of installed apps → instant launches |
| `jsonstore.py` | Atomic JSON reads/writes (retried through Windows sharing violations) |
| `workbook.py` | Goals, projects, commitments, entries — the spine of the planner/coach |
| `runtime_health.py` | Detects and explains a degraded runtime (model on CPU, VRAM taken) |
| `settings_store.py` | Runtime-tunable settings that apply without a restart |
| `preflight.py` | Pre-launch check: deps, model, GPU, embeddings cache, reminders |
| `logging_setup.py` | Rotating `data/dexter.log`; stdout/stderr tee'd into it |
| `env_boot.py` | Loads `.env` for every entry point, not just the server |

---

## Adding a tool

One declaration wires up everything — the LLM schema, the semantic matcher, and
argument validation:

```python
from tool_registry import ToolResult, tool

@tool(
    name="dim_lights",
    description="Turn the smart lights down.",
    params={"level": {"type": "integer", "description": "0-100", "required": False}},
    examples=["dim the lights", "lights down", "make it darker in here"],
    confirm=False,          # True → Dexter asks before running
    category="home",
)
def dim_lights(level: int = 30) -> ToolResult:
    return ToolResult.success(f"Lights at {level}%.", level=level)
```

Write `examples` the way you would actually *speak* — they are the training data
for Tier 1. Then add a case to `backend/test_router.py` so the phrasing is
protected against drift as more tools are added.

---

## Reminders

Two delivery channels fire together, so a reminder lands whether or not the
window is open:

1. **Windows toast** — persists in Action Center, works with Dexter closed
2. **In-app banner** — with Snooze (10 min) and Done

```
"remind me to check the render in 30 minutes"
"remind me to stand up every hour"
"what reminders do I have"
"cancel the reminder about the render"
```

Reminders survive restarts; any that came due while Dexter was down are surfaced
once on next launch. Closing the window only hides it: Dexter stays in the
system tray (Show / Reindex knowledge / Quit) and reminders keep firing.
`Ctrl+Alt+D` brings the window back. Set `DEXTER_TRAY=0` to make close quit.

---

## Today, notes, and what Dexter knows

**Agenda.** "What's on today" composes three things: workbook commitments due
or overdue (overdue first, with how often they have been moved), reminders
set for today, and calendar events when Google is connected. "Add a task"
creates a commitment; with a time it also sets a reminder. There is no
separate planner file any more.

**Notes.** "Note that ..." writes a Markdown file into your notes folder (the
`notes=` entry in `DEXTER_KNOWLEDGE_ROOTS`, or `data/notes` by default) and
indexes it immediately, so "what did I note about the render rig" works a
second later.

**Knowledge.** `DEXTER_KNOWLEDGE_ROOTS=tag=path;tag=path` names the folders
Dexter indexes (Markdown, text, CSV, PDF, Word, and the Studio's channel
guides, saved topics and project files). An incremental pass runs at startup
(capped at 60 s) and nightly at 02:30; "reindex knowledge" forces one.
Answers that use a passage name the file. Ask "search my notes for X" or
just ask the question.

**Profile.** Settings → About you (or "update my profile: ...") fills
`data/profile.json`; the block is in every prompt, so "what channels do I
run" is answered from it, not guessed.

---

## Google setup (optional)

1. Google Cloud project → enable the **Gmail** and **Calendar** APIs
2. Create an OAuth client ID, type **Desktop app**
3. Save the JSON as `data/google_credentials.json`
4. Say *"connect my google account"* — consent opens once

The token is stored at `data/google_token.json` and is gitignored.

---

## Testing

```bash
npm run check                        # everything: preflight, pytest, tsc, vitest
pytest backend -q                    # 307 backend tests, no server needed
npx vitest run                       # frontend smoke tests
python test_dexter_suite.py          # live end-to-end checks (server must be up)
```

`test_router.py` is the contract for intent handling. A new tool's example
phrasings can silently steal a phrase another tool used to own, so add a case
whenever you add a tool.

---

## Optional extras

Everything below degrades gracefully — Dexter reports what is missing instead of
crashing.

| Package | Without it |
|---|---|
| `sentence-transformers` | Tier 1 falls back to lexical matching (lower recall on unusual paraphrases) |
| `apscheduler` | Reminders are stored and listed but never fire |
| `winotify` | Toasts fall back to a slower PowerShell path |
| `pycaw`, `screen-brightness-control`, `pyperclip`, `mss`, `pygetwindow` | Those specific system tools report unavailable |
| `winsdk` | Media keys still work; "what's playing" does not |
| `google-api-python-client`, `google-auth-oauthlib` | Gmail/Calendar unavailable |

Enable full semantic matching (~2.5 GB, pulls in PyTorch):

```bash
pip install sentence-transformers
```

---

## Settings

The gear icon in the title bar changes model and behaviour settings
**while Dexter is running** — preferred backend, the fast-model tier, proactive
suggestions, and quiet hours. Anything that genuinely needs a restart says so instead of
looking applied. Values persist in `data/settings.json` and override `.env`.

## When something is wrong

Dexter now tells you rather than just feeling slow:

- A `[Startup] DEGRADED:` line, a `runtime` block on `/api/dexter/status`, and a
  banner in the window whenever the model has fallen back to the CPU — naming
  the process holding the GPU and how much VRAM is short.
- `data/dexter.log` (rotating) captures startup, routing, tool calls, and
  uncaught exceptions. Dexter runs in a WebView2 window with no console, so
  before this every diagnostic it printed went nowhere.

## Security notes

- The server binds **127.0.0.1** by default. The filesystem endpoints are
  unauthenticated, so do not bind `0.0.0.0` without adding auth first.
- `.env` is gitignored (it was previously tracked — commit `.env.example` instead).
- Tools marked `confirm=True` ask before running: `send_email`, `reply_to_email`,
  `write_file`, `rename_file`, `move_file`, `copy_file`, `delete_file`,
  `cancel_event`, `reschedule_event`, `close_window`, `sleep_pc`, `forget_fact`,
  `connect_google`, `run_skill`.
- `delete_file` sends to the Recycle Bin and **refuses outright** if `send2trash`
  is missing. An assistant acting on an occasionally-misheard command has no
  business deleting anything permanently.
- Every file path — source *and* destination — is canonicalised and checked
  against the `pathguard` allowlist. An unchecked destination on a move would be
  an escape hatch out of it.

Still open, by design of the current scope: API-key auth on the endpoints, a
filesystem path allowlist, and removing `shell=True` from the launcher paths —
all tracked in [`PLAN.md`](PLAN.md) (Roadmap v4) with the running record in
[`PROGRESS.md`](PROGRESS.md). `IMPROVEMENT_PLAN.md` is Roadmap v3, complete and
kept for history.
