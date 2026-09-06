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
