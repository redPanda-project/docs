#!/usr/bin/env bash
# Syncs the per-repo milestone views from this docs repo (the source of truth)
# into the sibling code repositories:
#
#   docs/milestones/backend/*.md  ->  <root>/redpandaj/milestones/
#   docs/milestones/frontend/*.md ->  <root>/redpanda-mobile/milestones/
#
# Usage:
#   scripts/sync_milestones.sh [--check] [--root <path>]
#
#   --check   Do not copy anything; list diverging files and exit 1 if any.
#   --root    Directory containing the three repo checkouts
#             (default: the parent directory of this docs repo).
#
# Files that exist only in a mirror are reported as ORPHAN and never deleted —
# resolve those by adding the file here or removing it in the code repo.
set -euo pipefail

DOCS_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(dirname "$DOCS_REPO")"
CHECK=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK=1; shift ;;
    --root) ROOT="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

sync_dir() {
  local src="$1" dst="$2" status=0 base

  if [[ ! -d "$src" || ! -d "$dst" ]]; then
    echo "missing directory: $src or $dst" >&2
    return 2
  fi

  for f in "$src"/*.md; do
    base="$(basename "$f")"
    if ! diff -q "$f" "$dst/$base" >/dev/null 2>&1; then
      if [[ $CHECK -eq 1 ]]; then
        echo "DIVERGED: $dst/$base"
        status=1
      else
        cp "$f" "$dst/$base"
        echo "synced:   $dst/$base"
      fi
    fi
  done

  for f in "$dst"/*.md; do
    base="$(basename "$f")"
    if [[ ! -f "$src/$base" ]]; then
      echo "ORPHAN (only in mirror, not deleted): $dst/$base"
      status=1
    fi
  done

  return $status
}

overall=0
sync_dir "$DOCS_REPO/docs/milestones/backend" "$ROOT/redpandaj/milestones" || overall=1
sync_dir "$DOCS_REPO/docs/milestones/frontend" "$ROOT/redpanda-mobile/milestones" || overall=1

if [[ $CHECK -eq 1 && $overall -eq 0 ]]; then
  echo "mirrors are in sync"
fi
exit $overall
