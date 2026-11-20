#!/bin/sh
# mk-ci-regression.sh <base|ovl> <FP> <device> [tag]: the audio PRD
# regression job for one image: the definitions in regression-cases.json
# (those that passed on both EVKs in the final runs). Same environment as
# mk-prd-job.py (IMAGE_URL, TD_URL, ROOT_PW, ...). Writes
# jobs/<tag>.yaml and jobs/<tag>-backstop.yaml; submit both, in order.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
var=${1:?base|ovl} fp=${2:?FP} dev=${3:?device}
tag=${4:-ci-regression-$var-${fp##*-}}
cases=$(python3 -I -c 'import json, sys; print(",".join(json.load(open(sys.argv[1]))[sys.argv[2]]))' \
	"$here/regression-cases.json" "$var")
python3 "$here/mk-prd-job.py" "$var" "$tag" "$fp" "$dev" "$here/../jobs" "$cases"
