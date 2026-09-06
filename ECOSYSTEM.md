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
window; closing the window ends the process (until Dexter's tray lands in 5.3).

## Environment variables

| Variable | Where | Meaning |
|---|---|---|
| `BUZZCAF_API_URL` | dexter `.env` | Studio base URL, default `http://127.0.0.1:8000` |
| `BUZZCAF_DIR` | dexter `.env` | Studio checkout, default sibling `../BuzzcafAI` |
| `BUZZCAF_PYTHON` | dexter `.env` | Interpreter used to launch the Studio, default `BUZZCAF_DIR/.venv/Scripts/pythonw.exe` then `sys.executable` |
| `BUZZCAF_PORT` | BuzzcafAI env | Studio port, default 8000 |
| `LLAMACPP_URL` | dexter `.env` | codeBuzz llama-server, default `http://127.0.0.1:8089/v1` |
| `DEXTER_LLM_PROVIDER` | dexter `.env` | `auto` (llamacpp → lmstudio → ollama → cloud), or a fixed provider |
| `DEXTER_KNOWLEDGE_ROOTS` | dexter `.env` / settings | `tag=path;tag=path` folders Dexter indexes (4.1) |
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
| (event stream) | `GET /api/studio/events` SSE: `project_created`, `step_started`, `step_completed`, `approval_needed`, `step_failed`, `buzzbrain_snapshot` | 3 |

BuzzBrain → Studio: `POST /api/buzzbrain/snapshot` (5.1).

## Where data lives

| Data | Location |
|---|---|
| Dexter memory (conversations, facts, summaries, documents) | `dexter/data/dexter_memory.db` |
| Dexter workbook (goals, commitments) | `dexter/data/dexter_workbook.db` |
| Dexter profile | `dexter/data/profile.json` (4.2) |
| Dexter settings, reminders, tasks, log | `dexter/data/*.json`, `dexter/data/dexter.log` |
| Studio projects and assets | `BuzzcafAI/backend/projects/<id>/` |
| Studio memory, saved topics, seed topics | `BuzzcafAI/backend/knowledge/` |
| BuzzBrain snapshots | `BuzzcafAI/backend/knowledge/buzzbrain/` (5.1) |
| Studio log | `BuzzcafAI/backend/logs/studio.log` (2.2) |

## Tracking

`PLAN.md` holds the items, `PROGRESS.md` the evidence, in each repo. An item
is done only when its acceptance criterion has been observed and the evidence
(command output, log line, test name) is written next to it.
