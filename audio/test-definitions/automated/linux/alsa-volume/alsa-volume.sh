#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (c) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# Volume control at the signal level: a sine plays out of the speaker while
# the microphones record, once per value of the volume controls. The level
# at the tone frequency must follow the volume: every step must change it by
# the expected amount (STEP_DB per control unit) within TOL_DB. A control
# run at MUTE_VALUE must not be detected, so a pass cannot come from a
# capture path that ignores the control. With AUDIO_BACKEND=pipewire the
# PCMs are PipeWire nodes, VOLUMES are dB (0 is unity) applied as the
# playback node's channelVolumes and MUTE_VALUE is ignored: the node is
# muted instead. Skipped with the reason without the card, the PCMs or
# the tools.

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
# controls set to each value, separated by ';'
VOLUME_CTLS=""
VOLUMES="81 71 61"
MUTE_VALUE="0"
STEP_DB="1"
TOL_DB="4"
PLAY_CHANNEL="2"
RATE="48000"
FREQ="1250"
TONE_DBFS="-6"
MIN_DB="20"
SECONDS_PER_TONE="2"
WAIT_SECONDS="60"

usage() {
    echo "Usage: $0 [-c <card>] [-p <playback PCM>] [-q <capture PCM>] [-m <play mixer>] [-n <capture mixer>]" 1>&2
    echo "          [-v '<ctl>;...'] [-V '<value> ...'] [-z <mute value>] [-s <dB per unit>] [-e <tolerance dB>]" 1>&2
    echo "          [-k <play channel>] [-R <rate>] [-f <Hz>] [-l <dBFS>] [-d <min dB>] [-T <s>] [-W <s>]" 1>&2
    exit 1
}

while getopts "c:p:q:m:n:v:V:z:s:e:k:R:f:l:d:T:W:h" o; do
    case "$o" in
        c) CARD="${OPTARG}" ;;
        p) PLAY_PCM="${OPTARG}" ;;
        q) CAPTURE_PCM="${OPTARG}" ;;
        m) PLAY_MIXER="${OPTARG}" ;;
        n) CAPTURE_MIXER="${OPTARG}" ;;
        v) VOLUME_CTLS="${OPTARG}" ;;
        V) VOLUMES="${OPTARG}" ;;
        z) MUTE_VALUE="${OPTARG}" ;;
        s) STEP_DB="${OPTARG}" ;;
        e) TOL_DB="${OPTARG}" ;;
        k) PLAY_CHANNEL="${OPTARG}" ;;
        R) RATE="${OPTARG}" ;;
        f) FREQ="${OPTARG}" ;;
        l) TONE_DBFS="${OPTARG}" ;;
        d) MIN_DB="${OPTARG}" ;;
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
    local v p
    info_msg "$1"
    report_skip "volume-setup"
    p=""
    for v in ${VOLUMES}; do
        report_skip "volume-${v}-detected"
        [ -n "${p}" ] && report_skip "volume-step-${p}-${v}"
        p="${v}"
    done
    report_skip "volume-muted-not-detected"
    exit 0
}

# pw_node_id <node name>: PipeWire object id of the node
pw_node_id() {
    pw-cli ls Node 2>/dev/null | awk -v n="$1" '/^\tid [0-9]+,/ { id = $2; sub(/,/, "", id) }
        index($0, "node.name = \"" n "\"") { print id; exit }'
}

# set_volume <value>: every control of VOLUME_CTLS to value (pipewire: the
# playback node to value dB, or muted for "mute")
set_volume() {
    local s="" c
    if [ "${AUDIO_BACKEND}" = pipewire ]; then
        if [ "$1" = mute ]; then
            pw-cli s "${NODE_ID}" Props '{ mute: true }'
        else
            # whole dB only: plain awk, no math library needed
            s="$(awk -v d="$1" 'BEGIN { r = 1; for (i = 0; i < -d; i++) r /= 1.1220184543; for (i = 0; i < d; i++) r *= 1.1220184543; printf "%.6f", r }')"
            pw-cli s "${NODE_ID}" Props "{ mute: false, channelVolumes: [ ${s}, ${s} ] }"
        fi
        return
    fi
    local old_ifs="${IFS}"
    IFS=';'
    for c in ${VOLUME_CTLS}; do s="${s}${c}=$1;"; done
    IFS="${old_ifs}"
    audio_mixer "${C}" "${s}"
}

# level <raw> <channel>: dBFS at FREQ on that channel over the second half
# of the tone (at least from 1 s): sinks that take a second or more to
# start (PAL deep buffer) must not dilute the level
level() {
    audio_field "$(audio_analyze "$1" S16_LE "${RATE}" 2 "${SKIP}" "${LEN}" "${FREQ}" | sed -n "$2p")" level
}

AUDIO_RATE="${RATE}"
# the tone plays from 0.5 s to SECONDS_PER_TONE + 0.5 s of each recording
SKIP="$(awk -v t="${SECONDS_PER_TONE}" 'BEGIN { s = 0.5 + t / 2; if (s < 1) s = 1; print s }')"
LEN="$(awk -v t="${SECONDS_PER_TONE}" -v s="${SKIP}" 'BEGIN { print t + 0.5 - s }')"
create_out_dir "${OUTPUT}"
missing="$(audio_tools_missing)"
[ -z "${missing}" ] || skip_all "missing tools: ${missing}"
[ -n "${VOLUME_CTLS}" ] || [ "${AUDIO_BACKEND}" = pipewire ] || skip_all "no VOLUME_CTLS"
if [ "${AUDIO_BACKEND}" = pipewire ]; then
    MUTE_VALUE=mute
    for v in ${VOLUMES}; do
        case "${v}" in 0|-[0-9]|-[0-9][0-9]) ;; *) skip_all "pipewire: VOLUMES are whole dB at or below 0, got ${v}" ;; esac
    done
fi
C="$(audio_wait_card "${CARD}" "${WAIT_SECONDS}")"
[ -n "${C}" ] || skip_all "no sound card '${CARD}' after ${WAIT_SECONDS} s"
if [ "${AUDIO_BACKEND}" = pipewire ]; then
    audio_pw_start "${PLAY_PCM}" "${CAPTURE_PCM}" || skip_all "no PipeWire nodes '${PLAY_PCM}' and '${CAPTURE_PCM}'"
    NODE_ID="$(pw_node_id "${PLAY_PCM}")"
    [ -n "${NODE_ID}" ] || skip_all "no id for node ${PLAY_PCM}"
    PLAYDEV="${PLAY_PCM}"
    CAPDEV="${CAPTURE_PCM}"
    info_msg "node ${PLAY_PCM} id ${NODE_ID}"
else
P="$(audio_pcm "${C}" playback "${PLAY_PCM}")"
Q="$(audio_pcm "${C}" capture "${CAPTURE_PCM}")"
[ -n "${P}" ] && [ -n "${Q}" ] || skip_all "card ${C}: no playback PCM '${PLAY_PCM}' or capture PCM '${CAPTURE_PCM}'"
PLAYDEV="hw:${C},${P}"
CAPDEV="hw:${C},${Q}"
fi
old_ifs="${IFS}"
IFS=';'
for c in ${VOLUME_CTLS}; do
    amixer -c "${C}" cget "name=${c}" | grep -E "type=|: values=" | sed "s/^/${c}: /"
done
IFS="${old_ifs}"

audio_settle
if audio_mixer "${C}" "${PLAY_MIXER}" && audio_mixer "${C}" "${CAPTURE_MIXER}" &&
   audio_record "${CAPDEV}" "${OUTPUT}/silence.raw" S16_LE "${RATE}" 2 "$((SECONDS_PER_TONE + 1))"; then
    report_pass "volume-setup"
else
    report_fail "volume-setup"
fi
audio_tone "${OUTPUT}/tone.raw" S16_LE "${RATE}" 2 "${PLAY_CHANNEL}" "${FREQ}:${SECONDS_PER_TONE}" "${TONE_DBFS}"

# measure every value first (louder microphone channel at the first,
# loudest value), then report each value and each step between neighbours
ch=""
first=""
for v in ${VOLUMES} ${MUTE_VALUE}; do
    out="${OUTPUT}/vol-${v}.raw"
    audio_settle
    if ! set_volume "${v}" || ! audio_play_record "${PLAYDEV}" "${OUTPUT}/tone.raw" S16_LE "${RATE}" 2 \
            "${CAPDEV}" "${out}" S16_LE "${RATE}" 2 "$((SECONDS_PER_TONE + 1))"; then
        rm -f "${out}"
        continue
    fi
    audio_analyze "${out}" S16_LE "${RATE}" 2 "${SKIP}" "${LEN}" "${FREQ}" | sed "s/^/vol ${v}: /"
    if [ -z "${ch}" ]; then
        ch=1
        awk -v a="$(level "${out}" 1)" -v b="$(level "${out}" 2)" 'BEGIN { exit !(b > a) }' && ch=2
        info_msg "analysing microphone channel ${ch}"
    fi
done
base="$(level "${OUTPUT}/silence.raw" "${ch:-1}")"
prev=""
for v in ${VOLUMES}; do
    out="${OUTPUT}/vol-${v}.raw"
    l=""
    r=fail
    if [ -s "${out}" ]; then
        l="$(level "${out}" "${ch}")"
        line="$(audio_analyze "${out}" S16_LE "${RATE}" 2 "${SKIP}" "${LEN}" "${FREQ}" | sed -n "${ch}p")"
        audio_detect "${line}" "${FREQ}" "${base}" "${MIN_DB}" 25 && r=pass
        info_msg "volume ${v}: ${l} dBFS at ${FREQ} Hz (silence ${base})"
    fi
    add_metric "volume-${v}-detected" "${r}" "$(awk -v l="${l:--200}" -v b="${base}" 'BEGIN { printf "%.1f", l - b }')" dB
    [ -z "${first}" ] && first="${r}"
    if [ -n "${prev}" ]; then
        if [ -n "${l}" ] && [ -n "${prevl}" ]; then
            d="$(awk -v a="${prevl}" -v b="${l}" 'BEGIN { printf "%.1f", a - b }')"
            e="$(awk -v a="${prev}" -v b="${v}" -v s="${STEP_DB}" 'BEGIN { printf "%.1f", (a - b) * s }')"
            info_msg "step ${prev} -> ${v}: level fell ${d} dB, expected ${e} dB"
            r=fail
            awk -v d="${d}" -v e="${e}" -v t="${TOL_DB}" 'BEGIN { x = d - e; if (x < 0) x = -x; exit !(x <= t) }' && r=pass
            add_metric "volume-step-${prev}-${v}" "${r}" "${d}" dB
        else
            report_fail "volume-step-${prev}-${v}"
        fi
    fi
    prev="${v}"
    prevl="${l}"
done
m="none -200"
[ -s "${OUTPUT}/vol-${MUTE_VALUE}.raw" ] &&
    m="$(AUDIO_CHECK_ANY=1 audio_tone_check "${OUTPUT}/vol-${MUTE_VALUE}.raw" "${FREQ}" "${SKIP}" "${LEN}" "${OUTPUT}/silence.raw" "${MIN_DB}")"
audio_report_control "volume-muted-not-detected" "${first}" "${m}"
set_volume "$(echo "${VOLUMES}" | awk '{ print $1 }')"
exit 0
