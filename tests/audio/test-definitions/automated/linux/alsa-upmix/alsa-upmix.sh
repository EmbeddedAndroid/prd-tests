#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (c) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# Upmixing: a mono stream played to the stereo output (UPMIX_DEV, by
# default the plug layer over the card's PCM) must come out of the
# speaker. Control: a stereo stream with the tone only on CONTROL_CHANNEL
# (a channel that speaker does not reproduce) must come out at least
# SEPARATION_DB lower, so the mono pass needs the mono channel copied onto
# the speaker's channel. Skipped without the card or tools.

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
UPMIX_DEV=""
CONTROL_CHANNEL="1"
RATE="48000"
FREQ="1250"
TONE_DBFS="-6"
MIN_DB="20"
SEPARATION_DB="15"
SECONDS_PER_TONE="3"
WAIT_SECONDS="60"

usage() {
    echo "Usage: $0 [-c card] [-p PCM] [-q PCM] [-m mixer] [-n mixer] [-D upmix dev] [-k control ch]" 1>&2
    echo "          [-R rate] [-f Hz] [-l dBFS] [-d dB] [-s separation dB] [-T s] [-W s]" 1>&2
    exit 1
}

while getopts "c:p:q:m:n:D:k:R:f:l:d:s:T:W:h" o; do
    case "$o" in
        c) CARD="${OPTARG}" ;;
        p) PLAY_PCM="${OPTARG}" ;;
        q) CAPTURE_PCM="${OPTARG}" ;;
        m) PLAY_MIXER="${OPTARG}" ;;
        n) CAPTURE_MIXER="${OPTARG}" ;;
        D) UPMIX_DEV="${OPTARG}" ;;
        k) CONTROL_CHANNEL="${OPTARG}" ;;
        R) RATE="${OPTARG}" ;;
        f) FREQ="${OPTARG}" ;;
        l) TONE_DBFS="${OPTARG}" ;;
        d) MIN_DB="${OPTARG}" ;;
        s) SEPARATION_DB="${OPTARG}" ;;
        T) SECONDS_PER_TONE="${OPTARG}" ;;
        W) WAIT_SECONDS="${OPTARG}" ;;
        h|*) usage ;;
    esac
done

for v in SECONDS_PER_TONE WAIT_SECONDS; do
    case "$(eval echo "\${${v}}")" in
        ''|*[!0-9]*) echo "${v} must be a whole number" 1>&2; usage ;;
    esac
done

skip_all() {
    info_msg "$1"
    report_skip upmix-setup; report_skip upmix-mono-detected; report_skip upmix-control-separation
    exit 0
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
DEV="${UPMIX_DEV:-plughw:${C},${P}}"
mono=""
CAPDEV="hw:${C},${Q}"
info_msg "mono stream to ${DEV}"

if audio_mixer "${C}" "${PLAY_MIXER}" && audio_mixer "${C}" "${CAPTURE_MIXER}" &&
   audio_record "${CAPDEV}" "${OUTPUT}/silence.raw" S16_LE "${RATE}" 2 "$((SECONDS_PER_TONE + 1))"; then
    report_pass upmix-setup
else
    report_fail upmix-setup
fi
audio_tone "${OUTPUT}/mono.raw" S16_LE "${RATE}" 1 all "${FREQ}:${SECONDS_PER_TONE}" "${TONE_DBFS}"
if audio_play_record "${DEV}" "${OUTPUT}/mono.raw" S16_LE "${RATE}" 1 \
        "${CAPDEV}" "${OUTPUT}/mono-cap.raw" S16_LE "${RATE}" 2 "$((SECONDS_PER_TONE + 1))"; then
    # shellcheck disable=SC2046
    set -- $(audio_tone_check "${OUTPUT}/mono-cap.raw" "${FREQ}" 1 "$((SECONDS_PER_TONE - 1))" "${OUTPUT}/silence.raw" "${MIN_DB}")
    r="$1"; [ "$1" = none ] && r=fail
    add_metric upmix-mono-detected "${r}" "$2" dB
    [ "${r}" = pass ] && mono="$2"
else
    report_fail upmix-mono-detected
fi
audio_tone "${OUTPUT}/ctl.raw" S16_LE "${RATE}" 2 "${CONTROL_CHANNEL}" "${FREQ}:${SECONDS_PER_TONE}" "${TONE_DBFS}"
if audio_play_record "${DEV}" "${OUTPUT}/ctl.raw" S16_LE "${RATE}" 2 \
        "${CAPDEV}" "${OUTPUT}/ctl-cap.raw" S16_LE "${RATE}" 2 "$((SECONDS_PER_TONE + 1))"; then
    # shellcheck disable=SC2046
    set -- $(AUDIO_CHECK_ANY=1 audio_tone_check "${OUTPUT}/ctl-cap.raw" "${FREQ}" 1 "$((SECONDS_PER_TONE - 1))" "${OUTPUT}/silence.raw" "${MIN_DB}")
    if [ "$1" = none ]; then
        report_fail upmix-control-separation
    elif [ -z "${mono}" ]; then
        info_msg "mono stream not detected: the separation proves nothing"
        report_skip upmix-control-separation
    else
        sep="$(awk -v m="${mono}" -v c="$2" 'BEGIN { printf "%.1f", m - c }')"
        info_msg "mono ${mono} dB, control $2 dB over silence: separation ${sep} dB"
        r=fail
        awk -v s="${sep}" -v x="${SEPARATION_DB}" 'BEGIN { exit !(s >= x) }' && r=pass
        add_metric upmix-control-separation "${r}" "${sep}" dB
    fi
else
    report_fail upmix-control-separation
fi
exit 0
