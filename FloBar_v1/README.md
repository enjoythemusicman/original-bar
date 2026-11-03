# FloBar v1 (test source)

This is a new Swift/AppKit codebase. It does not modify the Python v97/v99 files. It is not yet a release-ready or performance-verified app.

## Build locally

Requires Xcode Command Line Tools / Xcode with the license accepted, Swift 5.9+, macOS 11+, and an Apple Silicon Mac. macOS 11 is the declared minimum; runtime verification is still needed on each supported M1/macOS combination, especially opt-in Login Item and global HID capabilities.

```sh
cd "/Users/galtalor/Documents/chatGPT/FloBar/FloBar_v1"
swift test
zsh build_app.sh
```

The app bundle is created at `dist/FloBar.app`. The script uses an ad-hoc signature for local testing; it does not notarize the app. No packages are downloaded and the build does not require internet access.

## Important before live use

Do not use this source build in a performance until it has compiled, passed its unit and integration checks, and completed the approved MIDI, focus, guardian, thermal, and five-hour soak tests on the M1 setup. Some planned features still need implementation and device validation; see the project status and plan files in the parent folder.

FloBar requests no Full Disk Access. App settings and diagnostics use only the FloBar-specific Application Support and Logs folders. Importing legacy settings is an explicit file selection.
