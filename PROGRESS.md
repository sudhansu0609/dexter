# Dexter — Progress Log

Running record of what has actually landed, newest first. Plan: [`PLAN.md`](PLAN.md).
Every row is either **done** (with the evidence that proves it) or **open**.
Nothing is marked done on intent.

## v11.1 (2026-09-11) — it hears you, and it can do the rest

Requested as: "check what was done in last session, and why dexter is not able
to close any app that it opened and some basic tasks are not being performed,
i want it to be able to perform any task i ask it on this pc."

**What the last session left.** Roadmap v11 (below) was built and its 258
tests passed, but no progress entry was written and **Dexter was never
restarted on the new code**. The instance that was running (started 10:23,
before the 11:05 edits) still had the old `close_window`, whose `_rank_windows`
call was missing an argument - `data/dexter.log` 10:28: *"close Chrome." →
`'close_window' raised: _rank_windows() missing 1 required positional argument`*.
That is why nothing it opened would close. The current code closes cleanly
(verified live: Notepad opened, `close_app("notepad")` → "Closed Notepad (1
window)", gone).

**Why "basic tasks" were not happening - measured, not guessed.** Same log,
10:23-11:18:

| | count |
|---|---|
| sentences the speaker gate threw away as "someone else" | **141** |
| sentences that reached the router | 28 |
| owner scores on the rejected ones | 0.4x: 7 · 0.5x: 49 · 0.6x: 68 · 0.7x: 17 |

The 0.75 bar was measured with Kokoro voices standing in for people. On the
owner's real microphone the owner scores 0.45-0.74 most of the time, so five of
every six things said - "Dexter, close VS Code", "restart my PC" - were logged
as a visitor and never transcribed. On top of that, "restart my PC" had no tool
at all (the v9 "restart <app>" rule matched and yielded nothing), and the smart
tier was refused VRAM twice because LM Studio's 12B holds the card.

Suite: whole backend green · new tests: speaker grey zone + learning (11),
power tools (3), shell (35), router intent cases (11) · preflight 13 ok.

| Item | Status | Evidence |
|---|---|---|
| Speaker gate: threshold 0.75 → **0.60** | ✅ | `speaker_id.THRESHOLD` default, Settings → Voice → *Voice match strictness*, `.env.example` |
| Grey zone: a print up to 0.15 under the bar is **transcribed first** and accepted when the sentence starts with Dexter's name or follows one of yours (name gate, wake word, follow-up) | ✅ | `Verdict.grey`; `voice_pipeline._finalize` transcribes then decides; a print clearly under the bar still never reaches Whisper (privacy rule kept, test pinned) |
| The profile learns: every confident match nudges the owner's print toward this mic (EMA 0.08, saved every 5th); a grey acceptance never teaches, so a visitor saying "Dexter" cannot become you | ✅ | `SpeakerId.learn`; `summary().adapted`; tests: moves the score up, survives a restart, grey never teaches |
| The rejection log says what to do | ✅ | `[Voice] Ignored someone else (owner 0.58 …). If that was you: start with my name, or lower Voice match strictness in Settings.` |
| `restart_pc`, `shutdown_pc`, `sign_out` (all ask first; 10 s grace), `cancel_shutdown`, `empty_recycle_bin` (asks) | ✅ | `windows_tools.py`; fast rules at the head of `_FAST_RULES`: "I want you to restart my PC." → `restart_pc`; "restart vrdeep" still → `restart_app`, "shut down discord" still → `close_app` |
| `run_command`: one PowerShell command for the long tail ("what's my IP", "flush the DNS", "restart explorer") - **always asks, the question is the exact command**, denylist for formatting/disk/drive-root/system-folder/profile deletes, BitLocker, boot, firewall, `iex` from the internet, power; never slot-filled from the sentence | ✅ | `tools_shell.py`; `router._extract_slots` returns None for it; 35 tests incl. one real round trip |
| Dexter's own voice print was never stored (`[Speaker] Could not enrol Dexter's voice: name 'np' is not defined` on every start since v10.2), so the speaker gate could not recognise its own echo | ✅ (bug fixed) | `main.py` enrol function lacked the numpy import; status now reports `dexterVoice` |
| Live after restart: "close notepad" → `close_app` "Closed Notepad (1 window)"; "open notepad" … "close it" → Notepad; "restart my pc" → asks, "no" → cancelled; 161 tools registered | ✅ | chat API on the running instance, 2026-09-11 |
| Pop-up quick settings (gear in the title bar): **microphone** picker with live meter and the silent-mic warning, **model** (backend + model, shared with the full window via localStorage), a link to the full settings; Esc closes it first | ✅ | Owner: "in the pop up no option to change the default mic, also no option to change the default model". `src/components/PopupSettings.tsx`; 3 vitest cases (21 total); screenshot of the running pop-up at 125 % DPI (364×211) fits without overlap after the flex-shrink fix |
| **Ollama is started by Dexter** (owner: "why is dexter automatically not opening and loading ollama or lm studio models"). Ollama was installed, preferred (`DEXTER_LLM_PROVIDER=ollama`), not in the Run key, and nothing started it: after it went away at midday every sentence got "My local reasoning engine is offline". `llm_boot.py` starts `ollama serve` detached (log in `data/ollama.log`) or `lms server start`, at preflight, at startup and whenever a health probe finds the preferred backend down (once a minute at most); `hybrid_llm.get_available_providers` is the single hook | ✅ | Live: preflight started Ollama (pid 32760, parent = preflight) and pinned qwen2.5:3b; "what is the capital of Italy" → tier 1 fast_chat in 0.7 s; 11 tests in `test_llm_boot.py`; the unit-test conftest turns autostart off so no test can start a server |
| The offline reply says what failed | ✅ | "My local reasoning engine isn't answering: Model server returned 400: Failed to load model …" instead of asking the owner to check the server |
| **The tier models are settings** (owner: "make sure i can change the models in the local instance like ollama"): Settings → Model → *Fast model* / *Smart model*, options read live from Ollama's pulled tags and LM Studio's ids, applied on the next message, blank = off / backend default; the pop-up and full-window *Model* picker pins a backend + model per turn as before | ✅ | `settings_store` kind `model`; live `/api/dexter/settings` lists `qwen2.5:3b, qwen2.5:7b, …`; 3 tests |
| "close notepad" with only Notepad++ open closed Notepad++ | ✅ (bug fixed, found live) | `find_windows`: a known app with no window is closed, never the nearest title; test pinned |
| Tests isolated from the owner's real voice profile | ✅ (bug fixed) | `test_wake_word_alone_is_not_a_message` had started failing the moment `data/voice_profile.json` existed (real encoder scored the fake clip 0.37). `conftest.py` now installs a profile-less `SpeakerId` for every test |

**Still true on this machine, and not code:** LM Studio's `gemma-4-12b`
llama-server holds 15.8 of 16.3 GB VRAM. Sentinel refused Dexter's 7B twice
during the session (`reserve 5490 MiB → DENIED`), so anything that needs the
smart tier answers from whatever is resident or says the GPU is busy. The
deterministic rules above do not need a model; a free sentence does.

## v11 (2026-09-11) — Hands like mine (desktop control)

Requested as: "dexter opens chrome but not what i say in it, like opening a
different website or closing chrome or a url, similarly other tasks. make it
more smart so that it can do stuff like me, the minute stuff as well." Built
by an implementation agent from `PLAN.md` v11 the same morning; recorded here
the next session, from the code and its tests, because no entry was written
at the time and the running instance was never restarted onto it (see v11.1).

Suite: `test_desktop_control.py` + `test_browser_tools.py` + `test_router.py`
**258 passed** (fakes only; `conftest.py` forbids real keystrokes, real closes,
real foreground changes) · live: Notepad opened and closed through `close_app`.

| Item | Status | Evidence |
|---|---|---|
| P1.1 `chrome_control.py`: your Chrome, not Dexter's - open a URL as a new tab in the running instance (`chrome.exe <url>`), close tab / Chrome (WM_CLOSE, never kill), new / next / previous / reopen tab, back / forward / reload, find, zoom, fullscreen | ✅ | `availability()` on this PC: mode `own`, Chrome found and running, UIA present |
| P1.2 UIA reader: active tab URL, title, tab list, page text (budgeted) | ✅ | `browser_read_page` / `browser_tabs` in `own` mode |
| P1.3 Site names are sites: "open youtube" → `browser_open`, "open notepad" → `launch_app` | ✅ | `fast_path` pinned in `test_router.py` |
| P1.4 `browser_*` tools mode-aware (`DEXTER_BROWSER_MODE=own` default, `playwright` fallback) | ✅ | `test_browser_tools.py` both modes |
| P2.1 `desktop_control.py` + `tools_desktop.py`: `press_keys`, `type_text`, `scroll_window`, `click_text` (UIA), `read_screen_text`, `switch_to` (focus verified), `arrange_window` (min/max/restore/snap/close), `close_app` (every window, WM_CLOSE), `kill_app` (asks), `list_windows`, `app_shortcut`, `desktop_status` | ✅ | 12 desktop tools registered; process-first matching ("close chrome" = chrome.exe, not a file called chrome.png) |
| P2.2 Per-app shortcut recipes (Chrome, Edge, VS Code, Explorer, Spotify, Notepad, Discord, Premiere) | ✅ | `run_recipe` |
| P2.3 Safety: keys only to the named or focused window, refused if focus went elsewhere; secrets refused into an omnibox; `kill` asks; Dexter's own window is never "this window" | ✅ | tests for each refusal |
| P3.1 "close it / that tab / minimize this" resolve from what Dexter last opened (5 min focus memory) | ✅ | `resolve_reference`; "open notepad … close it" |
| P3.2 "in chrome, …" focuses first; "and / then" chains go to the agent loop with the desktop tools in front of the model | ✅ | `split_app_prefix`, `is_desktop_chain`, `CHAIN_CATEGORIES` |
| P3.3 The open-rule stops swallowing sentences (chain words, URLs, sites, > 3 words fall through) | ✅ | `test_router.py` |
| `close_window` (v8 tool) `_rank_windows` call fixed | ✅ | the crash in the 10:28 log; `test_windows_tools.py` |

## v10 (2026-09-10) — Voice is back, and it behaves

Requested as: "fix the voice commands of dexter, it shall be perfect and good
response and intuitive." Voice had been removed end to end in v5.1 (tag
`pre-v5` holds the old code); it is rebuilt here on the same local stack -
openWakeWord (`hey_jarvis`), silero-vad, faster-whisper, Kokoro - with the
things that made it feel like a command line fixed.

Suite: `backend/test_voice_pipeline.py` **61 passed** (fakes + a fake clock; no
models), whole backend **660 passed, 1 skipped** before the hub tests were
added · `npx tsc --noEmit` clean · `npm run build` clean · **18 vitest** ·
end-to-end with the real models and Kokoro-synthesized speech, no microphone
(scratch script, results in the table).

| Item | Status | Evidence |
|---|---|---|
| Wake word → command, one breath | ✅ | "Hey Jarvis, open Chrome" → `listening(wake)` → `transcript "open Chrome"` 1.2 s after the audio ended (Whisper `small`, CPU int8; CUDA build present but `cublas64_12.dll` missing, auto-fallback logged) |
| Wake word, pause, command | ✅ (bug fixed) | The tail of "...jarvis" is speech to the VAD, so a pause ended the capture and **"Jarvis" went to the chat as a message**. A 350 ms grace after the wake word and a "wake alone is not a message" rule fix it: "Hey Jarvis … what's on my agenda today?" → one transcript; "Hey Jarvis" + silence → `heard_nothing(silence)` and a 6 s follow-up window so you can just say it |
| Clipped wake word | ✅ (bug fixed) | With a 400 ms pre-roll Whisper heard "Service, open Chrome." and nothing stripped it. Pre-roll is 1.2 s so the whole phrase is in the clip, and the stripper knows Whisper's spellings of "jarvis" at the start of a sentence only ("Call the service desk" is untouched) |
| Follow-up without the wake word | ✅ | After `set_speaking(False)` + echo guard: "Remind me at five to call the bank." → `listening(followup)` → transcript, conf 0.75 |
| "stop" / "cancel" / "never mind" | ✅ | Classified as `command`, cancels generation, never reaches the chat; "stop the timer" and "cancel my 3pm meeting" are still messages (parametrised tests) |
| Push-to-talk | ✅ | `listen` control → `listening(manual)`; waits 6 s for speech instead of 4; over a reply it emits `interrupt` and cancels the stream |
| Ambient speech | ✅ | "I was thinking about lunch." with no wake word → no events |
| Whisper hallucinations | ✅ | "Thank you." / "you" / "thanks for watching" on near-silence dropped by `stt_engine` (stock-phrase list + no-speech / log-prob gates) → `heard_nothing(unclear)` instead of a chat turn |
| Two windows, one mic | ✅ (bug fixed) | `VoiceHub`: the pop-up and the full window both capture; the one shown/focused last `claim`s the mic, the other is told `standby` and its audio is dropped. Verified over the real WebSocket route with two clients. **Found live:** `leave()` deadlocked on a non-reentrant lock when the last window closed - would have frozen the socket handler; RLock + regression test |
| Spoken replies | ✅ | `chat/stream` with `voiceEnabled`: `tool_start, tool_result, reply, delivery, audio, done`; first Kokoro clip in 0.34 s; "It's eleven fifteen P M on Thursday, September ten." (prosody normalisation). Streamed LLM replies are voiced sentence by sentence on a worker thread |
| Spoken-shaped answers | ✅ | `voiceInput` adds a style note to the context: one to three sentences, no markdown/lists/paths/URLs - so what the model writes is what Kokoro can say |
| Auth | ✅ | Socket without the token or with a foreign Host → closed 1008; `/api/dexter/audio/<clip>` is token-exempt (Audio() cannot send headers) but only serves `speech_*` basenames; a traversal attempt → 401 |
| UI | ✅ | Mic button next to Send (state = colour, ring = input level), one-line hint under the composer that says what to do next, "spoken" tag on voice messages, Voice on/off in the title bar, Ctrl+Space / Esc, the pop-up comes forward while listening or speaking, **Settings → Voice** group |
| Shell | ✅ | WebView2 mic-permission hook restored in `desktop_app.py` (both windows); preflight reports voice input, speech output and the mic patch |

### v10.2 (2026-09-11) — it listens to you, keeps listening, and sounds like someone

Requested as: "dexter is not listening, also i want it to listen continuously
till i mute it, it shall listen to just me and it shall not pick up its own
voice, and make the voice more emotions, not like a robot."

**Not listening - diagnosed live.** `/api/dexter/status → voice.debug` on the
running instance: both windows connected, 2027 audio frames received, **peak
11 of 32767** - the mic was open and delivering digital silence. Windows mic
privacy was *Allow*; the machine has 13 capture endpoints, several virtual
(Oculus, Mixed Reality, Stereo Mix, iPhone Hands-Free, RTX Voice), and
`getUserMedia` with no device takes the system default. Fixes: the pipeline
skips a default that looks virtual and opens the first real mic; **Settings →
Voice → Microphone** lists them with a live meter; the hint line shows a
meter always and says *"The mic (X) is silent. Pick another"* after six
seconds of nothing. (The running app also predates every change here; it needs
a restart.)

| Item | Status | Evidence |
|---|---|---|
| Listen continuously until muted | ✅ | `DEXTER_WAKE_MODE=open` (Name / **Open** switch under the composer; Settings too): every utterance is captured, no name needed, until Voice off. Tests: takes everything, strips the name if said anyway, stays quiet on a cough |
| Only me | ✅ | `speaker_id.py` on Resemblyzer's speaker encoder (`pip install --no-deps resemblyzer`; webrtcvad stubbed). *Learn my voice* = ~10 s of speech through the same socket (`enroll` control, progress events). Real encoder, Kokoro voices as people: enrolled voice **0.81–0.90**, six other voices **0.46–0.73**, threshold 0.75 → 11/12 (one British male voice scored 0.73 against another British male voice - synthetic voices are closer than people). Pressing the mic bypasses the check; a visitor gets one "That wasn't your voice" every 15 s |
| Not its own voice | ✅ | Dexter's Kokoro voice is enrolled at startup (and again when the voice changes): its echo scored **0.92–0.93** against itself and is dropped before STT - on top of half-duplex + echo guard |
| More emotion | ✅ | Blend weights 0.15–0.35 (were 0.10–0.25) and speeds 0.88–1.16 (were 0.90–1.12); neutral now carries a touch of fenrir. The model opens every spoken reply with a mood tag (`[warm]` … `[thinking]`) that drives the voice and is stripped from the chat (`_take_mood_tag`, verified through `chat/stream`). **Dexter's voice** is a setting with *Try*: 12 Kokoro voices, heart/bella the most natural; a female base voice gets female blend partners |
| Tests | ✅ | `test_speaker_id.py` 17, `test_voice_pipeline.py` 84, backend **709 passed, 1 skipped**; tsc, build, 18 vitest clean; routes `voice/profile` GET/DELETE, `voice/preview`, mood-tag path all exercised through the test client |

### v10.1 (2026-09-11) — "Dexter" is the wake word

Requested as: "can we change the wake word to dexter instead of jarvis."
openWakeWord has no Dexter model and training one stays declined, so the
recognizer does the job (`DEXTER_WAKE_MODE=name`, the default): every
utterance the VAD hears has its opening checked by a greedy Whisper pass, and
only sentences that start with the name are transcribed in full and sent on.
`model` keeps the openWakeWord phrase (`hey_jarvis`), `both` answers to either;
**Settings → Voice** switches between them live.

| Check (real models, Kokoro speech in three voices) | Result |
|---|---|
| "Dexter, open Chrome." / "Dexter, what's on my agenda today?" / "Hey Dexter, remind me at five to call the bank." | `listening(name)` → transcript with the name stripped, conf 0.65–0.79 |
| "Dexter." alone, then "What time is it?" a second later | `heard_nothing(silence)` opens a follow-up window; the question lands as a transcript |
| "I was thinking about lunch." / "The dexterity test went well." / "Can you ask Dexter later?" | no events (gate only; nothing else transcribed) |
| "Dexter, stop." | `command stop`, generation cancelled |
| Five sentences of ambient chatter | no events. Gate cost per sentence: `small` 1.16 s, `base` 0.38 s, both 8/8 on the name check (three Kokoro voices, incl. "Dixterity" being correctly *not* the name) - so `base` is the gate model (`DEXTER_NAME_GATE_MODEL`) and `small` transcribes only what is for Dexter |

**Found by this run:** "Hey Dexter, remind me…" in the British voice was
rejected because the gate heard *"Hey, hey Dexter"*. The block in which a
capture begins was appended to the recording even though the pre-roll already
held it, so the first 128 ms of every command was doubled - a bug the
openWakeWord path had too, hidden because the doubled slice there was the tail
of the wake phrase. Fixed in `feed()` with a regression test that counts
samples; the name matcher also tolerates up to two lead-in words ("hey, um,
Dexter").

`test_voice_pipeline.py` **84 passed** (an autouse fixture pins the older
tests to `model` mode; the name-mode tests switch explicitly).

Not done: GPU Whisper (needs the CUDA 12 DLLs on PATH - `auto` picks it up
when they are there).

## v9 status at a glance (2026-09-10) — Sentinel rules, Dexter runs the apps

Requested as: "Dexter controls every app in `B:\youtubeProjects\Buzzcaf_Media`;
Sentinel is the supreme guardian of VRAM and RAM so the PC never hangs, and uses
SmartCleaner to free space; Dexter asks Sentinel for permission before loading
models." Plan: [`PLAN.md`](PLAN.md) v9 table · contract:
[`../GUARDIAN_PLAN.md`](../GUARDIAN_PLAN.md).

Suite: **608 passed, 1 skipped** (`python -m pytest backend -q`; 531 at the end
of the v9 build, +4 from the live integration run, **+55 from P1**, **+18 from
P4**) · `npx tsc --noEmit` clean · `npm run build` clean · **17 vitest** across
3 files · `python test_dexter_suite.py` **31 passed, 2 failed** — the two are
`Streaming chat` and `Tiered models`, both of which need a model, and the P1 and
P4 runs were both made with every LLM backend deliberately aimed at a dead port
(see the evidence below). Against a live Sentinel v2 during the v9 integration
run it was **30 passed, 0 failed**.

| Item | Status | Evidence |
|---|---|---|
| D1 protocol v2 client + lifecycle (**N1**) | ✅ done | `sentinel_client.py`: `hello`/`features`/`supports`/`version`, `register` with `ram_budget_mib`/`priority`/`label` accepting both `"Ok"` (v1) and `{"Registered":{token}}` (v2), `reserve(mib, ram_mib, for_pid, ttl_secs)`, `query`, `ram_status`, `disk_status`, `request_cleanup`, `app_status`, `unregister`. **Verified live against the installed v1 service** (PowerShell, read-only): `hello → {}`, `features → []`, `query(4096) → (True, {'absent': True, 'reason': 'this Sentinel has no Query'})`, `ram_status/disk_status/app_status → None`, whole handshake 0.01 s. 21 tests in `test_sentinel_client.py` — every v1 test kept, plus `test_an_old_service_that_does_not_know_hello_degrades_to_v1` asserting `ram_mib` never reaches the wire (an old service refuses a `Reserve` with unknown keys, which would turn "the guardian is old" into "Dexter cannot think"). Shutdown: `router.sentinel_stop()` → release → unregister → stop, tested |
| D2 the model steward (**N2**) | ✅ done | `model_steward.py`, 5 call sites rewired (`main.py` startup warm, `router._keep_fast_model_warm`, `_release_smart_model_soon`, `_on_sentinel_event`, `code_agent._make_room`). `test_no_module_loads_a_model_behind_the_stewards_back` greps every `backend/*.py` for `.warm_model(`/`.release_model(`/`.set_keep_alive(` outside the steward and `hybrid_llm.py` — a code rule nothing checks is a comment. Booking released after a granted load *and* after a failed one *and* after `warm_model` raising (three separate tests). A refusal loads nothing: `test_a_refusal_loads_nothing_and_names_the_holder`, and `route()`'s `test_a_refusal_still_answers_on_whatever_is_already_loaded` proves the escalation runs on the resident 3B instead of going quiet. Shed ladder tested rung by rung, including "a model we warmed is never treated as foreign" (a real bug: `unload()` forgets the model, so the foreign list has to be computed first) |
| D3 apps registry + tools (**N3**) | ✅ done | `apps_registry.py`, `procs.py`, `tools_apps.py`, `data/apps.default.json` with **all 17 projects**. Live on this machine: `/api/dexter/apps` returns 17 rows; "what's running" → `apps_overview` at **tier 0**; "is vrdeep running" → `app_status` tier 0; "stop caliber" → `stop_app` tier 0; "clean up space" → `request_cleanup` tier 0. `test_the_launch_scripts_exist_on_this_machine` checks every shipped launcher is really on disk. `test_it_refuses_to_guess` pins the substring rule: "open code" is VS Code, not BuzzCode ("code" is inside "buzzcode"), and "open chrome" is Chrome, not BuzzBrain (whose alias is "the chrome extension") — both were real misfires before the rule was tightened to id + one-word aliases, ≥5 characters |
| D3a **a file is not a process** | ✅ done (bug found live) | The first live `/api/dexter/apps` reported VrDeep, MastGaane **and** CaliberAI as running. None were. Their file probes were matching a two-hour-old `session.json`, a database (`data\mastgaane.db`) and a log (`data\logs\launcher.log`) — all of which are simply always on disk. Fixed: process/port/HTTP probes are sorted ahead of file probes, and a file probe only counts within `max_age_secs` (default 900; `0` means "existence is the signal", for the Studio's runtime file which is deleted on a clean exit). Re-verified live: VrDeep `RUNNING (process pythonw.exe, up 2h55m)`, MastGaane and CaliberAI correctly `stopped`. Four regression tests, including `test_the_shipped_registry_has_no_bare_file_liveness_probes` so a new one cannot be added without a freshness window |
| D4 resource-aware launch (**N4**) | ✅ done | `open_app` runs `exclusive_with` → `sentinel.query(vram, ram)` → RAM zone → launch → poll. Tests: a refused query launches nothing and names VrDeep; an exclusive app that is running blocks by name; RAM Red refuses, RAM Yellow warns and proceeds; a launch that never comes up says so instead of claiming success; a light app never bothers Sentinel |
| D5 API + panels (**N5**) | ✅ done | `/api/dexter/resources`, `/api/dexter/apps`, `/apps/{id}/start\|stop?confirm=`, `/nudge`. `ResourcePanel.tsx` + `AppsPanel.tsx` in the v8 light-minimal tokens. 10 vitest in `Panels.test.tsx`: three gauges with real figures, holders named, `Sentinel is not answering` + three "not reported" gauges when absent, `older build` + `read locally` for v1, `tray is not running`, and the two-step stop (first click → "3 job(s) in its queue", second carries `confirm: true`). `RuntimeBanner` renders `sentinel_down`/`tray_down` with a shield icon; the SSE frames `resources`/`gpu_busy`/`disk_tight`/`evicted` land as banners in the window and cards in the pop-up |
| D5a refusal wording (**N2**) | ✅ done (bug found live) | The first live run printed "0.0 GB of RAM free" for a **VRAM** refusal, while 22 GB of RAM was free: `"ram" in reason.lower()` matches the r-a-m inside "not enough **VRAM**". Fixed with a word-boundary regex `(?<!v)\bram\b`. Now: *"Sentinel won't give me 6.0 GB of video memory right now — 1.2 GB is free and VrDeep has 10.0 GB. I can park the background work (\"pause batch jobs\") or we wait for it to finish — your call. I'll keep going on the small model meanwhile."* and for the engine: *"…buzzcode has 12.1 GB. Say \"stop the coding engine\" to free it…"* |
| D6 coworker layer (**N6**) | ✅ done | `companion.machine_line()` in `context_block()` and the morning facts (`- Machine: GPU: 5.2 GB free of 16 (VrDeep 10 GB); Dexter on qwen2.5:3b; RAM 61 %; C: 22 GB free`), plus `_last_cleanup_line()` for what the tray freed overnight. Four initiative candidates (`disk_low`, `frozen_long`, `evicted_notice`, `gpu_free_queue_waiting`) ordered **after** the work ones — `test_work_still_comes_before_the_machine`. Mission Reminder: `mission_reminder_busy()` gates `companion.enabled()`, so a running focus block silences check-ins, tails, nudges and proposals in one place; a **paused** block does not, and a mirror older than ten minutes is ignored (the app quit and left the file behind — trusting it would mute Dexter for ever). 16 tests in `test_companion.py`, 11 in `test_initiative.py` |
| D7 settings (**N7**) | ✅ done | A **Machine** group in `settings_store.SETTINGS`: `vramBudget`, `ramBudget`, `manageLmStudio`, `engineVram`, `engineRam` all with `module="model_steward"` + `attr` so they hot-reload (`liveReload: true` in the UI); `autoClean` and `missionReminderRuntime` are env-only and say so. `SettingsPanel` labels the group. `.env.example` documents every new variable with why it exists |
| D8 verification (**N8**) | ✅ done | 531 backend tests (was 411 at v8 — **+120**), 15 vitest, `tsc` and `vite build` clean. New: `backend/conftest.py` — an autouse fixture that points `SENTINEL_PIPE` at a dead pipe, forces `MANAGE_LMSTUDIO=False` and makes `procs.kill_tree` **raise** if a test ever calls it. That file exists because a first run of the steward tests really did execute `lms unload --all` on the live machine; a unit test is not allowed to take BuzzEdit's model away or stop a render. `test_dexter_suite.py` scenario E (resources, `gpu status`, frozen list, registration, refusal wording, then the read-only app checks) skips itself when no pipe answers |
| D9 docs (**N9**) | ✅ done | `ECOSYSTEM.md` rewritten around the port map, the Sentinel contract (commands, events, version skew) and the steward rule; copied to `BuzzcafAI/` and verified byte-identical (SHA256 `19C5642C…60D66E`, 18 361 bytes both). `PLAN.md` v9 table at the top in the v7 style; this section; README "Sentinel and the apps" |
| **P1 ports: nothing hardcoded** | ✅ done | `backend/buzzcaf_ports.py` — the canonical Python implementation of [`../GUARDIAN_PLAN.md`](../GUARDIAN_PLAN.md) §11, stdlib only: `pick_port` (exclusive bind, step forward, never evict), `publish`/`withdraw` (read-merge-write under `ports.json.lock`, stale after 5 s, temp + `os.replace`), `discover` (env URL → live-pid ledger entry → step-forward scan, identity enforced at every step, small TTL cache), `ledger_path`, plus `entry`/`entries` for the two things a base URL cannot express. Ledger: `%LOCALAPPDATA%\Buzzcaf\ports.json` (`BUZZCAF_PORTS_FILE`). **31 tests** in `test_buzzcaf_ports.py`: a busy port steps forward, twelve threads publishing at once keep all twelve entries, a stale lock is broken instead of waited on, `withdraw` takes only its own key, a listener with the wrong `app` is ignored, a ledger entry with a dead pid is ignored, offline is cached, and the sibling copies (P2/P3 paths, skipped until they exist) plus both `ECOSYSTEM.md` copies are byte-identical. Backend suite **590 passed, 1 skipped** (was 535 - **+55**) |
| P1a Dexter takes a port instead of assuming one | ✅ done | `desktop_app.py` picks before it imports `main` and hands the number over in `DEXTER_BOUND_PORT`, so the CORS list, `/health`, the ledger entry and the window all name the same port; `main.py.__main__` picks for itself and prints `READY port=N`. `/health` answers `{"app":"dexter","status":"ok","port":n,"pid":n,"version":"2.0.0"}` — the identity route Dexter itself has been demanding of everyone else since v9. Published only once that route answers (a ledger written at startup is a ledger that lies), withdrawn in the lifespan shutdown, with `atexit` as a backstop; a process that never published never withdraws, so a pytest run cannot delete the live Dexter's entry. `/api/dexter/status` and `/resources` report `port`. `start_dexter.bat` no longer fixes 8098 — and no longer `Stop-Process`es whoever holds 3006/8098, which is precisely what §11 rule 2 outlaws |
| P1b the UI has no port in it | ✅ done | `src/api.ts` derives `API_BASE` from `window.location.origin` when the page is served by the backend, and from `VITE_DEXTER_PORT` otherwise; `vite.config.ts` is the only file in the repo with a port literal left, and it is a default the environment overrides (`VITE_DEXTER_PORT`, `VITE_DEXTER_UI_PORT`) feeding a dev proxy for `/api` and `/health`. `npm run build`, `npx tsc --noEmit` and `npx vitest run` (17 across 3 files, +2) all green |
| P1c consumers discover instead of assuming | ✅ done | `apps.default.json`: `port` → `preferred_port`, sub-service ports under `extra_preferred` (BuzzEdit comfyui 8188, MidnightBuzz xtts 8096 / vite 3005, CaliberAI vite 5173, BuzzcafCoffee next 3000), `health_path` + `ledger_id` where an identity route exists. `apps_registry.live_port()` runs `discover()` and reports the live port in `/api/dexter/apps` and in "what's running"; `stop` by `port_owner` follows the live port instead of killing whoever holds the preferred one. `buzzcaf_client` now tries `BUZZCAF_API_URL` → the ledger → `studio.runtime.json` → a scan, every candidate still gated on `app:"buzzcaf"`. `hybrid_llm.engine_url()` (used by `code_agent` too) falls back to the ledger's `buzzcode` entry when `LLAMACPP_URL` is unset, and re-asks on each health refresh because buzzcode starts after Dexter. Ollama and LM Studio stay env-driven |
| P1d **a wrong port is worse than no port** | ✅ done (bug found live) | The first live run reported *"BuzzEdit: running on 8099"* while Dexter was sitting on 8099 — BuzzEdit's Electron really was up, but its backend was not there. `live_port` was falling back to "the preferred port is open", which for an app that *can* be identified is a guess dressed as a fact, and would send the next launch straight into the collision. Now the fallback applies only to apps with no identity route (llama.cpp), and `_row_line` names a port only when it was actually identified. Four regression tests. The same pass removed the last literal loopback URLs from the registry: a probe or a shutdown route is written `{base}/api/...` and resolved against the port the app is really on, because `http://127.0.0.1:8099/api/health` sitting in a record is a hardcoded port wearing a different hat - `test_the_shipped_registry_carries_no_literal_loopback_url` keeps it that way. BuzzBrain is the one record that publishes under someone else's name (`buzzcaf`): it borrows the Studio's port for its URL, but the Studio answering there is never taken as evidence that BuzzBrain is loaded |
| P1e docs | ✅ done | `ECOSYSTEM.md` gains **§3 Ports** (rules 1–8 condensed, the ledger path, the helper API, and the two things every copy of the helper must keep — no `SO_REUSEADDR`, and never `os.kill(pid, 0)` on Windows, where it *terminates* the process it was asked about); §2 and §9 rewritten around "preferred, not an address"; §10 documents `DEXTER_PORT`, `BUZZCAF_PORTS_FILE`, `VITE_DEXTER_PORT`/`VITE_DEXTER_UI_PORT`. Copied to `BuzzcafAI/` and verified byte-identical (SHA256 `696BE009…A2129F`, 22 956 bytes both), and a test now checks it rather than a `diff` somebody remembers to run. `PLAN.md` v9 table gains P1; `.env.example` gains a Ports block |
| **P4 integration: the apps run together** | ✅ done | The first time P1's ports work has been exercised against the *real* P2/P3 apps rather than dummy listeners. Acceptance A: the Studio and BuzzEdit's backend, both preferring **8099**, came up on **8099** and **8100** — `Port 8099 was busy; taking 8100 instead` — both in `%LOCALAPPDATA%\Buzzcaf\ports.json`, each `/health` naming itself (`app:"buzzcaf"` / `app:"buzzedit"`), and headless Dexter's "what's running" named both with those numbers. Acceptance B: 8098 held by a dummy listener, Dexter took **8099**, `test_dexter_suite.py` **31 passed, 2 failed** (only `Streaming chat` and `Tiered models: fast reply`, the two turns that need a model), and after the stop the ledger was `{}` with the squatter still listening on 8098. Then BuzzFin, BuzzcafCoffee, MastGaane and CaliberAI one at a time with their preferred port occupied: **8757, 8011, 8788, 8001**, each identified and reported by `apps_registry.live_port()`. Five defects fixed (below) |
| P4a **the registry had the wrong door for four apps** | ✅ done (bug found live) | `apps.default.json` gave BuzzFin `health_path: "/health"` — which is a **404** behind BuzzFin's security gate, so `discover("buzzfin", 8756, "/health")` answered `None` against a *running, healthy* BuzzFin while `/api/v1/health` answered `http://127.0.0.1:8757`. CaliberAI, BuzzcafCoffee, MastGaane, MidnightBuzz and BuzzViolin had **no** `health_path` at all, which drops discovery to the weaker rungs: with CaliberAI live on 8001 and a squatter on 8000, the pre-P4 record answered **8000 — the squatter** (`live_port` = 8000 vs 8001 after). All six corrected from routes read off the running apps: `buzzfin /api/v1/health`, `caliberai /api/health`, `buzzedit /api/health`, `buzzviolin /health.json`, the rest `/health`. `test_the_registry_knows_each_apps_real_health_route` pins all ten, and `test_the_apps_that_answer_no_identity_route_declare_none` pins `buzzcode` as the single honest exception (its listener is a `llama-server`; the body will never say `app`) |
| P4b **the app readers still carried literal ports** | ✅ done (bug found live) | P1 cleared literal loopback URLs out of the registry but not out of `tools_apps.py`, where `buzzedit_status`, `finance_status` and `coffee_brief` each held one: `:8099/api/health`, `:8756/health`, `:8010/ops/api/dashboard`. On this machine all three were wrong at once — 8099 was the **Studio** (BuzzEdit was on 8100), BuzzFin's `/health` is the blocked route, and `/ops/api/dashboard` is a 404 because `ops/run.py` mounts no `root_path`. `_reader` now takes a *route* and resolves the base from `apps_registry.live_port()`, refusing rather than guessing when it cannot place the app. Verified live: `coffee_brief` → *"The coffee ops: today: 2026-09-10"* against Coffee on **8011**. Six tests, including `test_no_reader_carries_a_literal_loopback_port` |
| P4c the copies test enforced nothing | ✅ done (bug found live) | `COPY_PATHS` in `test_buzzcaf_ports.py` was written before P2 landed and named folders that do not exist (`BuzzcafAI\backend\`, `BuzzFin\server\`, `CaliberAI\backend\`); a fuzzy "look next door" fallback happened to find **3** of the 8 real copies, and a missing file was a `skip`. Now the seven real Python paths and the four Node ones are named exactly, a checked-out repo with no copy is a **failure**, and the Node helper is checked too — nothing in Dexter imports it, which is exactly why its copies had no guard anywhere |
| P4d the owner's setup script could not see Dexter | ✅ done (bug found live) | `setup_guardian.ps1` looked for Dexter with `Get-Process pythonw, python \| Where { $_.Path -like "*\dexter\*" -or $_.CommandLine -like ... }`. Dexter runs on the *system* interpreter (`C:\Python314\python.exe`, no venv) so the path never carries the repo name, and `Get-Process` has no `CommandLine` in Windows PowerShell, so that half was always `$null`. With Dexter live on 8099 the script printed `Dexter restart TODO would launch Dexter.vbs` — and a real run would have started a **second** Dexter beside the first. It now calls its own `Discover-App` first, in check mode too: `[OK] Dexter discovery answering as app=dexter on port 8099 (pid 41684)`, and stops that pid rather than a guess. Guarded by `test_the_setup_script_discovers_dexter_before_it_decides_anything` |
| P4f one Node ledger reader, not four | ✅ done | P2 left `BuzzFin/ui`, `CaliberAI/frontend` and `MidnightBuzz/frontend` each carrying its own ~15-line inline ledger reader — the `LOCALAPPDATA`/XDG fallback, the JSON read, the swallowed error — which is a fourth implementation of a contract that is supposed to have one per language. `BuzzEdit/scripts/buzzcaf-ports.mjs` is now copied byte-identical into each repo's `scripts/` and imported (`allowJs`, `checkJs: false` where the config is type-checked, so the shared file never grows types of its own), and `test_every_copy_of_the_node_helper_is_byte_identical_to_the_canonical_one` covers all four copies. All three still pass `npx tsc --noEmit` and their build, and each was re-run against a scratch ledger to prove it really reads it. **Found on the way:** `BuzzFin/ui/vite.config.ts` had a raw line break inside a string literal, so the UI **would not build at all** (`Unterminated string literal`) — BuzzFin's gate is `uv run pytest`, which never touches the UI. Fixed; `npm run build` green |
| P4e the ledger outlived the app | ✅ done (bug found live) | Stopping the Studio and BuzzEdit gracefully left `buzzcaf -> 8099` and `buzzedit -> 8100` behind, both naming dead pids. Both withdrew only from `atexit` / a `finally` around `uvicorn.run`, and on Windows a console control event (Ctrl+Break, closing the window, the machine shutting down) ends the process from the CRT's default handler the moment uvicorn's handler returns — neither ever runs. Dexter was already right by accident: it withdraws inside the **lifespan shutdown**, which is part of uvicorn's application shutdown and therefore happens first. Both siblings now do the same, gated on owning the entry so an Electron-launched backend never deletes its launcher's record. After the fix the same stop left the ledger `{}` |

**Live evidence, P1 (2026-09-10, VrDeep rendering throughout).** Headless Dexter
(`uvicorn` only, no window), every model backend pointed at a dead port so
nothing could load: the card was at **0.2 GB free of 15.7** when this began,
VrDeep and a `llama-server` holding the rest, and Ollama was not running. The
alternative was LM Studio trying to JIT-load a 12B beside a running render.

```
# 8098 deliberately occupied by a dummy socket server (pid 43016)
READY port=8099
[Ports] Dexter is on 8099; published to C:\Users\singh\AppData\Local\Buzzcaf\ports.json

GET http://127.0.0.1:8099/health
{"app":"dexter","status":"ok","port":8099,"pid":51124,"version":"2.0.0"}

netstat        8098 -> pid 43016 (the squatter, untouched)   8099 -> pid 51124 (Dexter)
%LOCALAPPDATA%\Buzzcaf\ports.json
{"dexter": {"extra": {}, "health": "http://127.0.0.1:8099/health",
            "pid": 51124, "port": 8099, "started_at": "2026-09-10T15:15:49+00:00"}}

python test_dexter_suite.py          -> 31 passed, 2 failed
  [OK]   Ports: /health identifies this server: app=dexter port=8099 pid=51124 v2.0.0
  [OK]   Ports: Dexter is published in the ledger: ...ports.json -> port 8099, pid 51124
  [OK]   Ports: /status and /resources report the live port: both report 8099
  [OK]   Apps: "what's running": Dexter: running on 8099 (847 MB RAM, up 1 min); VrDeep: running
  [FAIL] Streaming chat / Tiered models: fast reply   <- the two model turns

# clean exit
[headless] stop file seen; shutting down
%LOCALAPPDATA%\Buzzcaf\ports.json    -> {}        8098 and 8099 both closed

# squatter released, started the same way again
READY port=8098
{"app":"dexter","status":"ok","port":8098,"pid":25296,"version":"2.0.0"}
python test_dexter_suite.py          -> 31 passed, 2 failed   (the same two)
  [OK]   Apps: "what's running": Dexter: running on 8098 (701 MB RAM, up 1 min)
ledger after stop                    -> {}

# and the documented entry point, on a scratch ledger, then hard-killed
DEXTER_PORT=8140 python backend/main.py
Starting Dexter Engine on http://127.0.0.1:8140
READY port=8140
{"app":"dexter","status":"ok","port":8140,"pid":35048,"version":"2.0.0"}
Stop-Process -Force        # no clean exit, so the entry is left behind
entry('dexter')            -> port 8140, pid 35048
_pid_alive(35048)          -> False
discover('dexter', 8140)   -> None      # a live pid is a precondition, not a detail
```

The result is identical on 8098 and on 8099, which is the whole point: the port
makes no difference to anything else, and the two failures are the two turns
that need a model. `Planner toggle` failed on the first run and turned out to be
a pre-existing suite bug rather than a port one — it ticked `commitments[-1]`,
which after any earlier run is a commitment already ticked ("already done"), and
on a planner without one would have ticked off something of the owner's. It now
only touches commitments the suite itself created.

**Live evidence, P4 (2026-09-10, VrDeep still rendering, 0.2 GB of VRAM free,
Ollama down).** The first run of the real apps *together*. Every app headless,
every model backend on a dead port, everything stopped afterwards.

```
# ── Acceptance A: two apps that both prefer 8099 ───────────────────────────
BuzzcafAI  cd backend && python -m app.main      -> READY port=8099
BuzzEdit   LM_STUDIO_AUTOSTART=0 python main.py  -> Port 8099 was busy; taking 8100 instead.
                                                   Published buzzedit -> 8100 in the port ledger.
GET :8099/health      {"app":"buzzcaf","status":"ok","port":8099,"pid":51764,"version":"0.5.0"}
GET :8100/api/health  {"app":"buzzedit","status":"ok","port":8100,"pid":24976,"version":"0.2.0"}

Dexter (DEXTER_TRAY=0, LLM backends on 9911/9912/9913)  -> READY port=8098
%LOCALAPPDATA%\Buzzcaf\ports.json
  buzzcaf  8099 pid 51764 | buzzedit 8100 pid 24976 extra{comfyui:8188} | dexter 8098 pid 48824

GET /api/dexter/apps  running: dexter 8098, buzzcafai 8099, buzzbrain 8099,
                               buzzedit 8100, vrdeep (no port)
chat "what's running"  -> tier 0, apps_overview
  5 running: Dexter: running on 8098 (662 MB RAM, up 0 min); Buzzcaf Studio:
  running on 8099; BuzzBrain: running on 8099; BuzzEdit: running on 8100
  (292 MB RAM, up 0 min); VrDeep: running (up 27 min). GPU: 15.5 of 15.7 GB
  used, 0.2 GB free (Red). RAM: 11.2 GB free (Green).

# all three stopped
ports.json -> {}          8098, 8099, 8100 all closed

# ── Acceptance B: Dexter's own port taken ──────────────────────────────────
# 8098 held by a dummy listener (pid 43568)
READY port=8099   {"app":"dexter","status":"ok","port":8099,"pid":41684,"version":"2.0.0"}
python test_dexter_suite.py     -> 31 passed, 2 failed
  [OK]   Ports: /health identifies this server: app=dexter port=8099 pid=41684
  [OK]   Ports: Dexter is published in the ledger: ... -> port 8099, pid 41684
  [OK]   Apps: "what's running": Dexter: running on 8099 (704 MB RAM, up 1 min)
  [FAIL] Streaming chat / Tiered models: fast reply     <- the two model turns
setup_guardian.ps1 -Check       -> [OK  ] Dexter discovery  answering as app=dexter
                                          on port 8099 (pid 41684)
# stopped
ports.json -> {}    netstat: 8098 -> pid 43568 (the squatter, untouched); 8099 closed

# ── One at a time, each with its preferred port occupied ───────────────────
BuzzFin         8756 busy -> 8757   live_port 8757  probe {"running":true,"evidence":"port 8757"}
BuzzcafCoffee   8010 busy -> 8011   live_port 8011  coffee_brief -> "today: 2026-09-10"
MastGaane       8787 busy -> 8788   live_port 8788
CaliberAI       8000 busy -> 8001   live_port 8001
                                    (pre-P4 record, no ledger entry: live_port 8000
                                     -- the squatter. That is the P4a defect.)
ports.json -> {} after each
```

**Live evidence, 2026-09-10** (headless Dexter on port 8110, every model backend
pointed at a dead port so nothing could load; stopped afterwards, port free):

```
resources.sentinel : {"connected": true, "version": null, "features": []}   <- v1, degraded correctly
resources.vram     : RTX 5060 Ti, 15099 MiB free of 16050, zone Green
resources.ram      : 22597 MiB avail of 32540, source "local" (psutil - v1 has no RamStatus)
resources.disk     : C: 94 GB free, B: 82 GB free, source "local"
apps               : 17 rows; VrDeep RUNNING (process pythonw.exe, up 2h55m),
                     Mission Reminder RUNNING (process electron.exe), 15 stopped
status.sentinel    : registrations [["dexter", 33716, "assistant", 5120]]
toolCount          : 135  (was 123 at v8)

> what's running        tier=0  apps_overview   2 running: VrDeep (up 2h55m); Mission Reminder (12 MB RAM, up 8 min). GPU: 0.9 of 15.7 GB used, 14.7 GB free (Green). RAM: 21.7 GB free (Green).
> is buzzfin running    tier=0  app_status      BuzzFin is not running.
> what's in the vrdeep queue  tier=1  vrdeep_queue   VrDeep is rendering; 1 job(s) in the queue (updated 34 min ago).
> clean up space        tier=0  request_cleanup This Sentinel build can't run the cleaner yet (no cleanup support), so I haven't freed anything. C: 95 GB free, B: 83 GB free. Open SmartCleaner yourself with "open smartcleaner".
```

That last line is the shape of the whole release: when the guardian cannot do
something, Dexter says so and offers the next best move, rather than reporting
"done" over a no-op.

**Live against Sentinel v2, 2026-09-10 17:00–17:35** (the cross-repo
integration run, `GUARDIAN_PLAN.md` §7 step E). Everything in the paragraph that
used to sit here — "only verified with a fake" — has now been run against the
real G10–G15 service on `\\.\pipe\SentinelDev`, with VrDeep rendering and the
installed v1 service still answering on its own pipe. Full write-up with the raw
output: [`../Sentinel/docs/GUARDIAN_REPORT.md`](../Sentinel/docs/GUARDIAN_REPORT.md)
§7.

| Was fake-only | Live evidence |
|---|---|
| `Hello` answering | `{"Hello":{"version":"2.0.0","features":["ram_reserve","query","priority","disk","evict","app_status","cleanup"]}}`; `router` logged `protocol 2.0.0` where the v1 run said `protocol 1 (no Hello)` |
| v2 `Register` | `{"Registered":{"token":"dexter-1"}}` — the token path, not v1's `"Ok"`. `GpuStatus.registrations` → `[["dexter", 43728, "assistant", 5120]]` |
| RAM admission | `reserve(2048, ram_mib=1024)` → granted, `ram_free_mib 22371`; the steward's real bookings went out as `2864 MiB VRAM + 644 MiB RAM` (startup warm) and `5490 + 1563` (escalation), both granted in 1–5 ms |
| `Query` books nothing | granted with the same figures as `Reserve`, and `GpuStatus.reservations` stayed `[]` |
| `RamStatus` | `/api/dexter/resources.ram` came from Sentinel (`Green`, 18753 MiB avail), not the psutil fallback |
| `DiskStatus` / `RequestCleanup` actually queueing | "clean up space" → tier 0, 2 ms, `{"Cleanup":{"queued":true,"reason":"a safe cleanup is queued; the tray runs it in your session"}}`, and Sentinel's tray then ran the **real** SmartCleaner CLI (`--headless --json --scan-only`): 1 598 870 files, 1 613 candidates, `freed_bytes 0`, `last_cleanup {"at":"2026-09-10T17:09:58",...,"ok":true}`, `CleanupDone` on the event stream |
| `AppStatus` | one row per labelled rule with live PIDs — `VrDeep pipeline` (pid 33228) and `dexter` (`registered_as: "dexter"`). This is where a Sentinel defect turned up: a whitelisted process lost its rule's label and vanished from the table (fixed in `class.rs`) |
| `tray_alive` | `true` with the dev tray up; the service's `WARN tray not polling` and `INFO tray is polling again` were both seen |
| The steward's reserve → load → release | `steward: reserve 2864 MiB VRAM + 644 MiB RAM for startup -> granted in 1 ms` / `[Steward] startup: qwen2.5:3b resident in 2.4s (2864 MiB booked, released)` |
| Scenario E in `test_dexter_suite.py` | ran instead of skipping: **30 passed, 0 failed** |
| Sentinel restarting under Dexter | happened three times during the run; the subscriber re-registered by itself each time, no assistant restart |
| Shutdown | kill → the row leaves `GpuStatus.registrations` within one tick (Sentinel's G15 reaper — the open item below, now closed); `sentinel_stop()` → release, unregister, and the row is gone |

**Three Dexter defects the live run found, each fixed with a test:**

1. **An escalated turn never released its booking.** `route()` reserved 5490 MiB
   for the smart model and nothing handed it back — the reservation stood for
   its whole 90 s TTL while the model's real VRAM was *also* visible to
   Sentinel, so the same gigabytes were counted twice and the next claimant was
   refused on memory nobody was using. `backend/router.py` now releases in a
   `finally` around the escalated turn.
   `test_router.py::test_an_escalated_turn_hands_the_booking_back` covers both
   the normal and the raising path. Verified live: `GpuStatus.reservations` is
   `[]` immediately after an escalation that logged a granted 5490 MiB reserve.
2. **Unprompted check-ins loaded a model behind the steward's back.** The
   morning brief called `llm_router.chat(provider="auto")` directly; Ollama
   JIT-loaded the 4.5 GB smart model and no `Reserve` was ever sent. The
   `test_no_module_loads_a_model_behind_the_stewards_back` rule only greps for
   `warm_model` / `set_keep_alive`, and an implicit load through a generate call
   is invisible to it. `companion._may_compose()` now asks the steward first and
   falls back to the template on a refusal — a nudge is never worth taking the
   card from a render. Two tests in `test_companion.py`.
   **Still open:** `reflection.py` (3 sites) and `tasks.py` (1) call
   `llm_router.generate` the same way and have not been rewired.
3. **`/api/dexter/apps` took 16 seconds.** Seventeen apps probed one after
   another, most of them stopped and each paying a TCP connect plus an HTTP
   timeout per dead port. The verification suite timed out on it and the Apps
   panel sat blank. `apps_registry.overview()` now runs the probes on a pool:
   **16.1 s → 2.7 s**, order preserved.
   `test_apps_registry.py::test_overview_probes_the_apps_in_parallel_and_keeps_their_order`.

Two bugs in `test_dexter_suite.py` itself, both of which had been hiding behind
the scenario's own skip: the registration check called `.get()` on
`GpuStatus.registrations` rows, which are tuples on the wire
(`'list' object has no attribute 'get'`), and the planner-toggle check picked
`tasks[-1]` — the *reminder* the add had armed, which cannot be ticked off.

**What is still fake or unproven on Dexter's side:** `Preempt` and `Evicted`
have still never arrived from a real service (nothing on this machine has
preempted anything); no refusal has yet carried a real `blockers` list, so the
"VrDeep has 10 GB" wording is still only exercised by a constructed `Decision`;
`open_app` has still never launched a real project; and the LM Studio rung of
the shed ladder has never run for real (`conftest.py` refuses it, by design).

**Still open / for you:**

1. Install the new Sentinel (`Sentinel\make-installer.ps1`, admin). G10–G15 has
   landed and scenario E has now been run against it as a **dev instance** on
   `\\.\pipe\SentinelDev` — see the live table above — but the service the
   machine actually runs is still the v1 build, so day to day Dexter is still
   degrading to v1. The installer's `migrate-config` merges `[disk]`,
   `evictable` and the new rules into your live config and keeps your
   `live_kill = true`, your thresholds and VrDeep at priority 90.
2. ~~Sentinel keeps a **stale registration** when a client is killed~~ — fixed
   on Sentinel's side (G15 reaper) and verified live in this run: after
   `Stop-Process -Force` on the headless backend, `GpuStatus.registrations` was
   `[]` on the next tick.
3. Move BuzzEdit's backend off **8099** in its `.env` (the Studio steps forward,
   BuzzEdit does not). The registry tells them apart by health body today, which
   works but is a trap waiting for a third app.
4. Decide `DEXTER_VRAM_BUDGET_MIB` with your real working set open — the steward
   reports the measured sizes in `/api/dexter/resources`.
5. `DEXTER_MANAGE_LMSTUDIO=1` today: Dexter will run `lms unload --all` on a red
   warning. Set it to `0` if you want BuzzEdit to own LM Studio.

## v8 status at a glance (2026-09-07) — instant replies, a coder with a window, a pop-up

Overnight session, requested as: "smaller model to reply, bigger model for tasks, instant replies; test Dexter properly; buzzcode in its own window, not headless; release memory once done; be me for a day and fix what breaks; modern light minimal UI as a single pop-up, the rest from the tray."

| Item | Status | Evidence |
|---|---|---|
| Fast tier really answers first | ✅ done | Before: whenever buzzcode's 27B was up on 8089, `route()` skipped the small model entirely (every message waited on the 27B). Now `fast_backend()` picks the server that lists `DEXTER_FAST_MODEL` (Ollama), independent of `smart_backend()`; `test_router.py::test_fast_tier_keeps_running_while_the_big_llama_server_is_up` |
| Replies under a second | ✅ done | Three machine-level causes found and fixed: (1) **Ollama had no CUDA backend** — `lib/ollama/cuda_v12` held 2 of 14 DLLs, no `cuda_v13`, no `vulkan`; every server log since Aug 19 said `library=cpu`. Restored from the official v0.32.14 zip, restarted; `ollama ps` now `100% GPU`. (2) **`localhost` costs 2.05 s per call** on this Windows (IPv6 first); `hybrid_llm.local_url()` forces 127.0.0.1, `.env` + `src/api.ts` updated. (3) A closed local port times out instead of refusing; `port_open()` does a 0.3 s TCP check before HTTP probes. Live: simple turns 0.3–0.8 s (were 3–7 s), first streamed token 0.32 s |
| Small model kept honest | ✅ done | `looks_like_action()` / `needs_live_data()` skip the 3B for instructions and personal questions; `claims_action()` / `is_refusal()` throw away a fast reply that pretends ("the commitment will be dropped") or declines ("I don't have access to your screen") and escalate; `_SentinelGate` streams tokens but never leaks `<ESCALATE>` or a false claim. The 7B gets the same treatment in `agent_loop`: one nudge when it claims without calling a tool. 12 tests |
| Model residency | ✅ done | Ollama `keep_alive` via native `/api/generate`: 3B pinned `DEXTER_FAST_KEEP_ALIVE` (2h) at startup and after replies; 7B released after `DEXTER_SMART_KEEP_ALIVE` (30m) idle and unloaded before a buzzcode job (`code_agent._make_room`); `/api/dexter/status` → `tiers` shows what is loaded and whether on GPU. `test_model_residency.py` |
| Streaming text no longer garbled | ✅ done | SSE bodies were decoded as ISO-8859-1 (“How’s” → “Howâ€™s”); `_consume_stream` forces UTF-8; regression test |
| buzzcode in its own window | ✅ done | `code_console.py` opens in a new console (CREATE_NEW_CONSOLE): task header, live tool calls / verify / model text, mirrors every event into `data/code_jobs/<id>.jsonl`; `code_agent._follow_log` tails it and finishes the job at the launcher's exit record while the window stays open to read. `DEXTER_CODE_WINDOW=0` restores the hidden pipe. Live-tested with a stub buzzcode (job done in 5.5 s, 1 file, window titled `buzzcode · <repo> · <task>`); a real 27B run was skipped on purpose (VrDeep pipeline was on the GPU) — do one with "buzzcode: …" |
| Memory released when done | ✅ done | buzzcode stops its own llama-server at the end of a one-shot run; Dexter stops any engine it started itself (`_release_memory`), forgets 8089 in the provider cache, and unloads its 7B before the job. Also found and unloaded a stray 12B that LM Studio had JIT-loaded (Dexter's `auto` provider had picked LM Studio after it woke) — `.env` now prefers `ollama` |
| A day as the owner | ✅ done | `scratchpad day_in_life.py` (34 turns: mission, plan, reminders, notes, memory, apps, files, screen, code, Studio, comms, sign-off) run twice. Pass 1 found 12 failures; pass 2 all real: notes, facts, "mark … done", "what's on my screen", Tokyo time, "cancel the X reminder", "close notepad", sign-offs, "what did we get done" are deterministic now (`test_day_to_day_phrases_are_deterministic`, 20 phrasings). `close_window` ranks whole-word matches (notepad ≠ Notepad++). `get_current_time(place)` with a 120-city zone map |
| Live suite current again | ✅ done | `test_dexter_suite.py`: browse → `browser_open`, planner add asserts `dueDate`/reminder, streaming asserts token events (audio moved to the voice pipeline), new "Tiered models" check. 16/20 → 20/20 |
| New look: light, minimal, pop-up first | ✅ done | `src/index.css` tokens (one accent, system font, no glow), 154 colour literals swept to tokens, `Header`/`ChatFeed` rewritten. `src/Popup.tsx` at `/?mode=popup`: Dexter's last message + one reply line, quick replies, reminder card, Esc hides, auto-hide 30 s after a Dexter-initiated message. `desktop_app.py`: two windows (pop-up on top bottom-right; full app hidden until asked), tray Ask/Open/Reindex/Quit, `Ctrl+Alt+D` toggles the pop-up, `DEXTER_START_MODE=full` for the old behaviour. `npm run build` + `vitest` green |
| Windows Copilot | ⚠️ assessed, not built | See "Copilot" below |

**Still open / for you:** run one real `buzzcode: …` task to see the window with the 27B; the Verification/Cleanup commitments and wins logged during the day run were removed, but the two "fixed dexter's instant replies" wins remain in the mission log; system volume was left at 35%; a Notepad++ window (REPORT.md) was closed by the old "close notepad" behaviour before the fix — the file is untouched.

**Copilot (Windows app):** installed (`Microsoft.Copilot` 152.x, `ms-copilot:` URI works). Its window exposes only Minimise/Maximise/Close/“Open quick view” to UI Automation; the chat is a WebView2 page. So "Dexter asks Copilot" is possible only by driving it like a web page (Dexter's own browser tools on `copilot.microsoft.com`, or keystrokes into the app + reading back through the page's accessibility tree once it wakes), and it is brittle. Privacy: everything typed goes to Microsoft's cloud under your account and lands in Copilot history. If built, the rule set should be: only text you explicitly address with "ask copilot …", never Dexter's context (mission, files, screen, memory, activity), redact emails/paths/keys with a warning, show exactly what was sent, and an off switch in Settings.

## v7 status at a glance (2026-09-06)

| Item | Status | Evidence |
|---|---|---|
| K1 mission store + tools (**M1**) | ✅ done | `mission.py`, `tools_mission.py` (9 tools), fast rules; `test_companion.py` routes "today's win is ...", "our mission is ...", "this week's focus is ...", "I'm blocked by ...", "log a win: ...", "how are we doing", "not now", "check in with me" without a model |
| K2 stance in every prompt (**M2**) | ✅ done | `companion.context_block()` injected in `agent_loop`, `fast_stance()` in `_fast_reply`; `chitchat` greetings → `greeting_reply()` (today's win / first open loop); test asserts the block carries mission, focus, win, blockers, wins and today |
| K3 Dexter speaks first (**M3**) | ✅ done | `companion.say()` stores the turn and publishes SSE `companion` with quick replies; frontend renders it in the feed with tappable answers; morning/midday/evening/weekly wired into the scheduler, `greet_if_needed()` at launch, code-done and Studio approvals through `on_event`; short replies ("done" logs the win and closes the commitment, "later" snoozes 2 h) tested; quiet hours + snooze + once-per-day tested |
| K4 coaching tails (**M4**) | ✅ done | `after_reply()`: "N of M today" on completion, overdue nudge on add (rate-limited 15 min); tested |
| K5 Mission card (**M5**) | ✅ done | `MissionCard.tsx` + `useMission`, `/api/dexter/mission` GET/POST; `tsc` + `vite build` green |
| K8 Claude Code logs (**M8**) | ✅ done | `claude_sessions.py` + `tools_claude.py`; `test_claude_sessions.py` (folding with noise/sidechain/tool-result skipped, project filter, live session, context lines, narratives, session story, routing); joins the "Right now" line and the model context; Settings → Read Claude Code's logs |
| K7 activity awareness (**M7**) | ✅ done | `activity.py` + `tools_activity.py`; `test_activity.py` (sessions/totals from samples, project from title/path, budgeted recent-file scan skipping excludes and logs, narratives, context lines, fast rules); `/api/dexter/activity`; "Right now" line on the Mission card and in the model context; off-mission + long-stretch initiatives; nightly log compaction |
| K6 initiative + switch (**M6**) | ✅ done | `initiative.py` (6 candidates, once each per day, 4/day cap) on the proactive cron; `test_initiative.py` proves priority order, dedup, cap, evening nudge, and that mode `me` silences check-ins, tails, nudges and proposals while events still land; `set_initiative` tool + fast rules ("take the lead", "I'll take it from here"); `POST /api/dexter/initiative`; the YOU LEAD / DEXTER LEADS pill on the Mission card |

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
