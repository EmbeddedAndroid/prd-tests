#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (c) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# SoundWire enumeration: every peripheral the kernel created on a SoundWire
# bus must report "Attached" and be bound to a driver, and at least
# MIN_MASTERS buses and MIN_PERIPHERALS peripherals must exist. A
# peripheral that is described but never attached fails its case, and so
# does any peripheral with an all-zero ID (MIPI assigns no manufacturer ID
# 0: the ID was read from no device). Skipped when the kernel has no
# SoundWire bus.

# shellcheck disable=SC1091
. ../../lib/sh-test-lib
OUTPUT="$(pwd)/output"
RESULT_FILE="${OUTPUT}/result.txt"
export RESULT_FILE

MIN_MASTERS="1"
MIN_PERIPHERALS="2"
WAIT_SECONDS="60"

while getopts "m:p:W:h" o; do
    case "$o" in
        m) MIN_MASTERS="${OPTARG}" ;;
        p) MIN_PERIPHERALS="${OPTARG}" ;;
        W) WAIT_SECONDS="${OPTARG}" ;;
        h|*) echo "Usage: $0 [-m <min buses>] [-p <min peripherals>] [-W <s>]" 1>&2; exit 1 ;;
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
add_metric "soundwire-buses" "$([ "${masters}" -ge "${MIN_MASTERS}" ] && echo pass || echo fail)" "${masters}" count
add_metric "soundwire-peripherals" "$([ "${n}" -ge "${MIN_PERIPHERALS}" ] && echo pass || echo fail)" "${n}" count
dmesg | grep -iE "soundwire|sdw" | tail -20
exit 0
