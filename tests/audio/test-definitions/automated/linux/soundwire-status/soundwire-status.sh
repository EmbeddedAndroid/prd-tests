#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (c) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# SoundWire enumeration: every peripheral the kernel created on a SoundWire
# bus must report "Attached" and be bound to a driver, and at least
# MIN_MASTERS buses and MIN_PERIPHERALS peripherals must exist. A
# peripheral that is described but never attached fails its case, and so
# does any peripheral with an all-zero ID (MIPI assigns no manufacturer ID
# 0: the ID was read from no device). With STREAM_PCM, STREAM_COUNT short
# silent streams on that playback PCM (routed with STREAM_MIXER) must not
# address device number 0, where only a peripheral that has not attached
# answers: a stream programs the ports of attached peripherals only. The
# transfers are counted with a kprobe event on STREAM_XFER_PROBE, the bus
# controller's transfer op (its 2nd argument is the struct sdw_msg; the
# exported sdw_transfer can be inlined inside the SoundWire core and miss
# calls); skipped without kprobe events. Skipped when the kernel has no
# SoundWire bus.

# shellcheck disable=SC1091
. ../../lib/sh-test-lib
# shellcheck disable=SC1091
. ../../lib/audio/audio.sh
OUTPUT="$(pwd)/output"
RESULT_FILE="${OUTPUT}/result.txt"
export RESULT_FILE

MIN_MASTERS="1"
MIN_PERIPHERALS="2"
WAIT_SECONDS="60"
CARD=""
STREAM_PCM=""
STREAM_MIXER=""
STREAM_COUNT="10"
STREAM_XFER_PROBE=""

while getopts "m:p:W:c:s:x:n:X:h" o; do
    case "$o" in
        m) MIN_MASTERS="${OPTARG}" ;;
        p) MIN_PERIPHERALS="${OPTARG}" ;;
        W) WAIT_SECONDS="${OPTARG}" ;;
        c) CARD="${OPTARG}" ;;
        s) STREAM_PCM="${OPTARG}" ;;
        x) STREAM_MIXER="${OPTARG}" ;;
        n) STREAM_COUNT="${OPTARG}" ;;
        X) STREAM_XFER_PROBE="${OPTARG}" ;;
        h|*) echo "Usage: $0 [-m <min buses>] [-p <min peripherals>] [-W <s>]" 1>&2
             echo "          [-c <card>] [-s <stream PCM>] [-x '<ctl>=<v>;...'] [-n <streams>] [-X <xfer op>]" 1>&2; exit 1 ;;
    esac
done

create_out_dir "${OUTPUT}"
D=/sys/bus/soundwire/devices
if [ ! -d "${D}" ]; then
    info_msg "no SoundWire bus in this kernel"
    report_skip "soundwire-buses"
    report_skip "soundwire-peripherals"
    exit 0
fi
# give the peripherals time to appear and attach after boot
i=0
while [ "${i}" -lt "${WAIT_SECONDS}" ]; do
    n="$(ls -d "${D}"/sdw:* 2>/dev/null | wc -l)"
    [ "${n}" -ge "${MIN_PERIPHERALS}" ] && ! grep -qsv Attached "${D}"/sdw:*/status && break
    sleep 1
    i=$((i + 1))
done
ls -l "${D}"
masters=0
for m in "${D}"/sdw-master-*; do
    [ -e "${m}" ] && masters=$((masters + 1))
done
n=0
for d in "${D}"/sdw:*; do
    [ -e "${d}" ] || continue
    n=$((n + 1))
    s="$(cat "${d}/status" 2>/dev/null)"
    drv="$(basename "$(readlink "${d}/driver" 2>/dev/null)" 2>/dev/null)"
    name="$(basename "${d}" | tr ':' '-')"
    info_msg "${name}: status '${s}', driver '${drv}'"
    if [ "${s}" = Attached ] && [ -n "${drv}" ]; then
        report_pass "soundwire-${name}-attached"
    else
        report_fail "soundwire-${name}-attached"
    fi
done
zero=""
for d in "${D}"/sdw:*; do
    [ -e "${d}" ] || continue
    # sdw:<controller>:<link>:<manufacturer>:<part>:<class>:<unique>
    [ "$(basename "${d}" | cut -d: -f4-5)" = "0000:0000" ] && zero="${zero} $(basename "${d}")"
done
if [ -n "${zero}" ]; then
    info_msg "peripherals with an all-zero ID:${zero}"
    report_fail "soundwire-ids"
else
    report_pass "soundwire-ids"
fi

# stream_case: SoundWire transfers to device number 0 while short streams
# start and stop on STREAM_PCM
stream_case() {
    local t=/sys/kernel/tracing c p i fails all dev0
    c="$(audio_card "${CARD}")"
    [ -n "${c}" ] && p="$(audio_pcm "${c}" playback "${STREAM_PCM}")"
    if [ -z "${p}" ]; then
        info_msg "no playback PCM '${STREAM_PCM}' on card '${CARD}'"
        report_skip "soundwire-stream-attached-only"
        return
    fi
    if [ -z "${STREAM_XFER_PROBE}" ]; then
        info_msg "no SoundWire transfer op named (STREAM_XFER_PROBE)"
        report_skip "soundwire-stream-attached-only"
        return
    fi
    [ -d "${t}/events" ] || mount -t tracefs nodev "${t}" 2>/dev/null
    # dev_num is the byte at offset 4 of struct sdw_msg
    if ! echo "p:sdwstatus/xfer ${STREAM_XFER_PROBE} dev=+4(\$arg2):u8" >> "${t}/kprobe_events" 2>/dev/null; then
        info_msg "no kprobe events on ${STREAM_XFER_PROBE}"
        report_skip "soundwire-stream-attached-only"
        return
    fi
    audio_mixer "${c}" "${STREAM_MIXER}"
    echo > "${t}/trace"
    echo 1 > "${t}/events/sdwstatus/xfer/enable"
    echo 1 > "${t}/tracing_on"
    i=0 fails=0
    while [ "${i}" -lt "${STREAM_COUNT}" ]; do
        head -c 19200 /dev/zero | aplay -q -D "hw:${c},${p}" -t raw -f S16_LE -r 48000 -c 2 - || fails=$((fails + 1))
        i=$((i + 1))
    done
    echo 0 > "${t}/events/sdwstatus/xfer/enable"
    all="$(grep -c ' xfer: ' "${t}/trace")"
    dev0="$(grep -c ' xfer: .* dev=0$' "${t}/trace")"
    echo '-:sdwstatus/xfer' >> "${t}/kprobe_events"
    info_msg "${STREAM_COUNT} streams on hw:${c},${p}: ${fails} failed, ${all} SoundWire transfers, ${dev0} to device 0"
    if [ "${all}" -gt 0 ] && [ "${dev0}" -eq 0 ] && [ "${fails}" -eq 0 ]; then
        report_pass "soundwire-stream-attached-only"
    else
        report_fail "soundwire-stream-attached-only"
    fi
}
[ -n "${STREAM_PCM}" ] && stream_case

add_metric "soundwire-buses" "$([ "${masters}" -ge "${MIN_MASTERS}" ] && echo pass || echo fail)" "${masters}" count
add_metric "soundwire-peripherals" "$([ "${n}" -ge "${MIN_PERIPHERALS}" ] && echo pass || echo fail)" "${n}" count
dmesg | grep -iE "soundwire|sdw" | tail -20
exit 0
