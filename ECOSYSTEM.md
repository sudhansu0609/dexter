# Buzzcaf Ecosystem

How everything under `B:\youtubeProjects\Buzzcaf_Media` fits together, after the
v9 guardian work (2026-09-10). This file is kept **byte-identical** in `dexter/`
and `BuzzcafAI/`; edit one and copy it over the other.

Two rules govern the whole estate, and everything below is detail:

1. **Sentinel decides, everyone else asks.** Nothing here loads a model or
   starts a heavy job without asking Sentinel for the VRAM *and* RAM first, and
   every grant is released or consumed. Sentinel enforces the rest by class:
   freeze batch, evict *evictable* model servers, and never touch interactive or
   assistant processes except at hard-OOM.
2. **Dexter is Sentinel's hands in the user session.** It starts and stops the
   apps, unloads models cooperatively when told memory is tight, and never
   overrides a refusal — it explains it and offers the moves Sentinel allows.

---

## 1. The machine

RTX 5060 Ti 16 GB (16 050 MiB usable), i7-14700K (28 threads), 32 GB RAM,
commit limit 63.8 GB. Drives C: and B:, ~1 TB each.

On a 16 GB card **at most one** of {buzzcode's 27B, VrDeep, BuzzEdit's ComfyUI,
BuzzWorlds' UE editor} can run beside Dexter's small model. That single fact is
why the guardian exists, and why `exclusive_with` is in the app registry.

---

## 2. The port map

| Project | Role | Ports | Launch | Class | VRAM | RAM |
|---|---|---|---|---|---|---|
| `dexter/` | The assistant: chat, memory, knowledge, tools, and control of everything below | **8098** | `Dexter.vbs` (hidden `pythonw`), `start_dexter.bat` for a console | assistant | 3B ≈2.5 GB, 7B ≈5.5 GB | 1–2 GB |
| `BuzzcafAI/` | The YouTube studio: desktop app, 105 personas in 13 departments, workflows, BuzzBrain store | **8099** (steps forward if busy) | `Buzzcaf Studio.vbs`, `start_buzzcafai.bat` for dev, or Dexter starts it | interactive | WebView2 only | 0.5 GB |
| `BuzzBrain/` | Chrome extension pushing YouTube watch-page snapshots to the Studio | none | load unpacked in Chrome | — | — | — |
| `BuzzCode/` | The coder. CLI plus a `llama-server` it manages | **8089** | `buzzcode engine serve` | batch (assistant while the owner codes) | 12.1 GB | 6 GB cache |
| `BuzzEdit/` | Electron video editor + FastAPI + ComfyUI + LM Studio | **8099**(!), **8188**, **1234** | `START_APP.vbs` | batch | whisper 2–3 + ComfyUI 6–12 GB | 4–8 GB |
| `VrDeep/` | Render/deepfake pipeline: CustomTkinter + workers + ffmpeg nvenc | none | `launch_app.vbs` | batch | 9–11 GB | 14–15 GB commit |
| `MidnightBuzz/` | FastAPI + XTTS + Vite | **8095** (0.0.0.0!), **8096**, **3005** | `start_app.bat` | batch | XTTS/RVC 2–4 GB resident | 3–5 GB |
| `MastGaane/` | uv CLI + FastAPI + its own Chrome + Ollama; nightly at 00:30 | **8787** | `uv run mastgaane serve` | batch | whisper + Ollama 7B, 5–10 GB | 3 GB |
| `CaliberAI/` | FastAPI + Vite + Ollama/LM Studio | **8000**, **5173** | `CaliberAI.vbs` / `Stop CaliberAI.vbs` | interactive (its Ollama runner is batch) | via Ollama 5–9 GB | 1 GB |
| `BuzzFin/` | uv FastAPI behind an Edge kiosk | **8756** | `BuzzFin.bat` | interactive | none | 0.5 GB |
| `BuzzcafCoffee/` | Ops dashboard (FastAPI, optional Next) | **8010**, **3000** | `python ops/run.py --assistant` | interactive | none | 0.3 GB |
| `BuzzAnimate/` | Native wgpu app; **cargo builds on launch** | none | `BuzzAnimate.vbs` | interactive | 0.2–2 GB | 1–3 GB + cargo spike |
| `BuzzViolin/` | Tauri dev app (uses the mic) | **1420** | `BuzzViolin.bat` | interactive | none | 0.5 GB |
| `BuzzWorlds/` | UE 5.7 + Blender scripts | none | open the editor yourself | batch | 8–16 GB | 8–16 GB |
| `SmartCleaner/` | Disk cleaner: PySide6 GUI plus a headless CLI | none | `run.bat`, or `python -m smartcleaner --headless --json` | interactive | none | 0.2 GB |
| `missionRemider/` | Electron focus-block app with its own mission model | none | `start.cmd` | interactive | none | 0.3 GB |
| `Buzzcaf/` | Documents only — no app | — | — | — | — | — |
| `Sentinel/` | **The guardian.** LocalSystem service + user-session tray | `\\.\pipe\Sentinel` | Windows service | — | — | — |

Local model servers (none required to boot; all are probed):

| Server | Port | Notes |
|---|---|---|
| buzzcode `llama-server` | 8089 | Qwen3.8-27B, only up while buzzcode runs; Dexter's first choice when online |
| LM Studio | 1234 | Whatever is loaded; second choice. BuzzEdit may own it |
| Ollama | 11434 | `qwen2.5:7b` / `qwen2.5:3b`; third choice, and where Dexter's tiers live |
| OpenAI / Gemini | cloud | Only with a key configured; last |

Every number in the two tables above is a **preferred** port, not an address:
each app steps forward when its number is held and publishes where it landed.
Section 3 is the rule; this table is only what they ask for first.

**Port collisions to respect.** BuzzEdit's backend and the Studio both default
to **8099**; CaliberAI and BuzzEdit's dev server both want **5173**; CaliberAI
sits on **8000**, which is every other local app's default. None of those is
fatal any more — whoever gets there second takes the next port and says so — but
they are why the identity check in rule 4 exists: two apps on adjacent ports
that both answer `/health` are told apart by *what they say*, never by where
they are.

---

## 3. Ports — nothing hardcoded

The estate has more apps than free ports and several of them want the same
number: BuzzEdit's backend and the Studio both default to 8099, CaliberAI sits
on 8000, every Vite wants 5173. The rule that settles it (GUARDIAN_PLAN.md §11):
**a port is a wish, not a fact.**

1. **Preferred, never assumed.** Every listener reads `<APP>_PORT` (env or its
   own `.env`), defaulting to today's number. That is what it *asks* for.
2. **Step forward, never evict.** Try the preferred port, then
   `preferred+1 … preferred+20`, then let the OS choose (bind 0). "Busy" is
   decided by a real exclusive bind on `127.0.0.1`, not by a probe. **No
   launcher may kill whatever holds a port.**
3. **Publish.** Once its own health route answers, the app writes its entry into
   the shared ledger `%LOCALAPPDATA%\Buzzcaf\ports.json` (override
   `BUZZCAF_PORTS_FILE`): `{"<app>": {"port", "pid", "started_at", "health",
   "extra"}}`. Read-merge-write under `ports.json.lock` (retried, stale after
   5 s), written temp-then-replace, and removed on a clean exit — so one app's
   crash never loses another's entry. An app with its own runtime file (the
   Studio) keeps it **and** publishes.

   *Where to withdraw from.* Put the withdrawal in the server's **shutdown
   lifecycle** (FastAPI's lifespan / `on_event("shutdown")`), not only in
   `atexit` or a `finally` around `uvicorn.run`. On Windows a console control
   event — Ctrl+Break, closing the console window, the machine shutting down —
   ends the process from the CRT's default handler as soon as uvicorn's handler
   returns, and neither of those two ever runs. Ctrl+C is the one graceful stop
   where they do. Dexter, the Studio and BuzzEdit withdraw from the lifespan and
   keep `atexit` as a backstop. A leftover entry is not dangerous — rule 5 will
   not trust one whose pid is dead — but it costs every reader a probe.
4. **Identity.** Every health route answers
   `{"app": "<app>", "status": "ok", "port": n, "pid": n, "version": "…"}`. A
   scan that finds a listener but not the right `app` keeps scanning. This is
   the rule that stops CaliberAI being mistaken for the Studio, and BuzzEdit's
   backend for the Studio's, and it is not optional.
5. **Discover, in this order:** an explicit env URL → the ledger entry whose
   `pid` is alive *and* whose health names the app → a scan of
   `preferred … preferred+20` with the same identity check → offline. Results
   are cached for a few seconds.
6. **Front-ends** served by their own backend take the API base from
   `window.location.origin`. Vite dev servers get the backend port by env at
   launch and, failing that, read the ledger through the Node helper —
   `BuzzFin/ui`, `CaliberAI/frontend` and `MidnightBuzz/frontend` each import
   `scripts/buzzcaf-ports.mjs` rather than re-implementing the read. Desktop
   shells (pywebview, Electron, Tauri) open the port the backend *reports*
   (`READY port=N` on stdout, or the ledger), never the one they asked for.
7. **Sub-services** (ComfyUI, XTTS, RVC, Vite, Next) obey the same rule and are
   recorded under `extra`; the parent passes the chosen port down by env or arg.
8. **Third-party servers** (Ollama 11434, LM Studio 1234, ComfyUI's own default)
   come from env/config and the ledger, never from an assumption.

**The health routes, per app.** Rule 4 says *what* a health body must contain;
this table says *where* to ask, and the two are not the same. Several apps do
not serve `/health` at all, and asking the wrong one is indistinguishable from
the app being down — which is how BuzzFin managed to be permanently "not
running" in Dexter's registry until P4. These paths were each read off the
running app during the P4 integration run; `apps.default.json` carries them and
`test_apps_registry.py::test_the_registry_knows_each_apps_real_health_route`
keeps them honest.

| App | Ledger name | Health route | `extra` |
|---|---|---|---|
| Dexter | `dexter` | `/health` | — |
| Buzzcaf Studio | `buzzcaf` | `/health` | — |
| BuzzBrain | `buzzcaf` | `/health` (the Studio's — it has no listener of its own) | — |
| BuzzFin | `buzzfin` | **`/api/v1/health`** — a bare `/health` is 404 behind its security gate | — |
| BuzzcafCoffee ops | `buzzcafcoffee` | `/health` (and `/api/health`, same body plus db/vault) | `next` |
| MastGaane | `mastgaane` | `/health` | — |
| MidnightBuzz | `midnightbuzz` | `/health` | `xtts`, `vite` |
| CaliberAI | `caliberai` | **`/api/health`** | `vite` |
| BuzzEdit | `buzzedit` | **`/api/health`** | `comfyui` |
| BuzzViolin | `buzzviolin` | **`/health.json`** — a static file Vite serves; web mode only | — |
| buzzcode | `buzzcode` | *none.* Its listener is a `llama-server`: it answers `/health`, but with llama.cpp's own body, which will never say `app`. Read the ledger entry directly (`entry("buzzcode")`) — this is the one app discovery cannot identify, and the registry record deliberately has no `health_path` so nothing tries. | — |

**The helper.** One implementation per language, copied byte-identical;
`dexter/backend/test_buzzcaf_ports.py` fails when a copy drifts or is missing.

| Language | Canonical copy | Copies |
|---|---|---|
| Python | `dexter/backend/buzzcaf_ports.py` (stdlib only) | `BuzzcafAI/backend/integrations/`, `BuzzFin/server/buzzfin_server/`, `BuzzcafCoffee/ops/`, `MastGaane/mastgaane/`, `MidnightBuzz/backend/`, `CaliberAI/backend/app/`, `BuzzEdit/backend/` |
| Node | `BuzzEdit/scripts/buzzcaf-ports.mjs` (no deps) | `BuzzViolin/scripts/`, `BuzzFin/scripts/`, `CaliberAI/scripts/`, `MidnightBuzz/scripts/` |
| Rust | `cb-engine/src/ports.rs` | — |

```python
pick_port(preferred, span=20, host="127.0.0.1") -> int
publish(app, port, health, extra=None, ledger=None) -> None
withdraw(app, ledger=None) -> None
discover(app, preferred, health_path="/health", span=20, env_url=None, ttl=3) -> str | None
ledger_path() -> Path
entry(app) -> dict | None          # for a sub-service port under `extra`, or a
entries() -> dict                  # server that cannot name itself (llama.cpp)
```

Two things the Python helper does deliberately, and every copy must keep. It
never sets `SO_REUSEADDR` — the bind attempt has to fail when someone else is
there, which is the whole test. And it never calls `os.kill(pid, 0)` on Windows
to check liveness: CPython maps every signal but the console ones onto
`TerminateProcess`, so the POSIX idiom for "does this process exist" would
*kill* the process it asked about. Liveness there is `OpenProcess` +
`GetExitCodeProcess`.

---
## 4. The Sentinel contract

Newline-delimited JSON over `\\.\pipe\Sentinel`. Shapes are serde's
externally-tagged enums: `{"Reserve": {...}}` in, `{"Reserved": {...}}` back.
Every client call has a sub-second timeout and an "absent" result — **Sentinel
absent means old behaviour, never a hang.**

### Commands

| Command | Reply | Notes |
|---|---|---|
| `"Hello"` | `{"Hello":{version,features[]}}` | How a client detects an old service. Features: `ram_reserve`, `query`, `priority`, `disk`, `evict`, `app_status`, `cleanup` |
| `{"Register":{client,pid,class,budget_mib?,ram_budget_mib?,priority?,label?}}` | `{"Registered":{token}}`, or `"Ok"` from v1 | Priority default by class: interactive 80, assistant 60, batch 20. The name is bound to the pipe's client PID |
| `{"Reserve":{client,mib,ram_mib?,for_pid?,ttl_secs?}}` | `{"Reserved":{granted,free_mib,reserved_mib,ram_free_mib,ram_reserved_mib,expires_in_secs,reason,blockers[]}}` | `for_pid` is the process that will *consume* it (a spawned llama-server, not the caller) |
| `{"Query":{client,mib,ram_mib?}}` | the same shape, **books nothing** | Used before launching an app |
| `{"Release":{client}}` | `"Ok"` | Every grant is released or consumed |
| `"GpuStatus"` / `"RamStatus"` / `"DiskStatus"` | `{"Gpu":…}` / `{"Ram":…}` / `{"Disk":…}` | |
| `{"RequestCleanup":{client,mode,drive?}}` | `{"Cleanup":{queued,reason}}` | The tray picks it up and runs SmartCleaner |
| `"AppStatus"` | `{"Apps":[{label,class,priority,evictable,pids,vram_mib,ram_mib,frozen,registered_as}]}` | |
| `"PauseBatch"` / `"ResumeBatch"` / `"ListFrozen"` | `{"Frozen":[…]}` | |
| `{"Preempt":{client,target}}` | `{"Preempting":{deadline_secs}}` or `"Refused"` | |

### Events (to every subscriber)

`VramTight{level,free,over_budget[],deadline_secs}`,  `VramRelief`,
`RamTight{zone,phys_avail,commit_fraction,over_budget[],deadline_secs,ask}`,
`RamRelief`,  `Preempt{client,by,mib,ram_mib,deadline_secs}`,
`DiskTight{drive,free_bytes,level,ask}`,  `DiskRelief`,
`CleanupDone{freed_bytes,mode}`,  `Evicted{pid,name,label,reason}`,
`Frozen`,  `Resumed`,  `Heartbeat`.

### Version skew

The client sends `Hello` once and keys everything off the answer. A service that
does not know `Hello` is treated as v1: `features` is empty, `ram_mib` is **not
put on the wire** (an old service refuses a `Reserve` carrying unknown keys),
`Query` is skipped and answers "would be granted", and RAM/disk figures are read
locally with psutil so the UI still shows numbers instead of a blank gauge.

---

## 5. The steward rule

> **Nothing in Dexter calls `hybrid_llm.warm_model` / `release_model` /
> `set_keep_alive` except `backend/model_steward.py`.**

There is a test that greps for it
(`test_model_steward.py::test_no_module_loads_a_model_behind_the_stewards_back`),
because a code rule nothing checks is a comment. Before v9, five places loaded
models and only one asked Sentinel; none ever called `Release`.

Everything goes through one door:

```
ensure(model, purpose, provider)
    already resident?           -> yes, no reservation (nothing new is consumed)
    vram_estimate + ram_estimate
    Reserve(vram, ram, ttl=90)  -> refused? return the blockers, LOAD NOTHING
    warm_model                  -> observe /api/ps -> Release
                                -> failed        -> Release anyway
```

and one ladder when Sentinel asks for memory back (`VramTight`, `RamTight`,
`Preempt`):

1. the smart model (reloads in seconds)
2. on red, the fast model too (costs one slow reply; `VramRelief` brings it back)
3. foreign Ollama models — anything resident that Dexter did not warm
4. LM Studio, `lms unload --all`, **only** when `DEXTER_MANAGE_LMSTUDIO=1`
5. a buzzcode engine Dexter itself started

Freezing does not free VRAM on Windows (WDDM). That is exactly why cooperative
shedding comes first: it is the only rung that actually returns memory without
killing anyone's work. VrDeep is never evicted — it pauses itself at
`lane_pause_free_vram_mb`.

**When Dexter is refused** it does not go quiet. It answers on whatever model is
already resident and leads with who has the memory and what it is allowed to do:

> Sentinel won't give me 6.0 GB of video memory right now — 1.2 GB is free and
> VrDeep has 10.0 GB. I can park the background work ("pause batch jobs") or we
> wait for it to finish — your call. I'll keep going on the small model meanwhile.

---

## 6. Dexter runs the apps

`backend/apps_registry.py` + `data/apps.default.json` hold one record per
project: `launch` (the script the owner actually uses), `probes`, `stop`
(strategy, including `none`), `class`, `vram_mib`, `ram_mib`, `exclusive_with`,
`logs`. Owner overrides live in `data/apps.json`, which Dexter reads and never
writes.

| Tool | Says | Does |
|---|---|---|
| `apps_overview` | "what's running" | every project's live state plus the GPU/RAM lines |
| `app_status` | "is buzzfin running" | one project, with port and uptime |
| `open_app` | "open buzzedit", "start vrdeep" | `Query` first, `exclusive_with` check, RAM zone check, then launch and poll the probe |
| `stop_app` | "stop caliber" | the record's own strategy; **asks first** when a batch app is mid-job |
| `restart_app` / `app_logs` | "restart mastgaane", "buzzedit logs" | |
| `request_cleanup` | "clean up space" | forwards `RequestCleanup` to Sentinel, whose tray runs SmartCleaner |
| `vrdeep_queue`, `buzzedit_status`, `finance_status`, `coffee_brief`, `mastgaane_report` | "what's in the vrdeep queue" | read what each app already publishes |

All of these are Tier-0 rules: no model is involved in deciding what "stop
vrdeep" means. `buzzcaf_client` remains the Studio's specialist — the registry
delegates to it rather than reimplementing the port walk.

**A probe is about a process, not a file.** Process, port and HTTP probes come
first; a file only counts while it is fresh (`max_age_secs`). This was found the
hard way: a two-hour-old `session.json`, a database and a log file were reporting
VrDeep, MastGaane and CaliberAI as running when none of them were.

---

## 7. Control API: Dexter tool → Studio route

| Dexter tool | Studio route |
|---|---|
| `buzzcaf_status` | `GET /health` (must answer `app: "buzzcaf"`), `GET /api/studio/state` |
| `buzzcaf_start_studio` | launches `backend/desktop_app.py`, waits on `/health` |
| `buzzcaf_list_projects` / `buzzcaf_create_project` | `GET /api/projects`, `GET /api/brands` → `POST /api/projects` |
| `buzzcaf_execute_step` / `studio_approve_step` | `POST /api/projects/{id}/execute` / `/approve` |
| `studio_pending_approvals` | `GET /api/studio/state` |
| `studio_chat` | `POST /api/studio/chat` |
| `studio_discover_topics` | `POST /api/topics/discover` (check `source`: `model` vs `curated_seed`) |
| `studio_saved_topics` / `studio_save_topic` | `GET /api/topics/saved` / `POST /api/topics/save` |
| `studio_agents` / `studio_departments` | `GET /api/agents` (105 rows) / `GET /api/departments` (13) |
| `studio_ask_department` | `POST /api/departments/{name}/execute` |
| `youtube_channel_status` / `youtube_video_stats` | `GET /api/buzzbrain/channels`, `/videos/{id}`, `/latest` |
| `studio_analyze_video` | `POST /api/video-intel/analyze` |
| (event stream) | `GET /api/studio/events` SSE: `project_created`, `step_started`, `step_completed`, `approval_needed`, `step_failed`, `buzzbrain_snapshot`, `agent_invoked`, `department_task` |

BuzzBrain → Studio: `POST /api/buzzbrain/snapshot`.

---

## 8. Dexter's own API (the v9 additions)

| Route | Answers |
|---|---|
| `GET /api/dexter/resources` | `{sentinel:{connected,version,features}, vram, ram, disk, holders, frozen, reservations, trayAlive, models, lastShed, budgets}` |
| `GET /api/dexter/apps` | every registry row with its live status |
| `POST /api/dexter/apps/{id}/start` | the same tool the chat uses, so the same Sentinel check applies |
| `POST /api/dexter/apps/{id}/stop?confirm=` | a busy batch app answers `needsConfirmation` first |
| `POST /api/dexter/nudge` | a nudge from another local app (Mission Reminder) lands in Dexter's chat and pop-up instead of a second toast |

SSE frames added: `resources` (the steward acted), `gpu_busy`, `disk_tight`,
`evicted`. `RuntimeBanner` gained the codes `sentinel_down` and `tray_down`.

---

## 9. Finding the Studio

The Studio was the first app here to get this right, and section 3 generalised
its behaviour to everything else. Port 8000 is what every other local app
defaults to (CaliberAI was sitting on it and got mistaken for the Studio), so the
Studio prefers **8099**. If that is taken, `desktop_app.py` steps to the next
free port, writes where it landed to
`BuzzcafAI/backend/config/studio.runtime.json` — `{port, pid, started_at,
health_url}` — **and** publishes `buzzcaf` to the shared ledger. Both are removed
on a clean exit. Nothing assumes the port:

- Dexter's `buzzcaf_client` tries `BUZZCAF_API_URL`, then the ledger entry whose
  pid is alive, then the port in that runtime file, then a step-forward scan —
  and trusts none of them unless its `/health` says `app:"buzzcaf"`.
- BuzzBrain tries its saved URL, then scans 8099–8119 for `app:"buzzcaf"`.
- A second Studio launch attaches to the running one instead of starting another.
- `--dev` applies the same rule to Vite (5173 is every Vite's default), pinning
  with `--strictPort` and killing the tree on close so no orphan keeps the port.

---

## 10. Environment variables

| Variable | Where | Meaning |
|---|---|---|
| `BUZZCAF_API_URL`, `BUZZCAF_DIR`, `BUZZCAF_PYTHON` | dexter `.env` | Studio base URL (default `http://127.0.0.1:8099`), checkout, interpreter |
| `BUZZCAF_PORT`, `BUZZCAF_DEV_PORT` | BuzzcafAI | Preferred ports; the launcher steps forward and records the real one |
| **`DEXTER_PORT`** (8098) | dexter | The port Dexter *prefers*. It steps forward when it is held and publishes where it landed |
| **`BUZZCAF_PORTS_FILE`** | all | Moves the shared port ledger off `%LOCALAPPDATA%\Buzzcaf\ports.json` (tests, a second checkout) |
| **`VITE_DEXTER_PORT`**, **`VITE_DEXTER_UI_PORT`** (3006) | dexter | Backend port the Vite dev server proxies to, and the port Vite itself serves on |
| `LLAMACPP_URL`, `LM_STUDIO_URL`, `OLLAMA_URL` | both | The three local model servers |
| `DEXTER_LLM_PROVIDER` | dexter | `auto` (llamacpp → lmstudio → ollama → cloud), or a fixed provider |
| `DEXTER_FAST_MODEL`, `DEXTER_SMART_MODEL`, `DEXTER_TIERED_LLM` | dexter | The instant tier and the tool-capable one |
| `DEXTER_KNOWLEDGE_ROOTS` | dexter | `tag=path;tag=path` folders Dexter indexes |
| `DEXTER_TRAY`, `DEXTER_HOTKEY` | dexter | Hide-to-tray on close; the pop-up hotkey |
| `DEXTER_BROWSER_CDP` | dexter | Attach to an existing Chrome instead of Dexter's own window |
| `DEXTER_CODE_ROOTS`, `BUZZCODE_BIN` | dexter | Where buzzcode may work, and the executable |
| `DEXTER_CHECKINS`, `DEXTER_MIDDAY_HOUR` | dexter | Dexter's own check-ins |
| `DEXTER_CLAUDE_LOG`, `CLAUDE_PROJECTS_DIR` | dexter | Read Claude Code's transcripts (read-only; Dexter never runs it) |
| `DEXTER_ACTIVITY`, `DEXTER_ACTIVITY_ROOTS` | dexter | Foreground window + recently changed files |
| **`DEXTER_SENTINEL`, `SENTINEL_PIPE`** | dexter | Turn the guardian integration off; point at the dev pipe |
| **`DEXTER_VRAM_BUDGET_MIB`** (5120), **`DEXTER_RAM_BUDGET_MIB`** (3072) | dexter | What Dexter declares and books for itself |
| **`DEXTER_RESERVE_OVERHEAD_MIB`** (1024), **`DEXTER_RESERVE_TTL_SECS`** (90), **`DEXTER_PRIORITY`** (60) | dexter | Context/KV headroom, booking lifetime, class priority |
| **`DEXTER_ENGINE_VRAM_MIB`** (12500), **`DEXTER_ENGINE_RAM_MIB`** (6500) | dexter | Booked before buzzcode's 27B; refused ⇒ the code task is refused |
| **`DEXTER_MANAGE_LMSTUDIO`** (1) | dexter | Whether Dexter may `lms unload --all` on red. Set 0 if BuzzEdit owns LM Studio |
| **`DEXTER_AUTO_CLEAN`** (`safe`\|`off`) | dexter | Whether "clean up space" forwards a `RequestCleanup`. Dexter never deletes anything itself |
| **`DEXTER_APPS_ROOT`, `DEXTER_APPS_FILE`, `DEXTER_APP_LAUNCH_WAIT`** | dexter | Where the projects live, the owner's registry overrides, the launch probe window |
| **`MISSION_REMINDER_RUNTIME`** | dexter | Mission Reminder's runtime mirror. Read-only; Dexter stays quiet while a focus block runs |
| `GEMINI_API_KEY`, `OPENAI_API_KEY` | both | Cloud fallbacks |

---

## 11. Where data lives

| Data | Location |
|---|---|
| Dexter memory, workbook, profile, mission | `dexter/data/dexter_memory.db`, `dexter_workbook.db`, `profile.json`, `mission.json` |
| Dexter app registry (shipped / owner's) | `dexter/data/apps.default.json`, `dexter/data/apps.json` |
| Dexter activity log (seven days) | `dexter/data/activity.jsonl` |
| Dexter notes | the `notes` knowledge root, default `dexter/data/notes/*.md` |
| Dexter browser profile, screenshots, code-job logs | `dexter/data/browser_profile/`, `data/screenshots/`, `data/code_jobs/` |
| Dexter settings, reminders, scheduled tasks, window, log | `dexter/data/*.json`, `dexter/data/dexter.log` |
| Sentinel config (owner's; agents never write it) | `C:\ProgramData\Sentinel\config.toml` |
| Mission Reminder mirror | `%LOCALAPPDATA%\MissionReminder\runtime.json` |
| Studio projects and assets | `BuzzcafAI/backend/projects/<id>/` |
| Studio memory, saved topics, seed topics, video analyses | `BuzzcafAI/backend/knowledge/` |
| BuzzBrain snapshots | `BuzzcafAI/backend/knowledge/buzzbrain/` |
| Studio log | `BuzzcafAI/backend/logs/studio.log` |

Model stores — `~/.ollama`, LM Studio's models, `B:\models`,
`~/.buzzcode/engine` — are **never** auto-cleaned by anything here.

---

## 12. Tracking

`PLAN.md` holds the items, `PROGRESS.md` the evidence, in each repo.
`GUARDIAN_PLAN.md` (repository root) is the cross-repository contract. An item is
done only when its acceptance criterion has been observed and the evidence
(command output, log line, test name) is written next to it.
