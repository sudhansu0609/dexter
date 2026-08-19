# Dexter Roadmap v3 — From Rebuilt Assistant to Local-First Jarvis

**Status date:** 2026-08-19
**Replaces:** the previous IMPROVEMENT_PLAN.md (2026-08-12, stale) and OPTIMIZATION_PLAN.md (deleted; its 19 items are triaged into Phase 6 below).
**How Dexter works today:** see `README.md` — it is accurate and current.

## Vision

A voice-driven personal coworker / second brain on Windows 11, running primarily
on local LLMs (LM Studio / Ollama), that:

- **Feels alive** — streams tokens and speech instantly, never dead-ends when a
  local model stalls.
- **Remembers everything relevant** — semantic memory over facts, conversations,
  and documents, recalled without exact keywords.
- **Listens properly** — fully local wake word, VAD, and STT; barge-in that
  actually interrupts.
- **Handles tasks on my behalf** — background multi-step tasks with progress
  reporting, recorded replayable macros, proactive briefings, and
  voice-scheduled recurring jobs.

**Effort key:** S = under half a day · M = 1–3 days · L = a week-ish (solo, incremental)

---

## Current state (ground truth, 2026-08-19)

### Delivered in the v2.0.0 rebuild — ⚠️ still uncommitted (~3,200 lines, 20 new files)

- **3-tier router** (`backend/router.py`, 915 lines): regex fast path + chitchat
  (~1ms) → semantic match with deterministic slot filling (~15ms) → constrained
  ReAct agent loop (max 5 iterations, top-8 tool schemas, text-JSON tool-call
  parser for small models). 90 regression tests in `backend/test_router.py`.
- **Tool registry** (`backend/tool_registry.py`): one `@tool` decorator →
  OpenAI schema + Tier-1 example corpus + validated callable; uniform
  `ToolResult`; `execute()` never raises.
- **37 registered tools**: app launch, web, files, planner, notes, YouTube,
  Windows media control (SMTC), volume/brightness/clipboard/screenshot/window
  management/lock/sleep, real Google Calendar + Gmail OAuth, reminders, memory
  facts, BuzzcafAI client.
- **Memory** (`backend/memory.py`): stdlib SQLite (conversations + facts, WAL),
  keyword recall, last-8-turn history, facts injected into system prompt,
  nightly prune. *Not yet semantic — see Phase 3.*
- **Scheduler** (`backend/scheduler.py`): APScheduler (optional dep), reminders
  persisted + re-armed on boot, missed-reminder catch-up, recurrence,
  maintenance crons, 06:00 morning brief.
- **Voice output**: Kokoro ONNX + prosody layer (`backend/prosody.py`) — 8
  emotion profiles, text normalisation, chunked synthesis, **~450ms
  time-to-first-audio** (was 4849ms).
- **Notifications**: 3 channels (voice, Windows toast, in-app banner).
- **Hygiene**: atomic JSON stores (`backend/jsonstore.py`), CORS locked +
  loopback bind, `.env` untracked, cached LLM health checks (30s TTL).
- **Tests**: 90 router tests (pure logic) + 20 live E2E checks
  (`test_dexter_suite.py`).

### Corrections to previously stale claims

- `backend/main.py` is **712 lines** (not "~560, transport only") — ~60
  endpoints, 20 BuzzcafAI-specific.
- `backend/buzzcaf_client.py` is **541 lines**, hardcodes `../BuzzcafAI` /
  `../MidnightBuzz` sibling paths, and spawns the sibling backend on `0.0.0.0`.
- `sentence-transformers` is **not installed** — Tier 1 runs in the weaker
  lexical fallback. (Decision D1: install — see Decision Log.)
- Legacy modules (`dexter_tools.py`, `filesystem_tool.py`,
  `spilled_coffee_controller.py`, `skill_engine.py`) are **wrapped, not
  replaced** — still imported by `main.py`.
- The skill engine is **dead code**: `match_skill` / `learn_from_tool_calls`
  are never called; skills are display-only.
- `HybridLLMRouter.stream()` is **dead code** — the "streaming" chat endpoint
  runs the full route, then streams TTS audio chunks only. No token streaming.
- Voice **input** is browser `webkitSpeechRecognition` (Chrome's cloud
  recognizer) with a client-side wake word — no server STT, no VAD.

### Verified bugs (fixed in Phase 0)

| # | Bug | Location |
|---|---|---|
| 1 | `curate_video_ideas` treats the controller's dict return as a list → always `TypeError` ("Tool failed") | `backend/tools_core.py:377` |
| 2 | Skill engine dead code; junk legacy file `skills/user_command_reasoning.json` | `backend/skill_engine.py` |
| 3 | Confirmation state `_pending` is process-global → a stale or cross-session "yes" can trigger a forgotten destructive call | `backend/router.py:609` |
| 4 | `fast_path` returns `None` for the whole tier when a matched rule's arg builder fails, instead of falling through to later rules/tiers | `backend/router.py:224` |
| 5 | Raw truncate-on-open JSON writes bypass `jsonstore` atomics | `backend/spilled_coffee_controller.py:17` |
| 6 | `_local_timezone` returns abbreviations like `"IST"` where the Calendar API needs IANA zones (`Asia/Kolkata`) → wrong event times | `backend/google_tools.py:262` |
| 7 | Data drift: stale `day_planner.json` date, fired reminders never pruned, duplicate `overnight_content.json` entries — despite "maintenance jobs" | `backend/scheduler.py`, `data/*.json` |
| 8 | Frontend: invalid CSS `justifyContent:'between'` (line 1078), dead `provider`/`modelsStatus` state, stale `provider` closure (~line 582), blurry non-DPR canvas | `src/App.tsx` |

---

## Phase 0 — Foundation & Hygiene (M)

**Goal:** clean committed baseline, one accurate living roadmap, zero known bugs.

- [x] **0.1 Commit the rebuild** in logical groups (S): *(done 2026-08-19)*
  1. `chore:` gitignore `.pytest_cache/` + runtime `data/*.json` (keep seeds/`.gitkeep`); `.env` removal stays, `.env.example` in
  2. `feat(core):` `tool_registry.py`, `router.py`, `events.py`, `jsonstore.py`, `timeparse.py`
  3. `feat(llm):` `hybrid_llm.py`
  4. `feat(tools):` `tools_core.py`, `tools_assistant.py`, `windows_tools.py`, `media_tools.py`, `google_tools.py`, `app_index.py`, `buzzcaf_client.py`
  5. `feat(voice):` `prosody.py`, `voice_engine.py`
  6. `feat(memory+scheduler):` `memory.py`, `scheduler.py`, `notify.py`
  7. `feat(api):` `main.py`, `src/App.tsx`, modified legacy modules
  8. `test:` `backend/test_router.py`, `test_dexter_suite.py`
  9. `docs:` README + this roadmap
- [x] **0.2 Fix bugs 1–8** (M total, each S). *(done 2026-08-19; bug 3's
      per-session + TTL fix also completes Phase 1.5)* Bug 6 via `zoneinfo`/`tzlocal`
      (emit IANA names). Bug 7 via real maintenance jobs: prune fired reminders
      >7 days, dedup overnight content on write, roll planner date at 00:05.
      Bug 2 for now: delete the junk skill file; the redesign is Phase 5.3.
- [ ] **0.3 Legacy module retirement** (S): fold `dexter_tools.py` /
      `spilled_coffee_controller.py` logic into `tools_core.py`/`jsonstore.py`
      opportunistically; `filesystem_tool.py` gets rewritten naturally by the
      Phase 1 pathguard work.

**Acceptance:** `git status` clean · `pytest backend/test_router.py` green ·
`python test_dexter_suite.py` green · this file is the only plan doc · each bug
fix references its row above.

---

## Phase 1 — Security Hardening (M) — depends on 0

**Goal:** safe-by-default even if the loopback assumption ever breaks;
destructive actions session-scoped and confirmed.

- [x] **1.1 Localhost token auth** (S–M): random token generated at startup,
      required as `X-Dexter-Token` on every endpoint except health; frontend
      obtains it via Electron preload / bootstrap. Pairs with the `src/api.ts`
      extraction (Phase 6) — do together. *(done 2026-08-19: per-run token +
      Host-header check middleware in `main.py`; minimal `src/api.ts` with token
      bootstrap, 401-retry, and SSE query-param auth; TTS audio clips exempt
      because `Audio()` cannot send headers)*
- [x] **1.2 Path allowlist** (M): new `backend/pathguard.py` — canonicalized
      allowlist roots (Documents, Desktop, configured project dirs; `.env`
      configurable) applied to the file **tools** and the raw
      `/api/dexter/filesystem/*` **endpoints**. Denials return an explanatory
      `ToolResult` so the agent can tell the user why. *(done 2026-08-19:
      enforced inside `FileSystemTool`, the choke point both paths share)*
- [x] **1.3 Remove `shell=True`** (S): sweep `tools_core.py`,
      `dexter_tools.py`, `windows_tools.py`; use arg lists / `os.startfile`.
      *(done 2026-08-19 — launcher rebuilt on `os.startfile`/ShellExecute with a
      `shutil.which` fallback; bat/npm spawns use arg lists)*
- [x] **1.4 Sibling spawn fix** (S): `buzzcaf_client.py` spawns the sibling
      uvicorn on `127.0.0.1` (not `0.0.0.0`); sibling paths via env vars.
      *(done 2026-08-19: `BUZZCAF_DIR` / `MIDNIGHTBUZZ_DIR` in `.env`)*
- [x] **1.5 Per-session confirmations + TTL** (S): completes bug 3 — key
      `_pending` by session id, expire after ~120s, support explicit cancel.
      *(done 2026-08-19 as part of the Phase 0 bug fixes)*
- [x] **1.6 CORS tightening** (S): drop the any-`file://` origin allowance;
      restrict to the Vite dev origin + Electron origin. *(done 2026-08-19:
      explicit origin list, credentials disabled)*

**Acceptance:** token-less request → 401 · `read_file` on a system path →
polite denial · `grep -r "shell=True" backend/` empty · sibling binds loopback ·
a confirmation issued in session A cannot be consumed in session B.

---

## Phase 2 — Local-LLM Excellence (L) — depends on 0

**Goal:** local models feel instant and never dead-end. Highest-leverage phase
for the Jarvis feel.

- [ ] **2.1 True token streaming** (M): wire up the dead
      `HybridLLMRouter.stream()` in `hybrid_llm.py`; new SSE event types in
      `events.py` (`token`, `tool_call`, `tool_result`, `tts_chunk`,
      `provider_switched`, `done`); rework the chat endpoint to interleave —
      tokens to the UI immediately, completed sentences into the existing
      prosody chunker so speech starts before generation finishes. Frontend
      renders incremental tokens. Streams must be **cancellable** (Phase 4
      barge-in depends on this).
- [ ] **2.2 Mid-request fallback** (M): on stall/connection-reset from LM
      Studio/Ollama mid-generation, retry against the next provider in the
      chain; emit `provider_switched`. Gemini path: give it tool-calling parity
      or explicitly mark it chat-only in provider selection.
- [ ] **2.3 Neural Tier 1** (S): `pip install sentence-transformers`
      (**Decision D1: approved**) — `router.py` auto-upgrades from lexical to
      `all-MiniLM-L6-v2`. Shared with Phase 3 memory embeddings.
- [ ] **2.4 Function-calling eval harness** (M): `backend/test_agent_eval.py`
      modeled on `test_router.py`'s parametrized style — N prompts → expected
      tool + args, run against the live local model, score exact-tool /
      valid-args / iterations-used. Compare Qwen2.5-7B vs alternatives
      objectively.
- [ ] **2.5 Cleanup** (S): remove now-dead imports (`split_sentences` in
      `main.py`, `inspect` in `tool_registry.py`).

**Acceptance:** first visible token <1s and first audio <1.5s for a local
chitchat prompt · killing LM Studio mid-response still completes the answer via
fallback · eval harness produces a per-model scoreboard.

---

## Phase 3 — Second-Brain Memory (L) — depends on 2.3

**Goal:** Dexter remembers everything relevant and recalls it semantically.

- [ ] **3.1 Vector store** (**Decision D2: sqlite-vec**): loadable extension on
      the existing SQLite DB — zero services, one file. Fallback if the Windows
      wheel misbehaves: numpy brute-force cosine over an embeddings BLOB column
      (fine at this scale).
- [ ] **3.2 Semantic recall** (M): embeddings column for facts + conversation
      summaries in `memory.py`; shared embedding singleton extracted to
      `backend/embeddings.py` (used by router Tier 1 **and** memory); recall =
      vector top-k blended with the existing exact/LIKE path.
- [ ] **3.3 Auto-fact extraction** (M): nightly/idle local-LLM pass over recent
      turns → candidate facts, deduped against existing facts by embedding
      similarity, stored with source-turn provenance. Hooked into
      `scheduler.py`.
- [ ] **3.4 Daily/weekly summaries** (S–M): scheduler crons summarizing each
      day's conversations into dated rows; weekly rollup — the retrievable
      episodic memory, feeding the 06:00 morning brief.
- [ ] **3.5 Document ingestion** (M): `remember_document(path)` tool — chunk,
      embed, store with source path (pathguard applies); recall cites the
      source. Start with `.txt`/`.md`; PDFs later.

**Acceptance:** "what did I say about X last week" retrieves semantically ·
facts accumulate without duplicates · retrieval adds <150ms to routing.

---

## Phase 4 — Local Voice Pipeline (L) — depends on 2.1 (**Decision D3: full local, openWakeWord**)

**Goal:** replace browser `webkitSpeechRecognition` with wake word → VAD → STT
→ barge-in, fully offline.

- [ ] **4.1 Server-side STT** (M): new `backend/stt_engine.py` with
      **faster-whisper** (small/base, int8 — fine on CPU, better on GPU).
- [ ] **4.2 Capture transport** (M): mic capture stays in Electron; 16 kHz PCM
      frames stream to the backend over a new **websocket** route in `main.py`
      (the app's first WS — SSE remains the outbound channel).
- [ ] **4.3 Wake word** (S–M): **openWakeWord** (Apache-2.0, fully local,
      trainable "Dexter" model). Swap to Porcupine only if the false-accept
      rate annoys in practice.
- [ ] **4.4 VAD** (S): **silero-vad** (tiny torch model — torch already present
      after D1) gates STT so whisper only runs on speech segments.
- [ ] **4.5 Barge-in** (M): VAD-active during TTS playback → `interrupt` event
      → frontend pauses audio, backend cancels the generation stream (2.1).
- [ ] **4.6 Retire webkitSpeechRecognition** (S): keep as a fallback toggle for
      one release, then delete from `src/App.tsx`.

**Acceptance:** wake word works with the window unfocused · speech-end → first
reply audio <2.5s locally · "Dexter, stop" halts speech within ~300ms.

---

## Phase 5 — Agentic Autonomy (L+) — depends on 1, 2, 3 (**Decision: all four capabilities approved**)

**Goal:** "handle tasks on my behalf" — background execution, macros,
proactivity, scheduled automations.

- [ ] **5.1 Task queue + background execution** (M): new `backend/tasks.py` —
      asyncio queue with persisted task records
      (queued / running / waiting-confirmation / done / failed), progress events
      over the existing SSE bus; submit/list/cancel endpoints; frontend task
      tray (Phase 6).
- [ ] **5.2 Multi-step plans with checkpoint confirmations** (M–L): extend the
      Tier-2 loop — multi-step tasks first produce a step plan, execute as a
      background task, and **pause at destructive steps** using the Phase-1
      per-session confirmation mechanism ("I'm about to send this email — go
      ahead?"). Voice-confirmable via Phase 4.
- [ ] **5.3 Skill engine → recorded macros** (M): replace the dead auto-learn
      design with **explicit recording** — "Dexter, record a skill" → capture
      the tool-call sequence with parameter slots → save to `skills/*.json` →
      replay by name with slot filling (reuses `router.py`'s deterministic slot
      filler). Explicit beats silent learning for trust and debuggability.
- [ ] **5.4 Proactive suggestions** (M): periodic scheduler job — calendar next
      2h + priority unread email (`google_tools.py`) + overdue planner items →
      suggestion events → banner/voice via `notify.py`, with per-category
      **quiet hours and rate limits** (proactivity that nags gets disabled;
      conservative and configurable from day one).
- [ ] **5.5 Voice-scheduled recurring jobs** (S–M): "every Friday at 5, curate
      video ideas and email me the list" → existing `timeparse` recurrence +
      scheduler + task queue.
- [ ] **5.6 Guardrails** (S): max tool calls per background task, scope limits
      on cloud fallback, every step auditable in the task record.

**Acceptance:** a scheduled morning task checks calendar + email and briefs by
voice with progress visible · a recorded 3-step macro replays with a new
parameter · a destructive step blocks until confirmed · suggestions respect
quiet hours.

---

## Phase 6 — Frontend Triage (M–L) — scheduled alongside 1 / 2 / 5

Triage of the former OPTIMIZATION_PLAN.md's 19 items against the Jarvis vision:

| Old # | Item | Verdict |
|---|---|---|
| 11 | Centralize API into `src/api.ts` (+ auth token from 1.1, SSE client) | **Do first** (S) — prerequisite for everything else |
| 1 | Split `App.tsx` monolith → `src/components/` (Chat, VoiceOrb, TaskTray, Settings, Reminders, Planner, Buzzcaf) | **Do** (M) |
| 3 | TS interfaces for API responses | **Do** with 11 (S) |
| 8 | Fix empty catch blocks | **Do** during split (S) |
| 9 | Extract custom hooks | **Do** during split (S) |
| 10 | Error boundary | **Do** (S) |
| 17 | Offline detection / SSE reconnect | **Finish** (partially done: 4s auto-reconnect) (S) |
| 15 | Loading states | **Do** where tasks/tools run (S) |
| — | Token-stream rendering | **Do** — Phase 2.1 counterpart (S) |
| — | Task tray + confirmation dialogs | **Do** — Phase 5.1/5.2 counterpart (M) |
| — | DPR-aware canvas, remove dead `provider`/`modelsStatus` state | **Do** — bug 8, Phase 0 (S) |
| 4, 5, 7 | Memoize, throttle canvas, virtualize lists | **Defer** until a measured problem |
| 12 | electron-builder packaging | **Defer** to a "distribution" milestone |
| 13, 14 | Sandbox/GPU flags, window-state persistence | **Defer** |
| 2 | Extract 300+ inline styles | **Defer indefinitely** (cosmetic) |
| 18 | Keyboard shortcuts | **Defer** |
| 16 | Externalize config | **Subsumed** by `src/api.ts` (the stale `:8080` note is void — it's always `:8098`) |
| 6 | Debounce polling | **Obsolete** — polling replaced by SSE |
| 19 | Frontend tests | **Moved** to Phase 7.4 |
| — | i18n (old 10.5) | **Dropped** (Decision D5) |

**Acceptance:** no component over ~300 lines · `:8098` appears in exactly one
place (`src/api.ts`) · UI shows live token stream and background-task progress.

---

## Phase 7 — Testing & Reliability (M, threaded through all phases)

- [ ] **7.1 Agent-loop unit tests** (M): `backend/test_agent_loop.py` — mocked
      LLM responses driving Tier 2: iteration cap, malformed-JSON recovery,
      confirmation flow, fast-path fall-through (regression for bug 4).
- [ ] **7.2 Scheduler tests** (S): `backend/test_scheduler.py` — frozen time:
      re-arm on restart, missed-reminder catch-up, prune/dedup jobs
      (regression for bug 7).
- [ ] **7.3 Memory tests** (S): recall precision on a small fixture set; fact
      dedup.
- [ ] **7.4 Frontend smoke** (S–M): Vitest + Testing Library — app mounts,
      sends a message against a mocked `src/api.ts`, renders streamed tokens.
      Minimal, not coverage-chasing.
- [ ] **7.5 One check command** (S): script running pytest + vitest + tsc,
      green in <2 min without a live LLM (the live E2E suite stays separate).

---

## Decision log

| # | Decision | Resolution (2026-08-19) |
|---|---|---|
| D1 | Install sentence-transformers (~2.5 GB torch) | **Yes** — powers neural Tier-1 routing AND memory embeddings; silero-vad reuses torch |
| D2 | Vector store | **sqlite-vec** (extension of existing SQLite, zero services); numpy brute-force fallback |
| D3 | Voice input | **Full local pipeline**: faster-whisper + **openWakeWord** (Apache-2.0, no account) + silero-vad; Porcupine only if accuracy demands |
| D4 | Runtime `data/*.json` in git | **Gitignore** runtime data, keep seeds |
| D5 | i18n | **Dropped from roadmap** |
| D6 | Autonomy scope | **All four**: background multi-step tasks, proactive briefings, recorded macros, scheduled jobs |

## Milestones

| Phase | Theme | Effort | Depends on | Jarvis payoff |
|---|---|---|---|---|
| 0 | Hygiene, commits, 8 bugs | M | — | Stable baseline |
| 1 | Security | M | 0 | Safe autonomy later |
| 2 | Streaming + fallback + neural routing + eval | L | 0 | Feels alive; local-first delivered |
| 3 | Semantic memory | L | 2.3 | Second brain |
| 4 | Local STT / wake word / VAD / barge-in | L | 2.1 | True voice assistant |
| 5 | Background tasks, macros, proactivity | L+ | 1, 2, 3 | "Handles tasks for me" |
| 6 | Frontend triage | M–L | with 1/2/5 | Usable cockpit |
| 7 | Tests | M | ongoing | Doesn't regress |

**Cadence (solo, incremental):** Phases 0–1 in week one → Phase 2 the next 1–2
weeks → Phase 3 before Phase 4 (memory compounds daily) → Phase 5 once 1+2+3
exist. Rough horizon: **6–10 weeks** of evenings/weekends to Phase 5.
