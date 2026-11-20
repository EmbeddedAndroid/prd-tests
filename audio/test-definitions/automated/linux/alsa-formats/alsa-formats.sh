#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (c) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# Sample rates, formats and channel counts, checked at the signal level.
# For every rate x format x channel count of the lists:
#   native-<dir>-<combo>: the hw: PCM either supports the combination (its
#     hardware parameter ranges contain it) and then a sine played or
#     recorded with it must be found at the right frequency, or it refuses
#     it. A combination listed in NATIVE_PLAYBACK/NATIVE_CAPTURE must be
#     supported; any other must be refused or work: accepting it and
#     playing it at another rate (wrong pitch) fails.
#   plug-<dir>-<combo>: the same through plughw:, where alsa-lib converts to
#     what the PCM supports; the sine must be found at the right frequency.
# Playback is checked with a 48 kHz S16_LE microphone capture, capture with
# a 48 kHz S16_LE sine on the speakers. concurrent-playback: two playback
# PCMs mixed into one output play two sines at once and both must be found.
# Skipped with the reason when the card, a PCM or a tool is absent.

# shellcheck disable=SC1091
. ../../lib/sh-test-lib
# shellcheck disable=SC1091
. ../../lib/audio/audio.sh
OUTPUT="$(pwd)/output"
RESULT_FILE="${OUTPUT}/result.txt"
export RESULT_FILE

CARD=""
PLAY_PCM=""
PLAY_PCM2=""
CAPTURE_PCM=""
PLAY_MIXER=""
PLAY_MIXER2=""
CAPTURE_MIXER=""
CAPTURE_CHANNELS="2"
RATES="8000 16000 44100 48000 96000"
FORMATS="S16_LE S24_LE"
CHANNELS="1 2"
CAPTURE_RATES="8000 16000 48000"
# <rate>:<format>:<channels> the hw: PCMs must support
NATIVE_PLAYBACK="48000:S16_LE:2"
NATIVE_CAPTURE="48000:S16_LE:2"
FREQ="1000"
FREQ2="2500"
TONE_DBFS="-6"
MIN_DB="15"
TOLERANCE_HZ="25"
SECONDS_PER_TONE="2"
WAIT_SECONDS="60"

usage() {
    echo "Usage: $0 [-c <card>] [-p <playback PCM>] [-o <second playback PCM>] [-q <capture PCM>]" 1>&2
    echo "          [-m '<ctl>=<v>;...'] [-k '<ctl>=<v>;...' (second PCM)] [-n '<ctl>=<v>;...']" 1>&2
    echo "          [-C <capture ch>] [-r '<Hz> ...'] [-F '<fmt> ...'] [-H '<ch> ...'] [-x '<capture Hz> ...']" 1>&2
    echo "          [-N '<rate>:<fmt>:<ch> ...' (playback)] [-M '<rate>:<fmt>:<ch> ...' (capture)]" 1>&2
    echo "          [-f <Hz>] [-g <Hz>] [-l <dBFS>] [-d <dB>] [-t <Hz>] [-T <s>] [-W <s>]" 1>&2
    exit 1
}

while getopts "c:p:o:q:m:k:n:C:r:F:H:x:N:M:f:g:l:d:t:T:W:h" o; do
    case "$o" in
        c) CARD="${OPTARG}" ;;
        p) PLAY_PCM="${OPTARG}" ;;
        o) PLAY_PCM2="${OPTARG}" ;;
        q) CAPTURE_PCM="${OPTARG}" ;;
        m) PLAY_MIXER="${OPTARG}" ;;
        k) PLAY_MIXER2="${OPTARG}" ;;
        n) CAPTURE_MIXER="${OPTARG}" ;;
        C) CAPTURE_CHANNELS="${OPTARG}" ;;
        r) RATES="${OPTARG}" ;;
        F) FORMATS="${OPTARG}" ;;
        H) CHANNELS="${OPTARG}" ;;
        x) CAPTURE_RATES="${OPTARG}" ;;
        N) NATIVE_PLAYBACK="${OPTARG}" ;;
        M) NATIVE_CAPTURE="${OPTARG}" ;;
        f) FREQ="${OPTARG}" ;;
        g) FREQ2="${OPTARG}" ;;
        l) TONE_DBFS="${OPTARG}" ;;
        d) MIN_DB="${OPTARG}" ;;
        t) TOLERANCE_HZ="${OPTARG}" ;;
        T) SECONDS_PER_TONE="${OPTARG}" ;;
        W) WAIT_SECONDS="${OPTARG}" ;;
        h|*) usage ;;
    esac
done

cases() {
    local r f c
    for r in ${RATES}; do for f in ${FORMATS}; do for c in ${CHANNELS}; do
        echo "native-playback-${r}-${f}-${c}ch plug-playback-${r}-${f}-${c}ch"
    done; done; done
    for r in ${CAPTURE_RATES}; do for f in ${FORMATS}; do for c in ${CHANNELS}; do
        echo "native-capture-${r}-${f}-${c}ch plug-capture-${r}-${f}-${c}ch"
    done; done; done
    echo "concurrent-playback"
}

skip_all() {
    local t
    info_msg "$1"
    report_skip "formats-setup"
    for t in $(cases); do report_skip "${t}"; done
    exit 0
}

best() {
    awk '{ l = $4; sub(/level=/, "", l); if (b == "" || l + 0 > bl) { b = $0; bl = l + 0 } } END { print b }' "$1"
}

# base <Hz>: best-channel silence level at Hz
base() {
    audio_analyze "${OUTPUT}/silence.raw" S16_LE 48000 "${CAPTURE_CHANNELS}" 0.5 "${SECONDS_PER_TONE}" "$1" > "${OUTPUT}/base.txt"
    audio_field "$(best "${OUTPUT}/base.txt")" level
}

# judge <case> <analysis file> <Hz> <baseline>: report on the best channel
judge() {
    local line m
    line="$(best "$2")"
    m="$(awk -v l="$(audio_field "${line}" level)" -v b="$4" 'BEGIN { printf "%.1f", l - b }')"
    info_msg "$1: ${line} (silence $4 dBFS)"
    if audio_detect "${line}" "$3" "$4" "${MIN_DB}" "${TOLERANCE_HZ}"; then
        add_metric "$1" pass "${m}" "dB"
    else
        add_metric "$1" fail "${m}" "dB"
    fi
}

# hw_ranges <playback|capture> <device>: "<rates> | <formats> | <channels>"
# as the hw: PCM reports them (--dump-hw-params)
hw_ranges() {
    local tool=aplay out=/dev/zero
    [ "$1" = capture ] && { tool=arecord; out=/dev/null; }
    "${tool}" -D "hw:${C},$2" --dump-hw-params -d 1 "${out}" 2>&1 |
        awk -F': ' '/^RATE:/ { r = $2 } /^FORMAT:/ { f = $2 } /^CHANNELS:/ { c = $2 }
            END { print r " | " f " | " c }'
}

# in_range <value> <range>: the value is in an ALSA interval "[a b]",
# "(a b]", a list "a b c" or a single value
in_range() {
    echo "$2" | awk -v v="$1" '{
        s = $0; gsub(/[][()]/, " ", s); n = split(s, a, " ")
        if ($0 ~ /^[[(]/ && n == 2) { exit !(v + 0 >= a[1] + 0 && v + 0 <= a[2] + 0) }
        for (i = 1; i <= n; i++) if (a[i] == v) exit 0
        exit 1 }'
}

# supported <dir> <rate> <format> <channels>
supported() {
    local r
    r="$(hw_ranges "$1" "$([ "$1" = playback ] && echo "${P}" || echo "${Q}")")"
    in_range "$2" "${r%% | *}" && echo "${r}" | awk -F' [|] ' -v f="$3" '{ exit !(index(" " $2 " ", " " f " ")) }' &&
        in_range "$4" "${r##* | }"
}

# play_case <case> <device> <rate> <format> <channels>
play_case() {
    audio_tone "${OUTPUT}/tone.raw" "$4" "$3" "$5" all "${FREQ}:${SECONDS_PER_TONE}" "${TONE_DBFS}"
    if audio_play_record "$2" "${OUTPUT}/tone.raw" "$4" "$3" "$5" \
           "hw:${C},${Q}" "${OUTPUT}/$1.raw" S16_LE 48000 "${CAPTURE_CHANNELS}" "$((SECONDS_PER_TONE + 1))"; then
        audio_analyze "${OUTPUT}/$1.raw" S16_LE 48000 "${CAPTURE_CHANNELS}" 1 "$((SECONDS_PER_TONE - 1))" "${FREQ}" > "${OUTPUT}/$1.txt"
        judge "$1" "${OUTPUT}/$1.txt" "${FREQ}" "${B1}"
    else
        info_msg "$1: playback or capture failed"
        report_fail "$1"
    fi
}

# capture_case <case> <device> <rate> <format> <channels>
capture_case() {
    if audio_play_record "hw:${C},${P}" "${OUTPUT}/tone48.raw" S16_LE 48000 2 \
           "$2" "${OUTPUT}/$1.raw" "$4" "$3" "$5" "$((SECONDS_PER_TONE + 1))"; then
        audio_analyze "${OUTPUT}/$1.raw" "$4" "$3" "$5" 1 "$((SECONDS_PER_TONE - 1))" "${FREQ}" > "${OUTPUT}/$1.txt"
        judge "$1" "${OUTPUT}/$1.txt" "${FREQ}" "${B1}"
    else
        info_msg "$1: playback or capture failed"
        report_fail "$1"
    fi
}

# combo <dir> <rate> <format> <channels>: the native and the plug case
combo() {
    local t="$1-$2-$3-$4ch" want=0 dev
    case " $([ "$1" = playback ] && echo "${NATIVE_PLAYBACK}" || echo "${NATIVE_CAPTURE}") " in
        *" $2:$3:$4 "*) want=1 ;;
    esac
    dev="$([ "$1" = playback ] && echo "${P}" || echo "${Q}")"
    if supported "$1" "$2" "$3" "$4"; then
        info_msg "native-${t}: supported by hw:${C},${dev} (expected: ${want})"
        if [ "$1" = playback ]; then play_case "native-${t}" "hw:${C},${dev}" "$2" "$3" "$4"
        else capture_case "native-${t}" "hw:${C},${dev}" "$2" "$3" "$4"; fi
    elif [ "${want}" = 1 ]; then
        info_msg "native-${t}: refused by hw:${C},${dev} ($(hw_ranges "$1" "${dev}")), but expected"
        report_fail "native-${t}"
    else
        info_msg "native-${t}: refused by hw:${C},${dev} ($(hw_ranges "$1" "${dev}"))"
        report_pass "native-${t}"
    fi
    if [ "$1" = playback ]; then play_case "plug-${t}" "plughw:${C},${dev}" "$2" "$3" "$4"
    else capture_case "plug-${t}" "plughw:${C},${dev}" "$2" "$3" "$4"; fi
}

create_out_dir "${OUTPUT}"
missing="$(audio_tools_missing)"
[ -z "${missing}" ] || skip_all "missing tools: ${missing}"
i=0
while [ -z "$(audio_card "${CARD}")" ] && [ "${i}" -lt "${WAIT_SECONDS}" ]; do sleep 1; i=$((i + 1)); done
C="$(audio_card "${CARD}")"
[ -n "${C}" ] || skip_all "no sound card '${CARD}' after ${WAIT_SECONDS} s"
P="$(audio_pcm "${C}" playback "${PLAY_PCM}")"
Q="$(audio_pcm "${C}" capture "${CAPTURE_PCM}")"
[ -n "${P}" ] && [ -n "${Q}" ] || skip_all "card ${C}: no playback PCM '${PLAY_PCM}' or capture PCM '${CAPTURE_PCM}'"
info_msg "playback hw:${C},${P}: $(hw_ranges playback "${P}")"
info_msg "capture hw:${C},${Q}: $(hw_ranges capture "${Q}")"

if audio_mixer "${C}" "${PLAY_MIXER}" && audio_mixer "${C}" "${CAPTURE_MIXER}" &&
   arecord -q -D "hw:${C},${Q}" -t raw -f S16_LE -r 48000 -c "${CAPTURE_CHANNELS}" \
       -d "$((SECONDS_PER_TONE + 1))" "${OUTPUT}/silence.raw" > "${OUTPUT}/silence.log" 2>&1; then
    report_pass "formats-setup"
else
    cat "${OUTPUT}/silence.log"
    report_fail "formats-setup"
fi
B1="$(base "${FREQ}")"
B2="$(base "${FREQ2}")"
audio_tone "${OUTPUT}/tone48.raw" S16_LE 48000 2 all "${FREQ}:$((SECONDS_PER_TONE + 1))" "${TONE_DBFS}"

for r in ${RATES}; do for f in ${FORMATS}; do for c in ${CHANNELS}; do
    combo playback "${r}" "${f}" "${c}"
done; done; done
for r in ${CAPTURE_RATES}; do for f in ${FORMATS}; do for c in ${CHANNELS}; do
    combo capture "${r}" "${f}" "${c}"
done; done; done

# Two PCMs into one output: FREQ on the first, FREQ2 on the second.
P2=""
[ -n "${PLAY_PCM2}" ] && P2="$(audio_pcm "${C}" playback "${PLAY_PCM2}")"
if [ -z "${P2}" ]; then
    info_msg "no second playback PCM '${PLAY_PCM2}'"
    report_skip "concurrent-playback"
    exit 0
fi
audio_mixer "${C}" "${PLAY_MIXER2}"
audio_tone "${OUTPUT}/tone2.raw" S16_LE 48000 2 all "${FREQ2}:$((SECONDS_PER_TONE + 1))" "${TONE_DBFS}"
aplay -q -D "hw:${C},${P2}" -t raw -f S16_LE -r 48000 -c 2 "${OUTPUT}/tone2.raw" > "${OUTPUT}/tone2.log" 2>&1 &
p2=$!
if audio_play_record "hw:${C},${P}" "${OUTPUT}/tone48.raw" S16_LE 48000 2 \
       "hw:${C},${Q}" "${OUTPUT}/concurrent.raw" S16_LE 48000 "${CAPTURE_CHANNELS}" "$((SECONDS_PER_TONE + 1))" &&
   wait "${p2}"; then
    audio_analyze "${OUTPUT}/concurrent.raw" S16_LE 48000 "${CAPTURE_CHANNELS}" 1 "$((SECONDS_PER_TONE - 1))" "${FREQ}" > "${OUTPUT}/conc1.txt"
    audio_analyze "${OUTPUT}/concurrent.raw" S16_LE 48000 "${CAPTURE_CHANNELS}" 1 "$((SECONDS_PER_TONE - 1))" "${FREQ2}" > "${OUTPUT}/conc2.txt"
    l1="$(audio_field "$(best "${OUTPUT}/conc1.txt")" level)"
    l2="$(audio_field "$(best "${OUTPUT}/conc2.txt")" level)"
    info_msg "concurrent: ${FREQ} Hz ${l1} dBFS (silence ${B1}), ${FREQ2} Hz ${l2} dBFS (silence ${B2})"
    if awk -v a="${l1}" -v b="${B1}" -v c="${l2}" -v d="${B2}" -v m="${MIN_DB}" \
           'BEGIN { exit !(a - b >= m && c - d >= m) }'; then
        report_pass "concurrent-playback"
    else
        report_fail "concurrent-playback"
    fi
else
    kill "${p2}" 2>/dev/null
    cat "${OUTPUT}/tone2.log"
    report_fail "concurrent-playback"
fi
exit 0
