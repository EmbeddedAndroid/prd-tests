#!/bin/sh
# mk-td-tar.sh <out.tar>: tar the test definitions the audio PRD jobs use
# (prefix td/, td/REV = this checkout's short SHA). Upload the tarball
# where the LAVA dispatcher can fetch it and export TD_URL.
set -eu
here=$(cd "$(dirname "$0")/.." && pwd)
out=$(realpath -m "${1:?out.tar}")
rev=$(git -C "$here" rev-parse --short=8 HEAD 2>/dev/null || echo unknown)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/td"
cp -a "$here/test-definitions/automated" "$tmp/td/"
echo "$rev eliza-prd-tests $(date -u +%FT%TZ)" > "$tmp/td/REV"
tar -C "$tmp" -cf "$out" td
echo "$out"
