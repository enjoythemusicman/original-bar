# FloBar v2 test build

FloBar v2 is an isolated Swift/AppKit implementation copied into its own source folder. The earlier `FloBar_v1` source and app bundle are left unchanged.

## Build locally

Requires Xcode Command Line Tools / Xcode with the license accepted, Swift 5.9+, macOS 11+, and an Apple Silicon Mac. The current build script uses only the local Swift toolchain and does not access the internet.

```sh
cd "/Users/galtalor/Documents/chatGPT/FloBar/FloBar_v2"
swift test
zsh build_app.sh
```

The app bundle is created at `dist/FloBar_v2.0.app`. The script refuses to replace an existing bundle. The local ad-hoc signature is not notarized and is for testing only.

## Before live use

The v2 source and bundle must be compiled, pass automated checks, and be exercised on the M1 setup for interface interaction, actual MIDI feedback, Bluetooth state, Guardian restart and hang recovery, and the approved five-hour performance limit. A successful compile does not establish live reliability or the CPU target. No live MIDI or application interaction is performed by the build process.

Settings and diagnostics use FloBar-specific Application Support and Logs folders. Legacy settings and backups are imported only after explicit file selection.
