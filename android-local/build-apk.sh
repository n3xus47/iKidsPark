#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$ROOT_DIR/app/src/main"
SDK_DIR="${ANDROID_HOME:-/home/n3xus/Android/Sdk}"

if [[ ! -d "$SDK_DIR" ]]; then
  echo "Nie znaleziono Android SDK. Ustaw ANDROID_HOME albo zainstaluj SDK." >&2
  exit 1
fi

BUILD_TOOLS="$(find "$SDK_DIR/build-tools" -mindepth 1 -maxdepth 1 -type d | sort -V | tail -n 1)"
PLATFORM_JAR="$(find "$SDK_DIR/platforms" -mindepth 2 -maxdepth 2 -name android.jar | sort -V | tail -n 1)"

if [[ -z "$BUILD_TOOLS" || -z "$PLATFORM_JAR" ]]; then
  echo "Brakuje build-tools albo platform android.jar w Android SDK." >&2
  exit 1
fi

AAPT="$BUILD_TOOLS/aapt"
D8="$BUILD_TOOLS/d8"
ZIPALIGN="$BUILD_TOOLS/zipalign"
APKSIGNER="$BUILD_TOOLS/apksigner"

BUILD_ID="$(date +%Y%m%d-%H%M%S)"
WORK_DIR="$ROOT_DIR/build/$BUILD_ID"
GEN_DIR="$WORK_DIR/gen"
CLASS_DIR="$WORK_DIR/classes"
DEX_DIR="$WORK_DIR/dex"
DIST_DIR="$ROOT_DIR/dist"
KEYSTORE_DIR="$ROOT_DIR/keystore"
KEYSTORE="$KEYSTORE_DIR/ikids-local-debug.jks"

mkdir -p "$GEN_DIR" "$CLASS_DIR" "$DEX_DIR" "$DIST_DIR" "$KEYSTORE_DIR"

python3 "$ROOT_DIR/tools/generate_current_asset.py" >/dev/null

"$AAPT" package \
  -f \
  -m \
  -J "$GEN_DIR" \
  -M "$SRC_DIR/AndroidManifest.xml" \
  -S "$SRC_DIR/res" \
  -I "$PLATFORM_JAR"

javac \
  -encoding UTF-8 \
  -source 8 \
  -target 8 \
  -bootclasspath "$PLATFORM_JAR" \
  -d "$CLASS_DIR" \
  $(find "$SRC_DIR/java" "$GEN_DIR" -name '*.java' | sort)

"$D8" \
  --lib "$PLATFORM_JAR" \
  --output "$DEX_DIR" \
  $(find "$CLASS_DIR" -name '*.class' | sort)

UNSIGNED_APK="$WORK_DIR/ikids-park-local-unsigned.apk"
ALIGNED_APK="$WORK_DIR/ikids-park-local-aligned.apk"
SIGNED_APK="$DIST_DIR/ikids-park-local.apk"

"$AAPT" package \
  -f \
  -M "$SRC_DIR/AndroidManifest.xml" \
  -S "$SRC_DIR/res" \
  -A "$SRC_DIR/assets" \
  -I "$PLATFORM_JAR" \
  -F "$UNSIGNED_APK"

(
  cd "$DEX_DIR"
  "$AAPT" add "$UNSIGNED_APK" classes.dex >/dev/null
)

"$ZIPALIGN" -f -p 4 "$UNSIGNED_APK" "$ALIGNED_APK"

if [[ ! -f "$KEYSTORE" ]]; then
  keytool -genkeypair \
    -keystore "$KEYSTORE" \
    -storepass android \
    -alias ikidslocal \
    -keypass android \
    -keyalg RSA \
    -keysize 2048 \
    -validity 10000 \
    -dname "CN=iKids Park Local, O=iKids Park, C=PL" >/dev/null
fi

"$APKSIGNER" sign \
  --ks "$KEYSTORE" \
  --ks-pass pass:android \
  --key-pass pass:android \
  --out "$SIGNED_APK" \
  "$ALIGNED_APK"

"$APKSIGNER" verify "$SIGNED_APK"

echo "$SIGNED_APK"
