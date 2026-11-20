#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (c) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# Audio interfaces the board must expose, checked on the running card:
#   tdm, i2s     a card DAI link on an LPASS TDM or MI2S port (DT link-name
#                or the card's runtime links in debugfs), with its PCM
#   analog-mic   an analog codec (WCD) component on the card and an ADC
#                route for its microphones
#   i2c          an audio device on I2C bound to a sound driver
#   dp           a card DAI link on a DisplayPort audio port
# Each interface named in INTERFACES gets one case; a missing interface
# fails with what was looked for, so the report shows the gap. Without a
# running card every link and codec case fails.

# shellcheck disable=SC1091
. ../../lib/sh-test-lib
# shellcheck disable=SC1091
. ../../lib/audio/audio.sh
OUTPUT="$(pwd)/output"
RESULT_FILE="${OUTPUT}/result.txt"
export RESULT_FILE

CARD=""
INTERFACES="tdm i2s analog-mic i2c dp"
WAIT_SECONDS="60"

while getopts "c:i:W:h" o; do
    case "$o" in
        c) CARD="${OPTARG}" ;;
        i) INTERFACES="${OPTARG}" ;;
        W) WAIT_SECONDS="${OPTARG}" ;;
        *) echo "Usage: $0 [-c <card>] [-i '<tdm i2s analog-mic i2c dp>'] [-W <s>]" 1>&2; exit 1 ;;
    esac
done

create_out_dir "${OUTPUT}"
mountpoint -q /sys/kernel/debug || mount -t debugfs none /sys/kernel/debug 2>/dev/null
C="$(audio_wait_card "${CARD}" "${WAIT_SECONDS}")"
cat /proc/asound/cards

# DAI link names of the card: DT link-name properties and debugfs runtimes
links="$(for f in /proc/device-tree/sound*/*/link-name; do [ -f "${f}" ] && tr -d '\0' < "${f}" && echo; done)"
rt="$(ls -d /sys/kernel/debug/asoc/*/* 2>/dev/null | sed 's|.*/||')"
echo "DT DAI links:"; echo "${links}" | sed 's/^/  /'
echo "card runtimes:"; echo "${rt}" | sed 's/^/  /'
comps="$(cat /sys/kernel/debug/asoc/components 2>/dev/null)"
echo "components:"; echo "${comps}" | sed 's/^/  /'

check_link() {
    # $1 case, $2 ERE for the port name
    local m
    m="$(printf '%s\n%s\n' "${links}" "${rt}" | grep -iE "$2" | sort -u)"
    if [ -n "${m}" ] && [ -n "${C}" ]; then
        info_msg "$1: ${m}"
        report_pass "interface-$1"
    else
        info_msg "$1: no DAI link matching '$2' on the card"
        report_fail "interface-$1"
    fi
}

for f in ${INTERFACES}; do
    case "${f}" in
        tdm) check_link tdm "TDM" ;;
        i2s) check_link i2s "MI2S|I2S" ;;
        dp) check_link dp "DISPLAY_PORT|DP[0-9]*_RX|HDMI" ;;
        analog-mic)
            w="$(echo "${comps}" | grep -iE 'wcd' | head -3)"
            a="$([ -n "${C}" ] && amixer -c "${C}" controls 2>/dev/null | grep -iE "'(ADC[0-9]|TX DEC|AMIC)" | head -5)"
            info_msg "analog-mic: codec '${w}', ADC controls '${a}'"
            if [ -n "${w}" ] && [ -n "${a}" ]; then report_pass interface-analog-mic; else report_fail interface-analog-mic; fi ;;
        i2c)
            hit=""
            for d in /sys/bus/i2c/devices/*; do
                [ -e "${d}/driver" ] || continue
                drv="$(basename "$(readlink "${d}/driver")")"
                p=""
                [ -e "${d}/driver/module" ] && p="$(modinfo -n "$(basename "$(readlink "${d}/driver/module")")" 2>/dev/null)"
                # built-in drivers have no module: judge them by name
                case "${p}:${drv}" in
                    */sound/*:*|:*codec*|:*wcd*|:*wsa*|:*tas*|:*cs35*|:*max98*|:*rt5*|:*aw88*) hit="${hit} $(basename "${d}"):${drv}" ;;
                esac
            done
            info_msg "i2c audio devices:${hit:- none}"
            if [ -n "${hit}" ]; then report_pass interface-i2c; else report_fail interface-i2c; fi ;;
        *) info_msg "unknown interface ${f}"; report_skip "interface-${f}" ;;
    esac
done
exit 0
