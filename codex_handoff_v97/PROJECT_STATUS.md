# PROJECT STATUS — 2026-10-18

## Baseline
- **Production controller:** `mainstage_jamzone_fade_controller_FLOATING_BAR_v97.py`
- **SHA-256:** `f1c20f91ae5442f6981cbee750979cc958b1f50944d68801b7e8517e6e9d5a3e`
- **Production changes:** none authorized by this handoff. Preserve v97 unchanged.
- **Current overall stability status:** NOT VERIFIED. The nine-hour diagnostic run did not pass functional criteria.

## Findings requiring action
1. Diagnostic Floating Bar RSS grew to approximately 4.52 GB.
2. Tkinter callbacks attempted to update destroyed widgets (`TclError: invalid command name ...`).
3. Three crash reports show SIGABRT with Tk/Tcl finalization frames; attribution to the exact executable is not yet fully established.
4. Threaded diagnostic log recorded `root.after()`/Tk calls from non-main threads.
5. Mouse injection did not reliably correspond to Tk click events; timer tests were not verified.
6. Fade and playback test scenarios were incomplete; do not infer success from the scheduled run completing.
7. CoyoteMIDI sampling and system power-log capture are inconclusive.

## Prioritized pending tasks

### P0 — diagnostic harness safety and trustworthy measurements
- [ ] Verify process/source attribution for all three crash reports.
- [ ] Inspect v16 runner and diagnostic-copy source for unbounded retention, process duplication/restart logic, Tk thread violations, callback teardown and memory growth.
- [ ] Correct Tk access to main-thread-only; use a thread-safe queue for worker results.
- [ ] Cancel scheduled callbacks and guard destroyed widgets; make shutdown idempotent and stop/join workers before destroying Tk.
- [ ] Bound memory and implement a hard RSS stop/report threshold.
- [ ] Correct CoyoteMIDI matching and self-check process visibility against sampler output.
- [ ] Fix restart serialization and record PID/generation/reason.
- [ ] Separate attempted/injected/received/widget-hit/action-result event metrics.
- [ ] Make timer verification require running-state observations, countdown progression and completion/reset evidence.
- [ ] Make power log collection bounded with explicit timeout/truncation status.
- [ ] Add machine-readable pass/fail accounting; missing scenarios cannot pass.

### P1 — staged validation (after P0 static checks)
- [ ] Syntax/AST checks, hash gate and diff proving production v97 unchanged.
- [ ] Short startup/idle/shutdown test without MIDI/playback/click automation.
- [ ] Callback teardown/lifecycle test.
- [ ] Mouse event-delivery test only after instrumentation is validated.
- [ ] Timer-only test.
- [ ] Isolated MIDI/state tests with observed results.
- [ ] Supervised 30–60 minute soak on AC power with sleep prevention verified and hard resource limits.
- [ ] Longer test only after every earlier stage passes and the user specifically approves it.

### P2 — Floating Bar behavior/UI checks and enhancements
- [ ] Diagnose click responsiveness/resource usage before changing production code.
- [ ] Verify startup state synchronization.
- [ ] Verify timer single-click, double-click, long-press, reset while running, numeric-only input, countdown and negative-time display.
- [ ] Verify TT1/Fade/Option+Tab and last-three-second tile-background flashing.
- [ ] Investigate MIDI false alerts and shutdown warnings.
- [ ] Verify displayed version matches the running source.
- [ ] Evaluate full restart shortcut for conflicts with JamZone; old process must exit before a new process starts. Production implementation requires explicit approval.
- [ ] Verify active-command/disconnect display alternation every 3 seconds.
- [ ] Isolated check for P23 battery percentage and disconnect indication in MIDI INPUT; unresolved.
- [ ] Rotating message-box prompt enhancement remains unapproved: cycle every 10 seconds through `תציג עצמך`, `לחיים`, `אינסטה`, `משקה הבית`, `מאיפה אתם?`, with varied colors, correct RTL punctuation and suitable icons. Do not implement without explicit approval.
- [ ] Controlled-failure and long-duration stability testing before DMG packaging.

## Completed / do not reopen unless user reports a new defect
- IAC Bus 2 state feedback architecture.
- Floating Bar height reduced by 25%.
- Unused empty tile removed.
- Timer negative-time display.
- SMC/AIRSTEP JamZone/SBP window switching behavior.
- Guitar Mute red-light behavior.
- Retired mappings CH8 CC0 and CH1 CC57 removed from v97.

## Approval boundary
Codex may inspect and statically test files and may create separate diagnostic-only files. It must not modify production v97, alter MIDI/routing/UI behavior, change macOS settings, stop production processes, or run live MIDI/playback tests without explicit user approval.
