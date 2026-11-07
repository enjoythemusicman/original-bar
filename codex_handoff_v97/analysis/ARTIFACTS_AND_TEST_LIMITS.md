# Artifact inventory and verification limits

## Files included
- `controller/mainstage_jamzone_fade_controller_FLOATING_BAR_v97.py`: exact production baseline; do not edit.
- `diagnostics/mainstage_jamzone_fade_controller_FLOATING_BAR_v97_DIAGNOSTIC_v1.py`: instrumented diagnostic copy; not production.
- `diagnostics/floating_bar_v97_night_test_v16.py`: test harness source for analysis; do not rerun unchanged.
- `diagnostics/diagnostic_v1.log`, `crash_diagnostic_v1.log`, `crash(1).log`: supplied diagnostic logs.
- `diagnostics/Python-2026-10-18-072445.ips` and `Python-2026-10-18-092339.ips`: two available standalone crash reports.
- `diagnostics/floating_bar_full_diagnostic_20261018_162430.zip`: full diagnostic collection; contains the 00:57 crash report and system/process evidence.
- `diagnostics/floating_bar_diagnostics_20261018_151648.zip`: prior nine-hour diagnostic/test report archive.
- `diagnostics/floating_bar_full_diagnostic.py`: diagnostic collector source; not the controller.
- `reference/`: complete work instructions, prior status/continuity snapshots, analysis, and one current screenshot.

## Important limits
- This environment cannot verify live MainStage, JamZone, CoreMIDI, Bluetooth controller, macOS Tk behavior or physical mouse delivery.
- The three crash reports are available across standalone files and the diagnostic ZIP. Attribution to a specific executable must be established from each report's process metadata; similar stack frames alone are insufficient.
- Full raw exports of all three ChatGPT project conversations were not present among the accessible artifacts. The package does not claim to contain them.
