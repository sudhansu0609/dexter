# Dexter — Personal AI Super Assistant

A local-first desktop assistant for Windows. Runs your apps and files, plans your
day, actually reminds you, controls audio/media/system, and takes voice commands
hands-free — with the language model kept local (LM Studio / Ollama).

---

## Quick start

```bash
pip install -r requirements.txt      # see "Optional extras" below
npm install && npm run build
start_dexter.bat                     # native WebView2 window
```

Or run the pieces separately:

```bash
python backend/main.py               # API on http://127.0.0.1:8098
npm run dev                          # Vite dev server on :3006
npm run desktop                      # Electron shell
```

Copy `.env.example` to `.env` to configure. Everything works with defaults;
no API key is required.

### The model matters

Dexter is local-first. Load a model in LM Studio with solid **function calling** —
**Qwen2.5-7B-Instruct** or **Qwen3-8B** are the best quality-per-VRAM locally. A
3B model will misfire on multi-step requests. The loaded model is auto-detected
from `/models`, so there is nothing to configure after you swap it.

---

## Architecture

The language model is the **last** resort, not the first. Most commands never
reach it, which is what makes Dexter both fast and predictable on local hardware.

```
voice / text
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

Measured on this machine: Tier 0/1 answer in **3–30ms**; time-to-first-audio is
**~450ms** (streaming, sentence-chunked TTS).

### Backend modules

| File | Role |
|---|---|
| `main.py` | FastAPI transport only — endpoints, lifecycle, SSE streaming |
| `router.py` | The three tiers, slot extraction, agent loop, confirmations |
| `tool_registry.py` | `@tool` decorator → OpenAI schema + intent examples + validation |
| `tools_core.py` | Apps, files, planner, notes, media, BuzzcafAI |
| `tools_assistant.py` | Reminders, memory, time |
| `windows_tools.py` | Volume, brightness, clipboard, screenshots, windows, lock |
| `media_tools.py` | System-wide playback via Windows SMTC |
| `google_tools.py` | Gmail + Google Calendar |
| `scheduler.py` | APScheduler; fires reminders on 3 channels; maintenance jobs |
| `events.py` | SSE bus — the backend's only way to reach the user unprompted |
| `memory.py` | SQLite conversation history + durable facts |
| `hybrid_llm.py` | Provider routing, cached health, tool calling, streaming |
| `timeparse.py` | Natural-language times ("in 20 minutes", "every weekday at 9") |
| `app_index.py` | One-time scan of installed apps → instant launches |
| `jsonstore.py` | Atomic JSON reads/writes |

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

## Voice

### Speaking — expressive delivery

Kokoro is driven through a prosody layer (`backend/prosody.py`) rather than at a
fixed voice and speed. Each reply gets a `Delivery`: an emotion, a speed, and a
subtle voice blend.

| Emotion | When | Speed | Blend |
|---|---|---|---|
| `urgent` | a reminder fires | 1.12× | + `am_eric` |
| `upbeat` | an action succeeded | 1.07× | + `am_puck` |
| `warm` | greetings, small talk | 0.97× | + `am_adam` |
| `apologetic` | a tool failed or found nothing | 0.93× | + `am_onyx` |
| `reading` | long-form narration | 0.96× | + `am_onyx` |
| `calm` / `thinking` | long answers, deliberation | 0.94× / 0.90× | + `am_onyx` |

Emotion comes from three sources, in priority order: an explicit request, an
inline cue in the text, then inference from the reply and the tool result.
Blend weights stay under ~0.25 deliberately — past that Dexter stops sounding
like the same person in a different mood and starts sounding like someone else.

**Inline cues work.** `*excited*`, `[laughs]`, `(gently)` used to be deleted
before they could do anything; they now set the delivery and are removed from
what is spoken:

```
"*excited* Your render just finished!"   → upbeat, 1.07×
```

### Speaking — text normalisation

The single biggest naturalness win, and it was entirely absent. Raw text is
rewritten into something that *sounds* right:

| Written | Spoken |
|---|---|
| `10:30 AM` | "ten thirty A M" |
| `40%` | "forty percent" |
| `C:\Users\me\screenshot_1786554297.png` | "screenshot dot png" |
| `t-1786555392658` | *(dropped — never read an id aloud)* |
| `task(s)` | "tasks" |
| `A; B; C` | "A. B. C." (real pauses — Kokoro runs straight through `;`) |

### Reading things aloud

```
"read my agenda out loud"      "read out my notes"
"read the clipboard to me"     "read C:\path\to\notes.txt aloud"
```

`read_aloud` produces genuine narration rather than the terse summary — *"You
have three things left today. First, at two P M, Content Strategy. Then, at six
P M, Evening Walk."* — and requests the narrator register via a `_speech_emotion`
hint that the speech layer honours.

### Listening

- **Wake word** `"Dexter …"`, plus a **9-second follow-up window** after Dexter
  speaks during which no wake word is needed — so "open chrome" → "now make it
  full screen" works the way a conversation does.
- **Barge-in**: "stop" / "quiet" / "cancel" interrupts mid-sentence and clears
  the queued audio.
- **Echo suppression**: mic aborts while speaking, 1.2s cooldown, and a
  self-echo filter against the last spoken text.

Still open on the input side: server-side wake word (Porcupine) and VAD would
beat the current cooldown heuristics; the browser Speech API is also the weakest
link in noisy rooms.

---

## Reminders

Three delivery channels fire together, so a reminder lands whether or not the
window is open:

1. **Spoken** — Kokoro TTS through the app
2. **Windows toast** — persists in Action Center, works with Dexter closed
3. **In-app banner** — with Snooze (10 min) and Done

```
"remind me to check the render in 30 minutes"
"remind me to stand up every hour"
"what reminders do I have"
"cancel the reminder about the render"
```

Reminders survive restarts; any that came due while Dexter was down are surfaced
once on next launch.

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
pytest backend/test_router.py -v     # 90 routing tests, no server needed
python test_dexter_suite.py          # 20 live end-to-end checks (server must be up)
```

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
| `kokoro-onnx` | TTS falls back to edge-tts |
| `pycaw`, `screen-brightness-control`, `pyperclip`, `mss`, `pygetwindow` | Those specific system tools report unavailable |
| `winsdk` | Media keys still work; "what's playing" does not |
| `google-api-python-client`, `google-auth-oauthlib` | Gmail/Calendar unavailable |

Enable full semantic matching (~2.5 GB, pulls in PyTorch):

```bash
pip install sentence-transformers
```

---

## Security notes

- The server binds **127.0.0.1** by default. The filesystem endpoints are
  unauthenticated, so do not bind `0.0.0.0` without adding auth first.
- `.env` is gitignored (it was previously tracked — commit `.env.example` instead).
- Tools marked `confirm=True` (`send_email`, `write_file`, `close_window`,
  `sleep_pc`, `forget_fact`, `connect_google`) ask before running.

Still open, by design of the current scope: API-key auth on the endpoints, a
filesystem path allowlist, and removing `shell=True` from the launcher paths —
all scheduled in [`IMPROVEMENT_PLAN.md`](IMPROVEMENT_PLAN.md) (Roadmap v3, the
single living plan for everything above and beyond).
