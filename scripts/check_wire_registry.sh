#!/usr/bin/env bash
# Verifies that the generated block in docs/wire_registry.md (between the
# BEGIN/END GENERATED BLOCK markers) is a verbatim copy of
# redpandaj/src/main/resources/wire-registry.md.
#
# Usage:
#   scripts/check_wire_registry.sh [<path to redpandaj wire-registry.md>]
#
# Without an argument the file is fetched from redpandaj main on GitHub.
# Exit 1 on drift (a unified diff is printed). To fix drift, copy the
# redpandaj file verbatim between the markers (see "Regenerating" in
# docs/wire_registry.md).
set -euo pipefail

DOCS_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOC="$DOCS_REPO/docs/wire_registry.md"
URL="https://raw.githubusercontent.com/redPanda-project/redpandaj/main/src/main/resources/wire-registry.md"
BEGIN='<!-- BEGIN GENERATED BLOCK'
END='<!-- END GENERATED BLOCK'

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

if [[ $# -ge 1 ]]; then
  SRC="$1"
  cp "$SRC" "$tmp/upstream.raw"
else
  SRC="redpandaj main ($URL)"
  curl -fsSL --retry 3 --retry-all-errors --retry-delay 5 "$URL" -o "$tmp/upstream.raw"
fi
# Tolerate CRLF and a missing final newline on either side; awk below always
# terminates lines with LF.
tr -d '\r' < "$tmp/upstream.raw" | sed -e '$a\' > "$tmp/upstream.md"

if [[ "$(grep -c "^$BEGIN" "$DOC")" != 1 || "$(grep -c "^$END" "$DOC")" != 1 ]]; then
  echo "expected exactly one BEGIN and one END GENERATED BLOCK marker in $DOC" >&2
  exit 1
fi
awk -v b="$BEGIN" -v e="$END" \
  'index($0, e) == 1 { f = 0 } f { print } index($0, b) == 1 { f = 1 }' \
  "$DOC" | tr -d '\r' > "$tmp/block.md"

if diff -u --label "$SRC" \
           --label "docs/wire_registry.md (generated block)" \
           "$tmp/upstream.md" "$tmp/block.md"; then
  echo "wire registry mirror is in sync with $SRC"
else
  echo "::error file=docs/wire_registry.md::generated block drifted from redpandaj wire-registry.md — copy the file verbatim between the markers"
  exit 1
fi
