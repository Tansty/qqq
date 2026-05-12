#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STORAGE_DIR="${1:-$PROJECT_DIR/storage}"
BACKUP_DIR="${2:-$PROJECT_DIR/backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"

if [ ! -d "$STORAGE_DIR" ]; then
  echo "storage directory not found: $STORAGE_DIR" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
tar -czf "$BACKUP_DIR/qqq-storage-$STAMP.tar.gz" -C "$STORAGE_DIR" .
echo "$BACKUP_DIR/qqq-storage-$STAMP.tar.gz"
