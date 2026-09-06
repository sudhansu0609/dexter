# Dexter — Progress Log

Running record of what has actually landed, newest first. Plan: [`PLAN.md`](PLAN.md).
Every row is either **done** (with the evidence that proves it) or **open**.
Nothing is marked done on intent.

## v6 status at a glance (2026-09-06)

| Item | Status | Evidence |
|---|---|---|
| B1 browser control (**J1**) | ✅ done | Live 2026-09-06 through the tools: `browser_open("example.com")` → window + "Opened Example Domain" in 0.7 s; `browser_read_page` returns the text; `browser_click("More information")` fails honestly with "Visible options: Learn more"; `browser_open("youtube bhangarh fort", new_tab)` → YouTube results; `browser_tabs` lists 2; `browser_type("chandragupta maurya", into="search", submit)` searched; `browser_screenshot` saved a PNG; `test_browser_tools.py` 4 tests; "go to / open <site>" now route to `browser_open` (`test_router.py` updated) |
| C1 buzzcode + engine (**J2**) | ⚠️ built, live run pending | `code_agent.py` + `tools_code.py` (7 tools: code_task/status/cancel, list_code_projects, start/stop/local_model_status); event folding, repo resolution (`DEXTER_CODE_ROOTS`), yolo-only-when-asked, toast + banner on finish; `test_code_agent.py` 5 tests incl. a fake `buzzcode --json` run. A real headless run was not performed in this session (blocked by the session's permission policy); do one with `buzzcode: ...` from the Dexter window |
| S1 analyze tool (**J3**) | ✅ done | `studio_analyze_video` → Studio `POST /api/video-intel/analyze` (5-minute timeout); fast rule "analyse this video <url> for <channel>"; Studio-side evidence in `BuzzcafAI/PROGRESS.md` |

## v5 status at a glance (2026-09-06)

| Item | Status | Evidence |
|---|---|---|
| 0.1 commit + `pre-v5` tag | ✅ done | 6 commits `0a66577..83f7065`, `git tag -l` → `pre-v5` |
| 0.2 session token ACL | ✅ done | `icacls data\.session_token` → `WARHEAD-MOTHERS\singh:(F)` only (2026-09-06) |
| 0.4 ECOSYSTEM.md identical | ✅ done | `diff ECOSYSTEM.md ../BuzzcafAI/ECOSYSTEM.md` empty |
| 1.1 Studio client fixed | ✅ done | `buzzcaf_client.py` → port 8000, `BUZZCAF_DIR` only, `pythonw desktop_app.py` launch, `[]` brands on error, `/health` must say `app:"buzzcaf"`; `buzzcaf_execute_step` fails on error and reports `paused_for_approval`; `grep MIDNIGHTBUZZ\|8095\|3005` empty |
| 1.2 curation removed | ✅ done | `SpilledCoffeeAIController`, `curate_video_ideas`, `/spilledcoffee/*`, overnight UI, legacy skill, demo seed tasks deleted; tools 77 → 75; `pytest` 280 passed |
| 1.3 offline startup (**I4**) | ✅ done | `HF_HUB_OFFLINE` set when the cache exists; log: `[Embeddings] all-MiniLM-L6-v2 loaded in 0.3s (... offline=True)`; preflight has an embeddings-cache row |
| 1.4 voice removed | ✅ done | 6 backend modules + WS/TTS routes + mic patch + 4 settings + deps + `useVoice`/`VoiceVisualizer` removed; `import main` OK; `pytest` 280 passed; `tsc`, `vitest` (4), `vite build` green |
| 1.5 llamacpp provider (**I3**) | ⚠️ built, timing pending | `_provider_chain('auto')` → `['ollama','lmstudio']` with 8089 down (llamacpp first when up); fast tier skipped on llamacpp; **I3** timing not yet measured with the server up |
| 1.6 hidden launcher (**I1**) | ✅ done | `pythonw backend/desktop_app.py`: no new `conhost` (21 → 21), `/api/dexter/health` OK after **1.4 s**, log `[Desktop] Window opening after 1.2s` (2026-09-06). Slow preflight probes and the intent index now run after the window opens |
| 3.2 studio tools (**I5**) | ✅ done (LLM-driven run pending) | `tools_studio.py` (12 tools, 82 total); client gains `state/chat/discover/saved/save/agents/approve` + SSE listener → `studio` events, approval banners/toasts; panel shows approvals. Live 2026-09-06: `buzzcaf_status`, `studio_pending_approvals`, `studio_saved_topics`, `studio_agents` (115), `studio_discover_topics`, `buzzcaf_create_project` (project appeared in the Studio) all real; `project_created` event received. `test_tools_studio.py` 9 tests; router cases for the new phrasings |
| 4.1 knowledge ingestion (**I6/I7**) | ✅ built | `knowledge_ingest.py` (roots `tag=path;…`, mtime/size fingerprints, pdf/docx/json→prose, excludes for caches/venvs/code-repo docs), `memory.documents` + `ingest_sources`, `search_documents` with tag/file citations, nightly 02:30 job + 60 s startup pass, tools `search_knowledge`/`reindex_knowledge`; `test_knowledge_ingest.py` 6 tests. Live scan 2026-09-06: 338 files / 79 MB across youtube, docs, studio |
| 4.2 profile (**I8**) | ✅ built | `user_profile.py` → `data/profile.json`; block injected into both router prompts; tools `show_profile`/`update_profile`; `/api/dexter/profile`; Settings → "About you". Profile is empty until you fill it |
| 4.3 Google connected | ⬜ blocked on owner | needs `data/google_credentials.json` |
| 5.1 YouTube status tools (**I9**) | ✅ built | `youtube_channel_status`, `youtube_video_stats` over the Studio's `/api/buzzbrain/*`; client `buzzbrain_channels/latest/video`; tests in `test_tools_studio.py`. **I9** live check needs the extension reloaded in Chrome and one of your videos opened |
| 5.2 one agenda (**I10**) | ✅ done | `agenda.py` composes workbook commitments (overdue first) + today's reminders + calendar; `add_task`/`complete_task` are workbook operations; `DayPlannerTool`, `day_planner.json`, `sticky_notes.json` gone; `migrate_planner.py` ran (0 real tasks, 2 demo seeds dropped); notes are Markdown in the `notes` root (`data/notes` default) and indexed on write; scheduler arms heads-ups from commitments; `test_agenda.py` 3 tests, suite 305 passed; live `/api/dexter/planner` shows the 2 real overdue commitments and nothing invented |
| 5.3 tray + hotkey | ✅ done | `pystray` icon (Show / Reindex knowledge / Quit); `WM_CLOSE` to the window → window hidden, `/api/dexter/health` still 200; `Ctrl+Alt+D` → `IsWindowVisible` true again within 1 s (2026-09-06); `DEXTER_TRAY=0` restores close-to-quit |
| 5.4 docs | ✅ done | README (agenda, notes, knowledge, tray), ECOSYSTEM.md (data locations, tray), this table |

## v5 acceptance bar (what "intuitive" means, with evidence)

| # | Felt experience | Status | Evidence |
|---|---|---|---|
| I1 | One click opens Dexter | ✅ | `Dexter.vbs` → `[Desktop] Window opening after 1.1s`, no new `conhost` |
| I3 | Dexter is fast | ⚠️ | Tier 0/1 replies in 3–30 ms; LLM-turn timing with the 8089 server up not yet measured |
| I4 | Dexter starts offline | ✅ | `[Embeddings] ... offline=True`; no network call on the startup path |
| I5 | Dexter controls the Studio | ⚠️ | Tools verified against the live Studio (project created, `project_created` event received); an LLM-driven "open the studio and start a video about X" run is still to be done with a model server up |
| I6 | Dexter knows the studio | ✅ | `search_knowledge("Beyond3Baje voice")` cites `studio/prompts/channels/beyond3baje.md` |
| I7 | Dexter knows notes/docs | ✅ | Documents PDFs/CSVs indexed (335 files); `create_note` writes Markdown and indexes it immediately; nightly + `reindex knowledge` |
| I8 | Dexter knows me | ⚠️ | Profile block wired into both prompts; `data/profile.json` is still empty until you fill Settings → About you |
| I9 | Dexter knows YouTube status | ⚠️ | Tools + Studio store built and tested; needs BuzzBrain 1.2.0 reloaded in Chrome and one of your videos opened |
| I10 | One agenda, one notes place | ✅ | `/api/dexter/planner` composes workbook + reminders (+ calendar); `day_planner.json` retired; notes are `.md` files |
| I13 | Reversible | ✅ | `git tag -l` → `pre-v5`; `git show pre-v5:backend/voice_engine.py` works |

I2, I11, I12 are tracked in `BuzzcafAI/PROGRESS.md`.

## v4 status at a glance

| Part | Item | Status |
|---|---|---|
| F1 | `.env` loaded from every entry point | ✅ done |
| F2 | `jsonstore` atomic-replace retry | ✅ done |
| F3 | Tiered fast-model test coverage | ✅ done |
| F4 | Degraded-runtime detection (model on CPU) | ✅ done |
| F5 | Conversation continuity | ✅ done |
| F6 | Real logging | ✅ done |
| F7 | Wake-word truth in docs and UI | ✅ done |
| F8 | One start command, preflight-checked | ✅ done |
| F9 | Settings without restarts | ✅ done |
| S1 | `workbook.py` — goals/projects/commitments/entries | ✅ done |
| S2 | Tools over the spine (12 tools) | ✅ done |
| S3 | The daily loop (morning / evening / weekly) | ✅ done |
| S4 | Coaching signal (slip detection, roll counts) | ✅ done |
| S5 | Workbook UI panel | ✅ done |
| C1 | Web search + weather | ✅ done |
| C2 | File mutation (rename/move/copy/delete) | ✅ done |
| C3 | Calendar/email completion | ⚠️ built, unverified against live Google |
| R1 | pathguard + jsonstore tests | ✅ done |
| R2 | Frontend smoke tests | ✅ done |
| R3 | One check command | ✅ done |
| R4 | Workbook tests | ✅ done |

**Verification:** `313 passed, 1 skipped` (backend),
`4 passed` (frontend), `tsc --noEmit` clean, `vite build` clean, server boots
and serves. Tools went from **55 → 77**; test files from 7 → 15.

## 2026-08-24 — first launch

`start_dexter.bat` died on `ModuleNotFoundError: No module named 'webview'`
*after* preflight reported "0 failures" — preflight checked everything except
the shell it actually launches. Fixed, and the audit it prompted found the rest
of the damage from the vanished venv.

**Nine more declared-but-missing packages**, each silently disabling a whole
tool category rather than failing:

| Missing | What was dead |
|---|---|
| `pywebview` | the desktop window itself |
| `pycaw` | volume control |
| `screen-brightness-control` | brightness |
| `pyperclip` | clipboard read/write |
| `mss` | screenshots |
| `pygetwindow` | window management, "what am I looking at" |
| `winsdk` | "what's playing" |
| `winotify` | native toasts (slower PowerShell fallback) |
| `dateparser` | the long tail of natural-language times |

That is thirteen tools answering "unavailable" while everything looked healthy —
a large share of the friction, and invisible until asked for directly. All
installed; preflight now reports the capability matrix (`System control - 7
capabilities live`) and **fails** on a missing desktop shell.

**Installing `dateparser` changed reminder behaviour**, and nearly slipped
through: it resolves "friday" to Friday 00:00, and `parse_when` returned it — so
"remind me to call mom friday" would have quietly become a midnight alarm
instead of Dexter asking what time. `parse_when` keeps its contract (a bare day
is "no time given"); an explicit "at midnight" still works. Regression test
added, skipped when dateparser is absent.

### Still needing you, not code

| # | What | Why it is yours |
|---|---|---|
| 1 | **Free the GPU** — `.buzzcode/engine/llama-server.exe` holds 14.5 GB of 16 GB | Dexter now detects and reports this, but managing another app's processes is not its business (D9). Until then every LLM turn runs on CPU at ~9s. |
| 2 | **Google OAuth** — `data/google_credentials.json` is absent | Calendar/email tools are written and unit-tested but have never run against a real account. Say "connect my google account" after adding the file. |

---

## 2026-08-23

### Diagnosis — the intelligence layer was never the problem

Ran the eval harness that had never produced a valid number, plus a new probe of
the fast tier:

| Measurement | Result |
|---|---|
| Tool selection, `qwen2.5:7b`, 15 requests | **13/15** tool, 13/15 args |
| Chat restraint | **2/2** |
| Fast-tier escalation, `qwen2.5:3b`, 15 prompts | **15/15**, ~0.5s per decision |
| Latency, Tier 2 | **avg 9.27s**, worst 22.9s |

Cause of the latency, and of most of the felt friction:

```
ollama ps  -> qwen2.5:7b   size_vram: 0          model is 100% on CPU
nvidia-smi -> RTX 5060 Ti  14520 / 16311 MiB     VRAM already taken
holder     -> C:\Users\singh\.buzzcode\engine\prebuilt\llama-server.exe
```

### Silent degradations found while fixing the launcher

`start_dexter.bat` pointed at `..\MidnightBuzz\backend\voice_env`, **which no
longer exists**. It fell back to system python with a single `[WARN]`, and that
interpreter was missing two packages:

- **APScheduler** → reminders were stored, listed, and **never fired**. For an
  assistant sold as a planner, that is the whole product failing silently.
- **kokoro-onnx** → speech fell back to edge-tts, which needs the internet, and
  every prosody profile in `prosody.py` targets Kokoro.

Also absent: the Google API libraries (Gmail and Calendar were entirely
non-functional), `send2trash`, and `ddgs`. All now installed and pinned in
`requirements.txt`, and `preflight.py` fails loudly on the ones that matter.

### F1 — `.env` now loads from every entry point ✅

`load_dotenv()` existed only in `main.py:25`, so the eval harness, every pytest
suite, and any standalone script ran on hardcoded defaults — the eval was
scoring `llama3.2`, a model not installed here, at **0/15**. With the real config
it scores **13/15**. New `backend/env_boot.py`, imported by `main.py` and the ten
modules that read configuration at import time.

### F3 — the tiered fast model had no test coverage ✅

Fixing F1 immediately broke three tests, which is the point: the suite passed
only because `.env` never loaded, so `DEXTER_FAST_MODEL` was empty and the
**newest code in the router was never executed by any test**. `ScriptedLLM`
gained `resolve_provider`; a `no_tiering` fixture lets Tier-2 tests state their
intent instead of depending on the developer's `.env`.

### F2 — `jsonstore` was silently losing writes ✅

Surfaced as an intermittent `test_tasks` failure: `os.replace` is atomic on
Windows but not immune to sharing violations (antivirus, indexer, concurrent
reader). `save_json` printed and returned `False`, and no caller checks the
return value — so planner, reminder, and task state could just fail to persist.
Now retried with backoff. Covered by `test_jsonstore.py`.

### F4 — Dexter now says when it is degraded ✅

New `backend/runtime_health.py`: reads Ollama's `/api/ps` for actual model
placement, compares the model's size against free VRAM, and names the process
holding the card. Surfaces in three places — a `[Startup] DEGRADED:` line, the
`runtime` block on `/status`, and a dismissible banner in the UI
(`RuntimeBanner`). Live output:

> qwen2.5:7b needs about 4466 MB but only 1505 MB of 16311 MB VRAM is free
> (llama-server.exe). Dexter will fall back to the CPU on the next request…

The first threshold written here was wrong — a flat "is 1 GB free" check passed
happily with 1.5 GB free and a 4.7 GB model. It compares against the real model
size now.

### F5 — the UI no longer forgets the conversation ✅

Turns were persisted and fed to the model, but there was no way to read them
back, so every launch opened on a hardcoded welcome while Dexter itself
remembered the conversation. Added `GET /api/dexter/memory/history`; `useChat`
restores the last 12 turns. Covered by a frontend test.

### F6 — there is a log file now ✅

No `logging.basicConfig` existed anywhere and ~90 `print()` calls went to a
stdout that WebView2 does not show. `backend/logging_setup.py` configures a
rotating `data/dexter.log`, tees stdout/stderr into it (the existing tagged
prints are useful — they only lacked a destination), and logs uncaught
exceptions from the main thread and worker threads.

### F7 — the wake word is "hey Jarvis", and everything says so now ✅

`wakeword.py` loads openWakeWord's stock `hey_jarvis`, while the README and the
UI button both told you to say "Dexter" — so every interaction opened with a
word that wakes nothing. README, `.env.example`, and the header button now agree
with the code.

### F8 — one command, and it checks before it launches ✅

`backend/preflight.py`: dependencies, `.env`, data dir, frontend build, model
reachability, whether the configured model is actually pulled, GPU placement,
Kokoro assets, and APScheduler. `[FAIL]` blocks launch; `[WARN]` explains a
degraded capability. `start_dexter.bat` resolves the interpreter explicitly
(`DEXTER_PYTHON` → `.venv` → system) and runs preflight first.

### F9 — settings without editing .env ✅

Every knob was a module constant read once at import, so tuning anything meant a
file edit and a restart. `backend/settings_store.py` persists overrides, updates
the environment, **and patches the already-imported module constant** so the
change takes effect immediately — without that third step a settings screen
looks like it works and changes nothing. Restart-only settings say so in the UI.
Verified live: barge-in and the follow-up window both applied to a running
server.

### S1–S5 — the spine, and the loop that makes it a practice ✅

`backend/workbook.py` — goals → projects → commitments → entries, in SQLite
beside memory. The behaviours the flat planner could never have:

- **Rolling is counted.** A commitment moved six days running looks different
  from one written this morning (`roll_count`, `first_due`).
- **Completed work survives.** The old planner erased it at midnight; the weekly
  scorecard needs the history.
- **Slip detection** — overdue, or rolled ≥3 times, is flagged and challenged.
- **The day is closed deliberately** — an evening prompt at 21:00, an automatic
  record at 23:50 so a day is never lost, and a Sunday review.
- **The morning brief** now opens with what you committed to, what you are
  carrying, and what keeps moving — not a bare task count.

`backend/tools_workbook.py` adds 12 voice tools, with deterministic Tier-1 slot
extraction so they answer in ~80ms instead of ~9s. Live:

```
"what did I say I would do this week"  -> tier 1, 78ms
"what am I avoiding"                   -> tier 1
"how was my week"                      -> "Strong week. You kept 1 of 1…"
```

`WorkbookPanel` puts it on screen: today, carried-over work, roll counts, the
slipping callout, and a close-out button.

Two parsing problems surfaced while wiring this up:

- `timeparse.parse_when` returns `None` for bare dates, so "by Friday" silently
  dated every commitment to *today*. Added `parse_date` (21 tests).
- "What's due this week" used an ISO Monday–Sunday window. Asked on a Sunday
  evening that week has three hours left in it, so a commitment made for
  tomorrow came back as "nothing outstanding this week" — true and useless.
  Planning now looks at the next seven days (`upcoming()`); *reviewing* still
  uses the calendar week, which is the one case where the ISO boundary is right.

### C1 — Dexter can see the world now ✅

77 tools and not one could look anything up; paired with a local model frozen at
its training cutoff, every question about the real world was answered from
memory, confidently. `backend/tools_web.py` adds keyless `web_search`
(DuckDuckGo) and `get_weather` (wttr.in). Both verified live.

### C2 — files can be renamed, moved, copied, and deleted ✅

The README's own Tier-2 example ("find the invoice, rename it, and remind me at
5") could not run. `backend/tools_files.py` adds all four, every path — source
*and destination* — through `pathguard`, never overwriting silently, and delete
goes to the Recycle Bin or refuses outright. 14 tests, including every
allowlist-escape case.

"move X to Y" and "copy X to Y" differ by one verb, and the embedding matcher
confused them, so all four got deterministic Tier-0 rules.

### C3 — calendar and email completed ⚠️

`reschedule_event`, `cancel_event`, `search_email`, `reply_to_email`. Preserves a
meeting's real duration when moving it, resolves "my 3pm" by time as well as
title, and threads replies properly. 11 tests against a fake service —
**not verified against a real account**, because Google is not connected here.

### R1–R4 — the untested boundaries ✅

`pathguard` (the entire filesystem security boundary) and `jsonstore` (all
persistence) had no tests at all. Now 18 between them, plus 20 for the workbook,
22 for settings, and a frontend suite that had deps installed and zero tests
written. `npm run check` runs preflight + pytest + tsc + vitest.

---

## Older history

Roadmap v3 (Phases 0–6: hygiene, security, local-LLM excellence, semantic
memory, local voice pipeline, agentic autonomy, frontend triage) is recorded in
[`IMPROVEMENT_PLAN.md`](IMPROVEMENT_PLAN.md). It is complete and kept for
history; v4 does not repeat it.
