#!/bin/bash
set -euo pipefail

# MIDI Automation Build Script
# Builds the app using xcodegen + xcodebuild, signs it, and creates a zip

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

APP_NAME="MIDIAutomation"
SCHEME="MIDIAutomation"
BUILD_DIR="$SCRIPT_DIR/build"
APP_PATH="$BUILD_DIR/Build/Products/Release/$APP_NAME.app"

echo "=== Building $APP_NAME ==="

# Step 1: Generate Xcode project
echo "[1/5] Generating Xcode project..."
if ! command -v xcodegen &>/dev/null; then
    echo "Error: xcodegen not found. Install with: brew install xcodegen"
    exit 1
fi
xcodegen generate --quiet

# Step 2: Build
echo "[2/5] Building release..."
xcodebuild \
    -project "MIDIAutomation.xcodeproj" \
    -scheme "$SCHEME" \
    -configuration Release \
    -derivedDataPath "$BUILD_DIR" \
    -quiet \
    CODE_SIGN_IDENTITY="-" \
    CODE_SIGN_STYLE=Manual \
    ENABLE_APP_SANDBOX=NO

if [ ! -d "$APP_PATH" ]; then
    echo "Error: Build failed - $APP_PATH not found"
    exit 1
fi

# Step 3: Clear extended attributes (OneDrive adds these)
echo "[3/5] Clearing extended attributes..."
xattr -cr "$APP_PATH"

# Step 4: Sign nested Sparkle components inside-out, then the app
echo "[4/5] Signing app..."
SPARKLE_FW="$APP_PATH/Contents/Frameworks/Sparkle.framework"
if [ -d "$SPARKLE_FW" ]; then
    codesign --force --sign - "$SPARKLE_FW/Versions/B/XPCServices/Installer.xpc" 2>/dev/null || true
    codesign --force --sign - "$SPARKLE_FW/Versions/B/XPCServices/Downloader.xpc" 2>/dev/null || true
    codesign --force --sign - "$SPARKLE_FW/Versions/B/Updater.app" 2>/dev/null || true
    codesign --force --sign - "$SPARKLE_FW/Versions/B/Autoupdate" 2>/dev/null || true
    codesign --force --sign - "$SPARKLE_FW" 2>/dev/null || true
fi
codesign --force --deep --sign - "$APP_PATH"

# Step 5: Create zip
echo "[5/5] Creating zip..."
VERSION=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$APP_PATH/Contents/Info.plist")
ZIP_NAME="MIDIAutomation-v${VERSION}-aarch64.zip"
cd "$BUILD_DIR/Build/Products/Release"
ditto -c -k --keepParent "$APP_NAME.app" "$SCRIPT_DIR/$ZIP_NAME"
cd "$SCRIPT_DIR"

echo ""
echo "=== Build Complete ==="
echo "App:  $APP_PATH"
echo "Zip:  $SCRIPT_DIR/$ZIP_NAME"
echo "Version: $VERSION"
