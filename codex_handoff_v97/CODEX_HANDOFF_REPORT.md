# CODEX PROJECT HANDOFF — MUSIC MAN FLOATING BAR

**Checkpoint:** 2026-10-18  
**Production controller baseline:** `mainstage_jamzone_fade_controller_FLOATING_BAR_v97.py`  
**Priority:** protect live-show reliability; diagnose the test harness and runtime evidence before making production changes.

## 1. Mission and current architecture

This is a macOS live-performance control project integrating a Python/Tkinter Floating Bar with MainStage, JamZone, MIDI controllers and MIDI state feedback. The bar displays controller/state information and provides timer/status UI. It must remain reliable during live music performance and must not disrupt audio, MainStage, JamZone, controller mappings, or existing MIDI behavior.

Known environment from project records:
- MacBook Pro M1 running macOS Sonoma 14.6.1 was the documented development environment; latest diagnostic collection records its own macOS/environment details.
- Python 3.10.7.
- MainStage, JamZone, SBP, AIRSTEP Bluetooth, SMC/SMC-Mixer Bluetooth controller.
- MIDI uses `mido` / `python-rtmidi` and macOS CoreMIDI.
- IAC Driver Bus 1 is for existing control commands; IAC Driver Bus 2 is for status mirrors into the Floating Bar.
- Snoize MIDI Monitor 1.5.4 has been used to observe MIDI messages.

## 2. Exact production baseline — do not alter

File: `controller/mainstage_jamzone_fade_controller_FLOATING_BAR_v97.py`

SHA-256: `f1c20f91ae5442f6981cbee750979cc958b1f50944d68801b7e8517e6e9d5a3e`

This hash is the identity gate for the current v97 baseline. Recalculate it before and after any work. Keep v97 byte-for-byte unchanged unless the user explicitly approves a production-controller revision. Never overwrite it. Any diagnostic or experimental changes must be separately named and stored in a dedicated work folder.

Two obsolete cancellation mappings were explicitly approved for removal and are absent from v97:
- CH8 CC0
- CH1 CC57

Do not reintroduce these mappings or include them in tests.

## 3. Safety and change-control rules

1. Stability of the live performance system is the top priority.
2. No changes to production controller code, MIDI mappings, routing, MainStage configuration, JamZone behavior, launch configuration, or UI behavior without explicit user approval.
3. Do not stop/kill production processes, send MIDI, trigger playback/fades, simulate keyboard/mouse input, change macOS settings, or perform a live test unless the user explicitly approves that exact action and its consequences.
4. Static inspection, hashing, syntax/AST checks, and analysis of supplied artifacts are allowed.
5. Preserve all source versions. Never overwrite prior versions or user files. Write outputs only inside the dedicated Codex project/work directory.
6. Do not infer live success from static checks, a process exit code, a completed timer, or a completed scheduled test. Require observed evidence for each criterion.
7. If evidence conflicts, stop and report the conflict; do not silently reconcile it.
8. Keep normal diagnostics low-volume. Do not log every raw MIDI message by default.
9. Do not expose unrelated personal data from crash reports or system diagnostics in the report.
10. When asking the user to perform an action, provide one precise action at a time and wait for the result.

## 4. Current test status

A nine-hour diagnostic test was run on the user's Mac using a test harness derived from the v97 baseline. The supplied report says the scheduled run lasted 32,402 seconds and v97 source hash was unchanged. **The run did not pass its functional criteria and must be classified INCONCLUSIVE/FAILED, not PASS.**

Important measured results from the report:
- Diagnostic Floating Bar process RSS grew from approximately 149 MB near the start to approximately 4.52 GB near the end. This is a severe stability signal. The exact allocation source has not been identified.
- The stderr capture includes Tkinter callback exceptions such as `_tkinter.TclError: invalid command name ...`, where scheduled callbacks attempted to configure labels that had already been destroyed. This supports a widget/callback lifecycle race in the tested diagnostic path.
- Three Python crash reports show `SIGABRT` / `abort() called`, with stack frames including Tk/Tcl finalization functions (`TkFinalizeThread`, `Tcl_Exit`, `TkMacOSXSignalHandler`). This is consistent with a Tk/Tcl shutdown/finalization issue, but does not prove the exact trigger or establish that every crash came from the same script.
- The 00:57 crash predates the nine-hour test start; the other reports are at 07:24 and 09:23.
- The test recorded 1,091 injected mouse clicks but only 12 Tk click events. Synthetic click attempts are not evidence that the intended widget received the event.
- Both timer phases were not verified as running to the expected end state. The final heartbeat showed `timer_running: false` and `timer_remaining_seconds: 2700` despite a timer setup result that applied a 12,600-second phase-one value.
- Planned Full Fade: 0/10 executed. Planned Loop Fade: 0/10 executed. Only 2/10 quick-fade scenarios and 1/10 effect-CC tests executed. Some Note and CC toggle scenarios were also missing.
- The report's CoyoteMIDI sampler recorded no matches in 3,131 samples, but a later process snapshot showed CoyoteMIDI running. This is unresolved: either timing or process-name matching may explain it. Do not call it a proven outage.
- System power/sleep log collection timed out; power/sleep causality remains unproven. The test environment also experienced low battery/macOS sleep concerns; any future soak test must have AC power and verified sleep prevention.

The separate diagnostic logger also recorded repeated `TK_CALL_FROM_NON_MAIN_THREAD method=after widget=Tk` events while its Tk main loop continued running. This is a thread-safety warning, not proof on its own of the root cause. The diagnostic log contained two PIDs (19241 and 19253) at different startup times; the latter showed regular Tk callbacks. Multiple diagnostic processes existed during collection, so process ownership and duplicate-process prevention should be investigated carefully.

## 5. Root-cause confidence

### Confirmed by supplied artifacts
- The long test did not satisfy functional pass criteria.
- The diagnostic process had major memory growth.
- Tk callbacks attempted to touch destroyed widgets.
- Crash reports include SIGABRT and Tk/Tcl finalization frames.
- Synthetic click delivery measurements were insufficient to verify intended UI interaction.
- Timer and fade/playback scenarios were incomplete.
- CoyoteMIDI sampling and system power-log evidence are not reliable enough to support a definitive conclusion.

### Not yet established
- The exact cause of the 4.52 GB RSS growth.
- Whether the primary UI issue is non-main-thread Tk calls, callbacks surviving widget/root teardown, a diagnostic harness lifecycle bug, or a combination.
- Whether the three crash reports all belong to this Floating Bar script; identify each crash's process command/source path before attributing it.
- Whether each heartbeat stall is a true UI freeze or a callback failure/teardown/crash interval.
- Whether CoyoteMIDI was genuinely absent during all samples.
- Whether sleep/wake caused any particular failure.

## 6. Required next work — diagnostics only first

Do not begin another overnight test. First inspect the exact v16 test harness and the diagnostic copy to understand the source of resource growth, duplicate processes, callback lifecycle and test validity.

### Phase A — source-level analysis and safe test harness correction
1. Verify v97 SHA-256 against the value in this document and preserve a copy unchanged.
2. Inspect `diagnostics/floating_bar_v97_night_test_v16.py` and `diagnostics/mainstage_jamzone_fade_controller_FLOATING_BAR_v97_DIAGNOSTIC_v1.py`. Map every thread, scheduled callback, window/root destruction path, restart path, and memory-reporting path.
3. Explain the v16 harness lineage and confirm whether it is exactly the runner used for the supplied nine-hour report. Do not assume the filename/version mapping without evidence.
4. Find likely sources of unbounded memory growth: retained per-second heartbeat history, event arrays, log accumulation, unbounded queues, repeated process snapshots, image/UI resources, callback chains, and restart generations. Prove the candidate from source and report data before changing it.
5. Correct the CoyoteMIDI process-name detection and add an internal consistency check: if a known target is visible in a process snapshot but the sampler reports zero, mark monitoring INVALID rather than “absent.”
6. In the diagnostic copy/harness only, ensure Tk operations run on the Tk main thread; background workers should send data through a thread-safe queue polled by the main thread. Avoid calling `root.after()` directly from worker threads unless the specific runtime behavior is independently validated. Do not change production v97.
7. Cancel every scheduled callback on teardown; guard callbacks against destroyed widgets/root; make shutdown idempotent; stop and join MIDI receiver/worker threads before destroying the Tk root; avoid calling Tcl/Tk finalization from non-main threads.
8. Add bounded memory retention and a hard RSS ceiling. On threshold breach, stop the diagnostic run cleanly, preserve a compact report and exit; do not let the test consume multiple gigabytes.
9. Make test event accounting explicit: `attempted`, `OS-injected`, `Tk-event-received`, `expected-widget-hit`, and `result-observed` are separate metrics. A test cannot pass based on the injection command returning successfully.
10. Timer pass criteria must require observed running state, countdown progression, and an observed completion/reset outcome. Store a bounded sample of heartbeat history, not an unbounded log.
11. Make power log capture bounded and report success/timeout/truncation explicitly.
12. Prevent duplicate diagnostic controller processes; every restart should prove the previous process exited before starting a new one. Record PID, process command, generation, exit reason and restart cause.
13. Add a clear machine-readable summary with per-test planned/executed/passed/failed/skipped/invalid counts and reasons. Any missing scenario makes the overall run not PASS.
14. Do not use synthetic mouse/timer click tests again until the event-delivery chain is validated in a short test.

### Phase B — staged validation
1. Static checks: syntax compile, AST checks, source hash gate, diff proving production v97 unchanged, and a structural check for Tk calls from non-main threads in the diagnostic copy.
2. Short startup/idle/shutdown observation without automated clicks, MIDI actions or playback automation.
3. Short lifecycle test that closes the diagnostic window and confirms no scheduled callback touches destroyed widgets.
4. Short mouse event-delivery test only after the event delivery instrumentation distinguishes all stages.
5. Timer-only test with independently observed countdown and completion.
6. MIDI/state tests one group at a time; verify each action's actual result, not only the send event.
7. Supervised 30–60 minute soak with AC power, verified sleep prevention, memory ceiling, heartbeat ceiling, clean shutdown and no unplanned restart.
8. Only after all earlier stages pass, consider a longer test. A new long test needs a separate user-approved run; do not start it automatically.

## 7. Floating Bar feature/UI backlog

These are project-level requirements or pending checks. Do not implement them without approval where they change production behavior.

### Known completed and user-verified — do not reopen
- IAC Bus 2 state-button/control setup and status feedback routing into the bar.
- Overall Floating Bar height reduced by 25%.
- Unused empty tile removed.
- Timer negative-time display.
- SMC and AIRSTEP JamZone/SBP window switching behavior.
- Controller battery percentage display and message-box font sizing/placement were marked completed in prior status notes, but P23 battery reporting remains an unresolved runtime issue as described below.
- Guitar Mute red-light behavior was explicitly confirmed working. Preserve it.
- v97 removes obsolete CH8 CC0 and CH1 CC57 cancellation mappings.

### Pending diagnostics / production behavior checks
- Investigate click responsiveness and the observed high CPU/resource concern without modifying production behavior first.
- Confirm UI synchronization on startup.
- Verify timer controls: single click, double-click, long press, reset during run, numeric-only input and correct countdown/negative-time behavior.
- Verify TT1/Fade/Option+Tab behavior, including status-tile flashing during the last three seconds (tile background only, every 0.5 s, unless later explicitly changed).
- Check MIDI false alerts and Tkinter shutdown warnings.
- Verify the displayed version label matches the actual running file; earlier builds have displayed a mismatched version.
- Test full restart shortcut only after confirming it cannot conflict with JamZone keyboard controls; old process must exit before new starts and restart must be logged clearly.
- Confirm the active-command/disconnect status display alternates every 3 seconds without redesigning the display.
- P23 battery percentage and P23 disconnect indication in MIDI INPUT remain unresolved in the current broader project notes. Do not assume the v97 night test fixed them; they need a separately authorized, isolated check.
- Rotating message-box prompts remain a planned UI enhancement: cycle every 10 seconds through `תציג עצמך`, `לחיים`, `אינסטה`, `משקה הבית`, `מאיפה אתם?`; varied colors and visual balance; correct RTL punctuation; suitable Instagram/glass/wine/European flag icons if available. This must not be implemented until the user explicitly approves a new version.
- Future UI enhancement request: reduce the Floating Bar overall height by 25% is already completed; do not do it again.
- Future MIDI plan includes separate vocal mute and unwanted-guitar mute control where applicable; do not alter existing `M` behavior without explicit approval.
- Long-term release packaging/DMG only after controlled-failure and long-duration stability tests pass.

## 8. MIDI architecture invariants

- Keep IAC Bus 1 for existing control commands and IAC Bus 2 for state/status mirrors.
- Do not change established MIDI mappings or direct MainStage-to-SMC behavior.
- SMC faders are understood as CH1–CH8 CC0 by fader number, with the controller's buttons on channel 1. Treat this as a configuration invariant unless the user updates it.
- Never reintroduce the retired CH8 CC0 / CH1 CC57 cancellation mappings.
- Preserve the Guitar Mute red-light behavior.
- Do not change JamZone/SMC routing to pursue status reporting; the intended status path is IAC Bus 2.

## 9. Deliverables expected from Codex

1. A concise evidence-backed root-cause report with file/function/line references and confidence levels.
2. A corrected **separately named diagnostic/test harness**; do not edit production v97.
3. Automated tests that verify the harness's memory bounds, callback cancellation, thread ownership, restart serialization, and honest pass/fail accounting where feasible.
4. Static test output and a hash comparison proving v97 is unchanged.
5. A staged test plan and exact command for the user to run; do not run live Mac-dependent tests remotely or claim live behavior was verified.
6. Updated current-only continuity and status documents. Keep completed tasks closed and separate from pending tasks.
7. If a proposed fix requires changing production behavior, stop and request explicit approval before making that change.

## 10. Available evidence in this package

- Exact production v97 source and hash.
- Instrumented diagnostic copy and its log.
- v16 night-test runner source, which must be inspected rather than rerun unchanged.
- Full diagnostic collection ZIP from 2026-10-18 16:24.
- Earlier nine-hour test diagnostic ZIP and analysis.
- Two standalone `.ips` crash reports plus a third crash report embedded in the full diagnostic ZIP (00:57). Preserve timestamps and do not assume all reports belong to the same executable without verifying process metadata.
- Project work instructions and previous continuity/status snapshots for reference. The top-level current-only files in this package take precedence over older status snapshots if they conflict.

## 11. Chat transcripts

The full raw transcripts of all three project chats were not available as complete export files in the accessible project artifacts. This package therefore does **not** claim to include all three complete chat transcripts. It includes the available continuity/status records and project-specific evidence files. If Codex has direct access to the ChatGPT Project, it may inspect the three chats there, but must distinguish directly inspected transcript content from this package and must not claim to have read a transcript it cannot access.
