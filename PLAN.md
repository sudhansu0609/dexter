# Dexter Roadmap v13.9 — Measure the ceiling before tuning the thing under it

**Status date:** 2026-09-12 · **Progress log:** `PROGRESS.md` v13.9 · **Built:** from five asks in one sitting
**Status:** all five done, each verified against the real machine. 1604 backend tests, 42 frontend.

## The pattern worth keeping

Three of the five turned on a measurement that contradicted the obvious move.

- **Hand tracking.** The instinct is to tune thresholds. The models cost 9 ms a frame and the
  camera was delivering 16.6 fps, so two thirds of the quality was being thrown away before any
  threshold mattered. Media Foundation at the camera's native 640×360 doubled it.
- **Filter constants.** The published one-euro defaults are for metres; these coordinates are
  fractions of a frame, so β had to be ~100× larger. Measured across three hand speeds, β=30 beats
  no filter *everywhere* - which is the only reason it is safe to put underneath every pose decision.
- **Clicking page text.** Five routes into Chrome's accessibility tree, all empty. Modern Chrome
  publishes nothing without `--force-renderer-accessibility`, so the pixels are the only route that
  does not need a debug port. That is a fact about Chrome, not a gap in the code, and it is worth
  having written down before somebody tries UIA again.

### Decisions

- **The exact route applies only to the browser it is attached to.** Dexter's debuggable Chrome is a
  different browser; acting exactly on the wrong window is worse than acting approximately on the
  right one. Whichever browser is in front decides, with nothing to configure.
- **Accessibility first, pixels second.** Names are instant and right for an app's own furniture.
  OCR is two seconds and works on anything. The fallback is wired into the existing tools rather
  than offered as a separate one, so the fast path is never lost.
- **A weak match is never clicked.** Same rule as the verification tools: clicking the wrong thing
  on somebody's screen cannot be undone by asking again.
- **Scripts are scored, not found.** `stop_app.bat` next to `start_app.bat` is the normal case, and a
  project of only build scripts is named rather than guessed at.
- **Interrupting is allowed to hear one thing.** Letting the microphone stay open while Dexter speaks
  is safe only because nothing but a control phrase can come out of that window. The echo loop is
  then unreachable by construction rather than unlikely by threshold.
- **A catch-all goes last in the fast path.** Three ordering regressions this round, all caught by
  tests that existed. Specific checkers know more than generic ones.

## Open

- ~~OCR costs 2-3 s a look and is not cached.~~ **Done in v13.9.1**, and the frame hash alone was not
  enough: a real desktop is never identical twice (0.03% of pixels change every second - a clock, a
  caret, an FPS counter), so the cache also fingerprints each *word's own pixels*. A word that has
  not moved is clicked in 2.9 ms instead of 9.8 s; a word whose pixels changed buys a fresh read.
- `--force-renderer-accessibility` would give Chrome's page content to UIA instantly and for free,
  but only for a Chrome that Dexter starts. Worth offering as a one-time relaunch.
- The hand mouse now runs at 30 fps, so `confirm_frames=2` is a twentieth of a second rather than a
  tenth. The click debounce could be retuned now that frames are cheaper.
- `test_buzzcaf_ports.py` × 2 — still the pre-existing drift with the copies inside `BuzzcafAI/`.

---

# Dexter Roadmap v13.8 — Aimable, clickable, and able to answer a question

**Status date:** 2026-09-12 · **Progress log:** `PROGRESS.md` v13.8 · **Built:** from "can we control the speed… clicking is unstable" and "it can't check if Chrome is open"
**Status:** done, measured, and verified live over Playwright/CDP against a real YouTube page. 1539 backend tests, 42 frontend.

## Pointer speed, for an absolute pointer

There is no gain to turn up: the pointer is a *place*, not a velocity. Speed is the size of the
camera rectangle that maps onto the screen, scaled about its centre and clamped so the screen's
corners stay reachable by a hand that is still in shot. Settable from the panel, from Settings, and
out loud — the spoken route is not a nicety, because a pointer that is too fast cannot be used to
reach the slider that slows it down.

## Why clicking was unstable: three causes, not one

1. **The pointer moved through the click.** Pinching is a hand movement; the press landed away from
   where it was aimed. Now the pointer holds still from the moment a click pose appears, and only a
   movement that is both far (120 px, sized from the geometry, scaled by speed) and sustained
   (0.3 s) becomes a drag.
2. **A single frame could fire or drop a click.** Two consecutive frames now required either way —
   except park, which releases instantly, because the emergency stop cannot be debounced.
3. **A held pinch failed its own test.** `pinch_reach` keeps a fist from being read as a click, but
   pinching curls the finger and lowers that measurement. The bar is relaxed for the pinch already
   in progress, and only that one.

### Decisions

- **Size thresholds from the geometry, then test them against a hand that moves like a hand.** The
  first drag threshold was 30 px; a real pinch shifts 30–90. A test that moved a realistic amount is
  what caught it.
- **Say why the pointer is not moving.** "Holding still" in the panel and the preview. An
  unexplained stop reads as a freeze.
- **Questions deserve tools that answer them.** Lists plus inference is how a confident wrong answer
  gets made. Every verdict carries its route, and "I cannot tell from here" is a first-class answer.
- **A generic checker goes last in the fast path.** Specific ones know more: `local_model_status`
  knows the port, `app_status` knows whether a project is mid-job.
- **Weak evidence is not a yes.** A name inside a command line does not mean the program is running.

## Open

- Without the CDP route, only the front tab of each browser window is visible. That is a real limit
  of Windows accessibility, stated in the answer rather than hidden.
- `is_on_page` matches part of a word ("usb mic" finds "USB microphones"), which is usually wanted
  and occasionally loose. Words must be three characters or more.
- `test_buzzcaf_ports.py` × 2 — still the pre-existing drift with the copies inside `BuzzcafAI/`.

---

# Dexter Roadmap v13.7d — One espeak library per process

**Status date:** 2026-09-11 · **Progress log:** `PROGRESS.md` v13.7d · **Built:** from "do it" after the freeze diagnosis turned it up
**Status:** done and measured (13 temp directories per four sentences → 0), the 468.7 MB backlog swept, 16 tests.

### Decisions

- **Share the wrapper, take a lock.** phonemizer copies the DLL per instance to avoid concurrent use
  of espeak-ng's globals. A lock is the cheaper answer to the same problem, and `voice_engine` holds
  it around Kokoro — synthesis was already sequential, so it costs nothing.
- **Patch every module that builds one.** Three do (`base`, `espeak`, `mbrola`); patching only the
  obvious one took four copies per chunk down to one, which would have looked like success.
- **The sweep has to be strict, not clever.** It deletes only a `mkdtemp`-shaped directory holding
  exactly one file whose name is the copied library, older than ten minutes. Running over somebody's
  whole temp folder, "looks about right" is not a standard — 211 unrelated single-file directories
  sat next to the 1,172 real ones.
- **Optional by construction.** If the patch cannot apply, it says so and behaviour is unchanged.

## Open

- The per-chunk *time* is unchanged (~0.5 s); this was I/O and litter, not latency. The remaining
  0.5 s is Kokoro inference.
- `is_available()` building a whole wrapper just to answer a question is phonemizer's design, not
  something worth fighting further.

---

# Dexter Roadmap v13.7c — Never call a GUI toolkit's window setters from a worker thread

**Status date:** 2026-09-11 · **Progress log:** `PROGRESS.md` v13.7c · **Found:** because the owner left the third freeze running
**Status:** cause identified with py-spy, fixed, and pinned by a regression test that asserts the dangerous calls are absent.

## The cause

`window.on_top = True` in `DexterShell._raise` (added in v13.2 to lift the full window above the
always-on-top pop-up). pywebview sets .NET's `TopMost`; .NET issues a **synchronous**
`SetWindowPos`; that sends messages to the window's owning thread and waits. pythonnet holds the
GIL across the call, and the GUI thread needs the GIL to run the Python handlers attached to its
window events. Circular wait — and since one side held the GIL, the entire interpreter stopped.

Proven, not inferred: `NtUserSetWindowPos` holding the GIL, `PyGILState_Ensure` on the GUI thread,
`PyEval_RestoreThread` on every other thread, and `IsHungAppWindow` true for both windows.

### Decisions

- **Win32 through ctypes for anything that moves a window**, from any thread that is not the GUI
  thread. ctypes releases the GIL; `SWP_ASYNCWINDOWPOS` posts instead of sending. Both properties
  are needed, and neither is available through pywebview's setters.
- **The HWND is cached.** A raise should not go back through .NET at all.
- **The test asserts an absence.** A window stub records every marshalled property access, and
  `_raise` must leave it empty — the only way to stop this particular line coming back.
- **A silent process means the GIL.** In-process instrumentation cannot report it, by definition.
  `py-spy dump --pid <pid> --native` is the first move. The watchdog from v13.7b still earned its
  place: its *stopped* heartbeat and empty stacks file were what proved no Python thread was
  running, which is what pointed at the GIL.

## Open

- phonemizer copies the espeak DLL to a new temp directory on every synthesis (1,172 copies in
  `%TEMP%` here). Harmless but wasteful; caching one backend would remove it.
- Other pywebview setters on worker threads (`maximize`, `hide`, `show`) have the same theoretical
  shape. They have never hung in months of use, and `show()` is load-bearing, so they stay — but
  anything new on that path should go through Win32.

---

# Dexter Roadmap v13.7b — When it hangs, it has to say where

**Status date:** 2026-09-11 · **Progress log:** `PROGRESS.md` v13.7b · **Built:** from "I had to close dexter because it hanged again"
**Status:** the unbounded wait on the speech thread is fixed (both ends), and the watchdog is in. The cause of the two freezes is **not confirmed** — the next one will name the thread.

## The decision that matters

After two freezes whose only evidence was a log that stopped, the right move stopped being
"the next hypothesis" and became **instrumentation**. Three theories had survived a full round
of investigation; one was tested and disproved; none could be confirmed. That is a sign that the
information needed does not exist yet, and building more guesses on top of it is how an evening
disappears.

### Decisions

- **Evidence outside the logging system.** A deadlocked logging handler silences every thread and
  is itself a candidate, so the heartbeat is an `os.write` to a raw descriptor. If the heartbeat
  stops, the process is wedged; if it continues while HTTP requests stop, the WebView2 page is.
  The microphone lives in the page, so those two look identical from the outside — and that
  ambiguity is why two evenings produced no answer.
- **Stacks, automatically, once per stuck turn.** `faulthandler.dump_traceback(all_threads=True)`
  needs no cooperation from the stuck thread and allocates nothing. Once per turn, or a hang fills
  the disk with the same dump.
- **No unbounded wait inside a turn.** The speech thread was joined without a deadline, which can
  hold the `done` event the page waits on. Now 25 s, then answer anyway. And the page gives up on
  a silent stream after 150 s rather than thinking forever.
- **Say it, don't hide it.** Both timeouts produce a visible message. Silence is the symptom that
  cost two evenings.

## Open

- **The freezes are unexplained.** Candidates not yet excluded: the speech thread itself (now
  bounded and dumped), the WebView2 renderer, and something in the v13.6 polling that only bites
  in the live process. The heartbeat + stacks will point at one of them.
- If the stacks show the TTS thread, the next step is Kokoro/onnxruntime concurrency: the same
  session is used from the stream's synthesis thread and the non-streaming path.

---

# Dexter Roadmap v13.7 — Never launch a program you were not asked for

**Status date:** 2026-09-11 · **Progress log:** `PROGRESS.md` v13.7 · **Built:** from "dexter is hanging now"
**Status:** the misfire is fixed and tested (14 + 21 cases); the index rebuilt on this machine (1,602 → 692). The reported hang is **not reproduced** in the backend — see below.

## What actually happened

"can you open the hand tracking?" → `app_index.lookup("hand tracking")` → `C:\Program Files\Git\usr\bin\tr.exe`,
because matching was a substring test preferring the *shortest* key and `"tr" in "hand tracking"`. GNU `tr`
with no operands waits on stdin forever, so a console window appeared, took the foreground, and never left.

### Decisions

- **A wrong program is worse than no program.** Below four characters, match exactly or not at all; above it,
  match whole words. "I don't have that app" is a fine answer; `rm.exe` is not.
- **An application is the thing with a window.** The PE subsystem decides, not the name — the first attempt
  used name length and threw out VLC with the coreutils. Start Menu shortcuts are always apps, whatever they
  point at: a person made them, and they carry the arguments that make a CLI tool usable ("git bash").
- **Nothing Dexter launches keeps an open stdin.** A misresolved name must fail, not wedge. `run_command`
  remains the way to run a command line, and it asks first.
- **Dexter's own features are not searched for on the disk.** Anything the assistant *is* gets a rule above
  the launcher. This one cost an hour of looking in the wrong place; the next feature will too if the pattern
  is not kept.
- **A fix justified by a wrong theory is not a fix.** The `GetWindowText`-hangs explanation was plausible,
  load-bearing, and false; it was tested, disproved, and the module written for it deleted. The measurement
  is in `PROGRESS.md` so nobody re-derives it.

## Open

- **The reported hang is unexplained.** The turn routes in 0.0 s, the v13.6 polling costs 0.3 ms, and the
  per-turn context build is unaffected by a wedged console in the foreground. What remains unverified is the
  shell: whether a foreign window taking the foreground can leave the WebView2 windows unresponsive. If it
  happens again, that is where to look — and `faulthandler.dump_traceback_later` in `desktop_app` would
  settle it in one reproduction.
- `_MAX_SCAN_SECONDS` went 45 → 120: the PE reads took a cold scan to 34 s here, close enough to the old cap
  that a slower disk would have silently truncated the index.
- `test_buzzcaf_ports.py` × 2 — still the pre-existing drift with the copies inside `BuzzcafAI/`.

---

# Dexter Roadmap v13.6 — A gesture mouse has to show its work, and let you choose the gestures

**Status date:** 2026-09-11 · **Progress log:** `PROGRESS.md` v13.6 · **Built:** from "where do I see the hand tracking on the dashboard" and "add an option to customise the gestures"
**Status:** built, tested (65 backend cases + 9 UI), bundle rebuilt. Not committed.

## Why the readout is not a nicety

The hand mouse shipped with a floating OpenCV window and a tool you had to name. Nothing in the
app, nothing in `/api/dexter/status`. For most features that would be a missing convenience; for
this one it is the diagnosis, because **your hand is busy and the pointer is the symptom, not the
evidence**. "The camera cannot see you" and "it sees you perfectly, in a pose bound to do nothing"
look the same from the outside and have opposite fixes — and the first cut of this module was
reported as broken for exactly that reason (v13.2).

So every surface reports the **pose** and the **action** it was bound to, not one or the other:
the panel, the header chip, the pop-up chip, the preview window's caption, and the status payload.

## Why the gestures had to become settings

v13 chose the poses and wired each to a fixed job. Whether a fist parks or right-clicks is a
question about a person's hand, not about the code — and the evidence that this mattered was
already in the repo: the line describing the gestures in the start reply had been **wrong for two
releases**, because a hardcoded sentence cannot follow a map.

### Decisions

- **Pose and action are separate, with a map between them.** The detector has no opinion about
  meaning; `action_for()` is the only place that decides. That split is what makes everything else
  possible, including generating the spoken description instead of writing it out.
- **The defaults are the previous behaviour, exactly.** An upgrade changes nothing about anybody's
  hand mouse.
- **One channel for changes.** The spoken tool writes the setting the panel edits, which is the
  setting `.env` seeds. Three ways in, one source of truth.
- **Live, not "next time you start it".** You rebind *because* the current gesture is not working;
  stopping tracking to fix tracking is the wrong answer. Buttons held under the old map are
  released as part of the swap.
- **A nonsense binding costs you the default, not the use of your hand.** A typo in settings.json
  falls back to the shipped action rather than silently doing nothing.
- **Rehearsal is on the panel, next to Start.** The safe way to try a gesture you have just
  changed: every pose recognised, the real pointer untouched.
- **An unrecognised shape is bindable too.** Hands make shapes nobody planned for, and "sometimes
  it does nothing" is the worst failure a gesture mouse has.

## Open

- The preview window is still opened once, by the camera thread, so turning it on or off needs
  tracking restarted (every other hand setting is live). Worth closing if it ever annoys.
- The poses are finger-extension patterns plus two pinches. Rotation, handedness and a "thumbs up"
  are all detectable from the same landmarks if more bindable shapes are ever wanted.
- `test_buzzcaf_ports.py` × 2 — still the pre-existing drift with the copies inside `BuzzcafAI/`.

---

# Dexter Roadmap v13.5 — A window is not open until somebody can see it

**Status date:** 2026-09-11 · **Progress log:** `PROGRESS.md` v13.5 · **Built:** from "the maximised version of this app is not opening"
**Status:** fixed, tested (18 new cases), and the live `data/window.json` repaired. Not committed.

## The report, and why two releases missed it

v13.2 fixed a genuine cause of the same sentence — the full window rising *behind* the
always-on-top pop-up — and the running process had that fix. The remaining cause was not in
the shell at all: `data/window.json` held `x: -32000, y: -32000`, which is what Windows reports
for a **minimized** window. Dexter had saved it on a close-while-minimized and honoured it ever
since. The window opened perfectly, off every monitor.

**The lesson worth keeping:** "it doesn't open" and "it opens where you can't see it" are the same
report from the outside, and only the second one is consistent with a `show()` that returns True.
Persisted geometry is input from the last run — it gets validated like any other input.

### Decisions

- **A remembered position is a suggestion, not an instruction.** `on_screen()` judges it against the
  virtual desktop (`GetSystemMetrics` 76–79, so multi-monitor is fine and a since-unplugged monitor
  is not), requiring 120px of title bar to be reachable. Fail → drop it and let pywebview centre.
- **Size and position are separate facts.** A minimized close still tells you how big the window was;
  it tells you nothing about where it belongs. Only the position is discarded.
- **Check again at show time.** A file written before this release may already have placed the window
  off-screen, so `open_full()` walks it back before maximising rather than trusting creation.
- **Never trade a visible window for an invisible one.** The pop-up is hidden only once the full
  window is confirmed on-screen.

## Open

- `on_screen` uses the *virtual* desktop rectangle, not per-monitor work areas, so a window centred
  in the gap of an unusual monitor arrangement would pass. Worth tightening only if it ever happens.
- `test_buzzcaf_ports.py` × 2 — still the pre-existing drift with the copies inside `BuzzcafAI/`.

---

# Dexter Roadmap v13.4 — Playwright, and the answer to "can notes/reminders/schedules work"

**Status date:** 2026-09-11 · **Progress log:** `PROGRESS.md` v13.4 · **Built:** from two questions asked together
**Status:** built, tested, and **verified live against the real browser** (Chrome/153 on 9222; real Google and YouTube results; `open_result(2)` landed on the right page). Not committed.

## "Can Playwright make Dexter's actions more precise?"

Three answers, in order:

1. **It was already here.** `browser.py` is a Playwright driver (v6) and has always been able to attach over CDP.
2. **It was demoted deliberately** in v11, because it launched a *second* Chrome on its own profile: none of the owner's logins, and "close this tab" acting in a window they were not looking at. That is why v11-v13.3 drive the real browser through Windows accessibility, which can only see names.
3. **The third option is strictly better than both**, and is what v13.4 adds: Playwright over CDP attached to a Chrome that *is* the owner's, started by Dexter on a persistent profile. Chrome 136+ refuses remote debugging on the default profile, so a separate `--user-data-dir` is the only way this works; signing in there once makes it theirs.

### What the precision actually buys

| accessibility route | CDP route |
|---|---|
| the nth *name* that looked like a result | the nth `<a href>` in document order, with its URL |
| click, `sleep`, hope; check the window title | `goto`/`click` that waits, and reports the URL reached |
| Ctrl+C and read the clipboard | `window.getSelection().toString()` |
| type at whatever has focus | the field found by label / placeholder / role |
| **the window must be in front** | no focus stealing — Dexter can work while the owner does |

### Decisions

- **Never a dependency.** `page_control.precise()` is False until a debuggable Chrome answers, and every function falls back to the v13.1-v13.3 route. The reply says which route it took.
- **Its own profile, and said out loud.** A debug port is a local privilege; it is bound to loopback, off until asked for, and the tool's reply explains the profile and the sign-in once.
- **The extraction is three tiers, because one was wrong.** A plain `a[href]` scan returned Google's furniture (*Accessibility help, Google apps, AI Mode, Shopping*). Now: per-site selectors for where "open the third result" is said, then main-content links with nav/header/footer excluded and headings preferred, then the loose scan. Verified against live Google and YouTube.

## "Can Dexter take notes and set reminders or schedulers?"

Yes, and all of it is live on the running instance (`scheduler: running, 13 jobs, 1 pending reminder, toasts via winotify`): notes (one-shot and continuous dictation into notes/Notepad/Sticky/Notion), reminders on three channels (toast, banner, spoken), workbook commitments with rolling counted, recurring or one-shot **schedules that run any goal through the same tiers a typed message uses**, and the Mission Reminder bridge for blocks. See `PROGRESS.md` v13.4 for the phrasings.

## Open

- The in-page extraction is JavaScript: verified by the live run recorded in `PROGRESS.md`, not by a unit test. If a site redesigns, its tier-1 selector is one line in `browser_cdp.py`.
- `data/chrome_debug_profile` is a fresh Chrome until the owner signs in; until then "exact" applies to public pages only.
- `test_buzzcaf_ports.py` × 2 — still the pre-existing drift with the copies inside `BuzzcafAI/`.

---

# Dexter Roadmap v13.3 — Pointing at what is on the screen

**Status date:** 2026-09-11 · **Progress log:** `PROGRESS.md` v13.3 · **Built:** from a list of seven things the owner tried and found broken
**Status:** built and tested (1318 backend tests; the 2 failures are the pre-existing `buzzcaf_ports` drift in `BuzzcafAI/`). Not committed. Needs a restart to take effect.

The common thread in all seven: **the thing the owner meant was visible on screen, and Dexter had no way to point at it.** A sentence with no rule and no tool reaches the model, which answers it — which is exactly what "it says it's doing it but it does nothing" looks like from the outside.

| Said | Was | Now |
|---|---|---|
| "okay, open chrome for me please" | the comma defeated the filler strip, so the model got the whole sentence as an app name | filler stripped in the router *and* in the launcher (verb included) |
| "select the first profile" | no rule, no tool | `select_nth` — the nth thing of a kind in any window |
| "select the first video" | "select" was not a verb in the ordinal rule | three-way split: video → `youtube_play`, result/link → `open_result`, profile/option/row → `select_nth` |
| "search … in the current tab" | every browser tool opens a *new* tab | `open_in_current_tab` (Ctrl+L) + `search_here` |
| "like / subscribe / open the comments" | no tool | `youtube_page_action`, by YouTube's own accessible names; comments scroll into view first |
| "google X and click the first link" | searched for the whole sentence | `search_and_open` |
| "copy this … paste it in the Gemini tab" | no tool | `copy_text` / `paste_text`, which refuse rather than type into the wrong field |
| "in youtube, select the first video" | tried to focus a *window* called youtube | a site prefix switches tabs; an app prefix still focuses a window |

## Decisions

- **Position, words, or field — but always something visible.** Every new verb works off the accessibility tree, so what Dexter lists is what the owner can see, and a miss names the controls that *are* there instead of failing blankly.
- **A refusal beats a wrong action.** `paste_text` will not type into a field it cannot find, and will not paste an empty clipboard. For text going into someone's Gemini prompt, the wrong target is worse than nothing.
- **Claims are checked where they can be.** `open_result` compares the tab title before and after the click and says whether the page actually moved.

## Open

- **`select_nth` on an app that publishes nothing.** Some windows (games, custom renderers) expose no UIA tree at all; the tool says so and lists nothing, because there is nothing to list.
- **`test_buzzcaf_ports.py` × 2** — still the pre-existing drift with the copies inside `BuzzcafAI/`.
- A heredoc mangled one regex into a control character during this round (`\b` → `\b`). Scanned the tree for control characters; one instance, repaired. Regex edits go through the editor.

---

# Dexter Roadmap v13.2 — What using it found

**Status date:** 2026-09-11 · **Progress log:** `PROGRESS.md` v13.2 · **Built:** the same evening, from the owner's own use of v13.1
**Status:** built and tested. Not committed. The hand mouse is now verified on the owner's camera and hand (48 of 133 frames tracked at palm 0.87, gesture `point`, preview window open) — the thing v13.1 listed as unverified.

Four findings from using it, and in three of the four the cause was not what the symptom said:

| Symptom | Actual cause | Fix |
|---|---|---|
| "the previous model isn't offloaded" | a model left memory only on keep-alive expiry (30 min / 2 h), so switching *added* one | `model_steward.retire()`, wired into the ladder, an explicit model pick, and a Settings change; `DEXTER_OFFLOAD_PREVIOUS` |
| "I can't move the cursor with my hand" | tracking was fine (538 of 7936 frames had a hand) — an **open palm was mapped to park**, which is the first pose anyone makes | open hand steers, **fist** parks |
| (found while fixing that) a fist pressed the **right button** | in a closed hand the thumb tip sits 0.35 hand-widths from the middle fingertip, inside the pinch threshold | a pinch needs the finger *reaching out* (≥1.25 knuckle-lengths; open hand 2.0-2.4, fist 0.87-0.96) |
| "the pop-up window is not maximising" | `show()` does not raise a window, and the pop-up is `on_top`, so the main window came up behind it | `_raise()`: show → restore → `on_top` flip → `SetForegroundWindow`; and opening the full window hides the pop-up |
| "a small window shall show the tracking" | there wasn't one | `hand_preview.py`, on by default, with the palm score when it sees nothing |

## Decisions

- **The fast tier is not offloaded on every switch.** It is 3 GB and every chat turn needs it; unloading it whenever the big model runs would make the *next* sentence pay 2-3 s. `always` is there for anyone who would rather have the memory, and is one click away in Settings.
- **A preview is part of the feature, not a debug tool.** Without it, "the camera cannot see my hand" and "my hand is in a pose that deliberately does nothing" are indistinguishable — which is precisely how this was reported.
- **Thresholds come from measurements, not taste.** The pinch-reach bar and the palm-score floor were both set from numbers taken off real hands and this webcam, and both are in `.env` for a different room.

## Open

- **`test_buzzcaf_ports.py` × 2** — still the pre-existing drift between this repo's `buzzcaf_ports.py` / `ECOSYSTEM.md` and the copies inside `BuzzcafAI/`. Another project, and a sync the owner should make deliberately.
- **Open-mic mode hears the room.** With `wakeMode: open` (switched on in the live session) the v13.1 gate still lets a confident ambient sentence through — that is what open mic *means*. `Learn my voice` + `Only answer to my voice` is what makes "everything" mean the owner; with a television on, `name` mode is the right setting.

---

# Dexter Roadmap v13.1 — Reach

**Status date:** 2026-09-11 · **Progress log:** `PROGRESS.md` v13.1 · **Built:** the same day, in the owner's session
**Status:** built and tested (1200 backend tests pass; 2 pre-existing failures in `test_buzzcaf_ports.py` are another repo's drift). Not committed. One thing is **not** verified and is named as such below: hand tracking against the owner's own hand in front of their own camera.
**Owner's ask (2026-09-11):** "not able to open c drive or any such drive or list folders or open a specific folder, or open a specific video from opened youtube, or close buzzanimate or in general any app apart from the ones mentioned to it… hand tracking to work as a mouse using the webcam for click right click scroll… its listening to some random stuff" + "take notes from me continuously… notion or notepad or sticky notes or any tool i say… control BuzzcafAI and select tabs and texts from it… i want to use my computer while sitting away from it… schedule anything i want and use the missionreminder app automatically to save and review my missions" + "add option to mute dexter from pop up window".

## What was actually wrong (found, not guessed)

| Complaint | Cause | Fix |
|---|---|---|
| Can't open the C drive / list a folder | `pathguard` allowed six user folders; **and no tool listed a directory at all** | `DEXTER_FS_SCOPE` (machine/user), read vs write as separate questions, `folders.py` + four tools |
| Can't close BuzzAnimate | the registry record named `buzz-app.exe`; the binary is `buzzanimate.exe` | record fixed **and** a generic fallback in both layers, so the next wrong record is not a dead end |
| Can't open a specific video | `click_text` shared one 5 s budget across control types; YouTube's Buttons ate it before Hyperlink | per-type budget + ranked matches, and a YouTube module with two honest routes |
| Listening to random stuff | stored `followUpWindow: 15`, `bargeIn: true`, and a grey speaker verdict counting as "addressed" | one gate (`accept_utterance`), miss backoff, safer defaults, settings reset |
| No continuous note-taking | nothing existed | `dictation.py`: a mode, six sinks, spoken punctuation |
| Can't drive BuzzcafAI | no tool for another app's tabs/text | `list_controls`, `click_in_app`, `copy_text_from` |
| Missions not saved/reviewed | the app has no inbound API by design | read its runtime mirror; write by driving its own UI, with an outbox and a label map |
| No hand mouse | mediapipe has no Python 3.14 wheels | its two models through onnxruntime, with a mediapipe backend kept for a 3.12 venv |
| No mute in the pop-up | `muted` meant mic **and** voice | a separate `silent` state, a speaker button, both spelled out in quick settings |

## Decisions taken while building

- **Reading the disk is not writing to it.** The machine scope makes everything readable, and `C:\Windows`, Program Files, ProgramData and a drive root stay unwritable in *both* scopes. An assistant that occasionally mishears must not have a delete there.
- **An app is whatever Windows says it is.** Image name, exe path, the folder it runs from, command line, FileDescription — with editors and terminals excluded, so VS Code with a project open is never "the app".
- **Type into other people's apps rather than integrate with them.** Notion, Sticky Notes and Mission Reminder are driven the way a person drives them. No API keys Dexter has no business holding, and "any tool I say" becomes true rather than a list.
- **Say which route was taken.** "Clicked the link on your page" and "searched YouTube and opened the first result" are different claims; the same for a mission block that saved versus one sitting in the outbox.
- **Every gesture is a distinct shape.** Pinch, middle-pinch, two fingers, open palm — not thresholds on one pose — with hysteresis, a click rate limit, buttons released when the hand leaves, and `Ctrl+Alt+H` as a kill switch that does not need the pointer.

## Open, and why

- **Hand tracking against the owner's hand.** Verified as far as it can be without them: the anchor table matches the OpenCV Zoo reference to 7e-9, the decode matches theirs, a real photograph of a hand gives palm 0.879 / landmarks 0.986 / `fingers_out` = fist, and the camera delivers 77 frames in 3 s through the tracker. Say **"can you see my hand"** — it reports frames, share and confidence — then **"start hand mouse in dry run"** before letting it touch the pointer.
- **Mission Reminder's UI labels.** Read out of its source (`New block`, `Name`, `Starts at`, `Save block`, `Ctrl+1..7`), not from a running window. If a save fails, `mission_outbox` says so and `data/mission_ui.json` fixes it without code.
- **Dictation into Notion's *web* app** types into whatever has focus in that tab; the desktop app is preferred when it is installed.
- **`test_buzzcaf_ports.py` × 2** — `BuzzcafAI/backend/integrations/buzzcaf_ports.py` and its `ECOSYSTEM.md` have drifted from this repo's canonical copies. Pre-existing, in another project, and a sync the owner should make deliberately.

## Owner steps

1. Restart Dexter (the running instance predates all of this).
2. `"set up hand tracking"` → `"can you see my hand"` → `"start hand mouse in dry run"` → `"start hand mouse"`.
3. Try the seven complaints back: `"open the c drive"`, `"what's in my downloads"`, `"close buzzanimate"`, `"play the second video"` on an open YouTube, `"take notes in notepad"`, `"review my missions"`, and the pop-up's speaker button.
4. If the room still gets through to the mic: raise **Settings → Voice → Confidence for an unaddressed command**, or set the follow-up window to 0 and use the name every time.

---

# Dexter Roadmap v13 — The right model for the moment, on the GPU or off it

**Status date:** 2026-09-11 · **Progress log:** `PROGRESS.md` · **Built by:** an Opus implementation agent from this plan
**Status:** Phases 1-3 built 2026-09-11 (evidence in `PROGRESS.md` v13): backend 940 passed / 2 known pre-existing failures, 46 new tests; vitest 29; `tsc` and `npm run build` clean. P2.3's harness mode is built but its numbers are not measured here — it needs a real model, and this build touched none. Not verified on the running instance (port 8098 untouched), not committed; the owner steps below are outstanding.
**Addendum (owner's session, after the build):** `gemma4:e4b-it-qat` measured after the build ties qwen3.5:4b on tool choice (27/30) and settles more instructions on its own (10/12 vs 9/12); it is the fast model in `.env`. Gemma 3 has no tools capability on Ollama and is out. The fast-act gate was widened to any non-chain sentence reaching escalation. See PROGRESS.md v13.
**Owner's ask (2026-09-11):** "use a quicker but smart enough model like gemma 4 or you decide the best model, small for quick answers and has tooling, then move to another model for complex tasks. We can also offload some of the models from GPU if Dexter does not have this — implement it. Find the best models and create a plan."

## Measured on this machine (RTX 5060 Ti 16 GB, i7-14700K 20 cores, 32 GB RAM, Ollama 0.32.14)

`backend/bench_models.py` (new; keep it): cold load, VRAM as Ollama reports it, chat speed, and **tool choice on 30 cases** — the 15 from `test_agent_eval.py` plus 15 hard ones (two acts in one sentence, arguments that must be composed, a window that must be named, three questions that must *not* become a tool call). Each model gets the same 8 candidate tools Dexter would hand it; only the first call is judged; nothing is executed; `think:false`.

| model | VRAM MiB | chat tok/s | tool choice | tool call latency | verdict |
|---|---|---|---|---|---|
| qwen2.5:3b (today's fast) | 2059 | 175 | 20/30 | 0.45 s | calls tools for chat, wrong tool a third of the time |
| granite4.1:3b | 2385 | 155 | 25/30 | 0.42 s | fast, but twice took a joke for a tool |
| **qwen3.5:4b** | **2983** | **111** | **27/30** | 0.88 s | best of all seven; never called a tool on a question in the re-run |
| gemma4:e2b-it-qat | 1573 | 170 | 26/30 | 0.62 s | smallest VRAM; once made a haiku a note |
| qwen2.5:7b (today's smart) | 4528 | 93 | 25/30 | 0.68 s | |
| qwen3.5:9b | 5235 | 71 | 23/30 | 1.22 s | worse than its 4b sibling; drop it |
| gemma-4-12b (v12 top rung) | 7630 | 52 | 26/30 | 1.51 s | best on the composed-argument cases; the one to keep for real reasoning |

Two facts that shape the design, both measured, not read:

1. **Thinking models return nothing through Dexter's current path.** qwen3.5 through `/v1/chat/completions` spent all 200 tokens on reasoning and returned empty content; `think:false` is ignored there. Through Ollama's native `/api/chat` with `think:false` the same question answers in 0.6 s. Every qwen3.5 / gemma4 rung needs the native path.
2. **Ollama keeps a model where you put it.** `options.num_gpu: 0` loads a model on the CPU (3B: 24 tok/s, 0 MiB VRAM); a later request *without* the option keeps using that CPU instance; `num_gpu: N` gives a partial split (10 layers → 777 MiB, 35 tok/s). So a model can live in RAM while a render has the card, and come back to the GPU on the next load.

## Decisions (owner, 2026-09-11)

- **D1 Fast tier = `qwen3.5:4b`**, and it may **act**: a single tool call on the fast model when the semantic tiers had a clear candidate set, before any escalation. Most commands then answer in about a second without touching the smart rung. Escalate exactly as today when the fast model produces no call, an invalid call, a chain sentence, or after one tool round the task is not done.
- **D2 Smart ladder stays**: top `gemma-4-12b`, fallback `qwen2.5:7b` (v12). `qwen3.5:9b` is not used (measured worse than the 4b).
- **D3 Ollama through its native API.** `/api/chat` for the Ollama provider: `think` per request (off for fast and tool rounds; `"low"` allowed for the smart top rung on chain sentences), `keep_alive` and `options` per request, streaming, tools. LM Studio and llama-server keep the OpenAI-compatible path.
- **D4 A third place to run: RAM.** When Sentinel refuses VRAM for a rung but grants RAM, small rungs (≤ 5 GB file) load with `num_gpu: 0`. When Sentinel says VramTight, the fast model is **moved** to the CPU instead of unloaded, so quick answers keep coming at ~20 tok/s while the card is busy; it goes back to the GPU on the next load after relief, never eagerly. Big rungs (the 12B) are never run on the CPU — 8 tok/s is not an assistant.
- **D5 Sentinel stays the judge.** VRAM *and* RAM are booked with Sentinel before any load (it already takes `ram_mib`); a CPU load reserves RAM only. Nothing loads on a refusal.
- **Not doing:** vision/audio inputs, changing Sentinel's config, partial-offload tuning beyond P3.4, pulling anything else. The owner removes `qwen3.5:9b` (`ollama rm`) if the disk matters.

## Phase 1 — Native Ollama path (P1)

| # | Item | Acceptance |
|---|---|---|
| P1.1 | `hybrid_llm.py`: `_chat_ollama_native()` using `POST {ollama}/api/chat` with `messages`, `tools`, `stream`, `think`, `keep_alive`, `options`; streams `message.content` deltas to `on_token` exactly as the OpenAI path does; parses `message.tool_calls` into the same `LLMResponse.tool_calls` shape; `eval_count`/`eval_duration`/`load_duration` kept in `LLMResponse.usage` | `test_hybrid_native.py`: fake server; content, tool calls, cancellation mid-stream, `think:false` sent by default, an `options`/`keep_alive` pass-through |
| P1.2 | `chat(...)` gains `think: Optional[bool|str] = False`, `options: Optional[dict] = None`, `keep_alive: Optional[str] = None`; when the chosen provider is `ollama` the native path is used; other providers ignore the extras | existing `test_model_residency.py` + router tests unchanged and green |
| P1.3 | Thinking model safety on the OpenAI path: if a reply has empty `content` but non-empty `reasoning`/`reasoning_content`, the response is marked `error="model only thought"` so the router escalates instead of showing nothing | one test |

## Phase 2 — The fast model acts (P2)

| # | Item | Acceptance |
|---|---|---|
| P2.1 | `router.py`: `FAST_MODEL` default `qwen3.5:4b`, `FAST_KEEP_ALIVE` unchanged; `.env`, `.env.example`, `settings_store` defaults updated; preflight warns when the configured fast model is not pulled and says the `ollama pull` line | settings tests |
| P2.2 | `_fast_act()` between Tier 1 and the smart escalation: when `looks_like_action(text)` and the message is **not** a chain (`is_desktop_chain`) and `candidate_tools(text)` is non-empty, ask the fast model with those tools (`think:false`, `max_tokens 300`). A valid call on a non-confirm tool (or a confirm tool: asks as usual) is executed and the reply is the tool's summary voiced by the fast model in one line; no call / invalid args / unknown tool → escalate exactly as today. Never more than one round on the fast model | `test_router.py`: valid call runs the tool and does not escalate; no-call escalates; invalid args escalates; chain sentences skip `_fast_act`; confirm tools still ask |
| P2.3 | The eval harness gains a `--fast-act` mode that reports how many of the 30 cases the configured fast model would settle without escalation; numbers go in PROGRESS | harness runs (skipped in CI without a model) |

## Phase 3 — Off the GPU (P3)

| # | Item | Acceptance |
|---|---|---|
| P3.1 | `model_steward.ensure(model, purpose, provider, keep_alive, place="gpu"|"cpu")`: `place="cpu"` reserves **RAM only** (`vram_mib=0`, `ram_mib=file size + 15 %`) and loads with `options={"num_gpu": 0}`; residency bookkeeping records the place; `loaded_models()` reports `onGpu` from `size_vram` (already does) | fake Ollama: the load call carries `num_gpu: 0`; a Sentinel refusal of RAM loads nothing |
| P3.2 | Ladder rung 3: after the GPU rungs are refused, the **fallback** rung (and only rungs whose file is ≤ `DEXTER_CPU_RUNG_MAX_MIB`, default 5200) is tried with `place="cpu"`; `ladder` data says `place: "cpu"`; the ChatFeed line reads "on qwen2.5:7b (CPU) — X holds N GB" | router tests with fakes |
| P3.3 | Shed rung for the fast model on `VramTight` red / `Preempt`: **move, don't drop** — `unload` then `ensure(place="cpu")` when Sentinel grants the RAM; the steward logs "moved qwen3.5:4b to CPU"; on `VramRelief` nothing happens (D5, no eager warming); the next `ensure(place="gpu")` for the fast model — which `_keep_fast_model_warm` already calls after a fast reply — brings it back only if Sentinel grants VRAM | steward tests: red → move; RAM refused → plain unload as before |
| P3.4 | Optional partial split: `place="split"` with `num_gpu` computed from Sentinel's free VRAM (`free − headroom`) / (file size / layer count, from `/api/show`); used only for the fast model, only when a full GPU load is refused | one test on the arithmetic; skipped otherwise |
| P3.5 | Resources panel shows each resident model with its place (GPU / CPU / split) and MiB | vitest |

## Tests and rules for the build

- Fakes only; `conftest.py` already disables real model-server autostart and real desktop actions. Patch every Ollama HTTP call (`requests.post`/`Session.post` on the router's session) — no test may load, unload, move or pull a real model. Do not run `ollama` or `lms`; do not talk to the running Dexter on 8098 (the owner's session verifies live). Do not commit.
- Whole backend suite green (`python -m pytest -q` in `backend/`; the 2 `test_buzzcaf_ports.py` failures are a sibling-repo line-ending drift and are not yours); `npx tsc --noEmit`, `npx vitest run`, `npm run build` clean.
- File content with backslashes or regexes goes through the Write/Edit tools, never shell heredocs (this environment collapses `\\`).
- Record what landed in `PROGRESS.md` as **v13** above v12, with evidence; set this plan's status line.

## Owner steps (after the build)

1. Restart Dexter. The fast tier is `qwen3.5:4b` (pulled), the smart ladder `gemma-4-12b` → `qwen2.5:7b` (both present).
2. Optional: `ollama rm qwen3.5:9b` (6.6 GB, not used) and `ollama rm qwen2.5:3b` once the 4b has earned its place.
3. Watch `data/dexter.log` for `[Router] fast act:` lines — that is how often a command was settled without the big model.

---

# Dexter Roadmap v12 — Grow into the free card, step aside when it is needed

**Status date:** 2026-09-11 · **Progress log:** `PROGRESS.md` · **Built by:** an Opus implementation agent from this plan
**Status:** Phases 1-3 built 2026-09-11 (evidence in `PROGRESS.md` v12): backend 894 passed / 2 known pre-existing failures, 22 new tests; vitest 25; `tsc` and `npm run build` clean. Not yet verified on the running instance (port 8098 untouched), not committed. The owner steps below are still outstanding — until a better top rung is imported, the ladder has one rung.
**Owner's ask (2026-09-11):** "buzzcode does not run every time, neither does buzzedit, how can sentinel handle this situation?" → yes to: a smart-model ladder, Dexter making room for an app the owner asked for, and a visible reason when it stepped down.

## What is true today (measured in the code, not guessed)

- Nothing in Dexter is a fixed slice of the card. `model_steward.reserve` asks Sentinel for the model's **real file size** (`runtime_health._model_size_mb` over Ollama's `/api/show`) plus `RESERVE_OVERHEAD_MIB`, and Sentinel answers against **live free VRAM** (`data/dexter.log`: "5490 MiB requested but only 5489 MiB available (6257 MiB free, 768 MiB kept as headroom)"). `DEXTER_VRAM_BUDGET_MIB=5120` is only the estimate used when Ollama cannot report a size.
- Pressure is already cooperative: `VramTight red` / `RamTight` / `Preempt` events reach `model_steward._dispatch`, which runs `shed()` (smart → fast → foreign Ollama → LM Studio → an engine Dexter started) and `release()`. Bookings expire after 90 s. Models reload lazily on the next turn that needs them; `VramRelief` deliberately does nothing.
- Priorities in `C:\ProgramData\Sentinel\config.toml`: VrDeep 90, Dexter/Ollama runner 50, buzzcode's llama-server 30, ffmpeg 10. Higher preempts lower.
- **What is missing:** `router.smart_backend()` always names one model (`SMART_MODEL`, qwen2.5:7b), so an empty card buys nothing. `tools_apps.open_app` asks Sentinel with `query()` and, when refused, reports the blocker — even when the blocker is Dexter's own resident model. Nothing in a reply says "I am on the small model because BuzzEdit has the card".

## Decisions (owner, 2026-09-11)

- **D1 A ladder, not a bigger fixed model.** Two rungs: `DEXTER_SMART_MODEL` (top, the better model) and `DEXTER_SMART_MODEL_FALLBACK` (default `qwen2.5:7b`). At each escalation Dexter asks Sentinel for the top rung first and takes the fallback on a refusal. The fast 3B tier is untouched.
- **D2 Sentinel stays the judge.** No rung loads without a granted reservation; a refusal never loads anything (the existing rule). The ladder only changes *what is asked for*, in what order.
- **D3 Dexter yields to what the owner asked for.** "Open buzzcode" while Dexter's own model is the blocker → Dexter sheds its own models, releases the booking, re-queries, and launches. It never sheds anyone else's work to do this (that is Sentinel's call, by priority).
- **D4 Say so.** When the reply came from the fallback rung because the top was refused, the turn's data carries `ladder: {model, steppedDownFrom, blockers}` and the UI shows one quiet line. No apology text in the reply itself.
- **D5 No eager warming.** After relief the top rung comes back on the next escalation that gets it, never on the event.
- **Not doing:** pulling or converting models (the owner does `ollama pull` / `ollama create`; §Owner steps), changing Sentinel's config or priorities, touching LM Studio residency (LM Studio manages its own; a rung there is used as-is, no reservation possible beyond the budget estimate).

## Phase 1 — The ladder (P1)

| # | Item | Acceptance |
|---|---|---|
| P1.1 | `router.py`: `SMART_MODEL_FALLBACK = os.getenv("DEXTER_SMART_MODEL_FALLBACK", "qwen2.5:7b")`; `smart_ladder(preferred) -> List[Tuple[provider, model]]` = top rung then fallback, deduplicated, each resolved with `llm_router.provider_for_model` (a rung nobody serves is skipped, with one log line) | unit-tested with a fake `provider_for_model` |
| P1.2 | The escalation path in `route()` walks the ladder: `sentinel_reserve_for_smart(model)` per rung; first grant wins; all refused → the existing `gpu_busy` path (resident fallback / note) unchanged. `_release_smart_model_soon` is called for the rung actually used | `test_router.py`: top granted → top used; top refused, fallback granted → fallback used with `data["ladder"]`; both refused → `gpu_busy` exactly as before (existing tests still pass) |
| P1.3 | `model_steward._tier_models()` returns `(smart, fast, fallback)`-aware data so `shed()` unloads **every** smart rung that is resident (both, if both are), then fast on red; `resident_fallback` still prefers the fast model, else any resident rung | `test_model_steward*.py` / `test_model_residency.py`: shed with the 12B and the 7B resident unloads both |
| P1.4 | Settings → Model: a third `model`-kind row `smartModelFallback` (`DEXTER_SMART_MODEL_FALLBACK`, module `router`, attr `SMART_MODEL_FALLBACK`); help text explains the ladder in one sentence. `.env.example` documents both | `test_settings_store.py`: live-patches `router.SMART_MODEL_FALLBACK` |
| P1.5 | `vram_estimate` for a rung on LM Studio or unknown to Ollama uses the budget estimate as today; log which estimate was used | no change in behaviour for Ollama tags |

## Phase 2 — Make room (P2)

| # | Item | Acceptance |
|---|---|---|
| P2.1 | `model_steward.blockers_are_ours(blockers) -> bool`: true when every blocker Sentinel named is Dexter's own runner (Sentinel labels "Ollama runner (Dexter)" / process `llama-server.exe` whose parent is `ollama.exe`, or a pid in `loaded_models()`), false if anyone else is in the list | unit-tested on the blocker shapes `sentinel_client` returns today |
| P2.2 | `tools_apps.open_app`: on a refused `query()`, if `blockers_are_ours` → `shed("preempt", "making room for <app>")`, `release("making room")`, wait for `VramRelief` or up to `release_grace_secs` (10 s, poll `snapshot()`), re-`query()` once; then launch as today. The summary says what was given back: "Gave back qwen2.5:7b (4.9 GB) so BuzzEdit could start." | `test_apps*.py`: refused-with-our-blocker → shed called, second query granted → launched; refused-with-VrDeep-as-blocker → **no shed**, the existing "not enough room" reply |
| P2.3 | Same make-room step behind `start_local_model` (buzzcode's engine) since it needs 12.5 GB | one test |

## Phase 3 — Say so (P3)

| # | Item | Acceptance |
|---|---|---|
| P3.1 | `RouteResult.data["ladder"]` on every agent turn: `{"model", "provider", "steppedDownFrom": model or null, "blockers": [...]}` | present on stepped-down turns, absent otherwise |
| P3.2 | `src/components/ChatFeed.tsx`: a muted one-liner under a reply that stepped down: "on qwen2.5:7b — BuzzEdit holds 9.0 GB" (reuse the existing gpu_busy styling) | one vitest case |
| P3.3 | `ResourcePanel`: the current rung and why (from the last turn's ladder data over the events bus) | renders without a crash when absent |

## Tests and rules for the build

- Everything against fakes. `conftest.py` already forbids real keystrokes, real model-server starts (`llm_boot.ENABLED=False`) and LM Studio management; **no test may load or unload a real model** — patch `model_steward.unload`, `ensure`, `reserve`, `query`, `llm_router.provider_for_model` / `loaded_models`.
- The whole backend suite must stay green (`python -m pytest -q` in `backend/`, ~2 min; 2 pre-existing failures in `test_buzzcaf_ports.py` are a sibling-repo line-ending drift and are not yours). `npx tsc --noEmit`, `npx vitest run`, `npm run build` clean.
- Do not restart, stop, or talk to the running Dexter (port 8098) — the owner's session verifies live afterwards. Do not commit.
- File content with backslashes or regexes: use the Write/Edit tools, not shell heredocs (this environment collapses `\\` inside heredocs).
- Record what landed in `PROGRESS.md` as **v12**, newest-first above v11.1, with evidence (test counts, the phrases pinned), and set this plan's status line.

## Owner steps (after the build)

1. Put a better top rung in Ollama. The gemma-4-12B GGUF is already on disk in LM Studio's folder (`C:\Users\singh\.lmstudio\models\lmstudio-community\gemma-4-12B-it-GGUF`, 7.1 GB); `ollama create gemma-4-12b -f Modelfile` with `FROM <that .gguf>` imports it without a download, or `ollama pull` a 9–12B tag with tool calling.
2. Settings → Model → *Smart model* = that tag, *Smart model (fallback)* = `qwen2.5:7b`. Or `.env`: `DEXTER_SMART_MODEL=gemma-4-12b`, `DEXTER_SMART_MODEL_FALLBACK=qwen2.5:7b`.
3. Restart Dexter. With the card free, an escalated turn runs on the 12B; start BuzzEdit and the next escalation lands on the 7B with the reason under the reply.

---

# Dexter Roadmap v11 — Hands like mine (desktop control)

**Status date:** 2026-09-11 · **Progress log:** `PROGRESS.md` · **Built by:** an Opus implementation agent from this plan
**Status:** P1-P3 built 2026-09-11 (see `PROGRESS.md` v11); v11.1 the same day fixed why none of it was reachable by voice and added power + shell tools.
**Owner's ask (2026-09-11):** "dexter opens chrome but not what i say in it, like opening a different website or closing chrome or a url, similarly other tasks. make it more smart so that it can do stuff like me, the minute stuff as well."

## Why it fails today (measured in the code, not guessed)

| You say | What happens | Why |
|---|---|---|
| "open chrome" | your everyday Chrome opens | `_FAST_RULES` → `open_app` (registry miss) → `launch_app` → `app_index` |
| "open youtube" | "I couldn't find 'youtube' on this PC" | same rule: only URL-ish text (`youtube.com`) is treated as a site |
| "open youtube.com" | a **second** Chrome window opens (Dexter's own Playwright profile under `data/browser_profile`) | `browser_open` drives Dexter's browser, never yours |
| "close chrome" | closes one Chrome window by title, or nothing | `close_window` matches window titles; no notion of an app's process |
| "close this tab", "go back", "search for lo-fi on youtube" | act in Dexter's window, or "no page open" | the `browser_*` tools do not know the Chrome you are looking at |
| "scroll down", "press ctrl s", "type hello", "switch to code", "minimize this" | nothing | no keyboard / window / scroll tools exist |
| "open youtube and search for lo-fi beats" | usually right (agent loop), sometimes "couldn't find 'youtube and search...'" | the open-rule swallows the whole sentence when it is ≤ 4 words |

Two browsers is the root cause. Everything else is a missing hand.

## Decisions (owner, 2026-09-11)

- **D1 One Chrome, yours.** Dexter acts in the Chrome you use. Chrome 136+ refuses remote debugging on the default profile, so control is by three honest means: (a) `chrome.exe <url>` hands a URL to the running instance as a new tab; (b) keystrokes and window commands for tab/window actions; (c) Windows UI Automation (UIA) to *read* the active tab's URL, title, tab list and page text. Dexter's Playwright window stays only as an explicit fallback (`DEXTER_BROWSER_MODE=playwright`).
- **D2 The minute stuff is a toolset,** not a model trick: press keys, type text, scroll, click by visible text, focus/switch/minimize/maximize/snap/close windows and apps, read what is on screen. Deterministic, testable with fakes, no real keystrokes in tests.
- **D3 Context is the default.** "it / that / this / here / the tab" resolve to the active window; "in chrome, …" focuses first; a compound sentence is executed step by step by the agent loop and reported once.
- **D4 Nothing destructive without a word.** Closing an app with unsaved work is the app's own prompt; Dexter never force-kills a window it was asked to "close" (only "kill" does that, with confirmation). No key is ever sent to a window that is not the one named or in focus.

## Phase 1 — Your Chrome (P1)

| # | Item | Acceptance |
|---|---|---|
| P1.1 | `chrome_control.py`: find Chrome windows (pygetwindow + psutil, by process), the active one, open a URL/search in the running instance (`chrome.exe <url>`; launch if not running), close a tab (Ctrl+W), close Chrome (WM_CLOSE to every Chrome window, then wait; never TerminateProcess unless `kill`), new tab, next/previous tab, reopen closed tab, back/forward/reload, find in page, zoom, fullscreen | "open youtube" → a **new tab in your Chrome** (launches it if closed); "close chrome" → every Chrome window closes cleanly; "close this tab" / "next tab" / "go back" / "reload" act in the tab you see |
| P1.2 | UIA reader (pywinauto, `backend="uia"`): active tab URL (omnibox value), title, tab strip names, main document text (budgeted, ≤ 6 000 chars) | "what am I looking at" / "read this page" / "what tabs do I have open" answer from **your** Chrome |
| P1.3 | Site names are sites: `open youtube`, `open gmail`, `go to reddit`, `search youtube for X`, `google X` resolve through `_SITE_ALIASES` + a "known sites" table (extend `browser.normalize_target`) **before** the app launcher; `open chrome` still opens Chrome; `open X` unknown to both → one clear question, not "couldn't find" | 12 phrases in `test_router.py` pinned |
| P1.4 | The existing `browser_*` tools become **mode-aware**: default `own` routes open/close/tabs/back/read to `chrome_control`, `playwright` keeps today's behaviour; `browser_click`/`browser_type` in `own` mode use UIA (click by name) with keystroke fallback | `test_browser_tools.py` covers both modes with fakes |

## Phase 2 — The minute stuff (P2)

| # | Item | Acceptance |
|---|---|---|
| P2.1 | `desktop_control.py` + `tools_desktop.py`: `press_keys("ctrl+s")`, `type_text("…", submit=False)`, `scroll(direction, amount)`, `click_text("Save")` (UIA name match in the active window; pyautogui-free), `read_screen_text()` (UIA text of the active window, budgeted), `switch_to(app)`, `minimize/maximize/restore/snap_left/snap_right(window)`, `close_app(name)` (all windows of the process, graceful), `list_windows` grouped by app | "press control s", "type hello world", "select all and copy", "scroll down", "click save", "switch to code", "minimize this", "snap chrome to the left", "close notepad" |
| P2.2 | App recipes: a small table of per-app hotkeys (Chrome, Edge, VS Code, Explorer, Spotify, Notepad, Discord, Premiere) so "new tab", "new file", "find", "toggle sidebar", "next track" pick the right keys for the **active** app | recipe table tested; unknown app → generic keys or a one-line "in X I don't know how" |
| P2.3 | Safety: keys go only to the window named or in focus (focus is verified after `switch_to`, and refused if a *different* window took focus); `type_text` refuses secrets-looking input into a browser omnibox; `kill` needs confirmation; every action returns what it did and to which window | tests for every refusal |

## Phase 3 — Understanding (P3)

| # | Item | Acceptance |
|---|---|---|
| P3.1 | Reference resolution: "close it/this/that", "scroll here", "this tab", "the browser" resolve from the active window and the last thing Dexter opened (a `focus` memory in the router, per session, 5 min) | "open notepad" … "close it" closes Notepad; "open youtube" … "close that tab" closes it |
| P3.2 | "in <app>, …" and "<app>: …" prefixes focus first, then run the rest; "and then / and / then" chains run through the agent loop with the desktop tools in the candidate set; one combined reply | "in chrome, go to gmail and open the first unread" (best effort on the last step) |
| P3.3 | The fast-path open-rule stops swallowing sentences: anything with "and", "then", a URL, a site name, or > 3 words goes to the tiers below; `_NOT_AN_APP` extended | `test_router.py`: 20 new phrases, zero regressions |
| P3.4 | `test_agent_eval.py`: 15 desktop/browser cases so the model's choice is measured, not assumed | eval file runs (skipped without a model) |

## Phase 4 — Teach me (P4) — *not in this build*

Playbooks ("when I say start streaming: open OBS, …") on top of the macro recorder, and a Chrome extension with native messaging for full DOM access in your own Chrome. Planned; not built now.

## Not doing

- Remote-debugging the daily Chrome profile (Chrome forbids it since 136; a copy of the profile is not "your Chrome").
- Screen-vision clicking (OCR / vision model). UIA names cover what is clickable; a picture is Phase 4 material.
- Force-killing anything on "close".

---

# Dexter Roadmap v9 — Sentinel rules, Dexter runs the apps

**Status date:** 2026-09-10 · **Progress log:** `PROGRESS.md` · **Cross-repository contract:** [`../GUARDIAN_PLAN.md`](../GUARDIAN_PLAN.md)
**Owner's ask (2026-09-10):** "Dexter controls every app in
`B:\youtubeProjects\Buzzcaf_Media`; Sentinel is the supreme guardian of VRAM and
RAM so the PC never hangs, and uses SmartCleaner to free space; Dexter is my
coworker/coach and asks Sentinel for permission before loading models."

What was wrong: five places in Dexter loaded or unloaded a model, only one of
them asked Sentinel, and none of them ever called `Release`. Dexter could open
Chrome and start the Studio but could not tell you whether VrDeep was rendering,
and `launch_app` would happily fire up a 10 GB pipeline beside a 12 GB model
server. On a 16 GB card shared with VrDeep, BuzzEdit's ComfyUI and buzzcode's
27B, that is how the machine locks up.

| # | Item | Design | Acceptance |
|---|---|---|---|
| D1 | Sentinel client + lifecycle | `sentinel_client.py` speaks protocol v2: `hello()`/`features()`, `register(… ram_budget_mib, priority, label)` keeping the token, `reserve(mib, ram_mib, for_pid, ttl)`, `query`, `ram_status`, `disk_status`, `request_cleanup`, `app_status`, `release()`, `unregister()`. New fields go on the wire **only** when `Hello` says they are understood, so the v1 service installed today keeps working byte for byte. `router.sentinel_start()` registers and subscribes; `sentinel_stop()` releases, unregisters and stops the thread in the lifespan shutdown | **N1** `Hello` failing degrades to v1 (`features=[]`, no `ram_mib` on the wire, `Query` skipped and "would be granted"), verified live against the installed service in well under a second |
| D2 | **The model steward** | `model_steward.py` is the *only* module allowed to call `hybrid_llm.warm_model`/`release_model`/`set_keep_alive`, enforced by a grep test. `ensure()` estimates VRAM **and** RAM, `Reserve`s, loads, then `Release`s once the load is *observed* in `/api/ps` — or immediately on failure. `shed()` runs the ladder for `VramTight`/`RamTight`/`Preempt`: smart model → (red) fast model → foreign Ollama models → LM Studio → an engine Dexter started. Every old load path is rewired: startup warm, `_keep_fast_model_warm`, Tier 1.5 escalation, `code_agent` | **N2** no module outside the steward touches model residency; a refusal loads **nothing**, names the holder and the allowed move, and Dexter keeps answering on whatever is resident; every booking is released on success *and* on failure |
| D3 | The apps registry | `apps_registry.py` + `data/apps.default.json` (all 17 projects with real launch scripts, cwd, probes, stop strategies, class, VRAM/RAM, `exclusive_with`, logs; owner overrides in `data/apps.json`, never written by Dexter). `procs.py` (psutil, else `Get-CimInstance`/`tasklist`/`netstat`): find by name **and path**, port owner, kill tree, RSS, uptime. `tools_apps.py`: `app_status`, `apps_overview`, `open_app`, `stop_app`, `restart_app`, `app_logs`, `request_cleanup`, plus the thin readers `vrdeep_queue`, `buzzedit_status`, `finance_status`, `coffee_brief`, `mastgaane_report`. All Tier-0 rules. The Studio keeps `buzzcaf_client` | **N3** "open buzzedit", "start vrdeep", "is buzzfin running", "stop caliber", "what's running", "clean up space" resolve without a model; "open chrome" and "close notepad" still route as before |
| D4 | Resource-aware launch | `open_app` for a batch app or anything with `vram_mib>0`: `exclusive_with` check, `sentinel.query(vram, ram)`, RAM zone (Yellow warns, Red refuses), then launch and poll the probe for 30 s and report honestly. Refusals name the holders and the moves; a record's `warn` (MidnightBuzz binds `0.0.0.0`, BuzzAnimate builds on launch) is passed on | **N4** a refused `Query` launches nothing and names who has the memory; an exclusive app that is running blocks the launch by name |
| D5 | The UI | `GET /api/dexter/resources`, `GET /api/dexter/apps`, `POST /api/dexter/apps/{id}/start\|stop`, `POST /api/dexter/nudge` (loopback + token, for Mission Reminder). `ResourcePanel.tsx` (three gauges, holders, frozen, pause/resume/clean), `AppsPanel.tsx` (the v8 `AppLauncher` grown up: running/stopped, start/stop with confirmation, the quick OS launcher kept). SSE `resources`/`gpu_busy`/`disk_tight`/`evicted` → banners and the pop-up; `RuntimeBanner` gains `sentinel_down` and `tray_down` | **N5** an absent or old Sentinel reads as "the guardian is old", never as an empty gauge; stopping a busy batch app from the panel asks first and only the second click confirms |
| D6 | The coworker layer | One machine line in `companion.context_block()` and in the morning brief (who has the GPU, RAM %, disk free, last night's cleanup). Initiative candidates `gpu_free_queue_waiting`, `disk_low`, `frozen_long`, `evicted_notice`, ordered *after* the work ones. Mission Reminder's runtime mirror is read (never written) into "Right now", and **Dexter's own nudges are suppressed while one of its focus blocks is running** | **N6** the machine line is in every prompt and degrades to nothing when the guardian is down; a running Mission Reminder session silences check-ins, tails, nudges and proposals; a stale mirror does not |
| D7 | Settings | `settings_store.SETTINGS` gains a **Machine** group with `module`/`attr` so it hot-reloads: VRAM/RAM budgets, `DEXTER_MANAGE_LMSTUDIO`, `DEXTER_AUTO_CLEAN`, engine VRAM/RAM, `DEXTER_APPS_FILE`, `MISSION_REMINDER_RUNTIME`; `.env.example` documents all of them | **N7** changing the VRAM budget in Settings takes effect on the next reservation without a restart |
| D8 | Verification | `FakeSentinel` extended with the v2 commands while every v1 test stays; `test_model_steward.py` (incl. the monopoly grep), `test_apps_registry.py`, `test_tools_apps.py`, router refusal/lifecycle tests, vitest for both panels; `test_dexter_suite.py` scenario E, skipped when no pipe answers | **N8** `python -m pytest backend -q`, `npx tsc --noEmit`, `npx vitest run` and `npm run build` all green; nothing in the suite can reach a real process, a real model or the real Sentinel |
| D9 | Docs | `ECOSYSTEM.md` rewritten around the full port map, the Sentinel contract and the steward rule, and copied byte-identical to `BuzzcafAI/`; this table; `PROGRESS.md` evidence; a README section | **N9** both `ECOSYSTEM.md` copies hash the same |
| **P1** | **Ports: nothing hardcoded** | `backend/buzzcaf_ports.py` (stdlib only) is the canonical Python implementation of GUARDIAN_PLAN.md section 11 - `pick_port` / `publish` / `withdraw` / `discover` / `ledger_path` over a shared ledger at `%LOCALAPPDATA%\Buzzcaf\ports.json`, and it is copied byte-identical into every Python app. Dexter picks its own port from `DEXTER_PORT` (8098) upwards in `desktop_app.py` / `main.py`, answers `/health` with `{app, status, port, pid, version}`, publishes once that route answers and withdraws on exit; CORS, the window, `/status` and `/resources` all name the chosen port. `src/api.ts` derives the API base from `window.location.origin` (Vite dev proxies from `VITE_DEXTER_PORT`), `apps.default.json` carries `preferred_port` + `extra_preferred`, the registry probes `discover()` with the identity rule, `buzzcaf_client` reads the ledger before `studio.runtime.json`, and `LLAMACPP_URL` falls back to the ledger's `buzzcode` entry. `start_dexter.bat` no longer fixes a port or kills whoever holds one | **P1** with 8098 deliberately occupied Dexter starts on 8099, `/health` identifies it, the ledger has the entry, `python test_dexter_suite.py` passes against it, and the entry is gone after a clean exit |

**Not doing.** Dexter does not kill anything Sentinel has not classified, does
not delete files (SmartCleaner runs from Sentinel's tray, in the user session,
where it can actually see `%TEMP%`), does not override a refusal, and does not
write `C:\ProgramData\Sentinel`. Mission Reminder's mission model and Dexter's
stay two separate things — the bridge is read-only so neither can corrupt the
other.

---

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
