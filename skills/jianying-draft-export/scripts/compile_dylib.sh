#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$(dirname "$SCRIPT_DIR")/bin"
SRC_FILE="$BIN_DIR/src/jy_codec.cpp"
OUT_DYLIB="$BIN_DIR/libjycodec.dylib"
OUT_CLI="$BIN_DIR/jy_codec"

echo "Compiling $SRC_FILE -> $OUT_DYLIB and $OUT_CLI..."
clang++ -O3 -shared -fPIC -std=c++17 "$SRC_FILE" -o "$OUT_DYLIB"
clang++ -O3 -std=c++17 "$SRC_FILE" -o "$OUT_CLI"
chmod +x "$OUT_DYLIB" "$OUT_CLI"

echo "✅ Compilation successful!"
echo "   Dylib: $OUT_DYLIB"
echo "   CLI:   $OUT_CLI"
