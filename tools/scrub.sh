#!/bin/sh
# scrub.sh [dir...]: fail if a file in the given trees (default: this repo),
# or the git history and commit messages of a tree that is a git work tree,
# names an internal host, address, lease, ticket system or secret. Runs in
# CI and before anything is handed over for a push. Prints every hit.
set -u
top=$(cd "$(dirname "$0")/.." && pwd)
[ $# -gt 0 ] || set -- "$top"
pat='\b10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b|\b192\.168\.[0-9]{1,3}\.[0-9]{1,3}\b|\b172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3}\b'
pat=$pat'|[a-z0-9.-]+\.qualcomm\.com|atlassian|confluence|jira\.|sharepoint'
pat=$pat'|\blease[ #_-]*[0-9]{2,}|lease_id|private_key|BEGIN [A-Z ]*PRIVATE KEY'
pat=$pat'|password=|token=|\.internal\b|\.corp\b|/local/mnt/|/home/[a-z]+/|/usr2/'
# Site-specific names go in an untracked file, one regex per line.
extra=${SCRUB_EXTRA:-$top/tools/scrub-extra.txt}
[ -r "$extra" ] && pat=$pat"|$(grep -v '^#' "$extra" | grep . | paste -sd'|' -)"
pat="($pat)"
# Qualcomm hosts that are public download or documentation endpoints of
# upstream recipes
allow='(softwarecenter|qartifactory-edge|docs|www|oss)\.qualcomm\.com'
rc=0
for d in "$@"; do
	[ -e "$d" ] || { echo "scrub: no such path: $d"; rc=1; continue; }
	# grep relative to the tree, so its own path never matches
	if [ -d "$d" ]; then
		hits=$(cd "$d" && grep -rniIE "$pat" --exclude-dir=.git --exclude=scrub.sh -- . | sed -E "s/$allow/<public>/gI" | grep -iE "$pat" | sed "s|^\./|$d/|" || true)
	else
		hits=$(grep -niIE "$pat" -- "$d" | sed -E "s/$allow/<public>/gI" | grep -iE "$pat" || true)
	fi
	if [ -d "$d" ] && git -C "$d" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
		hist=$(git -C "$d" log --all -p --format='commit %H%n%B' -- . ':!tools/scrub.sh' 2>/dev/null |
			grep -niE "$pat" | sed -E "s/$allow/<public>/gI" | grep -iE "$pat" | sed 's/^/history:/' || true)
		[ -n "$hist" ] && hits=$(printf '%s\n%s' "$hits" "$hist")
	fi
	if [ -n "$(echo "$hits" | tr -d '[:space:]')" ]; then
		echo "$hits" | sed '/^$/d'
		rc=1
	fi
	nb=$(find "$d" -type f -not -path '*/.git/*' | while read -r f; do grep -qI . "$f" 2>/dev/null || echo "$f"; done | wc -l)
	[ "$nb" -gt 0 ] && echo "scrub: $d: $nb binary or empty files not scanned"
done
if [ $rc -ne 0 ]; then
	echo "scrub: FAIL"
	exit 1
fi
echo "scrub: clean ($(for d in "$@"; do find "$d" -type f -not -path '*/.git/*'; done | wc -l) files)"
