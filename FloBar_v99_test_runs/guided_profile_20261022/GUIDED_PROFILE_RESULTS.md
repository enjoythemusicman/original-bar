# FloBar v99 guided profile — 2026-10-22

## Scope

- Profiled the exact v99 source; SHA-256 matched `7e64e49aa0ba7f547312b5c38a68933630e209be40838f1a2dfba40de440b0ce`.
- No source code was changed.
- No network access was used.
- The test did not inject `P` or `L`. Opening v99 performed its normal startup MIDI synchronization.

## Measurements

- With FloBar closed, system load varied substantially during the baseline. MainStage itself appeared around 55–58% CPU in sampled snapshots, so whole-system CPU is not a clean proxy for FloBar's cost.
- With v99 open for about four minutes, macOS `top` reported 23 nonzero process samples: mean 44.11% CPU, range 42.6–45.7%.
- The Python process memory shown by `top` began around 101 MB, briefly peaked at 180 MB, and ended around 167 MB. This short sample is not enough to establish a memory leak.
- A five-second `sample` profile showed the Tk main thread spending most sampled time in `Tcl_WaitForEvent` / CoreFoundation event waiting. It did not identify the source of the process's sustained CPU use; a native profile alone does not map all Python frames to their source functions.

## User-observed input behavior

- A first click on the bar was needed before mouse interaction worked.
- After that, both message arrows responded when clicked directly on their button areas.
- Double-clicking the message area did not open the editor.
- Long-pressing the message area did open it.
- The editor was closed with Cancel without editing or saving settings.

This supports investigating window activation/first-click routing and overlapping event bindings in the redesign. It does not establish a single root cause.

## Close behavior discovered

The source wires `WM_DELETE_WINDOW` to `_hide_window` at line 192, and `_hide_window` withdraws the Tk root at lines 791–798. Static inspection therefore predicts that the red close control hides the window. The live observation is not conclusive, however: after the user clicked the red X, the bar disappeared, and the user later reported that they could not find a FloBar process in Activity Monitor. A later Activity Monitor screenshot shows `duetexpertd` at 70.5% CPU, but does not identify that process as FloBar; the user observed it falling toward 1% over about a minute. The screenshot and observation do not establish whether v99 exited, was hidden under a different process name, or ended for another reason. The earlier statement that the launch session remained alive should not be treated as proof that FloBar continued running after the close action. The script has a restart action but no normal Quit action in its context menu. Hidden-state CPU was not separately profiled.

For a conclusive follow-up, a future run should record the launched PID and executable command before interaction, then check that exact PID immediately after clicking the red X. This will distinguish window hiding from process exit without relying on the display name or CPU ranking in Activity Monitor.

## Artifacts

- `baseline_top.txt` — closed-bar baseline samples.
- `runtime_top.txt` — four-minute open-bar process and system samples.
- `v99_stack_sample.txt` — five-second process stack profile.

