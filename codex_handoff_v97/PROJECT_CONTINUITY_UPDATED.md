# PROJECT CONTINUITY — CURRENT STATE ONLY

**Checkpoint:** 2026-10-18  
**Project:** Music Man — MainStage/JamZone Floating Bar  
**Current controller baseline:** v97  
**Authoritative detailed handoff:** `CODEX_HANDOFF_REPORT.md`

## Current baseline and integrity

- Production source: `controller/mainstage_jamzone_fade_controller_FLOATING_BAR_v97.py`
- SHA-256: `f1c20f91ae5442f6981cbee750979cc958b1f50944d68801b7e8517e6e9d5a3e`
- v97 must remain byte-for-byte unchanged unless the user explicitly approves a production revision.
- Retired mappings CH8 CC0 and CH1 CC57 must remain absent.
- Keep IAC Bus 1 for existing control commands and IAC Bus 2 for status mirrors.
- Preserve Guitar Mute red-light behavior and all existing production MIDI mappings/routing.

## Current evidence status

The nine-hour diagnostic run did not pass its functional test criteria. It is INCONCLUSIVE/FAILED, not a successful stability test. Findings include diagnostic-process RSS growth from about 149 MB to 4.52 GB, Tkinter callbacks touching destroyed widgets, SIGABRT crash reports with Tk/Tcl finalization frames, failed mouse-event verification, incomplete timer/fade tests, unresolved CoyoteMIDI sampling and a timed-out power log. Details and confidence limits are in `CODEX_HANDOFF_REPORT.md` and `reference/FLOATING_BAR_DIAGNOSTIC_ANALYSIS_AND_PLAN.md`.

No exact root cause has been proven. Do not attribute every crash to v97 or to a particular thread without process metadata and source-level evidence.

## Next action

Inspect v97, the v97 diagnostic copy, and the v16 night-test runner. Develop and validate a separately named, bounded diagnostic harness only. Do not run another overnight test, send MIDI, trigger playback, stop processes, or change production code without specific user approval. First produce static evidence and a short, non-destructive test plan.

## Working rules

- Read `Complete_Project_Work_Instructions.txt` and `CODEX_HANDOFF_REPORT.md` first.
- Never overwrite source versions; work only in a dedicated project folder.
- Distinguish static tests from live macOS/CoreMIDI/MainStage/JamZone verification.
- No production behavior or UI enhancement changes without explicit approval.
- Update status and continuity after each verified milestone.
