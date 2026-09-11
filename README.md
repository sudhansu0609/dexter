# Dexter — Personal AI Super Assistant

A local-first desktop assistant for Windows. Runs your apps and files, plans your
day, actually reminds you, controls audio/media/system, and runs the Buzzcaf
Studio for you — with the language model kept local (a llama.cpp server,
LM Studio, or Ollama). Type, or talk: say **"Dexter"** (or press the mic)
and Dexter listens, answers out loud, and keeps listening for a follow-up.

## Voice (v10)

Everything runs on this machine: silero-vad to hear where a sentence ends,
faster-whisper (`small`, CPU int8) to write it down, Kokoro to speak, and
openWakeWord as an optional phrase model. Nothing spoken leaves the PC.

| You do | Dexter does |
|---|---|
| Say **"Dexter, open Chrome"** | Shows *Listening…*, chimes, then acts. There is no "Dexter" wake-word model anywhere, so the recognizer does it: the first two seconds of every sentence the room says are checked, and only ones that start with "Dexter" go further ("the dexterity test" and "ask Dexter later" do not). |
| Say **"Dexter"** and pause | Chimes and opens a six-second window for the rest. No second name needed. |
| Prefer an instant wake phrase | **Settings → Voice → How to wake Dexter → model** switches to openWakeWord's stock `hey_jarvis` phrase (fires the moment the phrase ends, no recognizer work until then). `both` answers to either. |
| Click **Open** under the composer | Open mic: everything you say is for Dexter, no name, until you mute. Pair it with *Learn my voice* so "everything" means you. |
| **Settings → Voice → Learn my voice** | Ten seconds of you talking. From then on Dexter answers only your voice (a visitor gets "That wasn't your voice, so I left it") and never its own echo: it keeps a voice print of its current Kokoro voice and drops anything that matches it. Turn off with *Only answer to my voice*. A sentence a little under the bar still counts when it starts with "Dexter" or follows one of yours, and every confident match teaches Dexter your voice on this mic; if it keeps saying "that wasn't your voice", lower *Voice match strictness*. |
| Nothing is heard | The line under the composer shows a live input meter, and after six seconds of digital silence says which mic is dead. **Settings → Voice → Microphone** lists the real ones; Windows defaults to virtual endpoints (Oculus, Stereo Mix, iPhone) surprisingly often, and Dexter skips those on its own. |
| Dexter sounds flat | **Settings → Voice → Dexter's voice**, with a *Try* button. Spoken replies also open with a mood the model picks (warm, upbeat, calm, apologetic, urgent, thinking) that sets the pace and timbre of the voice - the tag never reaches the chat. |
| Answer within ~9 s of a reply | Heard as a follow-up: **no wake word**. The window closes on its own. |
| Press the **mic** (or **Ctrl+Space**) | Talk now, no wake word. Pressing it while Dexter speaks cuts the reply off. |
| Say **"stop"**, **"cancel"** or **"never mind"** on their own | Dexter stops talking and the phrase never reaches the chat. **Esc** does the same. |
| Turn voice off (title bar) | The mic is released and replies are not spoken. Remembered across launches. |

Spoken questions get spoken-shaped answers (short, no lists, no paths) whether
the reply is voiced or not, and streamed replies are voiced sentence by
sentence, so speech starts before the model has finished. Reminders and
check-ins are spoken too. Only one shell window (the pop-up or the full one,
whichever was shown last) holds the mic, so a command is never heard twice.

Knobs live in **Settings → Voice** (how to wake, the name, follow-up window,
echo guard, wake sensitivity, barge-in) and in `.env` (`DEXTER_WHISPER_MODEL`,
`DEXTER_WAKE_MODE`, `DEXTER_VOICE=0` to turn the whole layer off). Barge-in - talking over Dexter - is **off** by
default because through speakers the mic hears Dexter's own voice; turn it on
with headphones. To run Whisper on the GPU, put the CUDA 12 cuBLAS/cuDNN DLLs
on `PATH` (`pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`); `auto` falls
back to CPU when they are missing and `/api/dexter/status` → `voice.device`
says which it picked.

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
python backend/main.py               # API on 127.0.0.1:8098, or the next free port
                                    # (it prints "READY port=N" and publishes it)
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
| `buzzcaf_client.py` | HTTP client for the Buzzcaf Studio (it prefers 8099; found through the port ledger, `studio.runtime.json` or a scan, and only trusted when `/health` says `app:"buzzcaf"`), launches its desktop app |
| `router.py` | The three tiers, slot extraction, agent loop, confirmations |
| `tool_registry.py` | `@tool` decorator → OpenAI schema + intent examples + validation |
| `tools_core.py` | Apps, files, agenda, notes, media |
| `tools_studio.py` | The Buzzcaf Studio: status, launch, projects, steps, approvals, topics, YouTube stats |
| `tools_knowledge.py` | `search_knowledge`, `reindex_knowledge`, profile tools |
| `browser.py` / `tools_browser.py` | Dexter's Chrome window (Playwright): open, read, click, type, tabs, screenshot |
| `buzzcaf_ports.py` | The shared port ledger (`%LOCALAPPDATA%\Buzzcaf\ports.json`): pick a free port, publish it, find another app's. Copied byte-identical into every Python app in the ecosystem — see `ECOSYSTEM.md` "Ports" |
| `code_agent.py` / `tools_code.py` | buzzcode jobs in your repos; start/stop the llama-server on 8089 (books its 12.5 GB from Sentinel first) |
| `sentinel_client.py` | The guardian's named pipe: `Hello`, register, reserve/query VRAM **and** RAM, RAM/disk/app status, cleanup, the event stream. Absent = old behaviour, never a hang (v9) |
| `model_steward.py` | **The only door to a model.** Reserves before every load, releases once it is resident, and runs the shed ladder when Sentinel says memory is tight. Nothing else may call `warm_model`/`release_model` — there is a test that greps for it (v9) |
| `apps_registry.py` / `procs.py` / `tools_apps.py` | All 17 projects under `Buzzcaf_Media`: launch, probe, stop, what each costs and what it cannot share the card with (v9) |
| `tools_assistant.py` | Reminders, memory, time |
| `agenda.py` | Today, composed from the workbook + reminders + calendar |
| `mission.py` / `companion.py` / `tools_mission.py` | The mission, Dexter's stance and voice, check-ins, coaching tails (v7) |
| `initiative.py` | Proposes the next move; the You lead / Dexter leads switch (v7) |
| `activity.py` / `tools_activity.py` | What you are working on: foreground window sessions + recently changed files (v7) |
| `claude_sessions.py` / `tools_claude.py` | What you and Claude Code did, from its transcripts (read-only) (v7) |
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

## Working with Dexter (v7)

Dexter is a coworker on one mission, not a command line with notifications.

- **Tell it the mission once.** "our mission is to get Beyond3Baje to 100k
  subscribers", "this week's focus is the Bhangarh script", "today's win is
  recording the voiceover". The Mission card above the chat shows them; empty
  slots start the sentence for you.
- **It speaks first, in the chat.** A morning message (at 06:00, or when you
  open Dexter during the day): yesterday in a line, what we committed to, what
  slipped, and one question: what makes today a win? A midday check on that
  win, an evening close-out, a Sunday review. Answer with a tap or a word:
  "done", "on it", "stuck", "later". "not now" pauses check-ins for two hours;
  Settings → Check-ins turns them off.
- **It coaches, briefly.** Finishing something reports the day's progress;
  adding a task while an older one is overdue names it; something moved three
  times gets a "break it down or drop it". One sentence, never a lecture.
- **It proposes the next move.** A commitment due in twenty minutes, no win
  picked by mid-morning, something overdue for three days, a video of yours
  gathering pace, saved topics with nothing in production: Dexter brings it
  up, once, with one-tap answers that act ("Move it to tomorrow", "Analyse
  this video", "Start a video about ..."). Four such proposals a day at most.
- **You can take the lead.** The pill on the Mission card flips between
  **DEXTER LEADS** and **YOU LEAD**. In YOU LEAD, Dexter never speaks first:
  no check-ins, nudges, coaching tails or proposals; it answers when asked.
  "I'll take it from here" and "take the lead" do the same in the chat.
- **It knows what you're doing.** Dexter samples the window in focus every
  thirty seconds and watches which files under your folders just changed, so
  "what am I working on" answers "You're in Premiere Pro on beyond3baje, 42
  min so far. Recently saved: ep4_cut.prproj (beyond3baje) 3 min ago." The
  Mission card shows it live, the morning message knows how yesterday went,
  and a long stretch in a work app on something that is not today's win gets
  one question. Settings → Activity awareness turns it off; the log stays on
  this PC for seven days.
- **It knows what you did in Claude Code.** Dexter reads Claude Code's own
  transcripts under `~/.claude/projects` (read-only, it never runs Claude):
  "what was I doing in claude", "what did claude change in dexter", "show me
  the last claude session" answer with the project, what you asked, the files
  Claude wrote, the commands it ran and its last words. A session in progress
  shows on the Right now line. Settings → Read Claude Code's logs turns it off.
- **It keeps the thread.** What you say today is in the model's context; a
  finished code job or a Studio step waiting for approval arrives as a
  message with "Approve" / "Check it first", not a banner.

## Sentinel and the apps (v9)

This PC runs a 16 GB card and, on a normal evening, some combination of a
render, a 27B model server, a ComfyUI graph and Dexter's own model. At most one
of the heavy ones fits beside Dexter. **Sentinel** — a Windows service on
`\\.\pipe\Sentinel` — is the guardian that decides who gets VRAM and RAM, and
Dexter is one of the claimants that can *choose* not to load.

**Dexter asks before it loads.** Every model load in Dexter goes through one
module, `backend/model_steward.py`, which books the VRAM *and* the RAM from
Sentinel first and hands the booking back the moment the model is resident (or
the load fails). Nothing else in the codebase is allowed to touch model
residency, and there is a test that greps for violations.

When Sentinel says no, Dexter does not go quiet and does not load anyway:

> Sentinel won't give me 6.0 GB of video memory right now — 1.2 GB is free and
> VrDeep has 10.0 GB. I can park the background work ("pause batch jobs") or we
> wait for it to finish — your call. I'll keep going on the small model meanwhile.

...and it means it: the reply is generated on whatever model is already in
memory. When Sentinel tells Dexter memory is tight it sheds cooperatively — the
big model first, then the small one, then any model it did not load itself, then
LM Studio, then a buzzcode engine it started. Freezing does not free VRAM on
Windows, which is why giving it back voluntarily is the first move.

**Dexter runs your projects.** Say it plainly; none of these touch the model:

| You say | What happens |
|---|---|
| "what's running" | every project's live state, plus the GPU and RAM lines |
| "is vrdeep running", "is buzzfin running" | one project, with its port and uptime |
| "open buzzedit", "start vrdeep", "start mastgaane" | Sentinel is asked first, conflicts are named, then it launches and waits for the app's own health check |
| "stop caliber", "stop the coding engine" | each project's own shutdown route — and it **asks first** if a render or a job is mid-flight |
| "restart the studio", "show me buzzedit's log" | |
| "what's in the vrdeep queue", "mastgaane report", "coffee brief" | reads what each app already publishes |
| "clean up space" | asks Sentinel to run SmartCleaner in your session (Dexter never deletes anything itself) |
| "pause batch jobs" / "resume batch jobs" | Sentinel freezes or thaws the background work |
| "gpu status" | what is on the card right now |

All 17 projects under `B:\youtubeProjects\Buzzcaf_Media` are in
`data/apps.default.json` — launch script, probes, how to stop it, what it costs,
and what it cannot share the card with. Your own changes go in `data/apps.json`,
which Dexter reads and never writes.

**In the window.** The expanded dashboard has a **Machine** panel (VRAM, RAM and
disk gauges, who is holding the card, what is frozen, buttons for pause/resume
batch and a safe clean) and a **Projects** panel (start and stop, with the
confirmation for a busy batch job). If Sentinel is not running, or is an older
build without RAM and disk commands, the panel says so — it does not show you an
empty gauge and let you guess.

**Nothing here is a hang risk.** Every call to Sentinel has a sub-second timeout
and an "absent" answer. With the guardian stopped, Dexter behaves exactly as it
did before it existed.

Settings → **Machine** tunes the budgets live: `DEXTER_VRAM_BUDGET_MIB` (5120),
`DEXTER_RAM_BUDGET_MIB` (3072), the buzzcode engine's booking (12500 / 6500),
whether Dexter may unload LM Studio, and whether "clean up space" is allowed to
forward a request at all. The cross-project contract is
[`ECOSYSTEM.md`](ECOSYSTEM.md).

---

## Browser, coder, video intelligence (v6)

**Browser.** "go to github.com", "search youtube for bhangarh fort", "click
sign in", "type chandragupta into the search box and press enter", "read this
page", "what tabs are open", "screenshot this page". Dexter drives its own
Chrome window (installed Chrome, profile in `data/browser_profile`, so logins
persist there). A click that finds nothing tells you what *is* clickable. To
attach to your everyday Chrome instead, start it with
`--remote-debugging-port=9222` and set `DEXTER_BROWSER_CDP=http://127.0.0.1:9222`.

**Coder.** "buzzcode: fix the failing test in dexter", "ask the coder to add
type hints to knowledge_ingest.py in dexter". Dexter opens buzzcode in its own
terminal window (v8) in that repository and comes back at once; you watch the
tool calls and the model's text live, and the result (files changed, its
summary, `git diff --stat`) arrives as a toast and a banner when it finishes.
buzzcode stops its model server at the end so the GPU is free again, and Dexter
unloads its own big model before the job starts. `DEXTER_CODE_WINDOW=0` goes
back to a hidden background process. "code status" while it runs. Files-only by
default; add "and let it run the tests" to allow
commands. "start the local model" brings up the 27B llama-server on 8089,
which Dexter then uses for its own replies too.

**Video intelligence.** "analyse this video https://youtu.be/... for
beyond3baje" asks the Studio why it performs and how to model your video on it;
the full breakdown is in the Studio's Analyze tab.

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
once on next launch. Closing a window only hides it: Dexter stays in the
system tray and reminders keep firing. Set `DEXTER_TRAY=0` to make close quit.

## The pop-up and the full window (v8)

Dexter lives as a small pop-up, bottom-right and on top: Dexter's latest
message and one line to answer it. It shows up when Dexter has something to
say (a check-in, a reminder, a finished code job) and tucks itself away 30 s
later unless you are typing; anything you start yourself stays until you press
`Esc` or close it. `Ctrl+Alt+D` or a click on the tray icon brings it up.

The full app - the conversation, the mission card, the planner, the Studio -
is a second window: tray menu → **Open Dexter**, or the expand button on the
pop-up. Tray menu: Ask Dexter / Open Dexter / Reindex knowledge / Quit. Set
`DEXTER_START_MODE=full` in `.env` to open the full window at launch instead.

The look is light and minimal on purpose: one accent, system font, no glow.
Every colour is a token in `src/index.css`.

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
pytest backend -q                    # 395 backend tests, no server needed
npx vitest run                       # frontend smoke tests
python test_dexter_suite.py          # live end-to-end checks (server must be up)
python test_day_in_life.py           # a day as the owner: 34 timed turns, table in reports/
```

**Instant replies (v8).** A small model (`DEXTER_FAST_MODEL`, qwen2.5:3b on
Ollama) answers conversation and general knowledge in well under a second and is
kept in memory for hours; anything that reads as an instruction, anything about
your own things, and any reply where the small model pretends or declines goes
to the tool-enabled model (`DEXTER_SMART_MODEL`), which is unloaded again after
`DEXTER_SMART_KEEP_ALIVE`. `/api/dexter/status` → `tiers` shows which model is
where and whether it sits on the GPU. Two things that silently made this slow
on Windows: use `127.0.0.1`, never `localhost`, in every local URL (2 s per
call otherwise), and check `ollama ps` says `GPU` - if it says `CPU`, Ollama's
`lib/ollama/cuda_*` folders are incomplete.

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
# dexter
