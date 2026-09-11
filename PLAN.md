# Dexter Roadmap v11 — Hands like mine (desktop control)

**Status date:** 2026-09-11 · **Progress log:** `PROGRESS.md` · **Built by:** an Opus implementation agent from this plan
**Status:** P1-P3 built 2026-09-11 (see `PROGRESS.md` v11); v11.1 the same day fixed why none of it was reachable by voice and added power + shell tools.
**Owner's ask (2026-09-11):** "dexter opens chrome but not what i say in it, like opening a different website or closing chrome or a url, similarly other tasks. make it more smart so that it can do stuff like me, the minute stuff as well."

## Why it fails today (measured in the code, not guessed)

| You say | What happens | Why |
|---|---|---|
| "open chrome" | your everyday Chrome opens | `_FAST_RULES` → `open_app` (registry miss) → `launch_app` → `app_index` |
| "open youtube" | "I couldn't find 'youtube' on this PC" | same rule: only URL-ish text (`youtube.com`) is treated as a site |
| "open youtube.com" | a **second** Chrome window opens (Dexter's own Playwright profile under `data/browser_profile`) | `browser_open` drives Dexter's browser, never yours |
| "close chrome" | closes one Chrome window by title, or nothing | `close_window` matches window titles; no notion of an app's process |
| "close this tab", "go back", "search for lo-fi on youtube" | act in Dexter's window, or "no page open" | the `browser_*` tools do not know the Chrome you are looking at |
| "scroll down", "press ctrl s", "type hello", "switch to code", "minimize this" | nothing | no keyboard / window / scroll tools exist |
| "open youtube and search for lo-fi beats" | usually right (agent loop), sometimes "couldn't find 'youtube and search...'" | the open-rule swallows the whole sentence when it is ≤ 4 words |

Two browsers is the root cause. Everything else is a missing hand.

## Decisions (owner, 2026-09-11)

- **D1 One Chrome, yours.** Dexter acts in the Chrome you use. Chrome 136+ refuses remote debugging on the default profile, so control is by three honest means: (a) `chrome.exe <url>` hands a URL to the running instance as a new tab; (b) keystrokes and window commands for tab/window actions; (c) Windows UI Automation (UIA) to *read* the active tab's URL, title, tab list and page text. Dexter's Playwright window stays only as an explicit fallback (`DEXTER_BROWSER_MODE=playwright`).
- **D2 The minute stuff is a toolset,** not a model trick: press keys, type text, scroll, click by visible text, focus/switch/minimize/maximize/snap/close windows and apps, read what is on screen. Deterministic, testable with fakes, no real keystrokes in tests.
- **D3 Context is the default.** "it / that / this / here / the tab" resolve to the active window; "in chrome, …" focuses first; a compound sentence is executed step by step by the agent loop and reported once.
- **D4 Nothing destructive without a word.** Closing an app with unsaved work is the app's own prompt; Dexter never force-kills a window it was asked to "close" (only "kill" does that, with confirmation). No key is ever sent to a window that is not the one named or in focus.

## Phase 1 — Your Chrome (P1)

| # | Item | Acceptance |
|---|---|---|
| P1.1 | `chrome_control.py`: find Chrome windows (pygetwindow + psutil, by process), the active one, open a URL/search in the running instance (`chrome.exe <url>`; launch if not running), close a tab (Ctrl+W), close Chrome (WM_CLOSE to every Chrome window, then wait; never TerminateProcess unless `kill`), new tab, next/previous tab, reopen closed tab, back/forward/reload, find in page, zoom, fullscreen | "open youtube" → a **new tab in your Chrome** (launches it if closed); "close chrome" → every Chrome window closes cleanly; "close this tab" / "next tab" / "go back" / "reload" act in the tab you see |
| P1.2 | UIA reader (pywinauto, `backend="uia"`): active tab URL (omnibox value), title, tab strip names, main document text (budgeted, ≤ 6 000 chars) | "what am I looking at" / "read this page" / "what tabs do I have open" answer from **your** Chrome |
| P1.3 | Site names are sites: `open youtube`, `open gmail`, `go to reddit`, `search youtube for X`, `google X` resolve through `_SITE_ALIASES` + a "known sites" table (extend `browser.normalize_target`) **before** the app launcher; `open chrome` still opens Chrome; `open X` unknown to both → one clear question, not "couldn't find" | 12 phrases in `test_router.py` pinned |
| P1.4 | The existing `browser_*` tools become **mode-aware**: default `own` routes open/close/tabs/back/read to `chrome_control`, `playwright` keeps today's behaviour; `browser_click`/`browser_type` in `own` mode use UIA (click by name) with keystroke fallback | `test_browser_tools.py` covers both modes with fakes |

## Phase 2 — The minute stuff (P2)

| # | Item | Acceptance |
|---|---|---|
| P2.1 | `desktop_control.py` + `tools_desktop.py`: `press_keys("ctrl+s")`, `type_text("…", submit=False)`, `scroll(direction, amount)`, `click_text("Save")` (UIA name match in the active window; pyautogui-free), `read_screen_text()` (UIA text of the active window, budgeted), `switch_to(app)`, `minimize/maximize/restore/snap_left/snap_right(window)`, `close_app(name)` (all windows of the process, graceful), `list_windows` grouped by app | "press control s", "type hello world", "select all and copy", "scroll down", "click save", "switch to code", "minimize this", "snap chrome to the left", "close notepad" |
| P2.2 | App recipes: a small table of per-app hotkeys (Chrome, Edge, VS Code, Explorer, Spotify, Notepad, Discord, Premiere) so "new tab", "new file", "find", "toggle sidebar", "next track" pick the right keys for the **active** app | recipe table tested; unknown app → generic keys or a one-line "in X I don't know how" |
| P2.3 | Safety: keys go only to the window named or in focus (focus is verified after `switch_to`, and refused if a *different* window took focus); `type_text` refuses secrets-looking input into a browser omnibox; `kill` needs confirmation; every action returns what it did and to which window | tests for every refusal |

## Phase 3 — Understanding (P3)

| # | Item | Acceptance |
|---|---|---|
| P3.1 | Reference resolution: "close it/this/that", "scroll here", "this tab", "the browser" resolve from the active window and the last thing Dexter opened (a `focus` memory in the router, per session, 5 min) | "open notepad" … "close it" closes Notepad; "open youtube" … "close that tab" closes it |
| P3.2 | "in <app>, …" and "<app>: …" prefixes focus first, then run the rest; "and then / and / then" chains run through the agent loop with the desktop tools in the candidate set; one combined reply | "in chrome, go to gmail and open the first unread" (best effort on the last step) |
| P3.3 | The fast-path open-rule stops swallowing sentences: anything with "and", "then", a URL, a site name, or > 3 words goes to the tiers below; `_NOT_AN_APP` extended | `test_router.py`: 20 new phrases, zero regressions |
| P3.4 | `test_agent_eval.py`: 15 desktop/browser cases so the model's choice is measured, not assumed | eval file runs (skipped without a model) |

## Phase 4 — Teach me (P4) — *not in this build*

Playbooks ("when I say start streaming: open OBS, …") on top of the macro recorder, and a Chrome extension with native messaging for full DOM access in your own Chrome. Planned; not built now.

## Not doing

- Remote-debugging the daily Chrome profile (Chrome forbids it since 136; a copy of the profile is not "your Chrome").
- Screen-vision clicking (OCR / vision model). UIA names cover what is clickable; a picture is Phase 4 material.
- Force-killing anything on "close".

---

# Dexter Roadmap v9 — Sentinel rules, Dexter runs the apps

**Status date:** 2026-09-10 · **Progress log:** `PROGRESS.md` · **Cross-repository contract:** [`../GUARDIAN_PLAN.md`](../GUARDIAN_PLAN.md)
**Owner's ask (2026-09-10):** "Dexter controls every app in
`B:\youtubeProjects\Buzzcaf_Media`; Sentinel is the supreme guardian of VRAM and
RAM so the PC never hangs, and uses SmartCleaner to free space; Dexter is my
coworker/coach and asks Sentinel for permission before loading models."

What was wrong: five places in Dexter loaded or unloaded a model, only one of
them asked Sentinel, and none of them ever called `Release`. Dexter could open
Chrome and start the Studio but could not tell you whether VrDeep was rendering,
and `launch_app` would happily fire up a 10 GB pipeline beside a 12 GB model
server. On a 16 GB card shared with VrDeep, BuzzEdit's ComfyUI and buzzcode's
27B, that is how the machine locks up.

| # | Item | Design | Acceptance |
|---|---|---|---|
| D1 | Sentinel client + lifecycle | `sentinel_client.py` speaks protocol v2: `hello()`/`features()`, `register(… ram_budget_mib, priority, label)` keeping the token, `reserve(mib, ram_mib, for_pid, ttl)`, `query`, `ram_status`, `disk_status`, `request_cleanup`, `app_status`, `release()`, `unregister()`. New fields go on the wire **only** when `Hello` says they are understood, so the v1 service installed today keeps working byte for byte. `router.sentinel_start()` registers and subscribes; `sentinel_stop()` releases, unregisters and stops the thread in the lifespan shutdown | **N1** `Hello` failing degrades to v1 (`features=[]`, no `ram_mib` on the wire, `Query` skipped and "would be granted"), verified live against the installed service in well under a second |
| D2 | **The model steward** | `model_steward.py` is the *only* module allowed to call `hybrid_llm.warm_model`/`release_model`/`set_keep_alive`, enforced by a grep test. `ensure()` estimates VRAM **and** RAM, `Reserve`s, loads, then `Release`s once the load is *observed* in `/api/ps` — or immediately on failure. `shed()` runs the ladder for `VramTight`/`RamTight`/`Preempt`: smart model → (red) fast model → foreign Ollama models → LM Studio → an engine Dexter started. Every old load path is rewired: startup warm, `_keep_fast_model_warm`, Tier 1.5 escalation, `code_agent` | **N2** no module outside the steward touches model residency; a refusal loads **nothing**, names the holder and the allowed move, and Dexter keeps answering on whatever is resident; every booking is released on success *and* on failure |
| D3 | The apps registry | `apps_registry.py` + `data/apps.default.json` (all 17 projects with real launch scripts, cwd, probes, stop strategies, class, VRAM/RAM, `exclusive_with`, logs; owner overrides in `data/apps.json`, never written by Dexter). `procs.py` (psutil, else `Get-CimInstance`/`tasklist`/`netstat`): find by name **and path**, port owner, kill tree, RSS, uptime. `tools_apps.py`: `app_status`, `apps_overview`, `open_app`, `stop_app`, `restart_app`, `app_logs`, `request_cleanup`, plus the thin readers `vrdeep_queue`, `buzzedit_status`, `finance_status`, `coffee_brief`, `mastgaane_report`. All Tier-0 rules. The Studio keeps `buzzcaf_client` | **N3** "open buzzedit", "start vrdeep", "is buzzfin running", "stop caliber", "what's running", "clean up space" resolve without a model; "open chrome" and "close notepad" still route as before |
| D4 | Resource-aware launch | `open_app` for a batch app or anything with `vram_mib>0`: `exclusive_with` check, `sentinel.query(vram, ram)`, RAM zone (Yellow warns, Red refuses), then launch and poll the probe for 30 s and report honestly. Refusals name the holders and the moves; a record's `warn` (MidnightBuzz binds `0.0.0.0`, BuzzAnimate builds on launch) is passed on | **N4** a refused `Query` launches nothing and names who has the memory; an exclusive app that is running blocks the launch by name |
| D5 | The UI | `GET /api/dexter/resources`, `GET /api/dexter/apps`, `POST /api/dexter/apps/{id}/start\|stop`, `POST /api/dexter/nudge` (loopback + token, for Mission Reminder). `ResourcePanel.tsx` (three gauges, holders, frozen, pause/resume/clean), `AppsPanel.tsx` (the v8 `AppLauncher` grown up: running/stopped, start/stop with confirmation, the quick OS launcher kept). SSE `resources`/`gpu_busy`/`disk_tight`/`evicted` → banners and the pop-up; `RuntimeBanner` gains `sentinel_down` and `tray_down` | **N5** an absent or old Sentinel reads as "the guardian is old", never as an empty gauge; stopping a busy batch app from the panel asks first and only the second click confirms |
| D6 | The coworker layer | One machine line in `companion.context_block()` and in the morning brief (who has the GPU, RAM %, disk free, last night's cleanup). Initiative candidates `gpu_free_queue_waiting`, `disk_low`, `frozen_long`, `evicted_notice`, ordered *after* the work ones. Mission Reminder's runtime mirror is read (never written) into "Right now", and **Dexter's own nudges are suppressed while one of its focus blocks is running** | **N6** the machine line is in every prompt and degrades to nothing when the guardian is down; a running Mission Reminder session silences check-ins, tails, nudges and proposals; a stale mirror does not |
| D7 | Settings | `settings_store.SETTINGS` gains a **Machine** group with `module`/`attr` so it hot-reloads: VRAM/RAM budgets, `DEXTER_MANAGE_LMSTUDIO`, `DEXTER_AUTO_CLEAN`, engine VRAM/RAM, `DEXTER_APPS_FILE`, `MISSION_REMINDER_RUNTIME`; `.env.example` documents all of them | **N7** changing the VRAM budget in Settings takes effect on the next reservation without a restart |
| D8 | Verification | `FakeSentinel` extended with the v2 commands while every v1 test stays; `test_model_steward.py` (incl. the monopoly grep), `test_apps_registry.py`, `test_tools_apps.py`, router refusal/lifecycle tests, vitest for both panels; `test_dexter_suite.py` scenario E, skipped when no pipe answers | **N8** `python -m pytest backend -q`, `npx tsc --noEmit`, `npx vitest run` and `npm run build` all green; nothing in the suite can reach a real process, a real model or the real Sentinel |
| D9 | Docs | `ECOSYSTEM.md` rewritten around the full port map, the Sentinel contract and the steward rule, and copied byte-identical to `BuzzcafAI/`; this table; `PROGRESS.md` evidence; a README section | **N9** both `ECOSYSTEM.md` copies hash the same |
| **P1** | **Ports: nothing hardcoded** | `backend/buzzcaf_ports.py` (stdlib only) is the canonical Python implementation of GUARDIAN_PLAN.md section 11 - `pick_port` / `publish` / `withdraw` / `discover` / `ledger_path` over a shared ledger at `%LOCALAPPDATA%\Buzzcaf\ports.json`, and it is copied byte-identical into every Python app. Dexter picks its own port from `DEXTER_PORT` (8098) upwards in `desktop_app.py` / `main.py`, answers `/health` with `{app, status, port, pid, version}`, publishes once that route answers and withdraws on exit; CORS, the window, `/status` and `/resources` all name the chosen port. `src/api.ts` derives the API base from `window.location.origin` (Vite dev proxies from `VITE_DEXTER_PORT`), `apps.default.json` carries `preferred_port` + `extra_preferred`, the registry probes `discover()` with the identity rule, `buzzcaf_client` reads the ledger before `studio.runtime.json`, and `LLAMACPP_URL` falls back to the ledger's `buzzcode` entry. `start_dexter.bat` no longer fixes a port or kills whoever holds one | **P1** with 8098 deliberately occupied Dexter starts on 8099, `/health` identifies it, the ledger has the entry, `python test_dexter_suite.py` passes against it, and the entry is gone after a clean exit |

**Not doing.** Dexter does not kill anything Sentinel has not classified, does
not delete files (SmartCleaner runs from Sentinel's tray, in the user session,
where it can actually see `%TEMP%`), does not override a refusal, and does not
write `C:\ProgramData\Sentinel`. Mission Reminder's mission model and Dexter's
stay two separate things — the bridge is read-only so neither can corrupt the
other.

---

# Dexter Roadmap v7 — A coworker, not a program

**Status date:** 2026-09-06 · **Progress log:** `PROGRESS.md`
**Owner's ask (2026-09-06):** "it seems like a program running and gives some
notifications. I want it to talk to me, try to know my plans, work like a
coworker, collaborate with me as if Dexter and I are together on a mission,
guiding, coaching, supporting and assisting me."

What was wrong: every unprompted word was a toast or a banner, every reply was
a tool's one-line summary, and the model had no idea what we are working
towards. The fix is a layer, not a rewrite:

| # | Item | Design | Acceptance |
|---|---|---|---|
| K1 | The mission | `mission.py` → `data/mission.json`: north star + why, this week's focus, today's win, blockers, wins log; `open_loops()` (overdue/slipping commitments, running code job, Studio approvals). Tools: `set_mission`, `set_focus`, `set_today_win`, `log_win`, `add_blocker`, `clear_blocker`, `mission_status`, `snooze_checkins`, `check_in`, all with fast rules | **M1** "today's win is X" / "our mission is X" work without a model; the Mission card shows them |
| K2 | The stance | `companion.context_block()` (how we work together + mission + today + open loops) in every agent prompt; a short stance in the fast layer; greetings ("hi", "good morning") answer with today's win or the first open loop | **M2** "hi" gets "Morning. Today's win is still 'X'. Where do we start?", never "How can I assist you" |
| K3 | Dexter speaks first, in the chat | `companion.say()` → a Dexter turn in the conversation (stored in memory, SSE `companion`, quick replies), toast only as a wake-up. Morning (06:00 or first launch of the day), midday (13:00), evening (close-out hour), weekly (Sunday); event-driven for a finished code job and a Studio approval. Composed by the model from facts when a provider is up, template otherwise; quiet hours, snooze, once-per-day | **M3** the morning message is in the chat feed with a question and quick replies; "done" / "on it" / "stuck" / "later" are understood as answers |
| K4 | Coaching tails | `companion.after_reply()` adds one sentence to tool replies when it moves things forward ("2 of 4 today", "'send invoice' is still open from 24 Aug; want it before this one?"), rate-limited | **M4** completing a task reports progress on the day; adding a task while something is overdue names it |
| K5 | The Mission card | `MissionCard.tsx` above the chat: north star, this week, today's win, blockers, open loops; empty slots pre-fill the input with the phrase Dexter understands | **M5** visible on launch |
| K6 | Initiative, and the switch | `initiative.py`: every 15 min, propose at most one next move (a commitment due within 30 min, no win picked by 10:00, something overdue 3+ days → move/break down/drop, today's win still open after 18:00, an own video gathering pace → offer the analysis, saved topics with nothing in production → offer to start one); each once a day, four a day at most; quick replies are real phrases. The **You lead / Dexter leads** pill on the Mission card (also "I'll take it from here" / "take the lead") silences check-ins, nudges, coaching tails and proposals in one click | **M6** proposals arrive as chat messages with one-tap actions; the switch stops all of them |
| K7 | Activity awareness | `activity.py`: foreground window (app, title, idle) every 30 s → sessions and daily totals by app and project; recently modified files under the knowledge roots and `B:\youtubeProjects` (budgeted scan, newest folders first); "Right now" in every prompt, on the Mission card, in the morning/evening facts; tools `what_am_i_doing`, `activity_summary`; initiative candidates *off-mission* (45 min in a work app on something that is not today's win) and *long stretch* (110 min without a break); Settings → Activity awareness; `data/activity.jsonl`, seven days | **M7** "what am I working on" answers with app, project, minutes and the last saved file; the Mission card shows it live |
| K8 | Reading Claude Code's logs | `claude_sessions.py` folds `~/.claude/projects/*/*.jsonl` (read-only) into sessions: project, branch, AI title, the owner's prompts (noise and subagent chatter skipped), files Claude wrote, commands it ran, its last words; a live session (transcript touched in the last 3 min) joins "Right now"; tools `claude_activity` (today / yesterday / this week, per project) and `claude_session`. Decision (owner, 2026-09-06): Dexter does **not** run Claude Code; buzzcode is the coder | **M8** "what was I doing in claude" and "what did claude change in dexter" answer from the transcripts |

Not doing: a separate "coach" persona or long pep talks (the stance says short, plain, one question at most); running Claude Code from Dexter (owner's decision: buzzcode only).

---

# Dexter Roadmap v6 — Hands: the browser, the coder, video intelligence

**Status date:** 2026-09-06 · **Progress log:** `PROGRESS.md` · **Contract:** `ECOSYSTEM.md`
**Owner's ask (2026-09-06):** Dexter must control the browser; Dexter must use
the buzzcode llama-server "so it can code for me"; the Studio needs a place to
drop a video or channel link and learn why it performs and how to model our
video on it.

| # | Item | Design | Acceptance |
|---|---|---|---|
| B1 | Browser control | `browser.py`: one Chrome window (installed Chrome via Playwright, own profile `data/browser_profile`, or attach with `DEXTER_BROWSER_CDP`) on a worker thread; `tools_browser.py`: open / read page / click by text / type into field / scroll / back / tabs / switch / close / screenshot; fast rules so "go to X", "click Y", "read this page" never touch the model | **J1** "go to github.com", "click sign in", "read this page" act in Dexter's window; failures name what *is* clickable |
| C1 | Coding through buzzcode | `code_agent.py` runs `buzzcode -C <repo> --mode edits\|yolo --json -p "<task>"` as a background job, folds its JSON-lines events (tool calls, edits, verify, finished) into a status, toasts and banners the result with `git diff --stat`; engine tools start/stop/inspect the llama-server on 8089 (also Dexter's first model provider). Repos come from `DEXTER_CODE_ROOTS`; `yolo` only when asked for in words | **J2** "buzzcode: fix X in dexter" returns at once, "code status" reports turns/files, completion arrives as a banner |
| S1 | Video intelligence (Studio) | Studio `video_intel.py` (yt-dlp: metadata, captions, most-replayed heatmap, channel baseline) + model read with the target channel's brand guide → why it works + blueprint; `POST /api/video-intel/analyze`; Analyze tab; Dexter tool `studio_analyze_video` | **J3** paste a link → metrics, peaks with transcript, why-it-works, blueprint; "save as topic" / "start a project" work; no model ⇒ labelled numbers-only read |

Not doing: driving the owner's already-open Chrome without a debugging port
(profile lock); running buzzcode in `ask` mode headless (it would hang).

---

# Dexter Roadmap v5 — The assistant that runs the Studio

**Status date:** 2026-09-06 · **Progress log:** `PROGRESS.md` · **Cross-project contract:** `ECOSYSTEM.md`
**Decisions (owner, 2026-09-06):** Dexter is the personal assistant and controls
BuzzcafAI. Voice is dropped for now. Local LLM first (codeBuzz `llama-server` on
8089 when up, then LM Studio, Ollama, cloud). Index `B:\youtubeProjects`,
`C:\Users\singh\Documents`, a notes vault, and the Studio's data.

Acceptance bar (evidence goes in `PROGRESS.md`): **I1** one-click launch, no
console · **I3** first token < 1.5 s p50 · **I4** starts offline · **I5**
"open the studio and start a video about X" works end to end · **I6/I7/I8**
knows the studio, notes/docs, and me, with citations · **I9** YouTube status
via BuzzBrain · **I10** one agenda · **I12** nothing fabricated · **I13**
`pre-v5` tag exists.

| # | Item | Effort | Acceptance |
|---|---|---|---|
| 0.1 | Commit dirty tree in groups, `git tag pre-v5` | S | clean tree, tag |
| 0.2 | `data/.session_token` ACL restricted to the current user (`icacls`) | S | `icacls data\.session_token` lists only the user |
| 0.4 | `ECOSYSTEM.md` identical to `BuzzcafAI/ECOSYSTEM.md` | S | `diff` empty |
| 1.1 | `buzzcaf_client.py`: port 8000, no MidnightBuzz, `pythonw` launch of `desktop_app.py`, `[]` brands on error, `/health` must say `app:"buzzcaf"`; `buzzcaf_execute_step` fails honestly and reports `paused_for_approval` | S | Studio up → real projects; Studio down → "offline", no invented brands |
| 1.2 | Delete `SpilledCoffeeAIController`, `curate_video_ideas`, `run_overnight_curation`, demo seed tasks, `/spilledcoffee/*`, overnight UI, legacy skill file | S | `grep -rni "spilledcoffee\|overnight" backend src` empty; tests green |
| 1.3 | `HF_HUB_OFFLINE` when the MiniLM cache exists; startup timing log lines; preflight WARN when cache missing | S | **I4** |
| 1.4 | Remove voice (modules, WS route, TTS thread, mic patch, settings knobs, deps, UI) — one commit after the tag | M | `import main` ok; tests + build green; requirements without STT/TTS deps |
| 1.5 | `hybrid_llm.py` provider `llamacpp` (`LLAMACPP_URL`, default 8089) first in chain when online; `runtime_health` reports it; hint when Ollama is on CPU | S | **I3** with 8089 up; clean fallback with it down |
| 1.6 | `preflight --quiet` MessageBox; `desktop_app.py` runs preflight, stale-dist guard, window size persisted, try/except → MessageBox; `Dexter.vbs` hidden launcher | S | **I1** |
| 3.2 | `buzzcaf_client.py` chat/state/topics/agents/approve + SSE reader → events bus + reminder on `approval_needed`; new `tools_studio.py` with 8 new tools; `BuzzcafPanel` approvals | M | **I5**; eval ≥ 13/15 |
| 4.1 | `knowledge_ingest.py`: `DEXTER_KNOWLEDGE_ROOTS`, `ingest_sources` table, pdf/docx extract, nightly 02:30 + startup incremental + `reindex_knowledge`, citations, `search_knowledge` tool | M | **I6/I7**; idempotent reindex tests |
| 4.2 | `profile.py` + `data/profile.json`, profile block in system prompt, `update_profile`/`show_profile`, Settings "About you"; channel ids pushed to Studio | S | **I8** |
| 4.3 | Google connected (needs `data/google_credentials.json` from the owner) | S | 9 tools live; morning brief shows calendar |
| 5.1 | `youtube_channel_status`, `youtube_video_stats` tools over the Studio's BuzzBrain routes | S | **I9** |
| 5.2 | One agenda (workbook + reminders + calendar), planner migrated and deleted; notes as markdown in the notes root; automation folded | M | **I10**; eval ≥ 13/15 |
| 5.3 | Tray icon, hide-on-close, `Ctrl+Alt+D` hotkey | M | close window → reminders still fire |

## Not doing in v5

- Training a wake word or any voice work.
- Deleting the `MidnightBuzz` folder (references removed only).
- Managing codeBuzz's GPU process: Dexter *uses* its server when up and falls back when not.

---

# Dexter Roadmap v4 — Make It the Coworker

**Status date:** 2026-08-23
**Supersedes:** `IMPROVEMENT_PLAN.md` (Roadmap v3, 2026-08-19 — Phases 0–6 delivered, kept for history).
**Living progress log:** `PROGRESS.md`

---

## Why there is a v4

Roadmap v3 delivered six phases of **plumbing** — token streaming, provider
fallback, neural routing, a vector store, a local wake-word/VAD/STT pipeline,
token auth, a frontend rebuild — and every one of them shipped and works.

The **job** never got a phase. Dexter was asked to be a planner, coach,
employee, and second brain, and what exists is a very good *command executor*.
Those four roles all need the same missing thing, which is why building them is
not four times the work:

> There is no persistent model of the user and their work over time.

The planner is a flat list of today's `{time, title, completed}`. Completed
tasks are erased at midnight; incomplete ones roll forward forever, unchanged
and un-noticed. There are no goals, projects, commitments, or history.
`grep -i "goal|habit|streak|accountab"` over `backend/` matches nothing but
background-task plumbing.

### Measured ground truth (2026-08-23)

The intelligence layer is **not** the problem. Measured on this machine:

| What | Result |
|---|---|
| Tool selection — `qwen2.5:7b`, 15 real requests (`test_agent_eval.py`) | **13/15** correct tool, 13/15 valid args |
| Chat restraint — must *not* call a tool | **2/2** |
| Fast-tier escalation — does `qwen2.5:3b` hand off when a tool is needed? | **15/15**, decision in ~0.5s |

What *is* wrong:

| Finding | Evidence |
|---|---|
| The 7B runs **entirely on CPU** — 7–23s per action turn (avg 9.27s) | `ollama ps` → `size_vram: 0`; RTX 5060 Ti at 14520/16311 MiB, held by `.buzzcode/engine/prebuilt/llama-server.exe` |
| Dexter never tells the user it is degraded | no health signal for device or VRAM anywhere |
| `load_dotenv()` lived only in `main.py` | every other entry point ran on hardcoded defaults; the eval scored a `llama3.2` that is not installed (0/15) |
| The tiered fast-model path had **zero** test coverage | the suite passed only because `.env` never loaded, so the tier was always off |
| `jsonstore.save_json` silently loses writes | Windows `os.replace` sharing violations → printed and returned `False`; planner/reminder/task state dropped |
| Voice: the README says say *"Dexter"*, the model is `hey_jarvis` | `wakeword.py:23` vs `README.md` |
| No continuity: the UI forgets every conversation | no history GET endpoint; `useChat.ts` starts from a hardcoded welcome |
| Ceremony: `.env` edits + restart to change anything | no settings surface; no single start command |

**Design rule for v4:** every item either removes friction from a daily
interaction or builds the spine. No new plumbing without a user-visible job
attached to it.

---

## Part 0 — Friction (F) — *do first, cheap, unblocks daily use*

| # | Item | Effort | Acceptance |
|---|---|---|---|
| F1 | `env_boot.py` — load `.env` from every entry point | S | eval, pytest, and scripts all resolve the configured model |
| F2 | `jsonstore` atomic-replace retry | S | a sharing violation delays a write instead of losing it |
| F3 | Tiered fast-model test coverage | S | Tier-2 tests pin tiering explicitly; the tier has its own tests |
| F4 | **Degraded-runtime detection** — model on CPU, VRAM occupied, missing deps | M | `/status` reports device and cause; the UI shows a warning; startup prints it |
| F5 | **Conversation continuity** — history endpoint + UI restore | S–M | relaunching restores the recent thread instead of a blank welcome |
| F6 | **Real logging** — rotating file log, not 90 `print()` calls | S | `data/dexter.log` captures startup, routing, tool calls, and errors |
| F7 | **Wake-word truth** — docs match `hey_jarvis`; phrase configurable | S | README and `.env.example` state the actual phrase |
| F8 | **One command to start**, health-checked | S | a single script brings up model + backend + UI and reports what is missing |
| F9 | **Settings without restarts** — runtime prefs endpoint + panel | M | voice, provider, quiet hours, barge-in changeable from the UI |

---

## Part 1 — The Spine (S) — *the actual product*

One substrate, four surfaces. Everything below reads and writes the same store.

```
Goal         the outcome you want            "ship the channel relaunch"
 └─ Project  a body of work under a goal     "record 8 videos"
     └─ Commitment  something you said       "script ep.3 by Friday"
         └─ Entry   what actually happened   "worked 90m, blocked on b-roll"
```

| # | Item | Effort | Acceptance |
|---|---|---|---|
| S1 | **`backend/workbook.py`** — the store: goals, projects, commitments, entries; SQLite alongside memory; embedded for recall | M | entities survive restarts; every write is atomic; planner tasks link to commitments |
| S2 | **Tools over the spine** — set/list/advance goals, open projects, make and keep commitments, log progress, ask for status | M | "I'm committing to X by Friday" and "how am I doing on the relaunch" both work by voice |
| S3 | **The daily loop** — morning plan (not a task count), midday nudge, **evening close-out**, **weekly review** | M–L | the close-out asks what happened, records it, and rolls the rest with a decision — not silently |
| S4 | **Coaching signal** — slip detection, streaks, stale-commitment challenge, honest weekly scorecard | M | "this has moved six days running — is it still real?" fires on the seventh |
| S5 | **Surfaces** — a workbook panel in the UI: goals, this week, what slipped, what got done | M | the window answers "where am I" without being asked |

**Acceptance for Part 1:** a full week can be run through Dexter — commitments
made by voice, progress logged, an evening close-out every day, a Sunday review
that tells the truth — without touching a JSON file by hand.

---

## Part 2 — Missing Capabilities (C)

| # | Item | Effort | Acceptance |
|---|---|---|---|
| C1 | **Web search** — the assistant currently cannot answer anything about the world | M | "what's the weather", "look up X" return real, cited results |
| C2 | **File mutation** — rename, move, copy, delete (to Recycle Bin), all pathguarded | S–M | the README's own example — *"find the invoice, rename it, remind me at 5"* — actually runs |
| C3 | **Calendar/email completion** — reschedule, cancel, reply, search | M | "move my 3pm to 4" works |

---

## Part 3 — Reliability (R)

| # | Item | Effort | Acceptance |
|---|---|---|---|
| R1 | `pathguard` + `jsonstore` tests — the security and persistence boundaries, both untested | S | escape attempts and write failures are covered |
| R2 | Frontend smoke tests (v3 item 7.4 — deps installed, zero tests written) | S–M | app mounts, sends a message against a mocked `api.ts`, renders streamed tokens |
| R3 | One check command (v3 item 7.5) | S | `npm run check` runs pytest + tsc + vitest green in under two minutes |
| R4 | Workbook tests | S | the daily loop and slip detection are covered under frozen time |

---

## Not doing (and why)

- **Packaging / installer** — worth doing, but it distributes friction faster
  than it removes it. Revisit after Part 1 is real.
- **Training a custom "Dexter" wake word** — decided against; `hey_jarvis`
  stays and the docs stop lying about it.
- **Retiring the browser speech fallback** — keep it until the local pipeline
  has run a full week without falling back.

## Decision log (additions to v3's)

| # | Decision | Resolution |
|---|---|---|
| D7 | Four roles, or one first? | **All four, one spine.** They are surfaces on the same store; building them separately is what produced four half-features. |
| D8 | Where does the spine live? | **SQLite next to memory**, not JSON. Relationships and history are the whole point; `jsonstore` is for flat state. |
| D9 | GPU contention with the `.buzzcode` llama-server | **Detect and report, do not manage.** Dexter says it is degraded and why; freeing the GPU stays the user's call. |
