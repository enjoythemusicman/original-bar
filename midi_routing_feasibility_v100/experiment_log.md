# FloBar v99 MIDI routing feasibility experiment

## Scope

This experiment is limited to MIDI-routing feasibility. The original v99 file is preserved unchanged. The v100-named test copy is also unchanged so far; it is a byte-for-byte copy of v99, not a new app implementation.

## Incident: Python/RtMidi native abort

- Crash report time: 2026-10-22 19:59:53 +0100.
- Process: Python 3.10.7 ARM64, launched by Codex.
- Failure: `EXC_CRASH (SIGABRT)` / `abort()` in `_rtmidi.cpython-310-darwin.so` while `MidiInCore::getCoreMidiClientSingleton` and `MidiInCore::initialize` constructed a CoreMIDI input client.
- The stack shows a native C++ abort, not a Python exception; normal Python exception handling cannot recover from this process termination.
- The report is from the MIDI probe, not the FloBar v99 process. The v99 file was not edited or launched for this probe.
- Root cause is not established by the report. It does not distinguish an RtMidi defect, CoreMIDI state, or an execution-context issue.
- A later approved isolated virtual-endpoint loopback probe succeeded and received one synthetic CC event exactly once. That proves only basic virtual endpoint delivery; it does not yet prove message replacement, UI-failure fallback, or recovery when the router is unavailable.
- Do not repeat CoreMIDI/RtMidi initialization inside the restricted execution context. Any further live CoreMIDI probe must be isolated from the physical AIRSTEP/SMC/P23 ports and run in the approved context. Avoid claiming fail-open behavior until it is demonstrated.

## Isolated routing probe results

The test-only `routing_poc.py` created unique CoreMIDI virtual endpoints and sent synthetic channel 1 CC119 messages. It did not open AIRSTEP, SMC, P23, either IAC bus, MainStage, or JamZone.

- Original passthrough: one CC119 message observed, unchanged; no duplicate.
- Replacement: CC119 was transformed to CC118; exactly one output observed.
- Simulated UI process crash: the separate probe router detected a stale heartbeat and passed the original CC119; exactly one output observed.
- Simulated UI process hang (paused heartbeat process): the router detected the stale heartbeat and passed the original CC119; exactly one output observed.
- Router unavailable: closing the router's virtual input endpoint resulted in no output. The single-path router did not fail open.
- Mean callback-to-observer latency: 0.044 ms over four samples on the local virtual route.
- Idle CPU: 0.002% of one logical CPU over a two-second sample with the probe endpoints open.
- Probe process peak RSS: 25,886,720 bytes (`ru_maxrss` on macOS).

These performance measurements describe only the tiny probe process and virtual endpoints. They are not FloBar v99 measurements, do not include physical Bluetooth MIDI, MainStage, UI rendering, or the separate heartbeat process, and are too short to predict live-use performance.

The UI crash/hang cases validate a possible process-separated design in a synthetic harness only. They do not test a real Tkinter UI failure or the v99 process. Preserving input when the routing engine itself fails is not achieved by this single-path design; a separate failover path or independent hardware/OS router would be required, and must be evaluated without creating duplicate commands in normal operation.

## Current findings from v99 source inspection

- v99's documented command path is AIRSTEP Bluetooth/USB -> shared handler -> IAC Driver Bus 1 -> MainStage.
- The application opens an AIRSTEP input and IAC Bus 1 output; it also reads MainStage state mirrors from IAC Bus 2.
- The v99 process combines the UI and MIDI engine. Its watchdog runs in the same process and restarts via `os.execv`; replacing that process interrupts its MIDI engine as well.
- The code is selective command handling, not a transparent pass-through router for every MIDI message. Unknown or unhandled messages can be dropped.
- The red window close action hides the window; the process and MIDI engine remain running.

## Files

- `FloBar_v100_midi_routing_test.py`: unchanged isolated copy of v99; SHA-256 matches the original v99 file.
- `routing_poc.py`: isolated routing proof-of-concept harness; does not modify or import the v99 app.
- `experiment_log.md`: this test-only notes file.

## CC42 live-demo copy prepared (not launched)

The user selected CH16 CC42 for the real-device example and confirmed that the experiment should follow v99's intended command behavior. The user also specified that, in the test routing, CC42 must not be resent to MainStage during TT1 because the source event should be intercepted before MainStage.

Source inspection confirms that v99's TT1 G2M branch currently sends CC42 to IAC Bus 1 and sends Cmd+Tab; repeat presses do the same. The idle branch in v99 does not send CC42. The standalone message reaching MainStage is therefore coming from another active MIDI path, not from v99's idle handler.

Created `FloBar_v99_CC42_routing_demo.py` as a separate copy. Its only command-routing changes are:

- Standalone input CH16 CC42 passes through IAC Bus 1 unchanged, preserving channel, controller number, and value.
- During TT1 and its two-second G2M repeat window, input CH16 CC42 is not sent to IAC; only the existing Cmd+Tab HID action runs.
- The demo's settings and crash diagnostics are isolated under `cc42_demo_data/` beside the copy.

The original v99 and `FloBar_v100_midi_routing_test.py` remain byte-identical at SHA-256 `7e64e49aa0ba7f547312b5c38a68933630e209be40838f1a2dfba40de440b0ce`. The demo copy passed Python AST syntax parsing and has SHA-256 `44229e726b21ea90f11d461b3b50b981b7b9c6302077d61df7df203443448d69`. It has not been launched and no MIDI was sent.

For a valid live check, MainStage must receive AIRSTEP through IAC Bus 1 only; any direct AIRSTEP-to-MainStage route (including through CoyoteMIDI) must be disabled for the test. Otherwise the application cannot suppress a message already delivered on that parallel route. See `CC42_routing_demo_instructions.md`.

The earlier test values CC119 and CC118 were merely unused synthetic Control Change numbers for the virtual-endpoint probe: CC119 was the test input and CC118 its replacement. They are not app commands or special MIDI messages. That probe tested routing/replacement without touching the user's live MIDI mappings.

Offline checks for the CC42 demo passed (4/4): standalone routing preserves channel 16, CC42, and value; the standalone handler sends it once; the TT1 handler consumes it; and the TT1 first/repeat helpers call Cmd+Tab without sending CC42. These were mocked unit checks only. The demo was not launched and no physical MIDI, MainStage, or JamZone was accessed.
