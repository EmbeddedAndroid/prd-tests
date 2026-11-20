#!/bin/sh
# mk-overlay.sh <out.tar> [vectors-dir]: tar the runner, the tests, the
# board profiles and the product maps for a LAVA rootfs overlay, unpacked
# at /prd on the board. YAML files get a JSON copy next to them so the
# board needs no YAML module; /prd/REV is this checkout's short SHA
# (with -dirty for uncommitted changes). vectors-dir, when given, is a
# directory of conformance vectors with <name>.frames files
# (tools/fetch-vectors.sh), copied to /prd/vectors.
# Needs python3 with PyYAML on the host.
set -eu
top=$(cd "$(dirname "$0")/.." && pwd)
out=$(realpath -m "${1:?out.tar}")
vec=${2:-}
rev=$(git -C "$top" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)
dirty=$(git -C "$top" status --porcelain 2>/dev/null | grep -q . && echo -dirty || true)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/prd/tools"
cp -a "$top/prd" "$top/tests" "$top/boards" "$top/products" "$tmp/prd/"
cp "$top/tools/run.py" "$tmp/prd/tools/"
mkdir -p "$tmp/prd/utils"
cp "$top/tests/audio/test-definitions/automated/utils/send-to-lava.sh" "$tmp/prd/utils/"
find "$tmp" -name __pycache__ -prune -exec rm -rf {} +
find "$tmp/prd/boards" "$tmp/prd/products" "$tmp/prd/tests" -name '*.yaml' | python3 -c '
import json, os, sys, yaml
for p in sys.stdin.read().split():
    with open(os.path.splitext(p)[0] + ".json", "w") as f:
        json.dump(yaml.safe_load(open(p)), f)
'
if [ -n "$vec" ]; then
	mkdir -p "$tmp/prd/vectors"
	cp "$vec"/* "$tmp/prd/vectors/"
fi
echo "$rev$dirty" > "$tmp/prd/REV"
tar -C "$tmp" --owner=0 --group=0 -cf "$out" prd
echo "$out"
