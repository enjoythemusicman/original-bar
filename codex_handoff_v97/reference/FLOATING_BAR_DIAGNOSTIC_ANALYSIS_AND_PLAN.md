# Floating Bar — Night Test Analysis and Next-Step Plan
**Evidence reviewed:** `floating_bar_diagnostics_20261018_151648.zip` and the nested nine-hour test archive.
**Scope:** Read-only analysis. No controller source, MIDI mapping, macOS settings, or live process was changed.

## Decision
**Do not repeat the nine-hour test yet.** The current test harness has clear measurement and UI-reliability problems, so repeating it unchanged is unlikely to produce trustworthy results.

## Findings supported by the files

### 1. The v97 source was preserved
The test summary records the same source SHA-256 at start and end:
`f1c20f91ae5442f6981cbee750979cc958b1f50944d68801b7e8517e6e9d5a3e`.
It also records `v97_source_unchanged: true` and `automatic_v97_relaunch: false`.

### 2. The nine-hour run completed, but the functional test did not pass
The scheduled run lasted 32,402 seconds. However, the action summary records:
- 10 planned Full Fade tests, 0 executed.
- 10 planned Loop Fade tests, 0 executed.
- 10 planned mouse probes, only 13 mouse-probe events in the event stream, with the report recording 1,091 injected clicks but only 12 Tk click events.
- Only 1 of 10 planned effect-CC tests executed.
- 2 of 10 planned quick-fade tests executed.
- 12 of 20 planned note-toggle tests and 4 of 50 CC-toggle tests were missing.
- Both timer phases were not verified as successfully running.
The run ending normally is not evidence that the controller passed its functional tests.

### 3. The test process accumulated substantial memory
The resource samples show the diagnostic Floating Bar process growing from about 149 MB RSS near the start to about 4.52 GB near the end. This is a major test-process stability signal. The test source and event-generation code need inspection before another long run. The data alone does not identify the exact allocation responsible.

### 4. Tkinter UI callbacks encountered destroyed widgets
The captured stderr contains Tkinter callback exceptions including:
`_tkinter.TclError: invalid command name ...`
They occur while callbacks attempt to configure labels that no longer exist. This directly supports a lifecycle/cleanup race in the tested UI path: scheduled callbacks are still running after their target widgets have been destroyed. It does not, by itself, establish the cause of every heartbeat stall.

### 5. Three Python crash reports are SIGABRT
The collected reports are dated 00:57, 07:24, and 09:23 and identify `SIGABRT` / `abort() called`. Stack text includes Tk/Tcl finalization functions such as `TkFinalizeThread`, `Tcl_Exit`, and `TkMacOSXSignalHandler`. This is consistent with a Tk/Tcl shutdown/finalization problem, but the crash reports alone do not prove the exact triggering race. The 00:57 crash predates the nine-hour test start.

### 6. CoyoteMIDI presence needs timestamped reconciliation
The report summary field is misspelled `chayotemidi`, but the sampling code searches process command text for `coyotemidi` and stores matching rows separately. All 3,131 samples recorded no match; a later process snapshot at 15:16 shows CoyoteMIDI running. This could mean the process was absent during the test and running afterward, or that the sampling method failed to match its process representation. The archive does not distinguish those possibilities. Treat the absence count as an unresolved finding, not proof of a process crash.

### 7. The system-log collection timed out
The diagnostic archive's `system_log_power_window.txt` collection ended with `TIMEOUT`. The archive therefore does not provide a complete system power/sleep/wake event history. The `last` output shows several reboot/shutdown entries earlier that morning, but it cannot explain each entry or establish that macOS restarted during the nine-hour test. Do not infer a definitive power cause from this archive.

### 8. The final heartbeat is not proof of timer success
At the end, the UI heartbeat shows `timer_running: false` and `timer_remaining_seconds: 2700`, while the timer setup result records that a 12,600-second phase-one value was applied. The timer's final state therefore does not verify that the requested timer actually ran for the expected duration.

## Root-cause assessment
**Confirmed:** the test run was incomplete; UI callbacks tried to update destroyed Tk widgets; the diagnostic process grew to about 4.52 GB RSS; CoyoteMIDI presence during the test remains unresolved; system power-log capture timed out.

**Not confirmed:** the exact cause of every UI heartbeat stall, the exact trigger for each SIGABRT, whether any individual sleep/wake event caused a test failure, and whether all injected mouse clicks reached the intended UI element.

## Action plan

### Phase A — prepare a corrected, bounded test harness (before any more overnight testing)
1. Preserve v97 and its recorded hash. Do not edit or replace it.
2. Correct the CoyoteMIDI process-name lookup and add a self-check that fails if the target process is present but the monitor reports zero present samples.
3. Fix Tk callback lifecycle handling in the **test harness copy only**: cancel scheduled callbacks on window teardown; guard callbacks against destroyed widgets; ensure Tk operations occur only on the Tk main thread.
4. Reduce the test to a short, controlled run first. Add a hard RSS ceiling and automatic stop/report if memory exceeds the chosen threshold; do not let a test process grow to multiple gigabytes.
5. Make mouse-probe results distinguish injection attempted, event received, and target hit. Do not count a run as passing merely because the external click command returned.
6. Make timer verification require observed `timer_running=true`, expected countdown progression, and a recorded completion/timeout state.
7. Make system-log collection bounded and report explicit success/timeout/truncation. Keep it targeted to relevant timestamps.
8. Add explicit pass/fail criteria and stop the run when the UI heartbeat or memory threshold is breached.

### Phase B — verification sequence
1. Static checks on the new harness and a comparison proving the v97 source is byte-for-byte unchanged.
2. A short run with no automated clicks, verifying startup/cleanup and callback cancellation.
3. A short mouse-probe run, verifying injected clicks correspond to Tk events and the expected widget.
4. Timer test in isolation.
5. MIDI/state tests in isolation, verifying each planned action has a matching observed result.
6. A 30–60 minute supervised soak with memory/heartbeat limits.
7. Only after all earlier stages pass, schedule a longer run. Do not use a nine-hour run as the first debugging test.

## Approval boundary
The user authorized diagnostic collection and analysis. No approval has been given here to alter the stable controller, MIDI behavior, or production launch configuration. The next implementation should be a separately named **test-harness-only** revision; v97 must remain untouched. If a change would affect production behavior, stop and request explicit approval first.

## Next action
Create and statically validate the corrected test-harness-only revision from the exact v16 test source contained in the archive, then provide that file and a short-run command. Do not launch it on the user's Mac; the user must run the controlled test and return its report.
