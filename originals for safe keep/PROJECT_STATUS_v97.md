# CURRENT STATUS — 2026-10-18: v97 and nine-hour diagnostic test prepared

**Current approved controller source:** `mainstage_jamzone_fade_controller_FLOATING_BAR_v97.py` (static checks passed; not yet live-tested).

**Retired mappings:** CH8 CC0 and CH1 CC57 were explicitly approved for removal. They are removed from v97 controller logic and omitted from all test scenarios. The original v96 file remains unchanged.

**Diagnostic runner:** `floating_bar_v97_night_test_v5.py`, nine hours after startup readiness and a 20-second manual mouse baseline, with 60–150 second random gaps between actions. It checks UI liveness/recovery, controller connectivity and receiver health, battery state, MainStage MIDI state feedback, volume ramps, JamZone playback/loop restoration, mouse hit-area and event-delivery behavior, timer phases, CPU stress, process resources, and crash reports.

**Safety:** preflight validates the exact v97 hash, app availability, macOS Accessibility permission, and required MIDI ports before stopping any controller process. It operates on a separate diagnostic copy, leaves v96/v97 source files untouched during the run, restores volume to 127, and restarts v97 afterward. HID P/L and mouse clicks are sent only after foreground verification. If v96 remains running at completion, it will not launch v97 over it.

**Static validation:** v97 source, runner, and generated diagnostic copy compile successfully; v97 hash is pinned in the runner. No live Mac/CoreMIDI/MainStage/JamZone/mouse run has yet occurred. Do not claim runtime stability until the nine-hour report is reviewed.

**Start command (both files in Downloads):**

`python3 "$HOME/Downloads/floating_bar_v97_night_test_v5.py"`

During the first 20 seconds, manually click only the timer text (single, double, and long presses). Keep AIRSTEP and SMC powered on/connected, JamZone looping, MainStage and SBP open, and Chrome/Spotify closed. Upload the Desktop ZIP after the test.

---

# MainStage / JamZone Floating Bar — Current Status

**Checkpoint:** 2026-10-17, after v94 and resource-optimization review  
**Latest generated script:** `mainstage_jamzone_fade_controller_FLOATING_BAR_v94.py`  
**Next version:** v95 is NOT created.  
**Current blocker:** User must choose the approved P23 disconnect-detection strategy before implementation.

## Confirmed user-tested behavior

- Guitar Mute red-light behavior in v89 is confirmed by the user to work correctly. Preserve it exactly.
- Existing IAC Bus 1 control / IAC Bus 2 status architecture must be preserved.
- Existing 3-second alternation between active MIDI-command display and disconnect display was requested and carried into the recent versions; live verification of all recent versions is not documented.
- Earlier project checkpoints include user-confirmed working versions (v45, v52, v76/v77 in historical notes). These are historical checkpoints, not proof that v89–v94 are live-stable.

## Recent version history

### v89 — created and statically checked; user log later exposed failures
- Guitar Mute Note 18 uses press-edge toggle logic; release does not toggle. User later confirmed the red-light behavior works correctly.
- Guitar effect tiles DLY–IR alone use the Guitar Mute red override; other tiles do not.
- MIDI INPUT command/value text was changed to multiline, with the `v...` value on its own lower line.
- P23 Bluetooth aliases and parsing were expanded.
- Static syntax/AST/targeted checks passed. No live test was performed by the assistant.
- User supplied a live log showing a serious error: `ControllerBatteryScheduler` crashed with `AttributeError: 'Controller' object has no attribute '_update_controller_batteries'`. The log also showed `P23=None%`, repeated `MIDI RECEIVED` CC11 messages, repeated IAC Bus 2 raw/status output, and SMC disconnect/reconnect messages.

### v90 — resource/battery/display revision; live results not confirmed
- Intended to reduce repeated diagnostics; keep MIDI processing intact.
- Intended to improve P23 parsing and startup/periodic battery refresh.
- Intended to alternate active-command and disconnect displays every 3 seconds without changing their visual designs.
- Static checks were reported as passed; no live Mac test was performed by the assistant.

### v91 — initial optimization; live results not confirmed
- UI refresh reduced from 20 to 10 times/second; avoid repainting unchanged tiles.
- Reduced duplicate Bluetooth enumeration checks.
- P23 JSON fallback limited to once every five minutes; retained last battery percentage while present.
- Raw MIDI diagnostics disabled by default; clock and reset overlay work reduced.
- Static checks passed; CPU/RAM improvement not measured on the Mac.

### v92 — optimization/reset revision; P23 problem reported
- UI tick set to once/second; clock scheduled every 30 seconds.
- Startup battery refresh scheduled after one second; system battery refresh also scheduled after one second, then every 60 seconds.
- Controller battery scheduler still present.
- Assistant stated RESET overlay was a runtime-error banner, not an actual reset, and removed it while retaining error logging/recovery. This was a code change claimed in the delivered version; user has not explicitly confirmed RESET removal is harmless.
- User reported P23 battery was not refreshed at startup or every 15 minutes. Thus P23 behavior was not working as required.

### v93 — P23 and responsiveness revision; issue persisted
- Added/retained startup and 15-minute battery scheduling; command-triggered JSON refresh limited to once per 30 seconds.
- Discrete MIDI state changes request a coalesced UI refresh after 30 ms; regular tick remains once/second; continuous CC11 should not trigger UI redraws.
- Clock set to 30-second updates.
- Static syntax/AST/parser/structural checks passed; no live device test.
- User reported P23 battery still did not update at all.

### v94 — further P23 parser revision; live result not yet provided
- Expanded JSON battery-field parsing, including possible device names stored as dictionary keys.
- Forced JSON fallback during the initial battery scan.
- Intended to retain the 15-minute battery schedule and 30-second cap on command-triggered refreshes.
- Four synthetic battery-parser tests and syntax checks were reported as passed; actual macOS Bluetooth data was not tested here.
- The user has not yet confirmed whether v94 fixes P23.

## Resource analysis performed; no optimization version created after v94

Source review identified potential unnecessary work:

1. Battery/presence checks can run more often than intended due to command-triggered refreshes and multiple `system_profiler` formats (text and JSON).
2. CoreMIDI port enumeration appears in more than one monitoring loop, potentially duplicating `mido.get_input_names()` checks; output names are also enumerated.
3. A discrete MIDI event calls `request_ui_refresh()` after 30 ms and that path runs a broad `tick()`, potentially doing more UI work than the changed component needs.
4. UI timing includes `tick()` once/second, clock updates, reminder/session flash callbacks at 500 ms, mode flash at 150 ms initially and then 500 ms, controller battery flash at 500 ms, and other event timers. Some can be event-driven, but behavior must be preserved unless separately approved.
5. `UIWatchdog` currently checks every 0.5 seconds; user approved changing it to every 5 seconds.
6. Mac battery `pmset -g batt` currently runs on a 60-second schedule; user approved changing it to every 15 minutes.
7. The source-review assistant noted two consecutive `listener.start()` calls in `run()` as a possible issue. This must be verified against the exact code path before changing it; do not treat it as a confirmed runtime error without testing.

No actual CPU or RAM benchmark comparing versions has been performed. Potential savings are inferred from source structure, not measured.

## Approved changes for the next version (v95, not yet implemented)

User explicitly authorized the following, with no changes to existing functional behavior unless described below:

- Optimize redundant Bluetooth/system battery polling and command-triggered scans.
- Reduce duplicate CoreMIDI enumeration while preserving disconnect/reconnect recovery.
- Reduce unnecessary UI repaint/tick work while keeping MIDI state changes responsive.
- Make blink scheduling event-driven where possible, preserving current defined visual behavior and timing.
- Change `UIWatchdog` interval from 0.5 seconds to 5 seconds.
- Change Mac system-battery polling from 60 seconds to 15 minutes.
- Keep controller battery scans at startup and every 15 minutes.
- Restore/fix P23 battery percentage display and ensure a disconnected P23 is shown in MIDI INPUT, as in earlier working versions.
- Keep console output to important events/errors; suppress repetitive raw MIDI output during normal use.
- Preserve Guitar Mute red lights, IAC Bus 1/Bus 2 architecture, existing mappings, existing button behavior, current display designs, and 3-second active-command/disconnect display alternation.
- Preserve clock behavior as agreed: user asked for a 30-second update interval for extra safety. Earlier assistant recommended once per minute, but the user's latest explicit instruction is every 30 seconds and supersedes that recommendation.

## One decision still required before v95

The user must choose P23 disconnect detection strategy:

1. **Maximum scan reduction:** full Bluetooth/battery scan at startup and every 15 minutes. P23 disconnect indication may be delayed until the next scan.
2. **Faster disconnect detection:** a separate, more frequent presence check, while expensive battery scans remain at startup and every 15 minutes. This uses some additional resources; exact macOS behavior needs verification.

Assistant asked the user to choose option 1 or 2. No choice has been received. Do not create v95 until this is resolved.

## Validation rules and limitations

- v89–v94 static/synthetic checks do not establish live macOS, Bluetooth, CoreMIDI, MainStage, JamZone, or GUI stability.
- The P23 percentage fix is not confirmed; the user has repeatedly reported failure in prior versions.
- No real CPU/RAM measurement has been made.
- Preserve all previous version files unchanged. Use a new filename/version for any authorized implementation.
- Before delivery, check Python syntax, AST parsing, relevant structural tests, exact title/version match, and that the original protected behavior remains present. Clearly distinguish static tests from live tests.

## Exact next step

Ask the user to choose **1 (15-minute scan only)** or **2 (separate faster P23 presence detection plus 15-minute battery scans)**. Then implement approved v95, run available static tests, update the continuity/status documents, and provide the exact Terminal command. Do not ask again for approval of the already-approved optimizations.


## New checkpoint — 2026-10-18 overnight diagnostic
- User approved a 9-hour diagnostic run for v96, including restart, MIDI scenarios, volume/mute tests, JamZone stop/restore, and click-event instrumentation.
- Runner: `floating_bar_v96_night_test.py`; command: `python3 "$HOME/Downloads/floating_bar_v96_night_test.py"`.
- It creates a separate instrumented v96 diagnostic copy, preserves original v96, monitors UI heartbeat/resources/crashes/mouse events, tests timer-active phases and controlled CPU stress, and restores original v96 after the test.
- Static compilation of runner and generated diagnostic copy passed. No live Mac test has yet been performed. Full details are in the newest checkpoint at the top of `PROJECT_CONTINUITY_UPDATED.md`.
