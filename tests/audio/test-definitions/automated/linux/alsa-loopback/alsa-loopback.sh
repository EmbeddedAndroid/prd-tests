#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (c) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# Acoustic loopback: play a sine out of each speaker in turn and a short
# stepped sweep, record the microphones at the same time, and check on the
# best microphone channel that the spectral peak is at the played frequency
# and that the level there is a margin above the silence baseline measured
# on the same channel just before. A control run with the output muted
# (MUTE_MIXER, or digital silence for MUTE_MIXER=silence) must NOT be detected, so a pass cannot come from the capture
# path alone. Without the card, the PCMs or the tools the test is skipped
# with the reason; a speaker that never makes it to the microphones fails.

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
# <name>:<1-based playback channel> ...
SPEAKERS="left:1 right:2"
PLAY_CHANNELS="2"
CAPTURE_CHANNELS="2"
RATE="48000"
FREQ="1000"
SWEEP="500 1000 2000 4000"
TONE_DBFS="-12"
MIN_DB="20"
TOLERANCE_HZ="25"
SECONDS_PER_TONE="2"
WAIT_SECONDS="60"

usage() {
    echo "Usage: $0 [-c <card>] [-p <playback PCM>] [-q <capture PCM>]" 1>&2
    echo "          [-m '<ctl>=<v>;...'] [-n '<ctl>=<v>;...'] [-u '<ctl>=<v>;...']" 1>&2
    echo "          [-s '<name>:<ch> ...'] [-P <play ch>] [-C <capture ch>] [-R <rate>]" 1>&2
    echo "          [-f <Hz>] [-w '<Hz> ...'] [-l <dBFS>] [-d <min dB>] [-t <tolerance Hz>]" 1>&2
    echo "          [-T <seconds per tone>] [-W <wait for card s>]" 1>&2
    exit 1
}

while getopts "c:p:q:m:n:u:s:P:C:R:f:w:l:d:t:T:W:h" o; do
    case "$o" in
        c) CARD="${OPTARG}" ;;
        p) PLAY_PCM="${OPTARG}" ;;
        q) CAPTURE_PCM="${OPTARG}" ;;
        m) PLAY_MIXER="${OPTARG}" ;;
        n) CAPTURE_MIXER="${OPTARG}" ;;
        u) MUTE_MIXER="${OPTARG}" ;;
        s) SPEAKERS="${OPTARG}" ;;
        P) PLAY_CHANNELS="${OPTARG}" ;;
        C) CAPTURE_CHANNELS="${OPTARG}" ;;
        R) RATE="${OPTARG}" ;;
        f) FREQ="${OPTARG}" ;;
        w) SWEEP="${OPTARG}" ;;
        l) TONE_DBFS="${OPTARG}" ;;
        d) MIN_DB="${OPTARG}" ;;
        t) TOLERANCE_HZ="${OPTARG}" ;;
        T) SECONDS_PER_TONE="${OPTARG}" ;;
        W) WAIT_SECONDS="${OPTARG}" ;;
        h|*) usage ;;
    esac
done

skip_all() {
    local s
    info_msg "$1"
    report_skip "loopback-setup"
    for s in ${SPEAKERS}; do
        report_skip "loopback-${s%%:*}-${FREQ}hz"
        for f in ${SWEEP}; do report_skip "loopback-${s%%:*}-sweep-${f}hz"; done
    done
    report_skip "loopback-muted-not-detected"
    exit 0
}

# record <case> <tone file or ""> <seconds>: capture while the tone plays
# (or in silence) into ${OUTPUT}/<case>.raw
record() {
    local out="${OUTPUT}/$1.raw"
    if [ -z "$2" ]; then
        audio_record "${CAPDEV}" "${out}" S16_LE "${RATE}" "${CAPTURE_CHANNELS}" "$3" > "${out}.log" 2>&1
        return $?
    fi
    audio_play_record "${PLAYDEV}" "$2" S16_LE "${RATE}" "${PLAY_CHANNELS}" \
        "${CAPDEV}" "${out}" S16_LE "${RATE}" "${CAPTURE_CHANNELS}" "$3"
}

# best <analysis file> <Hz>: the channel line with the highest level at Hz
best() {
    awk -v f="$2" '{ l = $4; sub(/level=/, "", l); if (b == "" || l + 0 > bl) { b = $0; bl = l + 0 } } END { print b }' "$1"
}

# baseline <Hz>: level of the silence capture at Hz on channel <ch>
baseline() {
    audio_field "$(audio_analyze "${OUTPUT}/silence.raw" S16_LE "${RATE}" "${CAPTURE_CHANNELS}" 0.5 \
        "$((SECONDS_PER_TONE))" "$1" | sed -n "${2}p")" level
}

# check <case> <capture raw> <Hz> <skip s> <len s> [expect absent]
check() {
    local a="${OUTPUT}/$1.txt"
    local line ch base
    audio_analyze "$2" S16_LE "${RATE}" "${CAPTURE_CHANNELS}" "$4" "$5" "$3" > "${a}"
    sed "s/^/$1: /" "${a}"
    line="$(best "${a}" "$3")"
    ch="$(audio_field "${line}" ch)"
    base="$(baseline "$3" "${ch}")"
    info_msg "$1: best channel ${ch}, level $(audio_field "${line}" level) dBFS at $3 Hz, peak $(audio_field "${line}" peak) Hz, silence ${base} dBFS"
    m="$(awk -v l="$(audio_field "${line}" level)" -v b="${base}" 'BEGIN { printf "%.1f", l - b }')"
    if [ -n "$6" ] && awk -v r="$(audio_field "${line}" rms)" 'BEGIN { exit !(r + 0 <= -199) }'; then
        # digital zeros: the capture died, the control proves nothing
        info_msg "$1: the capture is digital silence"
        r=fail
    elif audio_detect "${line}" "$3" "${base}" "${MIN_DB}" "${TOLERANCE_HZ}"; then
        r=pass
        [ -n "$6" ] && { info_msg "$1: tone detected with the output muted"; r=fail; }
    else
        r=fail
        [ -n "$6" ] && r=pass
    fi
    [ -z "$6" ] && [ "$3" = "${FREQ}" ] && [ "${r}" = pass ] && POSITIVE=1
    add_metric "$1" "${r}" "${m}" "dB"
}

create_out_dir "${OUTPUT}"
POSITIVE=""
missing="$(audio_tools_missing)"
[ -z "${missing}" ] || skip_all "missing tools: ${missing}"
i=0
while [ -z "$(audio_card "${CARD}")" ] && [ "${i}" -lt "${WAIT_SECONDS}" ]; do
    sleep 1
    i=$((i + 1))
done
C="$(audio_card "${CARD}")"
[ -n "${C}" ] || skip_all "no sound card '${CARD}' after ${WAIT_SECONDS} s"
if [ "${AUDIO_BACKEND}" = pipewire ]; then
    # PLAY_PCM and CAPTURE_PCM name PipeWire nodes
    audio_pw_start "${PLAY_PCM}" "${CAPTURE_PCM}" ||
        skip_all "card ${C}: no PipeWire nodes '${PLAY_PCM}' and '${CAPTURE_PCM}'"
    PLAYDEV="${PLAY_PCM}"
    CAPDEV="${CAPTURE_PCM}"
else
    P="$(audio_pcm "${C}" playback "${PLAY_PCM}")"
    Q="$(audio_pcm "${C}" capture "${CAPTURE_PCM}")"
    [ -n "${P}" ] && [ -n "${Q}" ] || skip_all "card ${C}: no playback PCM '${PLAY_PCM}' (${P}) or capture PCM '${CAPTURE_PCM}' (${Q})"
    PLAYDEV="hw:${C},${P}"
    CAPDEV="hw:${C},${Q}"
fi
info_msg "card ${C} (${AUDIO_BACKEND}): playback ${PLAYDEV}, capture ${CAPDEV}, ${CAPTURE_CHANNELS} channels"

if audio_mixer "${C}" "${PLAY_MIXER}" && audio_mixer "${C}" "${CAPTURE_MIXER}" &&
   record silence "" "$((SECONDS_PER_TONE + 1))"; then
    report_pass "loopback-setup"
else
    cat "${OUTPUT}/silence.raw.log" 2>/dev/null
    report_fail "loopback-setup"
fi
audio_analyze "${OUTPUT}/silence.raw" S16_LE "${RATE}" "${CAPTURE_CHANNELS}" 0.5 "${SECONDS_PER_TONE}" "${FREQ}" |
    sed 's/^/silence: /'

segs=""
for f in ${SWEEP}; do segs="${segs} ${f}:${SECONDS_PER_TONE}"; done
for s in ${SPEAKERS}; do
    n="${s%%:*}"
    ch="${s##*:}"
    audio_tone "${OUTPUT}/tone.raw" S16_LE "${RATE}" "${PLAY_CHANNELS}" "${ch}" "${FREQ}:${SECONDS_PER_TONE}" "${TONE_DBFS}"
    if record "${n}-tone" "${OUTPUT}/tone.raw" "$((SECONDS_PER_TONE + 1))"; then
        check "loopback-${n}-${FREQ}hz" "${OUTPUT}/${n}-tone.raw" "${FREQ}" 1 "$((SECONDS_PER_TONE - 1))"
    else
        report_fail "loopback-${n}-${FREQ}hz"
    fi
    audio_tone "${OUTPUT}/sweep.raw" S16_LE "${RATE}" "${PLAY_CHANNELS}" "${ch}" "${segs}" "${TONE_DBFS}"
    nseg=0
    for f in ${SWEEP}; do nseg=$((nseg + 1)); done
    if record "${n}-sweep" "${OUTPUT}/sweep.raw" "$((nseg * SECONDS_PER_TONE + 1))"; then
        k=0
        for f in ${SWEEP}; do
            # playback starts 0.5 s into the capture; analyse the middle of each step
            check "loopback-${n}-sweep-${f}hz" "${OUTPUT}/${n}-sweep.raw" "${f}" \
                "$(awk -v k="${k}" -v t="${SECONDS_PER_TONE}" 'BEGIN { print 0.5 + k * t + t / 4 }')" \
                "$(awk -v t="${SECONDS_PER_TONE}" 'BEGIN { print t / 2 }')"
            k=$((k + 1))
        done
    else
        for f in ${SWEEP}; do report_fail "loopback-${n}-sweep-${f}hz"; done
    fi
done

# Control: the same tone on every channel with the output muted, or with
# MUTE_MIXER=silence (a path whose gains userspace owns) digital silence
# of the same length played the same way.
if [ -n "${MUTE_MIXER}" ] && [ -z "${POSITIVE}" ]; then
    info_msg "loopback-muted-not-detected: no positive case passed, the control proves nothing"
    report_skip "loopback-muted-not-detected"
elif [ -n "${MUTE_MIXER}" ]; then
    audio_tone "${OUTPUT}/tone.raw" S16_LE "${RATE}" "${PLAY_CHANNELS}" all "${FREQ}:${SECONDS_PER_TONE}" "${TONE_DBFS}"
    mute_file="${OUTPUT}/tone.raw"
    mute_ctl="${MUTE_MIXER}"
    if [ "${MUTE_MIXER}" = silence ]; then
        mute_file="${OUTPUT}/zero.raw"
        mute_ctl=""
        head -c "$(wc -c < "${OUTPUT}/tone.raw")" /dev/zero > "${mute_file}"
    fi
    if audio_mixer "${C}" "${mute_ctl}" && record muted "${mute_file}" "$((SECONDS_PER_TONE + 1))"; then
        check "loopback-muted-not-detected" "${OUTPUT}/muted.raw" "${FREQ}" 1 "$((SECONDS_PER_TONE - 1))" absent
    else
        report_fail "loopback-muted-not-detected"
    fi
    audio_mixer "${C}" "${PLAY_MIXER}"
else
    info_msg "no MUTE_MIXER: no muted control"
    report_skip "loopback-muted-not-detected"
fi
exit 0
