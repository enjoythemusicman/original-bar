# Floating Bar v97 — findings and next diagnostic run

Date: 2026-10-18

## Confirmed from the supplied artifacts

1. The nine-hour runner reached its scheduled end, but this was **not a successful functional test**: both timer phases failed verification, repeated UI-heartbeat stalls caused diagnostic-process restarts, and fade/playback-stop scenarios were incomplete.
2. The supplied 07:24 Python crash report records `SIGABRT` on the main thread while executing Tkinter/Tcl finalization callbacks. The same report shows a separate thread inside `python-rtmidi`/CoreMIDI. This is evidence of a shutdown/finalization failure involving Tk/Tcl; it does **not by itself prove** that the MIDI thread caused the crash.
3. The v16 runner's timer and mouse checks depend on synthetic clicks being observed as Tk events. The test record reports 1,091 click attempts without corresponding Tk events. Those attempts cannot be treated as proof that the UI accepted clicks, and repeating the same click injection would not add reliable evidence.
4. The test also lost stable power conditions when macOS slept at low battery. Any long soak test must run with the Mac connected to power and sleep prevention verified before the test starts.
5. The original v97 must remain unchanged. No v97 code, MIDI mapping, routing, or UI behavior has been changed in this analysis.

## What remains unproven

- Whether the crash is caused by Tkinter calls from a non-UI thread, callbacks surviving widget/root teardown, an interaction between those paths, or another runtime fault.
- Whether the watchdog's stale heartbeat reflects a genuine frozen UI, a callback exception, or a process that is in the middle of crashing.
- Whether any missing CoyoteMIDI samples indicate a real process outage or a monitoring gap.

## Next diagnostic iteration — separate copy only

1. Do not repeat synthetic mouse/timer clicks until event delivery is validated independently; mark these as invalid/inconclusive in the test report.
2. Build a new diagnostic copy from the exact v97 source hash, leaving the original file untouched.
3. Add low-overhead, thread-safe diagnostics for: callback entry/exit and exception type; whether Tk calls are attempted from non-main threads; orderly shutdown start/end; and MIDI receiver thread stop/join status. Avoid logging every raw MIDI message.
4. First run static checks on the generated copy (source-hash gate, syntax compile, AST/structural checks, and verification that only the diagnostic copy contains instrumentation).
5. Then use a short, non-destructive observation run before any long soak. Do not induce a UI freeze or run playback/fade automation until the diagnostic startup and shutdown paths pass.
6. For the next live test, require AC power, verify sleep prevention, and record that these checks passed before timing begins.

## Verification boundary

The supplied artifacts can be inspected here, but this environment cannot run MainStage, JamZone, CoreMIDI, Tkinter on the user's Mac, or validate live audio/MIDI behavior. A static pass here must not be described as a live test pass.
