#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STORAGE_DIR="${1:-$PROJECT_DIR/storage}"

mkdir -p "$STORAGE_DIR/data"

if [ -f "$PROJECT_DIR/config.json" ] && [ ! -f "$STORAGE_DIR/config.json" ]; then
  cp "$PROJECT_DIR/config.json" "$STORAGE_DIR/config.json"
elif [ -f "$PROJECT_DIR/config.example.json" ] && [ ! -f "$STORAGE_DIR/config.json" ]; then
  cp "$PROJECT_DIR/config.example.json" "$STORAGE_DIR/config.json"
fi

if [ -d "$PROJECT_DIR/data" ]; then
  cp -n "$PROJECT_DIR"/data/*.json "$STORAGE_DIR/data/" 2>/dev/null || true
fi

echo "prepared storage at $STORAGE_DIR"
