# Buzzcaf Ecosystem

How the three projects fit together after the v5 plan (2026-09-06). This file
is kept **identical** in `dexter/` and `BuzzcafAI/`; edit both or copy one over
the other.

| Project | Role | Port | Launch |
|---|---|---|---|
| `dexter/` | The assistant. Text chat, memory, knowledge index, tools, and control of the Studio | 8098 | `Dexter.vbs` (hidden `pythonw`), `start_dexter.bat` for a console |
| `BuzzcafAI/` | The YouTube studio. Desktop app, Studio Assistant chat, 115 personas, workflows, BuzzBrain data store | 8000 | `Buzzcaf Studio.vbs`, `start_buzzcafai.bat` for dev, or Dexter starts it |
| `BuzzBrain/` | Sensor. Chrome extension that pushes YouTube watch-page snapshots to the Studio | none | Load unpacked in Chrome |

Local model servers (none are required to boot; all are probed):

| Server | Port | Notes |
|---|---|---|
| codeBuzz `llama-server` | 8089 | Qwen3.8-27B, only up while codeBuzz runs; Dexter's first choice when online |
| LM Studio | 1234 | Any loaded model; second choice |
| Ollama | 11434 | `qwen2.5:7b` / `qwen2.5:3b`; third choice |
| OpenAI / Gemini | cloud | Only if a key is configured; last |

## Launch order

1. Start whichever local model server you want (optional).
2. Launch Dexter. It probes the model servers and the Studio on 8000.
3. The Studio can be launched by hand or by telling Dexter "open the studio".

Both desktop apps run FastAPI in-process on `127.0.0.1` inside a pywebview
window. Closing the Studio window ends its process. Closing Dexter's window
hides it to the system tray (reminders, the scheduler and the Studio event
bridge keep running); `Ctrl+Alt+D` or the tray icon brings it back.

## Environment variables

| Variable | Where | Meaning |
|---|---|---|
| `BUZZCAF_API_URL` | dexter `.env` | Studio base URL, default `http://127.0.0.1:8000` |
| `BUZZCAF_DIR` | dexter `.env` | Studio checkout, default sibling `../BuzzcafAI` |
| `BUZZCAF_PYTHON` | dexter `.env` | Interpreter used to launch the Studio, default `BUZZCAF_DIR/.venv/Scripts/pythonw.exe` then `sys.executable` |
| `BUZZCAF_PORT` | BuzzcafAI env | Studio port, default 8000 |
| `LLAMACPP_URL` | dexter `.env` | codeBuzz llama-server, default `http://127.0.0.1:8089/v1` |
| `DEXTER_LLM_PROVIDER` | dexter `.env` | `auto` (llamacpp → lmstudio → ollama → cloud), or a fixed provider |
| `DEXTER_KNOWLEDGE_ROOTS` | dexter `.env` / settings | `tag=path;tag=path` folders Dexter indexes; the `notes` tag is where new notes are written |
| `DEXTER_TRAY` | dexter `.env` | `1` (default) hide-to-tray on close; `0` close quits |
| `DEXTER_BROWSER_CDP` | dexter `.env` | Attach to an existing Chrome (`http://127.0.0.1:9222`) instead of launching Dexter's window |
| `DEXTER_CODE_ROOTS` | dexter `.env` | Folders whose child repositories buzzcode may work in, default `B:\youtubeProjects\Buzzcaf_Media` |
| `BUZZCODE_BIN` | dexter `.env` | buzzcode executable, default `~/.cargo/bin/buzzcode.exe` |
| `DEXTER_CHECKINS`, `DEXTER_MIDDAY_HOUR` | dexter `.env` / settings | Dexter's own check-ins in the chat (v7); `1` (default) on; midday hour default 13 |
| `DEXTER_CLAUDE_LOG`, `CLAUDE_PROJECTS_DIR` | dexter `.env` / settings | Read Claude Code's transcripts (`~/.claude/projects`), default on; Dexter never runs Claude Code |
| `DEXTER_ACTIVITY`, `DEXTER_ACTIVITY_ROOTS` | dexter `.env` / settings | Activity awareness (foreground window + recently changed files), default on; extra roots to watch, default `B:\youtubeProjects` |
| `GEMINI_API_KEY`, `OPENAI_API_KEY` | both | Cloud fallbacks |

## Control API: Dexter tool → Studio route

| Dexter tool | Studio route | Phase |
|---|---|---|
| `buzzcaf_status` | `GET /health` (must answer `app: "buzzcaf"`), `GET /api/studio/state` | 1.1 / 3.1 |
| `buzzcaf_start_studio` | launches `backend/desktop_app.py`, waits on `/health` | 1.1 / 2.2 |
| `buzzcaf_list_projects` | `GET /api/projects` | live |
| `buzzcaf_create_project` | `GET /api/brands` → `POST /api/projects` | live |
| `buzzcaf_execute_step` | `POST /api/projects/{id}/execute` | live (honest errors from 1.1) |
| `studio_approve_step` | `POST /api/projects/{id}/approve` | 3 |
| `studio_pending_approvals` | `GET /api/studio/state` | 3 |
| `studio_chat` | `POST /api/studio/chat` | 3 |
| `studio_discover_topics` | `POST /api/topics/discover` (result is `curated_seed`) | 3 |
| `studio_saved_topics` / `studio_save_topic` | `GET /api/topics/saved` / `POST /api/topics/save` | 3 |
| `studio_agents` | `GET /api/agents` | 3 |
| `youtube_channel_status` / `youtube_video_stats` | `GET /api/buzzbrain/channels`, `/videos/{id}`, `/latest` | 5.1 |
| `studio_analyze_video` | `POST /api/video-intel/analyze` `{url, channel?, force?}` (also `GET /api/video-intel/recent`, `/{id}`) | v6 |
| (event stream) | `GET /api/studio/events` SSE: `project_created`, `step_started`, `step_completed`, `approval_needed`, `step_failed`, `buzzbrain_snapshot` | 3 |

BuzzBrain → Studio: `POST /api/buzzbrain/snapshot` (5.1).

## Dexter's other hands (v6)

| Hand | How | Notes |
|---|---|---|
| Browser | Playwright driving the installed Chrome (`channel="chrome"`), profile `dexter/data/browser_profile`; or attach to a Chrome started with `--remote-debugging-port=9222` via `DEXTER_BROWSER_CDP` | Tools `browser_*`; one worker thread owns the window; closing the window is fine, the next command reopens it |
| Coder | `buzzcode -C <repo> --mode edits\|yolo --json -p "<task>"` (headless JSON-lines) as a background job | Repos under `DEXTER_CODE_ROOTS`; `ask` mode is never used headless; results → toast + banner + `/api/dexter/code/jobs` |
| Local engine | `buzzcode engine serve` → llama-server on 8089 | The same server is Dexter's first-choice provider (`LLAMACPP_URL`); `start_local_model` / `stop_local_model` |
| Video intelligence | Studio `video_intel.py`: yt-dlp metadata, captions, "most replayed" heatmap, 30-video channel baseline, then one model call with the target channel's brand guide | No YouTube API key; results cached in `BuzzcafAI/backend/knowledge/video_intel/` |

## Where data lives

| Data | Location |
|---|---|
| Dexter memory (conversations, facts, summaries, documents) | `dexter/data/dexter_memory.db` |
| Dexter workbook (goals, commitments) | `dexter/data/dexter_workbook.db` |
| Dexter profile | `dexter/data/profile.json` |
| Dexter mission (north star, focus, today's win, blockers, wins) | `dexter/data/mission.json` |
| Dexter activity log (window samples, seven days) | `dexter/data/activity.jsonl` |
| Dexter knowledge index | `documents` + `ingest_sources` tables in `dexter_memory.db` |
| Dexter notes | the `notes` knowledge root, default `dexter/data/notes/*.md` |
| Dexter browser profile, screenshots, code-job logs | `dexter/data/browser_profile/`, `data/screenshots/`, `data/code_jobs/` |
| Studio video analyses | `BuzzcafAI/backend/knowledge/video_intel/<video or channel id>.json` |
| Dexter settings, reminders, scheduled tasks, window, log | `dexter/data/*.json`, `dexter/data/dexter.log` |
| Studio projects and assets | `BuzzcafAI/backend/projects/<id>/` |
| Studio memory, saved topics, seed topics | `BuzzcafAI/backend/knowledge/` |
| BuzzBrain snapshots | `BuzzcafAI/backend/knowledge/buzzbrain/` |
| Studio log | `BuzzcafAI/backend/logs/studio.log` |

## Tracking

`PLAN.md` holds the items, `PROGRESS.md` the evidence, in each repo. An item
is done only when its acceptance criterion has been observed and the evidence
(command output, log line, test name) is written next to it.
