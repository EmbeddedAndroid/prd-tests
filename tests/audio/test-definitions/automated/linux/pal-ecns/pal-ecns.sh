#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (c) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# Noise suppression / echo cancellation on the AudioReach PAL record path:
# a steady sine plays out of the speaker (PLAY_NODE) while the processed
# record node (PROC_NODE, the ECNS usecase) and then the unprocessed one
# (RAW_NODE) record it. The raw capture must carry the tone (the acoustic
# path works). A train of short bursts at the same frequency (100 ms on,
# 400 ms off), which noise suppression passes as non-stationary sound,
# must then reach the processed node within BURST_TOL_DB of the raw node:
# same microphone, same gain, graph running (both burst captures must
# carry the bursts). Over the second half of the steady tone the processed
# node must be at least SUPPRESS_DB lower than the raw one, net of the
# burst offset: a stationary tone is what NS removes, so that drop, on a
# path shown to be the same, proves the processing module ran. BURST_TOL_DB
# must stay below SUPPRESS_DB. PipeWire nodes only.

# shellcheck disable=SC1091
. ../../lib/sh-test-lib
AUDIO_BACKEND=pipewire
# shellcheck disable=SC1091
. ../../lib/audio/audio.sh
OUTPUT="$(pwd)/output"
RESULT_FILE="${OUTPUT}/result.txt"
export RESULT_FILE

PLAY_NODE=""
PROC_NODE=""
RAW_NODE=""
PLAY_CHANNEL="2"
RATE="48000"
FREQ="1250"
TONE_DBFS="-6"
MIN_DB="20"
SUPPRESS_DB="10"
BURST_TOL_DB="6"
SECONDS_PER_TONE="6"
PREFIX="ecns"

while getopts "p:P:r:k:R:f:l:d:s:b:T:x:h" o; do
    case "$o" in
        p) PLAY_NODE="${OPTARG}" ;;
        P) PROC_NODE="${OPTARG}" ;;
        r) RAW_NODE="${OPTARG}" ;;
        k) PLAY_CHANNEL="${OPTARG}" ;;
        R) RATE="${OPTARG}" ;;
        f) FREQ="${OPTARG}" ;;
        l) TONE_DBFS="${OPTARG}" ;;
        d) MIN_DB="${OPTARG}" ;;
        s) SUPPRESS_DB="${OPTARG}" ;;
        b) BURST_TOL_DB="${OPTARG}" ;;
        T) SECONDS_PER_TONE="${OPTARG}" ;;
        x) PREFIX="${OPTARG}" ;;
        *) echo "Usage: $0 [-p play node] [-P processed node] [-r raw node] [-k ch] [-R rate] [-f Hz] [-l dBFS] [-d dB] [-s suppress dB] [-b burst tolerance dB] [-T s] [-x case prefix]" 1>&2; exit 1 ;;
    esac
done

usage() {
    echo "Usage: $0 [-p play node] [-P processed node] [-r raw node] [-k ch] [-R rate] [-f Hz] [-l dBFS] [-d dB] [-s suppress dB] [-b burst tolerance dB] [-T s] [-x case prefix]" 1>&2
    exit 1
}
case "${SECONDS_PER_TONE}" in ''|*[!0-9]*) echo "SECONDS_PER_TONE must be a whole number" 1>&2; usage ;; esac

CASES="raw-tone-detected processed-bursts-match-raw tone-suppressed"
skip_all() {
    local c
    info_msg "$1"
    for c in ${CASES}; do report_skip "${PREFIX}-${c}"; done
    exit 0
}

# best <raw> <skip s> <len s>: analysis line of the louder channel at FREQ
best() {
    audio_analyze "$1" S16_LE "${RATE}" 2 "$2" "$3" "${FREQ}" |
        awk '{ l = $4; sub(/level=/, "", l); if (b == "" || l + 0 > bl) { b = $0; bl = l + 0 } } END { print b }'
}

create_out_dir "${OUTPUT}"
missing="$(audio_tools_missing)"
[ -z "${missing}" ] || skip_all "missing tools: ${missing}"
audio_pw_start "${PLAY_NODE}" "${PROC_NODE}" "${RAW_NODE}" || {
    pw-cli ls Node 2>&1 | grep node.name
    skip_all "PipeWire nodes ${PLAY_NODE} ${PROC_NODE} ${RAW_NODE} not all present"
}
audio_tone "${OUTPUT}/tone.raw" S16_LE "${RATE}" 2 "${PLAY_CHANNEL}" "${FREQ}:${SECONDS_PER_TONE}" "${TONE_DBFS}"
audio_settle
audio_record "${RAW_NODE}" "${OUTPUT}/silence.raw" S16_LE "${RATE}" 2 "$((SECONDS_PER_TONE + 1))" > "${OUTPUT}/silence.log" 2>&1
half="$(awk -v t="${SECONDS_PER_TONE}" 'BEGIN { print 0.5 + t / 2 }')"
hlen="$(awk -v t="${SECONDS_PER_TONE}" 'BEGIN { print t / 2 - 0.5 }')"
segs=""
i=0
while [ "${i}" -lt "$((SECONDS_PER_TONE * 2))" ]; do segs="${segs} ${FREQ}:0.1 0:0.4"; i=$((i + 1)); done
audio_tone "${OUTPUT}/bursts.raw" S16_LE "${RATE}" 2 "${PLAY_CHANNEL}" "${segs}" "${TONE_DBFS}"
sil="$(best "${OUTPUT}/silence.raw" "${half}" "${hlen}")"
echo "silence: ${sil}"

# rec <play file> <node> <out>: record the node while the file plays, then
# let the sink drain; a failed play or capture leaves no output file
rec() {
    audio_play_record "${PLAY_NODE}" "$1" S16_LE "${RATE}" 2 "$2" "$3" S16_LE "${RATE}" 2 "$((SECONDS_PER_TONE + 1))" ||
        { info_msg "play/record on $2 failed"; rm -f "$3"; }
    audio_settle
}
rec "${OUTPUT}/tone.raw" "${RAW_NODE}" "${OUTPUT}/raw.raw"
rec "${OUTPUT}/tone.raw" "${PROC_NODE}" "${OUTPUT}/proc.raw"
rec "${OUTPUT}/bursts.raw" "${RAW_NODE}" "${OUTPUT}/raw-bursts.raw"
rec "${OUTPUT}/bursts.raw" "${PROC_NODE}" "${OUTPUT}/proc-bursts.raw"
raw="$(best "${OUTPUT}/raw.raw" "${half}" "${hlen}")"
proc="$(best "${OUTPUT}/proc.raw" "${half}" "${hlen}")"
rawb="$(best "${OUTPUT}/raw-bursts.raw" "${half}" "${hlen}")"
procb="$(best "${OUTPUT}/proc-bursts.raw" "${half}" "${hlen}")"
echo "raw: ${raw}"
echo "processed: ${proc}"
echo "raw bursts: ${rawb}"
echo "processed bursts: ${procb}"
{ journalctl --no-pager -b -u pipewire -n 40 2>/dev/null; tail -n 40 "${XDG_RUNTIME_DIR}/pipewire.log" 2>/dev/null; } |
    grep -iE "pal|agm|error|fail" | tail -15

rl="$(audio_field "${raw}" level)"; pl="$(audio_field "${proc}" level)"
sl="$(audio_field "${sil}" level)"
rb="$(audio_field "${rawb}" level)"; pb="$(audio_field "${procb}" level)"
if audio_detect "${raw}" "${FREQ}" "${sl:--200}" "${MIN_DB}" 25; then rr=pass; else rr=fail; fi
add_metric "${PREFIX}-raw-tone-detected" "${rr}" "$(awk -v a="${rl:--200}" -v b="${sl:--200}" 'BEGIN { printf "%.1f", a - b }')" dB
db="$(awk -v a="${rb:--200}" -v b="${pb:--200}" 'BEGIN { printf "%.1f", a - b }')"
ro=fail
if [ "${rr}" = pass ] && [ -s "${OUTPUT}/raw-bursts.raw" ] && [ -s "${OUTPUT}/proc-bursts.raw" ] &&
   audio_detect "${rawb}" "${FREQ}" "${sl:--200}" "${MIN_DB}" 25 &&
   audio_detect "${procb}" "${FREQ}" "${sl:--200}" "${MIN_DB}" 25; then
    awk -v d="${db}" -v t="${BURST_TOL_DB}" 'BEGIN { if (d < 0) d = -d; exit !(d <= t) }' && ro=pass
else
    info_msg "bursts not detected on both nodes"
fi
add_metric "${PREFIX}-processed-bursts-match-raw" "${ro}" "${db}" dB
# suppression of the steady tone net of the gain offset the bursts showed
d="$(awk -v a="${rl:--200}" -v b="${pl:--200}" -v o="${db}" 'BEGIN { printf "%.1f", a - b - o }')"
r=fail
[ "${ro}" = pass ] && [ -s "${OUTPUT}/proc.raw" ] && awk -v d="${d}" -v s="${SUPPRESS_DB}" 'BEGIN { exit !(d >= s) }' && r=pass
add_metric "${PREFIX}-tone-suppressed" "${r}" "${d}" dB
exit 0
