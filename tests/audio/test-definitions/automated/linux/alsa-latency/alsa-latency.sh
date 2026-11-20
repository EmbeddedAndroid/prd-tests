#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (c) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# Low latency streams: playback and capture run together with a small
# period (PERIOD_FRAMES, BUFFER_PERIODS periods per buffer) for DURATION.
# The period the driver grants in each direction must be at most
# MAX_PERIOD_MS, neither stream may report an xrun and the played tone
# must be detected in the capture, so a pass needs both streams moving
# data at that period. A reference run at the default period (same
# streams, same mixer) isolates a failure to the small period; a control
# with the speaker muted must not be detected. The round-trip
# latency (speaker to microphone, including the acoustic path) is measured
# with alsabat --roundtriplatency when alsabat is installed and reported as
# a measurement; LATENCY_MAX_MS, when set, makes it a pass/fail limit.

# shellcheck disable=SC1091
. ../../lib/sh-test-lib
# shellcheck disable=SC1091
. ../../lib/audio/audio.sh
OUTPUT="$(pwd)/output"
RESULT_FILE="${OUTPUT}/result.txt"
export RESULT_FILE

CARD=""
PLAY_PCM=""
CAPTURE_PCM=""
PLAY_MIXER=""
CAPTURE_MIXER=""
MUTE_MIXER=""
PERIOD_FRAMES="240"
BUFFER_PERIODS="2"
DURATION="10"
PLAY_CHANNEL="2"
RATE="48000"
FREQ="1250"
TONE_DBFS="-6"
MIN_DB="20"
LATENCY_MAX_MS=""
MAX_PERIOD_MS="10"
WAIT_SECONDS="60"

usage() {
    echo "Usage: $0 [-c card] [-p PCM] [-q PCM] [-m mixer] [-n mixer] [-u mute mixer] [-F period frames]" 1>&2
    echo "          [-B periods] [-S s] [-k ch] [-R rate] [-f Hz] [-l dBFS] [-d dB] [-L max ms] [-P max period ms] [-W s]" 1>&2
    exit 1
}

while getopts "c:p:q:m:n:u:F:B:S:k:R:f:l:d:L:P:W:h" o; do
    case "$o" in
        c) CARD="${OPTARG}" ;;
        p) PLAY_PCM="${OPTARG}" ;;
        q) CAPTURE_PCM="${OPTARG}" ;;
        m) PLAY_MIXER="${OPTARG}" ;;
        n) CAPTURE_MIXER="${OPTARG}" ;;
        u) MUTE_MIXER="${OPTARG}" ;;
        F) PERIOD_FRAMES="${OPTARG}" ;;
        B) BUFFER_PERIODS="${OPTARG}" ;;
        S) DURATION="${OPTARG}" ;;
        k) PLAY_CHANNEL="${OPTARG}" ;;
        R) RATE="${OPTARG}" ;;
        f) FREQ="${OPTARG}" ;;
        l) TONE_DBFS="${OPTARG}" ;;
        d) MIN_DB="${OPTARG}" ;;
        L) LATENCY_MAX_MS="${OPTARG}" ;;
        P) MAX_PERIOD_MS="${OPTARG}" ;;
        W) WAIT_SECONDS="${OPTARG}" ;;
        h|*) usage ;;
    esac
done

for v in DURATION PERIOD_FRAMES BUFFER_PERIODS WAIT_SECONDS; do
    case "$(eval echo "\${${v}}")" in
        ''|*[!0-9]*) echo "${v} must be a whole number" 1>&2; usage ;;
    esac
done
CASES="lowlatency-setup lowlatency-reference-detected lowlatency-playback-period lowlatency-capture-period lowlatency-playback-no-xrun lowlatency-capture-no-xrun lowlatency-tone-detected lowlatency-muted-not-detected roundtrip-latency"
skip_all() {
    local c
    info_msg "$1"
    for c in ${CASES}; do report_skip "${c}"; done
    exit 0
}

# ll <case>: play tone.raw and record <case>.raw together at the small period
ll() {
    local rpid prc rrc
    arecord -v -D "${CAPDEV}" -t raw -f S16_LE -r "${RATE}" -c 2 --period-size="${PERIOD_FRAMES}" \
        --buffer-size="$((PERIOD_FRAMES * BUFFER_PERIODS))" -d "$((DURATION + 1))" "${OUTPUT}/$1.raw" \
        > "${OUTPUT}/$1.arecord.log" 2>&1 &
    rpid=$!
    sleep 0.5
    aplay -v -D "${PLAYDEV}" -t raw -f S16_LE -r "${RATE}" -c 2 --period-size="${PERIOD_FRAMES}" \
        --buffer-size="$((PERIOD_FRAMES * BUFFER_PERIODS))" "${OUTPUT}/tone.raw" > "${OUTPUT}/$1.aplay.log" 2>&1
    prc=$?
    wait "${rpid}"
    rrc=$?
    grep -E "period_size|buffer_size" "${OUTPUT}/$1.aplay.log" | sed "s/^/$1 aplay: /"
    grep -E "period_size|buffer_size" "${OUTPUT}/$1.arecord.log" | sed "s/^/$1 arecord: /"
    echo "$1: aplay rc ${prc}, arecord rc ${rrc}"
    [ "${prc}" -eq 0 ] && [ "${rrc}" -eq 0 ]
}


AUDIO_RATE="${RATE}"
create_out_dir "${OUTPUT}"
missing="$(audio_tools_missing)"
[ -z "${missing}" ] || skip_all "missing tools: ${missing}"
C="$(audio_wait_card "${CARD}" "${WAIT_SECONDS}")"
[ -n "${C}" ] || skip_all "no sound card '${CARD}' after ${WAIT_SECONDS} s"
P="$(audio_pcm "${C}" playback "${PLAY_PCM}")"
Q="$(audio_pcm "${C}" capture "${CAPTURE_PCM}")"
[ -n "${P}" ] && [ -n "${Q}" ] || skip_all "card ${C}: no playback PCM '${PLAY_PCM}' or capture PCM '${CAPTURE_PCM}'"
PLAYDEV="hw:${C},${P}"
CAPDEV="hw:${C},${Q}"
info_msg "period ${PERIOD_FRAMES} frames ($(awk -v p="${PERIOD_FRAMES}" -v r="${RATE}" 'BEGIN { printf "%.2f", p * 1000 / r }') ms), ${BUFFER_PERIODS} periods"

if audio_mixer "${C}" "${PLAY_MIXER}" && audio_mixer "${C}" "${CAPTURE_MIXER}" &&
   audio_record "${CAPDEV}" "${OUTPUT}/silence.raw" S16_LE "${RATE}" 2 3; then
    report_pass "lowlatency-setup"
else
    report_fail "lowlatency-setup"
fi
audio_tone "${OUTPUT}/tone.raw" S16_LE "${RATE}" 2 "${PLAY_CHANNEL}" "${FREQ}:${DURATION}" "${TONE_DBFS}"
if audio_play_record "${PLAYDEV}" "${OUTPUT}/tone.raw" S16_LE "${RATE}" 2 \
        "${CAPDEV}" "${OUTPUT}/ref.raw" S16_LE "${RATE}" 2 "$((DURATION + 1))"; then
    # shellcheck disable=SC2046
    set -- $(audio_tone_check "${OUTPUT}/ref.raw" "${FREQ}" 1.5 "$((DURATION - 2))" "${OUTPUT}/silence.raw" "${MIN_DB}")
    r="$1"; [ "$1" = none ] && r=fail
    add_metric "lowlatency-reference-detected" "${r}" "$2" dB
else
    report_fail "lowlatency-reference-detected"
fi
ok=0
ll run || ok=1
for d in aplay:playback arecord:capture; do
    f="$(sed -n 's/^ *period_size *: *\([0-9]*\).*/\1/p' "${OUTPUT}/run.${d%%:*}.log" | head -1)"
    ms="$(awk -v f="${f:-0}" -v r="${RATE}" 'BEGIN { printf "%.2f", f * 1000 / r }')"
    info_msg "${d##*:}: granted period ${f:-none} frames (${ms} ms)"
    r=fail
    [ -n "${f}" ] && awk -v m="${ms}" -v x="${MAX_PERIOD_MS}" 'BEGIN { exit !(m <= x) }' && r=pass
    add_metric "lowlatency-${d##*:}-period" "${r}" "${ms}" ms
done
if [ "${ok}" -eq 0 ] && ! grep -qiE "underrun|xrun" "${OUTPUT}/run.aplay.log"; then
    report_pass "lowlatency-playback-no-xrun"
else
    grep -iE "underrun|xrun|error" "${OUTPUT}/run.aplay.log" | head -5
    report_fail "lowlatency-playback-no-xrun"
fi
if [ "${ok}" -eq 0 ] && ! grep -qiE "overrun|xrun" "${OUTPUT}/run.arecord.log"; then
    report_pass "lowlatency-capture-no-xrun"
else
    grep -iE "overrun|xrun|error" "${OUTPUT}/run.arecord.log" | head -5
    report_fail "lowlatency-capture-no-xrun"
fi
# shellcheck disable=SC2046
set -- $(audio_tone_check "${OUTPUT}/run.raw" "${FREQ}" 1.5 "$((DURATION - 2))" "${OUTPUT}/silence.raw" "${MIN_DB}")
rt="$1"; [ "$1" = none ] || [ "${ok}" -ne 0 ] && rt=fail
add_metric "lowlatency-tone-detected" "${rt}" "$2" dB
if [ -n "${MUTE_MIXER}" ]; then
    audio_mixer "${C}" "${MUTE_MIXER}"
    if ll muted; then
        m="$(AUDIO_CHECK_ANY=1 audio_tone_check "${OUTPUT}/muted.raw" "${FREQ}" 1.5 "$((DURATION - 2))" "${OUTPUT}/silence.raw" "${MIN_DB}")"
    else
        m="none -200"
    fi
    audio_report_control "lowlatency-muted-not-detected" "${rt}" "${m}"
    audio_mixer "${C}" "${PLAY_MIXER}"
else
    report_skip "lowlatency-muted-not-detected"
fi

if command -v alsabat > /dev/null 2>&1; then
    alsabat -P "${PLAYDEV}" -C "${CAPDEV}" -r "${RATE}" -c 2 -f S16_LE --roundtriplatency > "${OUTPUT}/bat.log" 2>&1
    brc=$?
    tail -20 "${OUTPUT}/bat.log"
    # "... latency: <n>ms" or "... = <n> ms", depending on the alsa-utils version
    ms="$(sed -n 's/.*[Ll]atency[^0-9]*[:=] *\([0-9.]*\) *ms.*/\1/p' "${OUTPUT}/bat.log" | tail -1)"
    if [ "${brc}" -eq 0 ] && [ -n "${ms}" ]; then
        r=pass
        [ -n "${LATENCY_MAX_MS}" ] && awk -v m="${ms}" -v x="${LATENCY_MAX_MS}" 'BEGIN { exit !(m > x) }' && r=fail
        add_metric "roundtrip-latency" "${r}" "${ms}" ms
    else
        info_msg "alsabat rc ${brc}, no latency figure"
        report_fail "roundtrip-latency"
    fi
else
    info_msg "alsabat not installed: no round-trip latency figure"
    report_skip "roundtrip-latency"
fi
exit 0
