#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP="$ROOT/dist/FloBar.app"

if [[ -e "$APP" ]]; then
  echo "Refusing to replace existing app bundle: $APP" >&2
  echo "Move or rename that bundle yourself, then run this script again." >&2
  exit 73
fi

if ! /usr/bin/swift --version >/dev/null 2>&1; then
  echo "Swift/Xcode is not ready. If it reports the Xcode license, review and accept it in Terminal with: sudo xcodebuild -license" >&2
  exit 69
fi

cd "$ROOT"
/usr/bin/swift build -c release

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Helpers"
cp "$ROOT/.build/release/FloBar" "$APP/Contents/MacOS/FloBar"
cp "$ROOT/.build/release/FloBarGuardian" "$APP/Contents/Helpers/FloBarGuardian"
cp "$ROOT/Info.plist" "$APP/Contents/Info.plist"
chmod 755 "$APP/Contents/MacOS/FloBar" "$APP/Contents/Helpers/FloBarGuardian"
/usr/bin/codesign --force --deep --sign - "$APP"
echo "Built local Apple Silicon test app: $APP"
echo "This ad-hoc signed build is for local testing and is not notarized for distribution."
