#!/usr/bin/env bash
# Create a portable local recovery archive without credentials or runtime caches.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_ROOT="${ULTRON_BACKUP_DIR:-$HOME/.local/share/ultron-mission-control/backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="$BACKUP_ROOT/ultron-local-$STAMP.tar.gz"
RESTORE_DIR="$(mktemp -d)"
trap 'rm -rf "$RESTORE_DIR"' EXIT

mkdir -p "$BACKUP_ROOT"
tar -czf "$ARCHIVE" \
  --exclude='node_modules' \
  --exclude='.next' \
  --exclude='.env*' \
  --exclude='.git' \
  --exclude='*.log' \
  --exclude='ultron-mission-control/backups' \
  -C "$(dirname "$PROJECT_ROOT")" "$(basename "$PROJECT_ROOT")" \
  -C "$HOME/.local/share" ultron-mission-control

# A backup is only useful if it can be unpacked and contains the source tree.
tar -xzf "$ARCHIVE" -C "$RESTORE_DIR"
test -f "$RESTORE_DIR/$(basename "$PROJECT_ROOT")/package.json"
test -f "$RESTORE_DIR/$(basename "$PROJECT_ROOT")/src/app/page.tsx"
tar -tzf "$ARCHIVE" | grep -q '/\.env' && { echo "Refusing archive containing an env file" >&2; exit 1; } || true

printf 'archive=%s\nsha256=%s\nrestore_dry_run=PASS\n' \
  "$ARCHIVE" "$(sha256sum "$ARCHIVE" | cut -d' ' -f1)"
