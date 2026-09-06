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
