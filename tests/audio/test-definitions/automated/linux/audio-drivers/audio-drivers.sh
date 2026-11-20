#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (c) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# Upstream audio drivers: the card must be instantiated (by CARD_DRIVER,
# the upstream machine driver, when set), every loaded module that lives
# under kernel/sound or drives a DSP audio service must be in-tree
# (modinfo intree: Y, no O taint), the topology must have loaded (the card
# has PCMs and the kernel logged no topology error) and the DSP that runs
# the audio graphs must be running. Out-of-tree audio modules are listed by
# name; with every audio driver built in there are none to check and the
# module case is skipped.

# shellcheck disable=SC1091
. ../../lib/sh-test-lib
OUTPUT="$(pwd)/output"
RESULT_FILE="${OUTPUT}/result.txt"
export RESULT_FILE

CARD=""
CARD_DRIVER=""
REMOTEPROC=""
# extra module name patterns that count as audio (ERE)
AUDIO_MODULES="^snd"
WAIT_SECONDS="60"

while getopts "c:D:r:a:W:h" o; do
    case "$o" in
        c) CARD="${OPTARG}" ;;
        D) CARD_DRIVER="${OPTARG}" ;;
        r) REMOTEPROC="${OPTARG}" ;;
        a) AUDIO_MODULES="${OPTARG}" ;;
        W) WAIT_SECONDS="${OPTARG}" ;;
        *) echo "Usage: $0 [-c card] [-D machine driver] [-r remoteproc] [-a module ERE] [-W s]" 1>&2; exit 1 ;;
    esac
done

create_out_dir "${OUTPUT}"
i=0
while ! grep -q "${CARD:-^ *[0-9]}" /proc/asound/cards 2>/dev/null && [ "${i}" -lt "${WAIT_SECONDS}" ]; do
    sleep 1
    i=$((i + 1))
done
cat /proc/asound/cards
uname -r
if grep -q "${CARD:-^ *[0-9]}" /proc/asound/cards; then
    drv=""
    for c in /sys/class/sound/card*; do
        [ -e "${c}/device/driver" ] && drv="${drv} $(basename "$(readlink "${c}/device/driver")")"
    done
    info_msg "card driver(s):${drv}"
    if [ -z "${CARD_DRIVER}" ] || echo "${drv}" | grep -qw "${CARD_DRIVER}"; then
        report_pass audio-card-upstream-machine
    else
        report_fail audio-card-upstream-machine
    fi
else
    report_fail audio-card-upstream-machine
fi

oot=""
n=0
for m in $(awk '{ print $1 }' /proc/modules); do
    f="$(modinfo -n "${m}" 2>/dev/null)"
    case "${f}" in
        */sound/*) ;;
        *) echo "${m}" | grep -qE "${AUDIO_MODULES}" || continue ;;
    esac
    n=$((n + 1))
    it="$(modinfo -F intree "${m}" 2>/dev/null)"
    t="$(cat "/sys/module/${m}/taint" 2>/dev/null)"
    echo "audio module ${m}: intree '${it}' taint '${t}' ${f}"
    if [ "${it}" != Y ] || echo "${t}" | grep -q O; then oot="${oot} ${m}"; fi
done
info_msg "${n} audio modules, out of tree:${oot:- none}"
if [ "${n}" -eq 0 ]; then
    report_skip audio-modules-in-tree
elif [ -z "${oot}" ]; then
    report_pass audio-modules-in-tree
else
    report_fail audio-modules-in-tree
fi

e="$(dmesg | grep -iE "tplg|topology" | grep -iE "fail|error|err " )"
dmesg | grep -iE "tplg|topology" | tail -5
C="$(awk -v c="${CARD}" '/^ *[0-9]+ \[/ { id = $2; sub(/^\[/, "", id); gsub(/[]: ]/, "", id); n = $0; sub(/.* - /, "", n); if (c == "" || id == c || n == c) { print $1; exit } }' /proc/asound/cards)"
pcms="$(ls -d /proc/asound/card"${C:-none}"/pcm* 2>/dev/null | wc -l)"
info_msg "${pcms} PCMs on card ${C:-none}"
if [ "${pcms}" -gt 0 ] && [ -z "${e}" ]; then report_pass audio-topology-loaded; else echo "${e}"; report_fail audio-topology-loaded; fi

r=""
for d in /sys/class/remoteproc/remoteproc*; do
    [ "$(cat "${d}/name" 2>/dev/null)" = "${REMOTEPROC}" ] && r="$(cat "${d}/state")"
done
if [ -z "${REMOTEPROC}" ]; then
    info_msg "no audio DSP remoteproc named (-r)"
    report_skip audio-dsp-running
else
    info_msg "remoteproc ${REMOTEPROC}: ${r:-absent}"
    if [ "${r}" = running ]; then report_pass audio-dsp-running; else report_fail audio-dsp-running; fi
fi
exit 0
