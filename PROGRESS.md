# Dexter — Progress Log

Running record of what has actually landed, newest first. Plan: [`PLAN.md`](PLAN.md).
Every row is either **done** (with the evidence that proves it) or **open**.
Nothing is marked done on intent.

## 2026-10-15 — MastGaane is Dexter's music

The owner asked for MastGaane inside Dexter. (An Alexa skill route was built and then removed the
same day: Dexter controls the home devices directly, so Alexa is not needed and nothing is public.)

- `tools_music.py`: `mastgaane_play(focus)` ("play some ghazals", "play Hawa Mahal", "play songs by
  Jagjit Singh", bare "play some music") and `mastgaane_feedback` ("I like this song", "never play
  this again"), over MastGaane's new `/api/player/*` (its jockey on the PC speakers via ffplay).
  MastGaane is found by `live_port` and started through `open_app` if needed. `media_control` /
  `get_now_playing` hand over to MastGaane while it plays (ffplay never reaches the media keys).
  Fast rules in `router.py` ahead of the rest; "play some lofi music" / "put on some jazz" stay
  `play_on_youtube`.

**Evidence.** `test_tools_music.py` 20 passed. Full backend suite: 12 failures, the same 12 fail
with the music rules stripped out (pre-existing). Live, Dexter's tools against MastGaane on 8788:
"play hawa mahal" → episode playing, pause / resume / stop via `media_control`.
**Open:** Dexter restart to load the tools.

## v13.9.1 (2026-09-12) — The same screen is not read twice

The one caveat left open by v13.9: *"reading the screen costs 2-3 s per look and isn't
cached, so picking text is deliberate rather than instant."* It is cached now, and the
interesting part is what a frame hash alone turned out **not** to fix.

### A frame hash works, and almost never fires

Every reading is filed under a blake2b fingerprint of the pixels it read - 5.5 ms at
1080p, 22 ms at 4K, against seconds of OCR. Identical frame, identical answer:

| | |
|---|---|
| first read, both monitors (3840×1080, 178 boxes) | **11.9 s** |
| the same frame again | **0.012 s** |
| a fresh capture of the same desktop | **10.4 s — cache missed** |

That third row is the finding. A real desktop is never byte-for-byte the same twice: a
clock ticks, a caret blinks, an FPS counter counts. Measured over four captures 0.6 s
apart, **0.03-0.07% of pixels changed every time** - 13 tiles of 384, scattered widely
enough that re-reading "the changed region" would mean re-reading half the screen. A
cache that only fires on an identical frame would therefore have missed on almost every
real repeat pick, which is exactly the case it was built for.

### So the unit of proof is the word, not the frame

In that same measurement, **206 of 210 words sat on pixels that had not changed at
all**. So a click proves the smaller and far more useful thing: the pixels *under the
word asked for*. If they are unchanged, that word is still drawn there - nothing
scrolled it away and nothing is covering it, because either would have changed them.
Each reading therefore stores a fingerprint per word as well as per frame.

Live, on the owner's desktop, 1.2 s after a full read:

| | |
|---|---|
| first read of both monitors | **9.8 s** |
| `recall("Example Domains")` → clickable position | **2.9 ms** |
| `recall("BuzzAnimate")` | **2.2 ms** |
| `recall("FPS60GPU36%CPU12%")` | **refused** — its own pixels had changed, so it bought a real read |

That last row is the safety property working out loud, and it is the reason this is a
recall rather than a guess. Absence from a remembered reading proves nothing either -
the word may have appeared since - so a miss always buys a full read.

### What it cost

`backend/screen_text.py` gains the frame cache (`DEXTER_SCREEN_CACHE`, TTL 120 s, six
frames), per-word fingerprints, and `recall()`; `click()` now captures once and tries
the recall before paying for OCR. `read_screen` - "what does this say" - still pays in
full unless the frame is identical: there is nothing to prove a word by when every word
is wanted. The reply says which route it took ("still exactly where I last saw it")
so the speed is never mysterious. 34 tests in `test_screen_text.py`, 1618 backend green
(the two `test_buzzcaf_ports.py` failures are the same pre-existing drift in the sibling
repo).

## v13.9 (2026-09-12) — Better tracking, the right Chrome, clicking words, every project, and "stop"

Five asks in one sitting, and three of them turned on a measurement rather than an opinion.

### "Improve the hand tracking"

Measured first, on the owner's own webcam:

| | |
|---|---|
| palm detection | 5.7 ms per frame |
| landmark model | 3.1 ms per frame |
| camera, DirectShow 640×480 | **16.6 fps** |
| camera, Media Foundation 640×360 | **30.3 fps** (30.2 / 30.3 / 30.3 across three trials) |

**The models were never the problem.** Nine milliseconds of work was waiting sixty for a
frame. So:

1. **Twice the frames.** Media Foundation at 640×360 is this camera's native 16:9 mode -
   same horizontal detail as 640×480, double the rate. Frames are the currency of
   everything else: smoothing, debouncing, and motion blur per frame.
2. **The landmarks are filtered before anything reads them.** This was the real gap -
   *every* pose decision (finger out, pinch width, thumb reach) was computed from
   coordinates that jitter a pixel or two per frame, so a hand held still could flicker
   between poses. A one-euro filter now sits under all of it, **tuned by measurement**
   because the published defaults assume metres and these are fractions of a frame:

   | | still | slow move | fast move |
   |---|---|---|---|
   | unfiltered | 7.6 px error / 10.8 jitter | 7.6 / 11.0 | 7.6 / 12.4 |
   | 1.2 Hz, β=8 | 2.8 / 1.8 | 5.3 / 2.9 | 8.9 / 6.2 |
   | **1.2 Hz, β=30** | **3.3 / 2.3** | **4.4 / 3.5** | **5.7 / 6.5** |
   | 1.2 Hz, β=60 | 3.9 / 2.8 | 4.5 / 4.1 | 5.3 / 7.0 |

   β=30 is the knee: **more accurate than no filter at every speed**, with half the
   jitter. β=0.35 (my first guess) bought stillness with lag.
3. **A finger is "out" by the angle at its joint**, not by how far its tip is from the
   wrist. The old measure fails on a hand tilted towards the camera, where foreshortening
   shrinks every distance equally - and it has a dead band now, so a finger resting at the
   threshold stops turning two fingers into three and back.

### "Open a website in the Chrome I open, not a different one"

My fault, from v13.4. Dexter's debuggable Chrome is a separate browser on its own profile,
and nothing told the rest of the code that: `chrome_windows()` counted it as the owner's,
so "open youtube" could bring *Dexter's* browser forward and everything after it pointed
at a window they were not using. It is now identified by its profile on the command line
and excluded from "the Chrome you use", and `precise()` demotes itself to the
accessibility route whenever the browser in front is the owner's. Nothing to configure:
whichever browser is in play decides.

### "Click on a text on the browser or screen without the precise browser"

Measured, because the obvious answer kept failing:

| route | what it saw of the page |
|---|---|
| UIA on the Chrome window | 35 controls - Minimize, Back, Reload, Bookmark, the profile button - **nothing from the page** |
| after the documented `WM_GETOBJECT` poke | unchanged (Chrome answered; the tree stayed empty) |
| walking the render widget directly | 0 named nodes |
| MSAA through `oleacc` | 0 named nodes |
| the system screen-reader flag | already on, and still nothing |

**Modern Chrome does not publish page content to Windows accessibility at all** unless it
was started with `--force-renderer-accessibility`. That is the whole reason "select the
first profile" and "click the text X" failed - and the profile picker is itself a page.

So Dexter reads the screen. `screen_text.py` captures the window, OCRs it with
`rapidocr_onnxruntime` (already installed, offline, ONNX), and clicks the words. Verified
live: **27 pieces of text from a Chrome window in 2.2 s, "Learn more" found at score 1.0,
clicked, and the tab navigated.** Full resolution turned out to be both faster *and* more
accurate than downscaling - the detector resizes internally anyway, and shrinking first
turns "BuzzAnimate" into "BursAnirute".

Three things fell out of building it:

* **`SetForegroundWindow` does not work** from a background process - plain calls and
  `ShowWindow(SW_RESTORE)` both changed nothing, while minimize-then-restore won the
  foreground first time. A window that is not in front cannot be photographed, and a
  capture of the region would otherwise be a picture of the desktop - which is exactly
  what the first attempt produced.
* **`PrintWindow` comes back blank** for GPU-composited Chrome, so the window has to be
  raised rather than captured in place.
* The fallback is wired into what already exists rather than bolted on beside it:
  `chrome_control.click` tries the accessible name first and reads the screen second,
  `select_nth` falls back to reading the screen, and so does "is there a video called X".
  Plain "click sign in" therefore keeps its instant path and gains a slow one.

### "Launch all my projects by name"

Twenty-one folders next to Dexter, sixteen with a launcher, named however they were named:
`BuzzAnimate.bat`, `start_buzzcafai.bat`, `START_APP.bat`, `run.bat`, `start.cmd`. Finding
the project is fuzzy ("mission reminder" reaches a folder spelled `missionRemider`);
finding the *launcher* is a judgement, because the folders also hold `stop_app.bat`,
`setup.bat`, `launch_debug.bat`, `make-installer.bat` and `uninstall`. Scripts are scored:
the project's own name and start/launch/run earn points, teardown and build words lose
them heavily. **Twelve projects come out startable, and four are named instead of guessed
at** - BuzzCode, BuzzWorlds, MastGaane and Sentinel have only build scripts, and running
one of those on "open" is not a small mistake. The twelve in `apps_registry` still go
through their own route, so Sentinel is still asked for memory before a render starts.

### "I can't interrupt it mid-sentence"

The word already worked - "stop" has cut a reply since v10 - but the microphone was ignored
*entirely* while Dexter spoke, so it never arrived. Full barge-in was the only alternative
and it is off for a good reason: it fires on any sound, including Dexter's own voice coming
back through the speakers, which is the echo loop that cost an evening in v10.

Now the mic stays open while Dexter talks and what it hears may be **one thing only**: a
control phrase. Everything else from that window is dropped unheard. That is what makes it
safe where barge-in is not - Dexter's own voice transcribes to the words of its own reply,
and those are not control phrases, so **the echo loop is unreachable rather than
unlikely**. Say "stop", "be quiet" or "never mind" over a reply and it stops. Full barge-in
remains one setting away for headphones.

### Also

Two test flakes fixed rather than re-run: `test_activity` asserted "30 min" where a clock
tick makes it 29, and `test_claude_sessions` seeded a session "three hours ago" - which at
00:24 is *yesterday*. And three ordering regressions of my own, all caught by existing
tests: a generic "is X open" rule stealing `local_model_status`, a screen-reading rule
stealing "what's on my screen" from `get_active_window` and "read this page" from
`browser_read_page`, and a new click rule stealing "click sign in" from `browser_click`.
The fast path rewards specificity, and a catch-all belongs last.

Evidence: 15 cases in `backend/test_hand_tracking.py`, 20 in `test_screen_text.py`, 24 in
`test_project_launcher.py`, 6 new voice-interrupt cases, and conftest guards so no test can
photograph the owner's screen, move their mouse, or mistake a real Chrome for Dexter's.
Suite: **1604 backend passing**, the same 2 pre-existing drift failures; **42 frontend**;
bundle rebuilt.

## v13.8 (2026-09-12) — A pointer you can aim, a click that lands, and answers instead of lists

Three asks in one sitting.

### "Can we control the speed of the hand mouse pointer?"

Yes — and it is worth saying what "speed" even means here, because the pointer is
**absolute**: a rectangle of the camera image maps onto the whole screen, so your hand
has a *place* rather than a velocity. Speed is therefore the **size of that
rectangle**, scaled about its centre: above 1 it shrinks, so less hand movement
crosses the screen (faster, twitchier); below 1 it grows (slower, far easier to aim).
Clamped to 0.5–2.5, and never allowed to hang off the edge of the frame, because then
the far side of the screen would need a hand that has left the picture.

Three ways to set it, all live while tracking: a **slider on the dashboard panel**,
Settings → Hand mouse, or out loud — *"make the hand mouse slower"*, *"the hand
pointer is too fast"*, *"set the hand speed to 1.5"*. The spoken route matters more
than it looks: **when the pointer is too fast, reaching the slider that fixes it is
precisely the thing you cannot do.**

### "Clicking seems difficult as it is unstable"

Three separate things were fighting the owner's hand, and all three are now fixed:

| | |
|---|---|
| **The pointer kept moving through the click.** Pinching *is* a movement — thumb and finger come together, the whole hand shifts — so the press landed a few pixels from wherever it had been aimed. | The pointer now **holds still** from the moment a click pose appears, before the button is even confirmed. Move past the drag distance *and* hold for 0.3 s and it is unmistakably a drag, so the lock is dropped and the pointer follows again. The same "tap or drag?" rule a touchscreen uses: time, then distance. |
| **One noisy frame could fire a click, or drop one.** | A button goes down only after the pose has been seen `confirm_frames` (2) times in a row, and comes up only once it has been gone that many. At ~20 fps that is a tenth of a second — far below a deliberate tap, far above a single frame of landmark noise. **Park is exempt**: "any button is released" has to mean *now*. |
| **Holding a pinch curls the finger it pinches with**, which lowers the very `pinch_reach` measurement that had allowed the pinch to start — so a held click could fail its own test and be dropped mid-gesture. | While a pinch is held, its bar is relaxed to 72%. Only that pinch, so a **fist is still never a click** — the reason the bar exists. |

The drag distance was **sized from the geometry rather than guessed**: at speed 1 the
control rectangle is 0.64 of the frame across 1920 px, so one centimetre of hand at
arm's length is about 60 px, the incidental movement of making a pinch is 30–90, and a
deliberate drag is 300+. 120 sits cleanly between, and scales with speed. The first
attempt used 30 px, which every real pinch would have broken into a drag — caught by
a test that moved a hand the distance a hand actually moves.

The panel and the preview window both say **"holding still"** when the pointer is
locked, because a pointer that stops without explanation looks like a freeze, and this
project has earned that suspicion.

### "Dexter can't check if Chrome is open, or if a tab is open, or if a video is on the page"

Correct, and the reason is worth recording: every capability already existed —
`desktop_control` enumerates windows and processes, `browser_cdp` lists a real Chrome's
tabs with their URLs, `page_control.results()` reads what is on a page. But **every
tool returned a list**, and the model had to turn a list into a yes or no. That
inference is exactly where a confident wrong answer comes from.

So `backend/verify.py` + `tools_verify.py` answer questions, each with **how it knows**:

| asked | answered |
|---|---|
| "is chrome open" | *Yes — chrome is open with 1 window (from the open windows): Google Chrome.* |
| "is youtube open" | *Yes — youtube is open (tab 1): lofi hip hop radio - YouTube (exactly, reading Chrome's own tabs).* |
| "is there a video called lofi hip hop on this page" | *Yes — number 1 is "1 A.M Study Session 📚 [lofi hip hop]" (and 4 more like it). Checked 16 items, exactly, reading Chrome's own tabs.* |
| "what page am I on" | the title and the URL |

Verified live over Playwright/CDP against a real YouTube results page — the titles and
counts above are that run, not an example.

**Three things deliberately built in:**

1. **"I can't tell" is a real answer.** Without the exact route, Windows shows only the
   *front* tab of each browser window — so a "no" about some other tab would be a
   guess. It says so, and says how to fix it: *"Say 'start precise browser' and I can
   read every tab."*
2. **Weak evidence is not a yes.** `find_processes` also matches an app's name anywhere
   in a process's **command line**, which is right for "stop the thing I named" and
   much too loose for a question — asking "is notepad open?" from a shell whose own
   command line contained the word answered **yes, three times over**. A verdict needs
   the name to *be* the program.
3. **A generic check goes last.** `is_app_open` initially claimed "is the local model
   running", which `local_model_status` owns and answers far better. Every specific
   checker comes first; the window scan is the last resort. Caught by an existing test,
   which is the second time this week that ordering in the fast path has mattered more
   than the rule itself.

### Also

Two unrelated test flakes, fixed rather than re-run: `test_activity` asserted "30 min"
where a clock tick makes it 29, and `test_claude_sessions` seeded a session "three
hours ago" — which, at 00:24, is *yesterday*, so "today" counted one session instead of
two. A suite that fails after midnight is a defect in the suite.

Evidence: 12 new cases in `backend/test_hand_gestures.py` (speed as rectangle size,
the screen corners still reachable at every speed, the pointer holding still, a drag
still working, a flicker neither firing nor dropping a click, a held pinch surviving
the curl, a fist still never clicking), 26 in `backend/test_verify.py`, 4 in
`src/test/HandMouse.test.tsx`, and 6 router cases. Fifteen existing click tests were
updated to the new contract — a click now has to persist — with their assertions
unchanged. Suite: **1539 backend passing**, same 2 pre-existing drift failures; **42
frontend**; bundle rebuilt.

## v13.7d (2026-09-11) — One espeak library, not four copies per sentence

Noticed while diagnosing the freeze, fixed on the owner's "do it". `phonemizer` copies
the **entire espeak-ng DLL** into a fresh temp directory every time a backend is
constructed, and then loads the copy:

```python
self._tempdir = tempfile.mkdtemp()
shutil.copy(espeak, self._tempdir / f'{espeak.stem}_{uuid4()}{espeak.suffix}')
```

It has a reason — espeak-ng keeps its state in globals, so two live wrappers would
tread on each other. But `EspeakBackend.__init__` builds **two** wrappers (one inside
`is_available()`), `phonemizer.phonemize()` builds a backend per call, and Kokoro
phonemizes every chunk. Measured: **13 copies for four spoken sentences**, each
419 KB, every one left behind.

On this machine that had accumulated **1,172 directories, 468.7 MB**.

| | before | after |
|---|---|---|
| temp directories per 4 chunks | +13 | **+0** |
| wrappers built per process | one per call | **1** |
| leftovers in `%TEMP%` | 1,172 (468.7 MB) | **0** |
| time per chunk | 0.40–0.53 s | 0.37–0.53 s |

The time is honestly unchanged — the copy is fast once Windows has the file cached.
What goes away is three file copies per spoken sentence and half a gigabyte of litter,
on the exact code path where a frozen Dexter's synthesis thread was found sitting.

`backend/espeak_cache.py` does three things: shares one wrapper for the life of the
process (patching **all three** phonemizer modules that build one — patching only
`base` left `espeak.available_voices` still copying, four per chunk down to one rather
than none), holds a `synthesis_lock` that `voice_engine` takes around Kokoro, since
sharing is only sound if calls do not overlap, and sweeps the backlog.

**The sweep is deliberately strict**, because it runs over the whole temp folder: a
`tempfile.mkdtemp()`-shaped name, containing **exactly one** file, whose name is the
copied library, untouched for ten minutes. The owner's `%TEMP%` also held 211
single-file directories containing a `ports.json`; none of them matched. Ran it: 1,172
removed, 0 failed, **468.7 MB freed**. Dexter now sweeps once at startup, in the
thread that warms the voice, so this cannot build up again.

If the patch ever fails to apply — a new phonemizer layout, say — it says so and
everything works exactly as before.

Evidence: `backend/test_espeak_cache.py` (16 cases — one wrapper however many
backends, every module patched, and five cases pinning what the sweep must *not*
delete). Suite: **1495 passing**, same 2 pre-existing drift failures.

## v13.7c (2026-09-11) — Found it: one line of mine froze the interpreter

The owner left the third freeze running instead of killing it, and that changed
everything. The watchdog's heartbeat **stopped mid-turn** and `stacks.log` was
**empty** — which is itself the answer: the watchdog could not run either. No Python
thread could. That means the GIL was held and never given back, so `faulthandler`
was never going to work; the right tool was `py-spy`, which reads another process's
memory without needing the interpreter's cooperation.

```
Thread 34820 "Thread-14 (_call)":
    NtUserSetWindowPos (win32u.dll)                  <- holds the GIL
    System.Windows.Forms.ni.dll
    set_on_top (webview/platforms/winforms.py:1006)
    on_top (webview/window.py:267)
    _raise (desktop_app.py:443)                      <- mine, v13.2
    show_popup (desktop_app.py:400)

Thread 21688 "Dummy-9" (the GUI thread):
    PyGILState_Ensure (python314.dll)                <- waits for the GIL

every other thread:
    PyEval_RestoreThread (python314.dll)             <- waits for the GIL
```

Windows agreed: `IsHungAppWindow` was **true** for both Dexter windows, on that
thread, with no modal dialog anywhere.

### The deadlock, in one line

```python
window.on_top = True      # v13.2, to raise the full window above the on-top pop-up
```

pywebview turns that into .NET's `TopMost`, .NET turns *that* into a **synchronous**
`SetWindowPos`, and `SetWindowPos` on a window owned by another thread **sends** it
messages and waits. pythonnet does not release the GIL across a .NET call, and the
GUI thread needs the GIL to run the Python handlers wired to its window events. Two
threads in a circle — and because one of them was holding the GIL, **every** other
thread stopped with them. No logging, no heartbeat, no scheduler, no voice, no UI.
A process using 0% CPU, doing absolutely nothing, with 2 GB resident.

It explains every symptom of all three freezes, including the ones that sent me
chasing ghosts: the log ending mid-sentence (the next `print` needed the GIL), the
"wedged" disk I/O (those threads were waiting for the GIL, not the disk), and a
`tr.exe` misfire that was real but unrelated.

**And it was always right after a reply.** `show_popup` is what Dexter calls when it
has something to say, which is why every freeze followed a voice turn, and why it
looked like the *question* was to blame.

### The fix

Everything after `show()` in the raise path now goes through Win32 by hand:

| | |
|---|---|
| `_nudge_to_front` | `SetWindowPos` via **ctypes** (which does release the GIL) with **`SWP_ASYNCWINDOWPOS`**, which *posts* the request instead of waiting for it. Neither half of the deadlock is possible. |
| `_restore` | `ShowWindow(SW_RESTORE)` instead of `window.restore()` — another marshalled property that moves the window. |
| `_place` | the same for the off-screen rescue, which used `window.move()` / `window.resize()`. |
| `_handle` | finds the HWND once and remembers it, so a raise never goes back through .NET. |

The regression test asserts the **absence** of those calls: a window object that
records every marshalled property, and `_raise` must leave it empty.

### About the voice

"There is no voice any more" is the same bug. Two clips *were* synthesised at 23:26
and are still on disk — but the thread that delivers audio events to the page was
starved of the GIL, so nothing ever arrived. Synthesis itself measures 1.6 s for the
first chunk and 0.5 s after.

Two things came out of checking it anyway:

* **Mute is now visible in the full window.** Dexter's speaker can be muted
  separately from the microphone (v13.2), and that state had an indicator only in the
  pop-up — so "no voice" had a plausible cause with no way to see it. The header now
  shows a **Muted** button when it is on, and clicking it unmutes.
* **A silent turn says why in the log.** `[Stream] No audio for this turn: the page
  sent voiceEnabled=false`. Four different things can suppress speech and none of
  them were visible from the backend.

Noted, not fixed: phonemizer copies the whole espeak DLL to a fresh temp directory on
**every** synthesis — there are 1,172 of those copies in `%TEMP%` on this machine.
Wasteful, and it is what the frozen thread happened to be sitting in, but it is not
the bug.

Evidence: 5 new cases in `backend/test_desktop_window.py` (the absence of `on_top`
and `restore`, the cached handle, Win32 placement, and the no-handle fallback). Suite:
**1479 passing**, same 2 pre-existing drift failures; 38 frontend; bundle rebuilt.

**The lesson worth keeping:** when a process goes completely silent, the GIL is the
suspect, and in-process instrumentation cannot help by definition. `py-spy dump --pid
<pid> --native` is the first move, not the last.

## v13.7b (2026-09-11) — It hung again, so now it keeps its own evidence

"I had to close dexter because it hanged again." Second freeze, same shape: the log
ends mid-turn and that is **all** there is.

```
2026-09-11 23:06:34  faster_whisper: Processing audio with duration 00:04.400
[Voice] Heard (4.4s, conf 0.72): 'can I set up my hand tracking?'
Batches: 100%|####| 1/1     <- the semantic match
Batches: 100%|####| 1/1     <- the memory recall
(nothing, ever again)
```

Reproduced on its own, that turn is fine: Tier 1 → `hand_mouse_setup` at 0.90
confidence, 8.6 s, no camera. The reply's text-to-speech is fine too (the path in it
is stripped before speaking). So the hang is not in the turn's *content*, and after
two evenings of plausible-but-unconfirmable theories the honest conclusion was that
**the instrumentation was the missing feature, not the next hypothesis.**

### One real defect found, on exactly that path

```python
finally:
    tts_queue.put(None)
    await run_in_threadpool(tts_thread.join)     # no timeout
    emit("done", {})
```

The streaming turn joins its speech thread **without a deadline**, and that join sits
between a turn and the `done` event the page waits for. If synthesis ever sticks —
Kokoro, onnxruntime, anywhere — the stream never ends; and since the page's
`isThinking` never clears, and **the microphone lives in that page**, Dexter stops
listening as well. Total silence, process alive, nothing logged. That is the exact
symptom, twice.

Both halves are now bounded: the join waits `DEXTER_TTS_JOIN_TIMEOUT` (25 s) and then
answers anyway, saying so and dumping stacks; and the page gives up on a stream that
has sent nothing for 150 s rather than waiting forever, with a message instead of
silence. Losing the tail of a spoken sentence is a small cost. Losing the assistant is
not.

### And the watchdog, so the next one is not a guessing game

`backend/watchdog.py`, started before anything else can get stuck:

| | |
|---|---|
| `data/heartbeat.txt` | every 10 s: thread count, turns completed, **the age of the turn in flight**, and when the last HTTP request arrived. Written with `os.write` to a raw descriptor — deliberately *not* through `logging`, because a jammed logging handler silences every thread and is itself a suspect. |
| `data/stacks.log` | **every thread's stack** the moment a turn passes 25 s, naming the blocked thread and the frame it is in. Also on a hard native crash, which under `pythonw` otherwise leaves nothing at all. |
| `POST /api/dexter/debug/stacks` | the same dump on demand, for when Dexter has gone quiet but HTTP still answers. |

That last row is the fork this investigation never resolved: **if requests stop while
heartbeats continue, the front end froze; if heartbeats stop too, the process did.**
One line in a file will now settle it.

Building it found a bug in itself, caught by its own test: two overlapping turns (the
pop-up and the full window both route) shared a token, so finishing one hid the other.
Turns in flight are a dict now, and the **oldest** is the suspect.

Evidence: `backend/test_watchdog.py` (13 cases — that the heartbeat survives a broken
`logging`, that a stuck turn yields the blocked thread's frames, that it dumps once
rather than per tick, and that a crashed turn never looks stuck forever), verified
end-to-end against a thread that really does block. Suite: **1474 passing**, the same
2 pre-existing drift failures; 38 frontend; bundle rebuilt.

**Still open:** the cause of the two freezes is not confirmed. What is fixed is the
unbounded wait that matches the symptom precisely, and what is new is that the next
occurrence will name the thread.

## v13.7 (2026-09-11) — "Open the hand tracking" launched GNU `tr`

Reported as "dexter is hanging now". The log says what happened in two lines:

```
[Voice] Heard (3.9s, conf 0.72): 'can you open the hand tracking?'
[Smart Launcher] Attempt 1: 'C:\Program Files\Git\usr\bin\tr.exe'
```

and then nothing, ever again.

**Two bugs met.** The first is one line in `app_index.lookup`, which matched a spoken
name against the installed-app index by plain substring, in both directions,
preferring the **shortest** key:

```python
candidates = [(len(key), key) for key in apps if target in key or key in target]
return apps[min(candidates)[1]]
```

`"tr" in "hand tracking"` is true, and `tr` is two characters, so it won. The same
line had already sent **"open Person to Profile" → `pr.exe`** and **"open the key" →
`keytool.exe`** earlier the same day; all three are in today's log. The index held
1,602 "applications", among them `rm`, `dd`, `sh`, `ls` and `bash` — every one of them
a phrase away from being launched.

The second is why it *stuck*: `tr` with no operands **waits on stdin forever**. Two of
them were still sitting on the machine hours later, and the console window Windows
allocates for them had come to the foreground and stayed. An assistant that appears to
have done nothing, with a dead black window in front of everything, is
indistinguishable from a hang.

### Three fixes, at three depths

| | |
|---|---|
| **Matching** | A name shorter than four characters now only ever matches **exactly** (so `vlc` still works and `tr` never does); longer ones match on **whole words** — `tr` is no longer "inside" `tracking`. Query-inside-name still prefers the shortest key ("code" → Code, not Code Insiders); name-inside-query now prefers the **longest**, i.e. the most specific app actually named. |
| **What counts as an app** | An application is the thing with a window. The scan reads each candidate's PE header and skips console programs, and skips `Git\usr\bin`, bundled `jre\bin` and their like outright. **1,602 entries → 692**, and the 614 dropped are `adb`, `bash`, `7za`, `aerender`, the Docker CLIs… Start Menu shortcuts are exempt, because somebody made those on purpose — so "git bash", "command prompt", "powershell" and "python" all still resolve, through the shortcut that carries the right arguments. |
| **Launching** | Nothing Dexter starts keeps an open stdin. A console program goes through `Popen(..., stdin=DEVNULL)`, so a misresolved name can never again leave a process waiting for somebody to type into it. Dexter is not a terminal; it has `run_command` for that, and that one asks first. |

### And the hand mouse is not a program on the disk

The deeper mistake: "open the hand tracking" asks for one of **Dexter's own features**,
and it was being searched for out in Program Files. Five Tier-0 rules now sit *above*
the app and site rules — start, stop, status, list the gestures, change one — so the
sentence the owner actually said is instant and never reaches the launcher:

* "can you open the hand tracking?", "show me the hand tracking", "use the gesture
  control" → **start**
* "turn off the hand mouse", "close the hand tracker" → **stop**
* "is hand tracking on", "what gesture do you see" → **status**
* "what do the hand gestures do" → **the map**
* "make a fist the right click", "set the open palm to scroll" → **rebind**, and that
  builder yields unless *both* halves name a real pose and a real action, so "make the
  window bigger" is left alone.

### One theory, tested and wrong

The first explanation was that reading the foreground window's title — which happens on
**every** chat turn, to tell the model what is on screen — had blocked on the wedged
console, `GetWindowTextW` being a `SendMessage` with no timeout. It is not: Windows
reads another process's caption directly, by design, *precisely* so this cannot hang.
Proven here against a window with no message loop at all (returned in 0.0 s), so the
module written on that premise was deleted rather than kept. The per-turn context build
was then measured with a wedged console in the foreground: unaffected.

So the hang itself is **not reproduced in the backend** — that turn routes in 0.0 s,
the v13.6 status polling costs 0.3 ms, and nothing on the chat path blocks. What is
proven is the misfire, and that it leaves a stuck console window owning the foreground.
If it recurs after a restart, the next place to look is the shell and its WebView2
windows, not the router.

Evidence: `backend/test_app_index.py` (14 cases — the three sentences that went wrong,
the near-misses that must still resolve, what gets indexed, and that nothing is
launched with an open stdin) and 21 new routing cases in `backend/test_router_v13.py`.
Suite: **1461 passing**, the same 2 pre-existing `test_buzzcaf_ports.py` drift failures.

## v13.6 (2026-09-11) — The hand mouse, on the dashboard, with the gestures you choose

Asked as "where do I see the hand tracking on the dashboard" — and the honest
answer was **nowhere**. It had shipped in v13.1 with two surfaces: a floating
OpenCV window, and a tool you had to ask by name. Nothing in the app, and nothing
in `/api/dexter/status`, so nothing in the UI *could* have shown it.

That is a bad gap for this feature in particular. A gesture mouse is the one thing
you cannot debug by looking at what it controls: your hand is busy, and the pointer
is the symptom rather than the evidence. The two failures look identical from the
outside —

* the camera cannot see your hand, and
* it can see it perfectly, in a pose that is bound to do nothing

— and they have opposite fixes. So the panel's job is to tell them apart.

### What is on the dashboard now

`HandMousePanel`, in the right-hand column of the full view:

| | |
|---|---|
| the live line | "Open palm → moving the pointer", or "Looking — best 0.31 of 0.50 needed" |
| hand confidence | the palm score against the threshold, with the threshold marked, so "close" and "nowhere near" look different |
| how it is doing | fps, camera index, what share of frames had a hand in them, whether the tracking window is open, and the hotkey that stops it |
| **Start** / **Rehearse** | a rehearsal recognises every pose and moves nothing, which is how you try a changed gesture safely |
| **Gestures** | the map, editable inline |

And because tracking is something you turn on and then walk away from, there is a
**Hand chip in the header** (both views) and one in the **pop-up**, shown only while
it is running, green when it can see your hand and amber when it cannot — click
either to stop. That is the part that matters when the pointer starts moving and you
want to know whether Dexter is doing it.

### Gestures are a setting now, not a line of code

v13 picked the poses and wired each to a fixed job. Whether a fist should park or
right-click is a question about a person's hand, so a **pose** (the shape) and an
**action** (what it does) are now separate things with a map between them:

| pose | default | |
|---|---|---|
| open palm | move the pointer | v13.2: making this do nothing was the whole of "I can't move the cursor" |
| index finger | move the pointer | for precision |
| two fingers | scroll | |
| three fingers | move the pointer | |
| fist | nothing | the one pose nobody makes by accident |
| pinch (thumb-index) | left button, held | so a tap is a click and a hold-and-move is a drag |
| middle pinch (thumb-middle) | right button, held | |
| any other shape | move the pointer | bindable rather than hidden: hands make shapes nobody planned for |

Seven actions to bind to, two of them new: **middle button** and **double click**
(which fires once per pose, not forty times a second). The defaults are exactly
v13.5's behaviour, so nobody's hand mouse changes by upgrading.

Three ways to change one, all the same channel — the tool writes the setting the
panel edits, so they cannot disagree:

* the panel's Gestures list;
* Settings → **Hand mouse** (one choice per pose, plus steadiness, pinch closeness
  and the preview window);
* out loud: **"make a fist the right click"**, "set the open palm to scroll",
  "what do the hand gestures do".

**It applies while tracking is running**, which is the only sensible answer: you
change a gesture *because* the current one is not working, and "restart hand
tracking to pick this up" would mean stopping the thing you are trying to fix.
Whatever button was held under the old map is released as part of the swap — a
button left down by a binding that no longer exists would be unkillable without the
mouse this is replacing.

### Three things found while building it

1. **A hardcoded sentence had been wrong for two releases.** The start reply said
   "open palm to park" ever since v13.2 made an open palm steer. It is generated
   from the live map now (`gesture_sentence()`), which is also the only way it can
   stay true for somebody who has changed a binding.
2. **Hysteresis could not let go of a pinch.** Moving from a thumb-index pinch to a
   thumb-middle one left the index gap inside the *open* threshold, so the old pose
   held and the swap was impossible without opening the hand in between. Now the
   held pinch yields when the other one is plainly nearer.
3. **A test-isolation bug this created.** `settings_store.update` writes
   `os.environ` directly — that is how a setting takes effect without a restart —
   so monkeypatch cannot undo it, and one test saving "a fist is the right click"
   changed what a fist meant for every test that ran after it, in another file.
   `conftest.py` now restores the whole `DEXTER_HAND_*` block after each test. Same
   class of bug as the CDP one in v13.4, found the same way.

Evidence: `backend/test_hand_gestures.py` (65 cases — the shapes, that the defaults
are unchanged, rebinding, the two new actions, nonsense bindings, the spoken
phrasings, the settings round-trip, and that a running tracker is told) and
`src/test/HandMouse.test.tsx` (9 — chiefly that "can't see you" and "bound to do
nothing" read differently). Suite: **1421 backend passing** (the same 2 pre-existing
`test_buzzcaf_ports.py` drift failures) and **38 frontend**.

## v13.5 (2026-09-11) — The window was opening. 32,000 pixels off the desktop.

Reported as "the maximised version of this app is not opening". v13.2 had already
fixed a real version of this (the full window came up *behind* the always-on-top
pop-up, so the button looked dead), and the running process had that fix — started
21:24, after the change. So this was something else, and it was not in the shell code
at all. It was in `data/window.json`:

```json
{ "width": 380, "height": 520, "x": -32000, "y": -32000 }
```

**(-32000, -32000) is the position Windows reports for a *minimized* window.** Close
Dexter while it is minimized and `_save_window` wrote that down as "where the window
was"; every launch afterwards faithfully created the full window there. It opened
every time, instantly, exactly as asked — at a coordinate on no monitor. From the
owner's chair that is identical to a button that does nothing, which is why two
releases looked at `show()` instead. The saved size was the giveaway: 380×520 is the
`min_size`, not a size anyone chose.

Three places now have to agree before a position is believed:

| | |
|---|---|
| `on_screen(x, y, w, h)` | rejects the `<= -30000` sentinel and any rectangle that leaves less than 120px of title bar inside the virtual desktop (`GetSystemMetrics` 76–79, so a second monitor counts — and an *unplugged* one does not) |
| `_load_window()` | clamps the size to the screen and **drops** an unbelievable position, because pywebview centres a window when `x`/`y` are absent |
| `_save_window()` | always keeps the size; writes a position only if it passes, else reuses the last good one and says why |

And because a poisoned file may already have been obeyed at creation time,
`open_full()` checks at show time: a window that is off-screen is resized to fit and
moved back (centred, a third of the way down) *before* `maximize()`, and the pop-up is
only hidden once the big window is confirmed visible — hiding the one window that works
and showing nothing is the worst of the available outcomes.

The live `data/window.json` was repaired too, so the install in front of the owner is
usable without waiting for a clean close.

Evidence: `backend/test_desktop_window.py` (18 cases — the sentinel, an unplugged
second monitor, a sliver still counting as visible, the size surviving a position that
does not, the walk-back at show time, and that a full window which refuses to show
never costs you the pop-up). Suite: **1356 passing**, the same 2 pre-existing
`test_buzzcaf_ports.py` drift failures.

## v13.4 (2026-09-11) — Playwright where it helps, on the browser you actually use

Asked as: "can Playwright be implemented to make Dexter's actions more precise?"
The answer turned out to be yes, no and yes — worth writing down in that order.

**Yes, it was already here.** `browser.py` (v6) is a full Playwright driver and has
always been able to attach over CDP (`DEXTER_BROWSER_CDP`). **No, it was demoted on
purpose**: in v11 it launched a *second* Chrome on its own profile, so "open YouTube"
opened a browser that knew none of the owner's logins and "close this tab" acted in a
window they were not looking at. That is why everything since drives the real browser
through Windows accessibility — which sees only *names*.

**And yes, there is a third option that is strictly better than both**, which is what
this release adds: Playwright attached over CDP **to a Chrome that is the owner's**.
`backend/browser_cdp.py` starts Chrome with `--remote-debugging-port` on a persistent
profile (`data/chrome_debug_profile`; Chrome 136+ refuses remote debugging on the
*default* profile, so a separate `--user-data-dir` is not a preference, it is the only
way it works at all). Sign in there once and it stays signed in.

What becomes exact:

| before (accessibility) | now (CDP), when it is on |
|---|---|
| "the third result" = the third accessibility name that looked like a title | the third `<a href>` in document order, with its URL |
| a click, a `sleep`, then a hopeful look at the window title | `goto` / `click` that **waits** for the navigation, and reports the URL it reached |
| "copy that paragraph" = Ctrl+C and hope | `window.getSelection().toString()` — the real string |
| typing into whatever has focus | the field found by its label, placeholder or role |
| the window must be in the **foreground** | no focus stealing: Dexter can act while the owner works elsewhere |

`page_control` prefers it and falls back to the accessibility route whenever it is
not up, so this is an upgrade that never becomes a dependency:
`precise_browser` (start / status / stop) is the switch, and every reply says which
route it took.

**The extraction needed real work, and the live test is why.** The first cut was a
plain `a[href]` scan; on a Google results page it returned *Accessibility help,
Google apps, AI Mode, Shopping, Short videos* — the site's own furniture, which comes
first in document order. It is now three tiers: a per-site selector for the places
where "open the third result" is actually said (Google, YouTube, Bing, DuckDuckGo,
GitHub, Reddit, Stack Overflow), else links inside the main content region with
nav/header/footer/aside excluded and headings preferred, else the loose scan so an
unknown page still answers. A leading timestamp is stripped, because a YouTube
thumbnail's own text is "1:01:14 Now playing" rather than a title.

Verified live, against the real browser:

```
launched: Chrome/153.0.8010.36 on http://127.0.0.1:9222
search_here -> route cdp, "best usb microphone 2026 - Google Search"
google   1. Best USB microphones 2026: Plug-and-play mics ... MusicRadar
         2. Best Microphone to Buy in 2026? (Price, Sound Quality ...) Reddit
         3. Best USB Microphones 2026 - Tested & Compared  sound&gear
youtube  1. lofi hip hop radio - beats to relax/study to
         2. 1 A.M Study Session [lofi hip hop]
         3. Coffee Shop Radio - 24/7 Chill Lo-Fi & Jazzy Beats
open_result(2) -> reddit.com/r/microphone/comments/1rtej2a/best_mi…  changed: True
selection -> 'Helpful products, built with you in mind'
```

Evidence: `backend/test_browser_cdp.py` (18 cases — both routes, and that the
fallback is intact when the port is absent). The in-page extraction is JavaScript and
is verified by the live run above rather than by a unit test.

**And a test-isolation bug this created, worth recording.** The debuggable Chrome left
running after the live check made nineteen `test_page_control.py` cases take the exact
route: `available()` asks a real port whether a real browser is there, so the suite's
behaviour depended on what the owner happened to have open. `conftest.py` now forces
the CDP route off for every test (and makes `launch` raise), exactly as it already does
for Sentinel, LM Studio, keystrokes and the process table; the tests that are *about*
launching opt back in explicitly. This is the same class of bug the conftest guards
were written for, and it is the second time a "real machine" dependency has crept in
through a new integration.

### Also answered: notes, reminders, schedules

Asked in the same breath, so for the record — all of this is live on the running
instance right now (`/api/dexter/status` → scheduler: running, 13 jobs,
1 pending reminder, toasts via winotify):

* **Notes** — "note that the mic arrives Friday" writes a Markdown file into the
  notes folder and indexes it immediately; "take notes" (v13.1) turns dictation on
  until you say stop, into notes, Notepad, Sticky Notes, Notion or any window.
* **Reminders** — "remind me to call the editor at 6" fires on three channels at
  once (Windows toast, in-app banner, spoken), so it lands whether or not the window
  is open. "What are my reminders", "cancel the studio reminder".
* **Commitments** — "I'm committing to scripting episode three by Friday" goes into
  the workbook, with rolling counted.
* **Schedules** — "every morning at 8 review my missions" is a *recurring job* that
  runs the goal through the same tiers a typed message would, so anything Dexter can
  do it can do on a schedule. Cron or one-shot; "what's scheduled" lists them.
* **Mission blocks** — the Mission Reminder bridge (v13.1) saves and reviews blocks
  in that app.

## v13.3 (2026-09-11) — Picking what is on the screen

Seven reports from using it, and the common thread is worth naming: in every case
the thing the owner meant was *visible on screen*, and Dexter had no way to point
at it. "It says it's doing it but it does nothing" is what that looks like from the
outside, because a sentence that reaches the model gets answered rather than done.

### 1. "Okay, open Chrome for me" -> "there is no app called okay open chrome" — **done**

`_clean` stripped leading politeness, but not when Whisper put a **comma** after it,
and not a trailing "please". So that exact spoken sentence fell past the whole fast
path and reached the model, which passed the raw utterance to the launcher as an app
name. Both layers now strip it: the router's filler patterns allow the comma and a
dozen more words, and `AppLauncherTool.clean_name` strips the politeness *and* the
verb, so even a sentence that arrives at the last layer resolves to "chrome".

### 2. "Select the first profile / the first video / the first link" — **done**

Three different things, and v13.1 had one rule that sent all of them to the video
player (and "select" was not even in its verb list, so the owner's actual words
matched nothing):

| what they said | what it means | tool |
|---|---|---|
| the first **video** / one / thumbnail | the video list | `youtube_play` |
| the first **result** / link | the page's results | `open_result` |
| the first **profile** / option / row / item | anything on screen | `select_nth` |

`select_nth` is what makes Chrome's profile picker work: its cards are list items
with a person's name on them, so there is nothing to type and no fixed label -
only "the first one". It reports what it clicked, and on a miss it names what it
could see.

### 3. "Search a video by its name in the current tab" — **done**

There was no implementation at all: every browser tool opens a **new** tab, because
`chrome.exe <url>` is the only command line Chrome offers and it has no
replace-this-tab form. The address bar does: `chrome_control.open_in_current_tab`
does Ctrl+L, types the URL, Enter - what a person does - and `search_here` uses it,
staying on the site you are already on unless you name another.

### 4. Comments, like, subscribe — **done**

`youtube_control.page_action`: like, unlike, dislike, subscribe, unsubscribe,
comments, share, save, description, notifications, join. Found by the accessible
names YouTube gives them (its like button has carried a count in its label for
years, so the match is a contains on the stable prefix). "Open the comments" is a
*scroll* before it is a click, because YouTube only builds that section when it
comes into view.

### 5. "Search anything on YouTube or Google and click the links" — **done**

`search_and_open` does both halves in one turn ("google best mic and open the first
link" used to search for the whole sentence including "and open the first link"),
and `page_results` lists what a page is offering so one can be chosen by number.

### 6. "Copy a section of text and paste it in any other tab at any field" — **done**

`copy_text` takes the selection, everything, or a named field's contents (read
through UIA, without moving the caret). `paste_text` switches tab, clicks the named
field, pastes, and optionally presses Enter - in that order, and **refusing** rather
than guessing: a field that is not there, a tab that will not switch, or an empty
clipboard all stop the turn instead of typing into the wrong place.

### 7. "In YouTube, select the first video" — **done**

A site prefix is a *tab*, not a window. "In chrome, …" focuses a window (as before);
"in youtube, …" now switches to that tab first and then runs the rest of the
sentence there. Before, the loose comma form tried to focus a window called youtube
and failed.

### And a claim that can be checked

`open_result` records the tab title before the click and compares it after: the
reply says "clicked the second result" *and* whether the page actually moved. That
is the direct answer to "it says it is doing it but it does nothing" - when nothing
happened, the reply now says nothing happened.

### A hazard worth recording

One line of `router.py` was written through a shell heredoc and a `\b` in it became
a **literal backspace byte** in the file, which silently broke the regex that strips
a site name out of a search query. `data/`-wide scan for control characters found
exactly that one. Third time this hazard has bitten in this repo (see
`MEMORY.md` / bash-heredoc-backslashes): regex-heavy edits go through the editor,
not the shell.

Evidence: `backend/test_page_control.py` (36 cases), `backend/test_router_v13.py`
(139), 1318 backend tests pass.

## v13.2 (2026-09-11) — One model at a time, a hand that actually moves the pointer

Four things the owner found by using v13.1 on the live machine, all fixed the same
evening. Each one is recorded with what was actually wrong, because in three of
the four the cause was not what the symptom suggested.

### 1. "If Dexter switches model, or I switch it, the previous one gets offloaded" — **done**

Until now a model left memory **only when its keep-alive expired**: 30 minutes for
a smart rung, two hours for the pinned fast one. So switching *added* a model
instead of replacing one:

* a turn that stepped down the ladder left **both** rungs resident for half an
  hour (gemma-4-12b at 7.6 GB *and* qwen2.5:7b at 4.5 GB);
* changing a tier in Settings left the old tag holding its gigabytes for the rest
  of its keep-alive — two hours, for the fast one;
* picking a model by hand in the UI bypasses the tiers entirely, and the tiers
  stayed loaded anyway.

`model_steward.retire()` is the new primitive — unload everything *we* loaded
except what is named — and it is wired into the three places a model changes:
after an escalated turn (superseded rungs go, the fast tier stays), when a turn
runs on an explicitly chosen model (the tiers go, since they are not in use), and
when the owner changes `fastModel` / `smartModel` / `smartModelFallback` in
Settings (`settings_store.update` now reports what it offloaded). A rung also goes
**immediately** rather than on its keep-alive when Sentinel says memory is not
green.

Three rules it will not break: nothing another app loaded is ever touched (only
`_ours`), a tag that is still a tier is left alone (changing the fallback to the
model that is already the smart rung must not unload it), and
`DEXTER_OFFLOAD_PREVIOUS=off` restores v13 exactly. `always` is the strictest
setting — only the model that just answered stays resident, at the cost of a
reload on the next switch.

Evidence: `backend/test_model_offload.py` (17 cases, including the order trap:
`replaced()` is called *after* the setting is applied, and refuses if it is called
before).

### 2. "I am not able to move the cursor with my hand mouse" — **done**

Not a tracking failure. The session log settles it: **538 of 7936 frames had a
hand in them**, so the camera saw the hand fine — and then did nothing with it,
because v13.1 mapped an **open palm to "park"**. The first thing anyone does is
hold a flat hand up to the camera and move it; Dexter deliberately ignored exactly
that. An open hand now steers, pointing with the index finger still steers, and
the **fist** is the park — the one pose nobody makes by accident.

Which exposed the second bug, on the first fist: in a closed hand the thumb tip
lies against the middle fingertip at 0.35 hand-widths, well inside the pinch
threshold, so a fist was read as a **right click**. Measured on photographs of
both poses: an open hand's fingertips reach 2.0-2.4× past their own knuckles, a
fist's sit at 0.87-0.96. A pinch now requires the pinching finger to be reaching
out (≥1.25), which separates them with room to spare.

Also: `DEXTER_HAND_PALM_SCORE` 0.6 → **0.5**, the reference implementation's own
default. On this webcam a palm scores 0.65-0.74 even on an empty scene (the
detector proposes, the landmark model verifies and rejects), and a real hand
scores 0.87; 0.6 was a guess that cost real detections.

Verified end to end on the live machine: a photograph of an open hand →
`find()` → `step()` → landmark 0.993, gesture `point`, `move_to` recorded; a
photograph of a fist → `park`, nothing pressed; and with a hand in front of the
real camera, 48 of 133 frames tracked (≈100% of the last three seconds) at palm
0.87 with gesture `point`.

### 3. "A small window shall open to show my hand mouse tracking" — **done**

`backend/hand_preview.py`: a 360 px always-on-top window drawn from the tracking
thread, showing the 21-point skeleton, the pose it decided, the control rectangle
your hand has to stay inside to reach every corner of the screen, the pointer
anchor, the frame rate, and — when it sees nothing — the **palm score against the
bar it needs to clear**. That last line is the difference between "it doesn't
work" and "the room is too dark".

On by default (`DEXTER_HAND_PREVIEW=0` to turn it off, or `"start hand mouse
without the preview"`). Esc or Q in the window stops tracking, which makes it a
visible kill switch as well as a diagnostic. A preview that throws disables itself
rather than taking tracking down with it. `hand_mouse_status` now also reports
`palmScore` / `bestScore` / `palmNeeded`, so the same diagnosis is available
without looking at the window.

Verified live: the window opened from the tracking thread (`preview.open: True`,
no error) and tracked a hand through it at 17 fps.

### 4. "The pop-up window is not maximising" — **done**

`DexterShell.open_full()` was `show()` + `restore()`. Neither raises a window:
Windows does not hand the foreground to a process that merely showed something,
and the pop-up is `on_top=True`, so the main window came up *behind* it — which
looks exactly like a button that does nothing. It now goes through `_raise()`:
show, restore, flip `on_top` (which raises it within the process), then
`SetForegroundWindow` on the window's real handle. And because the gesture means
"make this the big window", opening the full window now **hides the pop-up**.
`show_popup` uses the same path, and `maximize_full()` exists for a window that
should fill the screen. If the shell still cannot bring it forward, the pop-up
says so and points at the tray icon instead of staying silent.

### Also

* The UI bundle was rebuilt (`dist/assets/index-af642f45.js`); `DEXTER_AUTOBUILD=1`
  makes the shell do that by itself when `src/` is newer.
* Said out loud, **"mute yourself"** and **"you can speak again"** are now voice
  commands classified in the voice layer, so they never reach the chat — distinct
  from "stop", which cuts the reply that is playing.

## v13.1 (2026-09-11) — Reach: the whole disk, any app, a video, a hand, and notes

Requested as: "dexter is working but it's not able to open c drive or any such
drive or list folders or open a specific folder, or open a specific video from
opened youtube, or close buzzanimate or in general any app apart from the ones
mentioned to it... also can we have hand tracking to work as a mouse using the
webcam for click right click scroll etc? also its listening to some random
stuff", then "also i want it to take notes from me continuously if i say so, for
it it can use notion or notepad or sticky notes or any tool i say. its shall also
be able to control BuzzcafAI and select tabs and texts from it, i want to use my
computer while sitting away from it and dexter doing it for me. i also want it to
have capacity to schedule anything i want and use the missionreminder app
automatically to save and review my missions", then "also add option to mute
dexter from pop up window".

Seven complaints, and each one turned out to have a specific cause rather than a
vague one. The causes are what is worth recording.

### 1. Drives and folders — **done**

Two separate things were missing, and only fixing both makes "open the C drive"
work:

* **Permission.** `pathguard` allowed six user folders and the project directory,
  so every drive, and most of the machine, answered "that's outside the folders
  Dexter is allowed to touch". It now has a **scope**: `machine` (the default -
  look anywhere the owner can look) or `user` (the old behaviour, selected
  automatically when `DEXTER_FS_ROOTS` is set, so an install that pinned its roots
  keeps exactly the reach it had). Reading and writing are now *different
  questions*: `resolve()` vs `resolve_write()`. Writing, moving or deleting inside
  Windows itself (`C:\Windows`, Program Files, ProgramData, a drive root) is
  refused in both scopes — a misheard "delete that" there is not recoverable.
  `DEXTER_FS_DENY` is the opposite valve: folders Dexter must not even read.
* **Vocabulary.** Nothing turned "the C drive", "my downloads", "the BuzzAnimate
  folder" or `d:\films` into a path, and **there was no tool that listed a folder
  at all** — `search_files` could find a file by name; nobody could ask what was
  *in* a directory. New `backend/folders.py` (`spoken_path`, `drives`,
  `list_folder`, `find_folder`) and four tools: `list_folder`, `list_drives`,
  `open_folder`, `find_folder`.

Tier 0 rules were added for all of it, each gated so it only fires when the words
really are about a folder — a drive letter, a path separator, the word
folder/drive/directory, or one of the user's own folders by name. That is what
keeps "open buzzedit" an app and "open the buzzanimate folder" a folder.

Evidence: `backend/test_folders.py` (29 cases), `backend/test_router_v13.py`.
Live: `folders.describe_drives()` → `B: New Volume 63 of 932 GB free; C: 57 of
930 GB free; D: ... H: 232 of 699 GB free`; `list_folder("C:\\")` → `24 folder(s),
3 file(s)`; `resolve_write(r"C:\Windows\x.txt")` → `None`.

### 2. "Close BuzzAnimate" (and any app) — **done**

This failed twice over, and the first half was a plain wrong fact in the
registry: `data/apps.default.json` said BuzzAnimate's process is `buzz-app.exe`.
The binary is `buzzanimate.exe` (`BuzzAnimate/target/release/`). So "is
BuzzAnimate running" answered *no* while it was on screen, and "close it"
answered "it is not running". Record corrected.

A corrected record is not a fix, though — the next renamed binary would do the
same. So both layers now have a generic fallback:

* `apps_registry.probe()` ends with **"anything running out of this app's own
  folder"**, and `stop()` falls back to stopping that. Editors and terminals are
  excluded by image name, so VS Code with the project open is never mistaken for
  the app.
* `desktop_control.find_processes()` matches a spoken name against everything
  Windows knows about a process: image name, executable path, the folder it runs
  from, its command line, and the **FileDescription the vendor compiled in** (how
  "open broadcaster software" finds `obs64.exe`). `find_windows` falls back to it,
  so "close Teams" finds `ms-teams.exe`.
* A running-but-windowless app used to be reported as "it doesn't have a window
  open", which reads as "it isn't running". It now says what it is, offers a
  force-stop, and for one of the owner's own projects goes through the registry's
  proper shutdown instead.

Evidence: `backend/test_desktop_apps.py` (16 cases, including "an editor with the
folder open is not the app running" and "a record that says do not stop is still
never stopped").

### 3. A specific video on an open YouTube — **done**

`browser_click` could not do it: `click_text` walked the UIA tree type by type
with **one shared budget**, and a YouTube page has hundreds of Buttons — the five
seconds were gone before the walk reached Hyperlink, where the video links are. So
every "play the second video" died on a timeout. The budget is now per control
type with a global deadline and an early exit on an exact match, and matches are
ranked (exact → starts-with → contains) instead of first-found.

New `backend/youtube_control.py` has two routes and says which one it took:
click the link the owner can *see* (page_videos reads the accessibility tree and
filters out navigation and durations), or resolve the title through YouTube's own
results page over HTTP and open `watch?v=…` in their Chrome. `youtube_play`,
`youtube_results` and `youtube_control` (pause, next, captions, full screen, skip
an ad) are the tools; playback prefers the player's own named buttons over
keystrokes, because `k` typed into the search box is the letter k.

`play_on_youtube` also stopped lying: it used to open a *results page* in whatever
Windows calls the default browser and report "Playing 'X' on YouTube".

Evidence: `backend/test_youtube_control.py` (24 cases). Live: the search parser
against the real results page returned `rFZHOHl-L8A lofi hip hop radio / Lofi
Girl`.

### 4. "It's listening to some random stuff" — **done**

The cause was in `data/settings.json`, and it was two stored settings plus a hole
in the rule:

* `followUpWindow: 15.0` — for fifteen seconds after *every* reply, any speech
  onset in the room became a command.
* `bargeIn: true` — which re-opens the v10 echo loop: through speakers the mic
  hears Dexter's own voice.
* And inside that window a **borderline speaker verdict counted as "addressed to
  me"** purely because the window was open (v11.1's grey-zone rule).

So: one gate, `voice_pipeline.accept_utterance()`, applied to every way in. A
sentence that was not addressed by name, by the wake word or by the mic button now
has to look like something said *to* an assistant — more than one word and a
transcript the recogniser stands behind — with answers ("yes", "go ahead") always
allowed and "stop" always obeyed. A grey verdict in the window needs a fuller
sentence. After two windows that caught nothing meant for Dexter, it stops
re-opening them until its name is said. Defaults changed: window 9 → 6 s, echo
guard 800 → 1200 ms, and the stored 15 s / barge-in-on were reset to the safe
values (both still one click away in Settings, for headphones).

Evidence: `backend/test_voice_gate.py` (21 cases, both directions: the television
is ignored, a real follow-up and a one-word "yes" still land).

### 5. Continuous note-taking, into any tool — **done**

`backend/dictation.py` makes dictation a *mode*: while it is on, what the mic
hears is written down rather than answered — no model in the loop, no turn in the
chat, and in `name` mode the name gate is skipped (nobody says "Dexter" before
every sentence of a note). Sinks: a Markdown note in the notes folder (indexed),
a named file, **Notepad, Sticky Notes, Notion** or any open window (typed into,
which is the honest way to support "any tool I say" — Dexter has no Notion API key
and no business having one), or the clipboard. Spoken punctuation, "new
paragraph", "scratch that", "pause notes" and six ways of saying stop.

Evidence: `backend/test_dictation.py` (35 cases, including "while dictating
nothing reaches the chat" and "a file outside the allowed folders is refused
before a word is said").

### 6. Driving BuzzcafAI (and any app's UI) from across the room — **done**

`list_controls`, `click_in_app` and `copy_text_from` in `tools_desktop.py`: list
an app's tabs and buttons by name, press one in a named window, and copy its
selection (or everything) back out through the clipboard. The Studio is a web view
in a desktop window, like most of the owner's apps, so Windows already exposes its
tabs and buttons — no screenshot and no vision model.

### 7. Mission Reminder, and scheduling anything — **done**

Mission Reminder deliberately has no inbound API: its state is its own and the
bridge to the rest of the machine is one-way (`GUARDIAN_PLAN.md` §6/MR1). Rather
than reach into another app's storage — which corrupts it the moment both sides
write — `backend/mission_bridge.py` uses the app the way a person does: its own
`Ctrl+1..7` tabs, its named fields ("Name", "Starts at") and its named buttons
("New block", "Save block"). Reading stays the runtime mirror it publishes.

Two promises: every write is recorded in `data/mission_outbox.json` *before* it is
attempted, so a failure is never silent; and the labels are data
(`data/mission_ui.json`), so a redesigned app is a one-line fix. Tools:
`mission_review`, `mission_save_block`, `mission_save_goal`,
`mission_save_statement`, `mission_start_block`, `mission_outbox`.

Scheduling already existed (`schedule_task` → `tasks.schedule_goal`, cron or
one-shot) but nothing *routed* to it: "every morning at 8 review my missions" went
to the model, which promised and scheduled nothing. Four Tier 0 shapes now cover
how it is said, and each refuses unless the time actually parses — so "in the
downloads folder find the invoice" is not mistaken for a schedule, and "every time
I open chrome" is not scheduled for 9am.

Evidence: `backend/test_mission_bridge.py` (20 cases), `backend/test_router_v13.py`.

### 8. Hand tracking as a mouse — **done**

MediaPipe has no wheels for this machine's Python (3.14; they stop at 3.12), so
its two models are driven directly through onnxruntime:
`backend/hand_tracker.py` (palm detection 192×192 → 2016 anchored boxes, hand
landmarks 224×224 → 21 points, detection only when tracking has lost the hand),
`backend/mouse_input.py` (SendInput, so a drag started by a pinch behaves like one
started by a hand), `backend/handmouse.py` (the poses). A `mediapipe` backend is
kept and preferred when importable, so a 3.12 venv gets the faster pipeline for
free.

The poses are each a distinct *shape* rather than a threshold on the same shape:
point = move, pinch = left button held (so a tap is a click and a hold-and-move is
a drag), thumb-to-middle = right button, two fingers = scroll, open palm or fist =
park. Hysteresis on both pinches, an absolute mapping from a rectangle in the
camera image to the screen (so your hand has a *place*), a click rate limit, every
button released when a hand leaves the frame, Ctrl+Alt+H as a kill switch, and a
`dry_run` that drives a recorder so the poses can be rehearsed with the pointer
untouched.

Verification, because a tracker that "runs" and sees nothing is the failure mode
here: the generated anchor table was compared against the reference implementation
in the OpenCV Zoo (2016 rows, max difference 7e-9), the decode matches theirs, and
the whole pipeline was run on a real photograph of a hand — palm score 0.879 from
the reference, landmark confidence 0.986 from ours, and `fingers_out` correctly
read it as a fist with the thumb out. The webcam opens and delivers 77 frames in
3 s through the tracker (`hand_mouse_check`). **Not yet verified: the owner's own
hand in front of their own camera** — say "can you see my hand" and it reports
frames, share and confidence.

Tools: `hand_mouse_setup` (downloads the two models, 7.6 MB, once),
`hand_mouse_check`, `start_hand_mouse`, `stop_hand_mouse`, `hand_mouse_status`.

Evidence: `backend/test_handmouse.py` (21 cases on synthetic landmarks and a
recording mouse).

### 9. Mute from the pop-up — **done**

The pop-up's mic button muted *everything*; there was no way to say "stop talking,
keep listening", which is the common wish (a call, a recording, someone else in
the room). `useVoice` now has a separate `silent` state, shared with the full
window through `localStorage`, gating both the TTS request and playback; the
pop-up bar has a speaker toggle and the quick-settings panel spells out both kinds
of quiet. *"Mute yourself"* and *"you can speak again"* are the same switch said
out loud — classified in the voice layer, so they never reach the chat, and
distinct from "stop", which cuts the reply that is playing. Anything already
queued stops at once: a mute that finishes its sentence is not a mute.

### Where this leaves the suite

1200 backend tests pass. Two failures are pre-existing and unrelated to this work
(`test_buzzcaf_ports.py`: the copies of `buzzcaf_ports.py` and the ecosystem
contract inside `BuzzcafAI/` have drifted from this repo's canonical ones — a sync
the owner should make deliberately). `npx tsc --noEmit` is clean.

## v13 (2026-09-11) — The right model for the moment, on the GPU or off it

Requested as: "use a quicker but smart enough model like gemma 4 or you decide
the best model, small for quick answers and has tooling, then move to another
model for complex tasks. We can also offload some of the models from GPU if
Dexter does not have this — implement it. Find the best models and create a
plan." Built by an Opus implementation agent from `PLAN.md` v13, Phases 1-3, the
same day.

**What was measured first** (`backend/bench_models.py`, kept): cold load, VRAM as
Ollama reports it, chat speed, and tool choice on 30 cases — the 15 from
`test_agent_eval.py` plus 15 hard ones (two acts in one sentence, arguments that
must be composed, a window that must be named, three questions that must *not*
become a tool call). Same 8 candidate tools Dexter would hand the model; only the
first call judged; nothing executed; `think:false`.

| model | VRAM MiB | chat tok/s | tool choice | tool call latency | verdict |
|---|---|---|---|---|---|
| qwen2.5:3b (v12's fast tier) | 2059 | 175 | 20/30 | 0.45 s | calls tools for chat, wrong tool a third of the time |
| granite4.1:3b | 2385 | 155 | 25/30 | 0.42 s | fast, but twice took a joke for a tool |
| **qwen3.5:4b** | **2983** | **111** | **27/30** | 0.88 s | best of all seven; never called a tool on a question in the re-run |
| gemma4:e2b-it-qat | 1573 | 170 | 26/30 | 0.62 s | smallest VRAM; once made a haiku a note |
| qwen2.5:7b (v12's smart tier) | 4528 | 93 | 25/30 | 0.68 s | |
| qwen3.5:9b | 5235 | 71 | 23/30 | 1.22 s | worse than its 4b sibling; dropped |
| gemma-4-12b (v12 top rung) | 7630 | 52 | 26/30 | 1.51 s | best on the composed-argument cases; kept for real reasoning |

Two measured facts shaped the build, and both are now load-bearing:

1. **Thinking models returned nothing through Dexter's old path.** qwen3.5
   through `/v1/chat/completions` spent all 200 tokens on reasoning and returned
   empty content; `think:false` is ignored there. Through Ollama's native
   `/api/chat` the same question answers in 0.6 s. So the Ollama provider now
   speaks the native API.
2. **Ollama keeps a model where you put it.** `options.num_gpu: 0` loads a model
   on the CPU (3B: 24 tok/s, 0 MiB VRAM) and a later request *without* the option
   keeps using that instance; `num_gpu: N` gives a partial split. So a model can
   live in RAM while a render owns the card, and come back on its next load.

Suite: whole backend **940 passed**, 1 skipped, ~100 s (the 2
`test_buzzcaf_ports.py` failures are the known sibling-repo line-ending drift,
unchanged by this work) · **46 new backend tests** · vitest **29 passed** (4 new)
· `npx tsc --noEmit` and `npm run build` clean. Fakes only: no test loads,
unloads, moves, pulls or starts anything on this machine — and the suite now
*enforces* that rather than relying on each test remembering (see the conftest
row).

| Item | Status | Evidence |
|---|---|---|
| **Fast model decided by measurement (owner's session):** `gemma4:e4b-it-qat` pulled and benchmarked after the build - 27/30 tool choice (ties qwen3.5:4b), 2943 MiB VRAM, 106 tok/s, 0.70 s per tool call, never took a question for a command; `--fast-act`: settles **10/12** instructions without the big model (qwen3.5:4b 9/12) with one wrong action instead of two. Set in `.env` (`DEXTER_FAST_MODEL=gemma4:e4b-it-qat`); qwen3.5:4b stays the code default and the documented alternative. Gemma 3 ruled out: no tools capability on Ollama | ✅ | `bench_models.py` runs 16:0x-16:2x IST; `ollama show gemma4:e4b-it-qat` lists no `tools` badge yet every call parsed |
| Fast-act gate widened (found live): "what track is playing right now" matched neither `looks_like_action` nor `needs_live_data`, the fast chat pass declined it, and it went to the 12B (7.8 s). Now any non-chain sentence that reaches escalation gets the fast model's one tool round | ✅ (bug fixed) | after the fix: 1.8 s `fast_act` with `get_now_playing`; "weather in Jaipur … umbrella" 2.2 s `fast_act` with `get_weather`; `test_router.py` 195 passed (+1, two older fakes taught about the acting round) |
| CPU placement verified live: `ensure(place="cpu")` booked 6740 MiB RAM with Sentinel, `/api/ps` showed the e4b at 0 MiB VRAM, Dexter's own fast turn answered on it (7.6 s - degraded, but answering), `ensure(place="gpu")` brought it back in 4.5 s. Under real pressure the move asked for RAM and was **refused** (5739 MiB available while LM Studio was loading) and fell back to a plain unload, as designed | ✅ | `[Steward] live-cpu-check … resident in RAM`; `[Steward] fast-model-to-cpu: refused … not enough RAM`; `DEXTER_CPU_RUNG_MAX_MIB=6600` in `.env` because the e4b file is 5861 MiB |
| Known: the VRAM estimate for a rung is its file size, and the e4b's file (5.9 GB, per-layer embeddings) is twice what it uses on the card (2.9 GB), so its startup booking over-asks by ~4 GB; `Decision.message()` words a *granted* CPU decision as a refusal (cosmetic) | open | `[Steward] startup: … 6885 MiB VRAM booked` vs `/api/ps` 2943 MiB |
| **P1.1 Ollama's own `/api/chat`.** `hybrid_llm._chat_ollama_native()`: `messages`, `tools`, `stream`, `think`, `keep_alive`, `options`; content deltas to `on_token` exactly as the OpenAI path does; `message.tool_calls` parsed into the same `LLMResponse.tool_calls` shape; `eval_count` / `eval_duration` / `load_duration` kept in the new `LLMResponse.usage` | ✅ | `test_hybrid_native.py` (16 tests, fake server): content + usage, tool calls, NDJSON streaming, cancellation mid-stream (partial text, `cancelled`, no error), an error frame, a dead Ollama, a server that rejects `tools`, and a model that cannot think being asked again without the key |
| P1.2 `chat(…, think=False, options=None, keep_alive=None)`; the Ollama provider takes the native path, every other provider ignores the extras | ✅ | `think:false` asserted on every native request by default; `options`/`keep_alive` pass-through pinned, including an explicit option winning over the default; LM Studio gets none of the three and still answers. `test_model_residency.py` and all 181 pre-v13 router tests unchanged and green. `DEXTER_OLLAMA_NATIVE=0` is the way back |
| P1.3 A reply that is **only** reasoning is an error (`"model only thought"`), so the router escalates instead of showing nothing | ✅ | `hybrid_llm.ONLY_THOUGHT`; non-streaming and streaming both covered; reasoning *alongside* an answer is left alone, and a tool call after thinking counts as an answer. The inner monologue is never streamed to the UI |
| **P2.1 Fast tier = `qwen3.5:4b`.** Router default, `settings_store` default, `.env`, `.env.example`. Blank still means "no fast tier" | ✅ | `test_settings_store.py`: the Settings row's default and effective value are `qwen3.5:4b` and agree with the router's own default |
| Preflight warns when a tier's model is not pulled, and says the `ollama pull` line | ✅ | `preflight.check_llm` now checks `FAST_MODEL`, `SMART_MODEL` and `SMART_MODEL_FALLBACK` against what the servers list; WARN names the tag and the fix |
| **P2.2 The fast model acts** (D1). `router._fast_act()` between Tier 1 and the escalation: one tool round on the small model with the same candidate set the big one would get, `think:false`, `max_tokens 300`, no `on_token`. A valid call runs and the reply is the tool's summary voiced in one line | ✅ | `test_router.py`: the tool runs, `action="fast_act"`, `agent_loop` is never reached, `data.fastAct` is set. 9 tests around the boundary |
| …and escalates exactly as before on: no call, invalid arguments, a tool that was not on offer, a chain sentence, or a tool that failed. Confirm tools still ask. Never more than one round | ✅ | one test each; the hallucinated-tool and invalid-argument cases assert that **nothing ran**; `is_desktop_chain` sentences never reach the fast model at all; `DEXTER_FAST_ACT=0` restores v12 behaviour (the pre-existing "instructions skip the fast model" cases now run with the switch off, and still pass) |
| The voiced line is a courtesy, never a risk: empty, padded, or sentinel-leaking output falls back to the tool's own summary | ✅ | `_voice_tool_result` + `test_the_voiced_line_falls_back_to_the_tools_own_summary` (4 bad shapes) |
| P2.3 `bench_models.py --fast-act`: how many of the 30 cases the configured fast model would settle without escalation, by the router's own rules | ✅ code, ⏳ numbers | `bench_models.fast_act()`; it counts settled / escalated / **would have acted wrongly**, and skips the cases that are not instructions. Not run here — it needs a real model, and this build was not allowed to touch one. The owner's run fills in the number |
| **P3.1 A third place to run** (D4). `model_steward.ensure(…, place="gpu"\|"cpu"\|"split", free_mib=…)`: `place="cpu"` books **RAM only** (`vram_mib=0`, file size + 15 %) and loads with `options={"num_gpu": 0}`; the place is remembered (`place_of`) and `loaded_models()` reports it | ✅ | `test_model_steward.py`: the load carries `num_gpu: 0`, the booking is 0 MiB of VRAM and `size × 1.15` of RAM, and a refusal of that RAM loads nothing and books nothing |
| Moving is an unload and a fresh load, and only ever after the memory for the new place is granted. A model Ollama split by itself counts as "on the card" | ✅ | `_needs_move` is deliberately asymmetric — without it the keep-warm loop would reload the fast model every two minutes for ever; 5 cases pinned |
| **P3.2 Ladder rung 3.** After every GPU rung is refused, the **fallback** rung is tried in RAM — only if its file is ≤ `DEXTER_CPU_RUNG_MAX_MIB` (5200). `data["ladder"]["place"] = "cpu"` | ✅ | `test_router.py`: both rungs refused → `ensure(place="cpu")` for `qwen2.5:7b` only, it answers, the ladder data says `cpu`; a 12B is never put there; RAM refused too → the existing `gpu_busy` path, unchanged |
| The chat line reads "on qwen2.5:7b (CPU) — VrDeep holds 10.0 GB", and appears even when there was only one rung to begin with | ✅ | `ChatFeed.ladderLine` / `ladderWorthSaying`; 2 new vitest cases |
| **P3.3 Shed rung 2: move, don't drop.** On `VramTight` red / `Preempt` the fast model is unloaded (the VRAM goes back first, always) and then reloaded into RAM when Sentinel grants it — "moved qwen3.5:4b to CPU" | ✅ | `test_model_steward.py`: red → unload then `num_gpu:0` load, step reads "moved …"; **RAM refused → the plain unload it always was**; a model too big for RAM is simply dropped |
| `VramRelief` still loads nothing (D5). The fast model returns to the card on the next turn that used it — `_keep_fast_model_warm` → `ensure(place="gpu")` — and only if Sentinel grants the VRAM | ✅ | the relief handler is unchanged (`test_relief_ends_the_wait_early` still passes); `_warm_quietly` asks for the card back only when the model is wholly in RAM, and a refusal leaves it answering from RAM rather than costing us the copy we have |
| `make_room` (v12, P2.2) does **not** move the fast model: the owner asked for an app, and loading 3 GB back into RAM in the same breath is the opposite of making room | ✅ | `shed(move_fast=False)`; `test_making_room_for_an_app_never_moves_the_fast_model`, and `test_tools_apps.py` pins the flag on the call |
| **P3.4 Partial split.** `place="split"` puts `(free − headroom) / (file size / layer count)` layers on the card, layer count from Ollama's `/api/show`; used for the fast model only, only after a full GPU load is refused | ✅ | `split_layers` arithmetic pinned (9 layers of 36 from 2000 MiB free with 1024 of headroom; 0 when a single layer will not fit; capped at the model's own layer count; 0 when Ollama cannot say); a split load books both halves and names `num_gpu: 9`; `_warm_quietly` tries `gpu → split → cpu`, each with its own reservation |
| **P3.5** The Resources panel names each resident model's place and size | ✅ | `ResourcePanel.modelLine` → "qwen3.5:4b 2.9 GB (CPU)", "qwen2.5:7b 4.9 GB (GPU)", "gemma-4-12b 2.0 GB (split)"; the rung line adds "in system memory"; 2 new vitest cases |
| **The suite can no longer reach a real model.** `conftest.py` now stubs `llm_router.warm_model` / `release_model` / `set_keep_alive` (loud failure), `loaded_models` (empty) and `chat` (a "no provider" response) for every test | ✅ | found the hard way while building P2.2: a ladder test that never mentions a model asked the owner's live Ollama and it **listed the owner's open windows**. Ollama is running on this machine, so an unstubbed `chat()` does not fail — it succeeds, and then the tool it picked runs for real |

**Two deliberate readings of the plan.**

- P2.2 says the fast model escalates "after one tool round the task is not
  done". A tool that returned `ok=False` is read as exactly that: the turn goes
  to the big model rather than letting the small one repair its own mistake.
- P3.3 says the shed rung moves the fast model on a `Preempt`. `make_room` also
  raises a `Preempt`-level shed, and there the move is switched off on purpose
  (row above) — the two callers want opposite things from the same rung.

**Not verified live, and not committed.** Nothing here was run against the
Dexter on port 8098, no model was loaded, moved or pulled, and `ollama` / `lms`
were never invoked — the owner's session verifies live (`PLAN.md` v13 § Owner
steps). `.env` now names `qwen3.5:4b` / `gemma-4-12b` / `qwen2.5:7b`; if a tag
is missing on the machine the ladder skips that rung with a log line, as it has
since v12.

## v12 (2026-09-11) — Grow into the free card, step aside when it is needed

Requested as: "buzzcode does not run every time, neither does buzzedit, how can
sentinel handle this situation?" → a smart-model ladder, Dexter making room for
an app the owner asked for, and a visible reason when it stepped down. Built by
an Opus implementation agent from `PLAN.md` v12, Phases 1-3, the same day.

**Why it happened.** Nothing here was a Sentinel bug. `router.smart_backend()`
named exactly one model, so a free card bought nothing and a busy one cost the
whole turn; and `tools_apps.open_app` read out the refusal even when the only
thing standing in BuzzEdit's way was **Dexter's own resident model**. Sentinel
was arbitrating correctly between claimants — one of the claimants just never
offered to move.

Suite: whole backend **894 passed**, 1 skipped, ~96 s (the 2 `test_buzzcaf_ports.py`
failures are the known sibling-repo line-ending drift, unchanged by this work) ·
**22 new backend tests** · vitest **25 passed** (4 new) · `npx tsc --noEmit` and
`npm run build` clean. Fakes only: no test loads, unloads, starts or stops
anything on this machine.

| Item | Status | Evidence |
|---|---|---|
| **Verified live (owner's session, 15:20-15:25 IST):** top rung `gemma-4-12b` imported into Ollama from the LM Studio GGUF (7.4 GB, tools capability) and set via Settings; free card → Sentinel granted 6144 MiB, the 12B answered with the weather tool (43 s cold, 13 s warm); with ~9.6 GB held by LM Studio + an inflated-context 3B → log `smart ladder: gemma-4-12b refused, trying qwen2.5:7b`, 7B granted, `/api/dexter/resources` → `ladder.steppedDownFrom = gemma-4-12b`; both refused → the existing `gpu_busy_degraded` reply on the resident 3B | ✅ | `data/dexter.log` 15:19:56 (granted 6144), 15:24:59 (both DENIED, 5479 available), 15:25:26 (fallback granted); backend 894 passed re-run |
| **The ladder.** `DEXTER_SMART_MODEL` (top) then `DEXTER_SMART_MODEL_FALLBACK` (default `qwen2.5:7b`); `router.smart_ladder()` resolves each rung with `llm_router.provider_for_model`, deduplicates, and skips a rung nobody serves with one log line | ✅ | `router.SMART_MODEL_FALLBACK`, `smart_ladder`, `_rung_backend`; tests: skipped rung, same tag on both rungs = one rung, no top rung = no ladder (the backend's own default, exactly as before v12) |
| Every escalation walks it: `sentinel_reserve_for_smart` per rung, first grant wins, `_release_smart_model_soon` for the rung **actually used**. All refused → the existing `gpu_busy` / resident-fallback path, untouched | ✅ | `test_router.py`: top granted → only the top is asked for; top refused → `["gemma-4-12b", "qwen2.5:7b"]` asked, `qwen2.5:7b` run, the refused rung never loaded; every rung refused → `gpu_busy`, nothing loaded. The four pre-v12 Sentinel tests still pass unchanged |
| **Sentinel stays the judge** (D2): the ladder changes what is asked for, never whether a refusal is honoured | ✅ | no new load path; `model_steward` is still the only door (the monopoly grep test still passes) |
| `shed()` gives back **every** resident smart rung, not just the top one; `resident_fallback` prefers the fast model, then any rung | ✅ | `test_model_steward.py`: 12B + 7B both resident → both unloaded, neither mistaken for "not ours"; fallback picks a resident rung when the 3B is gone |
| Settings → Model → *Smart model (fallback)*, live-reloading `router.SMART_MODEL_FALLBACK`, options read from the servers; `.env.example` documents both rungs | ✅ | `settings_store.SETTINGS` row `smartModelFallback`; `test_settings_store.py` live-patches the attribute and asserts `liveReload`; blank = one rung |
| **Make room** (D3): `model_steward.blockers_are_ours` is true only when *every* blocker Sentinel named is Dexter's own runner ("Ollama runner (Dexter)", `ollama.exe`, or a `llama-server.exe` whose parent is `ollama.exe`) | ✅ | unit-tested on the blocker shapes `sentinel_client` returns: our runner ✓, VrDeep ✗, buzzcode's llama-server ✗, mixed list ✗, empty list ✗, stray llama-server only when Ollama is its parent |
| `open_app`: refused + the blocker is ours → `shed("preempt", "making room for <app>")`, `release("making room")`, wait for `VramRelief` or up to `DEXTER_RELEASE_GRACE_SECS` (10 s), **re-query**, then launch. The summary says what was given back | ✅ | `test_tools_apps.py`: "Gave back qwen2.5:7b (4.9 GB) so Batchy could start. Batchy is up." · refused with **VrDeep** as the blocker → no shed at all, the existing "Not enough room" reply · a refusal that survives the shed launches nothing |
| Same step behind `start_local_model` (buzzcode's engine, 12.5 GB) | ✅ | `tools_code._make_room_for_engine`; `test_code_agent.py`: ours → shed then start, "Gave back … so the coding engine could start."; VrDeep → no shed, the engine's own refusal is reported |
| **Say so** (D4): a stepped-down turn carries `data["ladder"] = {model, provider, steppedDownFrom, blockers}`; a turn that got its first choice carries nothing | ✅ | `test_router.py` pins the exact dict, and `"ladder" not in res.data` on a top-rung turn. No apology text in the reply itself |
| The chat shows one quiet line under such a reply | ✅ | `ChatFeed.ladderLine` → "on qwen2.5:7b — BuzzEdit holds 9.0 GB"; 2 vitest cases (rendered when stepped down, absent otherwise) |
| `ResourcePanel` shows the current rung and why, from the last turn over the events bus | ✅ | `model_steward.note_ladder` / `last_ladder` → `snapshot()["ladder"]`, published as `resources`; "Thinking on **qwen2.5:7b** — stepped down from gemma-4-12b (BuzzEdit has the card)"; 2 vitest cases, including "absent → no line, no crash" |
| **No eager warming** (D5): `VramRelief` still loads nothing — it only unblocks a `make_room` that is waiting for exactly that news | ✅ | `_dispatch` sets `_relief` and publishes; `test_relief_ends_the_wait_early` |
| P1.5 `vram_estimate` logs which number it used (Ollama's real file size vs the configured budget); no behaviour change for Ollama tags | ✅ | `model_steward.vram_estimate` debug line; the existing estimate tests are unchanged |

**One deliberate reading of the plan.** P3.1's item text says `data["ladder"]`
goes on *every* agent turn; its acceptance column says "present on stepped-down
turns, absent otherwise". The acceptance wins — a good turn should not carry an
explanation — and the panel's need for the current rung is served by the
steward's `last_ladder()` instead, which is set on every escalation.

**Not done, because it is not code.** The top rung is still `qwen2.5:7b` on this
machine: importing a better model is the owner's step (`PLAN.md` v12 § Owner
steps — `ollama create gemma-4-12b` from the GGUF already in LM Studio's folder,
then Settings → Model). Until then the ladder has one rung and behaves exactly
as v11.1 did. Nothing here was verified against the running Dexter on 8098; it
was not restarted, and no commit was made.

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
