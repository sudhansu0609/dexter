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
- [x] **0.3 Legacy module retirement** (S): fold `dexter_tools.py` /
      `spilled_coffee_controller.py` logic into `tools_core.py`/`jsonstore.py`
      opportunistically; `filesystem_tool.py` gets rewritten naturally by the
      Phase 1 pathguard work. *(done 2026-08-19: both modules folded into
      `tools_core.py` verbatim and deleted; the dead `BuzzcafAITool` class was
      dropped — `buzzcaf_client.py` is the real integration. `filesystem_tool.py`
      stays: pathguard (1.2) already rewrote its enforcement core.)*

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

- [x] **2.1 True token streaming** (M): *(done 2026-08-19)* `chat(on_token=…)`
      in `hybrid_llm.py` supersedes the dead `stream()` (removed); the agent
      loop forwards deltas through a `_TokenGate` that suppresses text-protocol
      JSON tool calls; the stream endpoint emits `token` events and feeds
      completed sentences to a dedicated TTS thread so speech starts before
      generation finishes; frontend renders the live token stream and finalizes
      on `reply`. Reasoning models' `reasoning_content` deltas are correctly
      not streamed as answer text. *Cancellation is cooperative at sentence
      granularity today — full cancel lands with Phase 4 barge-in.*
- [x] **2.2 Mid-request fallback** (M): *(done 2026-08-19)* `_provider_chain`
      retries the whole request on the next healthy provider when one fails
      (including mid-stream drops — partial text is discarded, the UI clears it
      via `provider_switched`); an explicit provider choice is honoured without
      fallback. Gemini is **chat-only**: skipped whenever tools are required.
- [x] **2.3 Neural Tier 1** (S): *(done 2026-08-19)* sentence-transformers
      installed; router runs `all-MiniLM-L6-v2` (257 phrases over 46 tools);
      all routing tests pass in neural mode.
- [x] **2.4 Function-calling eval harness** (M): *(done 2026-08-19)*
      `backend/test_agent_eval.py` — 15 tool prompts + 2 chat-restraint
      prompts, first tool call captured (nothing executes), scores exact-tool /
      valid-args / restraint / latency. Skipped unless `DEXTER_RUN_EVALS=1`;
      also runs standalone via `python backend/test_agent_eval.py`.
- [x] **2.5 Cleanup** (S): *(done 2026-08-19)* dead `stream()` method and
      unused `split_sentences` / `inspect` imports removed.

**Acceptance:** first visible token <1s and first audio <1.5s for a local
chitchat prompt · killing LM Studio mid-response still completes the answer via
fallback · eval harness produces a per-model scoreboard.

---

## Phase 3 — Second-Brain Memory (L) — depends on 2.3

**Goal:** Dexter remembers everything relevant and recalls it semantically.

- [x] **3.1 Vector store** (**Decision D2: sqlite-vec**): *(done 2026-08-19)*
      sqlite-vec v0.1.9 loads on this machine; distances computed SQL-side via
      `vec_distance_cosine` over a plain BLOB column, with a numpy scan
      fallback over the same column when the extension fails to load. One
      schema, two search paths.
- [x] **3.2 Semantic recall** (M): *(done 2026-08-19)* new
      `backend/embeddings.py` shared MiniLM singleton (router Tier 1 + memory
      use one model instance); `memory.py` embeds facts, summaries, and
      document chunks; `recall()` blends exact → LIKE → semantic → token
      overlap; `semantic_context()` injects request-relevant memories into
      every agent prompt (~6ms on the live DB); startup backfill embeds
      pre-existing rows.
- [x] **3.3 Auto-fact extraction** (M): *(done 2026-08-19)* new
      `backend/reflection.py` — LLM pass over recent turns promotes durable
      facts with `auto:YYYY-MM-DD` provenance, deduped by embedding similarity
      (threshold 0.82, calibrated on MiniLM). Runs nightly at 23:40 and
      backfills at startup for machines asleep at night. Live test: 4 genuine
      facts extracted, ~1 duplicate on re-run.
- [x] **3.4 Daily/weekly summaries** (S–M): *(done 2026-08-19)* day summaries
      (23:40 cron + startup catch-up, idempotent per date), Sunday weekly
      rollup, both embedded for episodic recall; the 06:00 morning brief now
      opens with yesterday's summary.
- [x] **3.5 Document ingestion** (M): *(done 2026-08-19)* `remember_document`
      tool — pathguard-checked, text-like files up to 400KB, paragraph-packed
      ~1000-char chunks, recalled with the file cited as source. PDFs later.

**Acceptance:** "what did I say about X last week" retrieves semantically ·
facts accumulate without duplicates · retrieval adds <150ms to routing.

---

## Phase 4 — Local Voice Pipeline (L) — depends on 2.1 (**Decision D3: full local, openWakeWord**)

**Goal:** replace browser `webkitSpeechRecognition` with wake word → VAD → STT
→ barge-in, fully offline.

- [x] **4.1 Server-side STT** (M): new `backend/stt_engine.py` with
      **faster-whisper** (small/base, int8 — fine on CPU, better on GPU).
      *(done 2026-08-19: lazy-loaded singleton, graceful degrade when absent)*
- [x] **4.2 Capture transport** (M): mic capture stays in Electron; 16 kHz PCM
      frames stream to the backend over a new **websocket** route in `main.py`
      (the app's first WS — SSE remains the outbound channel). *(done
      2026-08-19: `src/voice/localPipeline.ts` — AudioWorklet capture at 16 kHz,
      int16 frames over `/api/dexter/voice/ws` with token auth, auto-reconnect)*
- [x] **4.3 Wake word** (S–M): **openWakeWord** (Apache-2.0, fully local,
      trainable "Dexter" model). Swap to Porcupine only if the false-accept
      rate annoys in practice. *(done 2026-08-19: `backend/wakeword.py`,
      "hey jarvis" stock model until a "Dexter" model is trained)*
- [x] **4.4 VAD** (S): **silero-vad** (tiny torch model — torch already present
      after D1) gates STT so whisper only runs on speech segments. *(done
      2026-08-19: `backend/vad.py`, 32 ms chunks as the pipeline clock)*
- [x] **4.5 Barge-in** (M): VAD-active during TTS playback → `interrupt` event
      → frontend pauses audio, backend cancels the generation stream (2.1).
      *(done 2026-08-19: `backend/cancellation.py` flag threaded through
      router → hybrid_llm stream loop; frontend reports TTS state via the
      `speaking` control message and kills playback on `interrupt`)*
- [x] **4.6 Retire webkitSpeechRecognition** (S): keep as a fallback toggle for
      one release, then delete from `src/App.tsx`. *(done 2026-08-19: local
      pipeline is the default engine; webkit remains the automatic fallback
      when STT/VAD deps are missing, forceable via localStorage
      `dexterVoiceEngine` = 'browser' | 'local'. Delete next release.)*

**Acceptance:** wake word works with the window unfocused · speech-end → first
reply audio <2.5s locally · "Dexter, stop" halts speech within ~300ms.

---

## Phase 5 — Agentic Autonomy (L+) — depends on 1, 2, 3 (**Decision: all four capabilities approved**)

**Goal:** "handle tasks on my behalf" — background execution, macros,
proactivity, scheduled automations.

- [x] **5.1 Task queue + background execution** (M): *(done 2026-08-19)* new
      `backend/tasks.py` — single worker thread (two tasks fighting over one
      desktop is a bug, not a feature), records persisted to `data/tasks.json`,
      `task` events on the SSE bus, submit/list/cancel/confirm endpoints, task
      tray in the UI, and spoken announcements on finish/checkpoint via the
      existing reminder channel.
- [x] **5.2 Multi-step plans with checkpoint confirmations** (M–L): *(done
      2026-08-19)* plan-then-execute — the LLM plans steps, each step runs
      through the normal three-tier router in a task-scoped session, so a
      destructive step parks the task in `waiting_confirmation` via the
      Phase-1 mechanism. Approval comes from tray buttons, the confirm
      endpoint, or a spoken yes/no (routed to the single waiting task when the
      chat has no pending confirmation of its own). Verified live: 3-step plan
      executed with progress and a correct final summary.
- [x] **5.3 Skill engine → recorded macros** (M): *(done 2026-08-19)*
      `skill_engine.py` deleted; new `backend/macros.py` — "record a skill
      called X" captures executed tool calls (via a `TOOL_OBSERVERS` hook in
      `_run_tool`), "stop recording" saves to `skills/*.json` with the first
      step's longest string arg as the substitutable slot; `run_skill` is
      confirm=True so one go-ahead covers the recorded sequence. Legacy prose
      skills list as non-executable.
- [x] **5.4 Proactive suggestions** (M): *(done 2026-08-19)* new
      `backend/proactive.py` — 15-min scan of calendar (next 2h), unread email
      (hourly at most), overdue planner items; banner/toast only, voice
      reserved for events <20 min away; quiet hours (`DEXTER_QUIET_HOURS`),
      per-category cooldowns, same-day dedup, master off-switch
      (`DEXTER_PROACTIVE=0`).
- [x] **5.5 Voice-scheduled recurring jobs** (S–M): *(done 2026-08-19)*
      `schedule_task` tool → `timeparse` recurrence/one-shot →
      `data/scheduled_tasks.json`, re-armed on boot, fires goals into the task
      queue; list/cancel by voice.
- [x] **5.6 Guardrails** (S): *(done 2026-08-19)* 8-step plan cap, 16 tool
      calls per task, 30-min checkpoint timeout fails safe, full audit trail
      (plan, per-step outcomes, timestamps, tool-call count) in the task
      record.

**Acceptance:** a scheduled morning task checks calendar + email and briefs by
voice with progress visible · a recorded 3-step macro replays with a new
parameter · a destructive step blocks until confirmed · suggestions respect
quiet hours.

---

## Phase 6 — Frontend Triage (M–L) — ✅ done 2026-08-19

Triage of the former OPTIMIZATION_PLAN.md's 19 items against the Jarvis vision:

| Old # | Item | Verdict |
|---|---|---|
| 11 | Centralize API into `src/api.ts` (+ auth token from 1.1, SSE client) | **✅ Done** — every endpoint is a typed fn in `src/api.ts`; nothing else builds a URL or names the port |
| 1 | Split `App.tsx` monolith → `src/components/` (Chat, VoiceOrb, TaskTray, Settings, Reminders, Planner, Buzzcaf) | **✅ Done** — 11 components; `App.tsx` is now 205 lines of composition |
| 3 | TS interfaces for API responses | **✅ Done** — all shapes in `src/types.ts`, shared by hooks + components |
| 8 | Fix empty catch blocks | **✅ Done** — no `catch (e) {}` remains in `src/` |
| 9 | Extract custom hooks | **✅ Done** — `src/hooks/`: useVoice, useChat, useServerEvents, usePlanner, useOvernight, useSkills, useBuzzcaf, useFileSearch, useOnlineStatus |
| 10 | Error boundary | **✅ Done** — `ErrorBoundary` wraps `<App/>` in `main.tsx` |
| 17 | Offline detection / SSE reconnect | **✅ Done** — `useOnlineStatus` + offline badge in header; SSE keeps the 4s reconnect and now tracks `connected` |
| 15 | Loading states | **✅ Done** — planner-add, curate, buzzcaf step/start, file search, and the thinking/tool-activity label all surface progress |
| — | Token-stream rendering | **Do** — Phase 2.1 counterpart (S) |
| — | Task tray + confirmation dialogs | **Done** with Phase 5 (2026-08-19) |
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

*(done 2026-08-19)* The 1,516-line `App.tsx` is gone: every panel is a
presentational component in `src/components/` (largest is `VoiceVisualizer`, 183
lines) and every unit of behaviour is a hook in `src/hooks/`. `App.tsx` is 205
lines of pure composition; two shared refs (`isThinkingRef`,
`commandHandlerRef`) break the voice↔chat cycle without a god-object.
`tsc --strict` + `vite build` are green. **Deliberate exception to the ~300-line
rule:** `useVoice.ts` is 384 lines. The hands-free loop (continuous recognition,
wake word, follow-up window, echo suppression, barge-in, TTS + streamed-audio
queue) is one irreducibly-coupled state machine over ~8 shared refs, has no
frontend tests yet (see 7.4), and was extracted verbatim to guarantee zero
behaviour change; splitting it would add cross-module ref plumbing and real
regression risk for a cosmetic win. Revisit once 7.4 gives it test coverage.

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
| D2 | Vector store | **sqlite-vec** (extension of existing SQLite, zero services); numpy brute-force fallback. *Confirmed working (v0.1.9) 2026-08-19.* |
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
