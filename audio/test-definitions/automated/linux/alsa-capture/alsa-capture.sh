#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (c) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# Microphone capture levels and capture across a DSP restart. Every capture
# channel (one per microphone, as CAPTURE_MIXER routes them) is recorded in
# silence and while a sine plays on the speakers; each channel must show the
# sine's frequency as its spectral peak, a level a margin above its own
# silence level, and a silence level that is not digital zero (a dead or
# unclocked microphone records exact zeros). Then the DSP that runs the
# audio path is crashed through remoteproc debugfs two seconds into a long
# capture: the capture must end rather than hang, the card must come back,
# and a new capture must detect the sine again. Skipped with the reason when
# the card, the PCMs, the tools or the remoteproc are absent.

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
# Names of the capture channels in order, e.g. "dmic0 dmic1 dmic2 dmic3".
MICS="mic0 mic1"
RATE="48000"
FREQ="1000"
TONE_DBFS="-12"
MIN_DB="10"
TOLERANCE_HZ="25"
SECONDS_PER_TONE="3"
REMOTEPROC="adsp"
BUSY_SECONDS="10"
WAIT_SECONDS="60"

usage() {
    echo "Usage: $0 [-c <card>] [-p <playback PCM>] [-q <capture PCM>]" 1>&2
    echo "          [-m '<ctl>=<v>;...'] [-n '<ctl>=<v>;...'] [-M '<mic> ...'] [-R <rate>]" 1>&2
    echo "          [-f <Hz>] [-l <dBFS>] [-d <min dB>] [-t <tolerance Hz>] [-T <s>]" 1>&2
    echo "          [-r <remoteproc>] [-b <busy s>] [-W <wait s>]" 1>&2
    exit 1
}

while getopts "c:p:q:m:n:M:R:f:l:d:t:T:r:b:W:h" o; do
    case "$o" in
        c) CARD="${OPTARG}" ;;
        p) PLAY_PCM="${OPTARG}" ;;
        q) CAPTURE_PCM="${OPTARG}" ;;
        m) PLAY_MIXER="${OPTARG}" ;;
        n) CAPTURE_MIXER="${OPTARG}" ;;
        M) MICS="${OPTARG}" ;;
        R) RATE="${OPTARG}" ;;
        f) FREQ="${OPTARG}" ;;
        l) TONE_DBFS="${OPTARG}" ;;
        d) MIN_DB="${OPTARG}" ;;
        t) TOLERANCE_HZ="${OPTARG}" ;;
        T) SECONDS_PER_TONE="${OPTARG}" ;;
        r) REMOTEPROC="${OPTARG}" ;;
        b) BUSY_SECONDS="${OPTARG}" ;;
        W) WAIT_SECONDS="${OPTARG}" ;;
        h|*) usage ;;
    esac
done

NCH=0
for m in ${MICS}; do NCH=$((NCH + 1)); done
RESTART_CASES="dsp-restart-during-capture capture-ends-after-restart capture-after-restart kernel-clean-after-capture-restart"

skip_all() {
    local m
    info_msg "$1"
    report_skip "capture-setup"
    for m in ${MICS}; do
        report_skip "capture-${m}-silence-live"
        report_skip "capture-${m}-${FREQ}hz"
    done
    for m in ${RESTART_CASES}; do report_skip "${m}"; done
    exit 0
}

wait_card() {
    local i=0
    while [ -z "$(audio_card "${CARD}")" ] && [ "${i}" -lt "${WAIT_SECONDS}" ]; do
        sleep 1
        i=$((i + 1))
    done
    C="$(audio_card "${CARD}")"
    [ -n "${C}" ] || return 1
    if [ "${AUDIO_BACKEND}" = pipewire ]; then
        PLAYDEV="${PLAY_PCM}"
        CAPDEV="${CAPTURE_PCM}"
        audio_pw_start "${PLAY_PCM}" "${CAPTURE_PCM}"
        return
    fi
    P="$(audio_pcm "${C}" playback "${PLAY_PCM}")"
    Q="$(audio_pcm "${C}" capture "${CAPTURE_PCM}")"
    PLAYDEV="hw:${C},${P}"
    CAPDEV="hw:${C},${Q}"
    [ -n "${P}" ] && [ -n "${Q}" ]
}

route() {
    audio_mixer "${C}" "${PLAY_MIXER}" && audio_mixer "${C}" "${CAPTURE_MIXER}"
}

# tone_capture <name>: capture while the sine plays; analysis in <name>.txt
tone_capture() {
    audio_play_record "${PLAYDEV}" "${OUTPUT}/sine.raw" S16_LE "${RATE}" 2 \
        "${CAPDEV}" "${OUTPUT}/$1.raw" S16_LE "${RATE}" "${NCH}" "$((SECONDS_PER_TONE + 1))" || return 1
    audio_analyze "${OUTPUT}/$1.raw" S16_LE "${RATE}" "${NCH}" 1 "$((SECONDS_PER_TONE - 1))" "${FREQ}" > "${OUTPUT}/$1.txt"
    sed "s/^/$1: /" "${OUTPUT}/$1.txt"
}

create_out_dir "${OUTPUT}"
missing="$(audio_tools_missing)"
[ -z "${missing}" ] || skip_all "missing tools: ${missing}"
wait_card || skip_all "no card '${CARD}' with playback PCM '${PLAY_PCM}' and capture PCM '${CAPTURE_PCM}' after ${WAIT_SECONDS} s"
info_msg "card ${C} (${AUDIO_BACKEND}): playback ${PLAYDEV}, capture ${CAPDEV}, mics ${MICS}"

audio_tone "${OUTPUT}/sine.raw" S16_LE "${RATE}" 2 all "${FREQ}:${SECONDS_PER_TONE}" "${TONE_DBFS}"
if route && audio_record "${CAPDEV}" "${OUTPUT}/silence.raw" S16_LE "${RATE}" "${NCH}" \
        "$((SECONDS_PER_TONE + 1))" > "${OUTPUT}/silence.log" 2>&1; then
    report_pass "capture-setup"
else
    cat "${OUTPUT}/silence.log"
    report_fail "capture-setup"
fi
audio_analyze "${OUTPUT}/silence.raw" S16_LE "${RATE}" "${NCH}" 1 "$((SECONDS_PER_TONE - 1))" "${FREQ}" > "${OUTPUT}/silence.txt"
sed 's/^/silence: /' "${OUTPUT}/silence.txt"
tone_capture tone || info_msg "tone capture failed"

k=0
for m in ${MICS}; do
    k=$((k + 1))
    s="$(sed -n "${k}p" "${OUTPUT}/silence.txt")"
    t="$(sed -n "${k}p" "${OUTPUT}/tone.txt" 2>/dev/null)"
    # A clocked microphone has a noise floor; exact zeros mean no data.
    rms="$(audio_field "${s}" rms)"
    if [ -n "${rms}" ] && awk -v r="${rms}" 'BEGIN { exit !(r > -150) }'; then
        add_metric "capture-${m}-silence-live" pass "${rms}" "dBFS"
    else
        add_metric "capture-${m}-silence-live" fail "${rms:--200}" "dBFS"
    fi
    base="$(audio_field "${s}" level)"
    margin="$(awk -v l="$(audio_field "${t}" level)" -v b="${base}" 'BEGIN { printf "%.1f", l - b }')"
    if [ -n "${t}" ] && audio_detect "${t}" "${FREQ}" "${base}" "${MIN_DB}" "${TOLERANCE_HZ}"; then
        add_metric "capture-${m}-${FREQ}hz" pass "${margin}" "dB"
    else
        info_msg "${m}: ${t:-no capture} (silence ${base} dBFS)"
        add_metric "capture-${m}-${FREQ}hz" fail "${margin}" "dB"
    fi
done

if [ "${AUDIO_BACKEND}" = pipewire ]; then
    info_msg "DSP restart during capture runs on the ALSA backend only"
    for c in ${RESTART_CASES}; do report_skip "${c}"; done
    exit 0
fi
RPROC=""
for r in /sys/class/remoteproc/remoteproc*; do
    [ "$(cat "${r}/name" 2>/dev/null)" = "${REMOTEPROC}" ] && RPROC="${r}"
done
mount -t debugfs none /sys/kernel/debug 2>/dev/null
RPROC_DEBUG="/sys/kernel/debug/remoteproc/$(basename "${RPROC:-none}")"
if [ -z "${RPROC}" ] || [ ! -w "${RPROC_DEBUG}/crash" ]; then
    info_msg "no remoteproc ${REMOTEPROC} or no debugfs crash file"
    for c in ${RESTART_CASES}; do report_skip "${c}"; done
    exit 0
fi

t0="$(cut -d' ' -f1 /proc/uptime)"
id0="$(ls -di "/sys/class/sound/card${C}/" | awk '{ print $1 }')"
arecord -q -D "hw:${C},${Q}" -t raw -f S16_LE -r "${RATE}" -c "${NCH}" -d "${BUSY_SECONDS}" \
    "${OUTPUT}/busy.raw" > "${OUTPUT}/busy.log" 2>&1 &
bpid=$!
sleep 2
echo 1 > "${RPROC_DEBUG}/crash"
i=0
back=0
while [ "${i}" -lt "${WAIT_SECONDS}" ]; do
    sleep 1
    i=$((i + 1))
    c="$(audio_card "${CARD}")"
    id="$([ -n "${c}" ] && ls -di "/sys/class/sound/card${c}/" 2>/dev/null | awk '{ print $1 }')"
    [ -z "${c}" ] && gone=1
    [ -n "${id}" ] && [ "${id}" != "${id0}" ] && gone=1
    if [ "$(cat "${RPROC}/state")" = running ] && [ "${gone:-0}" = 1 ] && [ -n "${c}" ]; then
        back=1
        break
    fi
done
if [ "${back}" = 1 ]; then
    add_metric "dsp-restart-during-capture" pass "${i}" "s"
else
    info_msg "after ${i} s ${REMOTEPROC} is $(cat "${RPROC}/state"), card '$(audio_card "${CARD}")'"
    report_fail "dsp-restart-during-capture"
fi
i=0
while kill -0 "${bpid}" 2>/dev/null && [ "${i}" -lt $((BUSY_SECONDS + 5)) ]; do
    sleep 1
    i=$((i + 1))
done
if kill -0 "${bpid}" 2>/dev/null; then
    kill "${bpid}"
    report_fail "capture-ends-after-restart"
else
    wait "${bpid}"
    info_msg "arecord ended after the restart (exit $?)"
    report_pass "capture-ends-after-restart"
fi
cat "${OUTPUT}/busy.log"
if [ "${back}" = 1 ] && wait_card && route && tone_capture after; then
    line="$(awk '{ l = $4; sub(/level=/, "", l); if (b == "" || l + 0 > bl) { b = $0; bl = l + 0 } } END { print b }' "${OUTPUT}/after.txt")"
    ch="$(audio_field "${line}" ch)"
    base="$(sed -n "${ch}p" "${OUTPUT}/silence.txt")"
    base="$(audio_field "${base}" level)"
    if audio_detect "${line}" "${FREQ}" "${base}" "${MIN_DB}" "${TOLERANCE_HZ}"; then
        report_pass "capture-after-restart"
    else
        info_msg "after restart: ${line} (silence ${base} dBFS)"
        report_fail "capture-after-restart"
    fi
else
    report_fail "capture-after-restart"
fi
n="$(audio_kernel_errors "${t0}")"
audio_since "${t0}" | grep -E "WARNING:|BUG:|Oops|Internal error|Unable to handle|external abort" | head -n 10
if [ "${n}" -eq 0 ]; then
    report_pass "kernel-clean-after-capture-restart"
else
    add_metric "kernel-clean-after-capture-restart" fail "${n}" "lines"
fi
exit 0
