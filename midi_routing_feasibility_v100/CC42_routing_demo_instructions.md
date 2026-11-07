# CC42 routing demo based on v99

## Confirmed behavior in original v99

- During TT1's first five seconds, v99 forwards CH16 CC42 through IAC Bus 1 and sends Cmd+Tab. Repeated CC42 during the two-second repeat window also forwards CC42 and sends Cmd+Tab.
- While idle, v99 itself does not forward CH16 CC42. The standalone command reaching MainStage therefore comes through another active MIDI route.

## Demo copy behavior

- Standalone CH16 CC42 is passed through unchanged to IAC Bus 1, preserving channel 16, CC42, and its value.
- During TT1, CH16 CC42 is consumed; the demo sends only the existing Cmd+Tab HID action. Repeats in the two-second window also send only Cmd+Tab.
- The original v99 file and the byte-identical v100 feasibility copy are unchanged. The demo stores its settings and crash log under `cc42_demo_data` beside this file.

## Before running

1. Close the original v99 process so two FloBar processes cannot handle the same controller input.
2. MainStage must receive AIRSTEP through IAC Bus 1 only during this test. Disable any direct AIRSTEP-to-MainStage route in CoyoteMIDI or MainStage; FloBar cannot retract a MIDI message that another route has already delivered.
3. Do not run the test if that routing condition is uncertain; it could cause duplicate CC42 delivery or make the TT1 suppression test invalid.

## Manual check

1. Start `FloBar_v99_CC42_routing_demo.py` with the same Python environment used for v99.
2. Press standalone CC42 once. Confirm the MIDI monitor shows exactly one CH16 CC42 with its original value. Press it once more to restore the G2M toggle if needed.
3. Start TT1 with CC44, then press CC42 within the first five seconds. Confirm the demo logs the HID-only path and that no CH16 CC42 is emitted to IAC Bus 1. The early CC42 cancels TT1 before its fade ramp starts.

This manual demo checks CC42 routing while the copy is running. It does not prove fallback if the routing process itself crashes; the synthetic virtual-endpoint probe remains the only completed failure-path test.
