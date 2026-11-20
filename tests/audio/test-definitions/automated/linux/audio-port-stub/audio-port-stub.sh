#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (c) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# Software path of an audio port (I2S, TDM) up to the port clock, for a
# board where nothing external is attached to the port. The card carries
# a DAI link from the port to a stub codec (a DT codec without controls
# or GPIOs). A silent stream through the link checks, in order, one case
# each:
#   <NAME>-card   the card CARD is registered (fail when the device tree
#                 has a link matching LINK but no card bound)
#   <NAME>-link   the card has a DAI link matching LINK
#   <NAME>-route  the MIXER settings apply (the DSP topology has a graph
#                 routed to the port)
#   <NAME>-start  the stream runs DURATION s at RATE with exit code 0 and
#                 no driver or DSP error
#   <NAME>-clock  the frame rate of the stream, from the PCM hw_ptr over
#                 the run, is RATE within TOL_PCT: the DSP consumes or
#                 produces data at the configured rate
# Kernel lines since the start that match ERRORS and report a failure,
# error or timeout, lines that match DSP_ERE, and an xrun fail
# <NAME>-start. On a start failure every new kernel error line is shown
# and the layer is named: the DSP refused the port configuration (a line
# matches DSP_ERE), the DSP did not answer (a timeout), or the kernel
# refused it before the DSP. The first failing case names the layer at
# fault (device tree link, topology, DSP port configuration, port
# clock); later cases are skipped. A pass covers everything up to the
# port; the pads, the external codec and the wiring are not exercised
# and need the end to end test. Without the card CARD and without a
# matching link in the device tree every case skips (the image carries
# no stub links); with the card, bad parameters fail. The MIXER controls
# get their earlier values back when the test ends, and a case the test
# could not reach is reported as fail.

# shellcheck disable=SC1091
. ../../lib/sh-test-lib
# shellcheck disable=SC1091
. ../../lib/audio/audio.sh
OUTPUT="$(pwd)/output"
RESULT_FILE="${OUTPUT}/result.txt"
export RESULT_FILE

CARD=""
NAME="port"
LINK=""
DIRECTION="playback"
PCM=""
MIXER=""
RATE="48000"
CHANNELS="2"
DURATION="6"
TOL_PCT="2"
ERRORS="ASoC|audio"
DSP_ERE="DSP returned error|Error \\(-?[0-9]+\\) Processing"
WAIT_SECONDS="60"

usage() {
    echo "Usage: $0 [-c card] -n name -l link ERE [-d playback|capture] -p PCM [-m mixer]" 1>&2
    echo "          [-r rate] [-k channels] [-S s] [-t tolerance %] [-e error ERE] [-D DSP error ERE] [-W s]" 1>&2
    exit 1
}

while getopts "c:n:l:d:p:m:r:k:S:t:e:D:W:h" o; do
    case "$o" in
        c) CARD="${OPTARG}" ;;
        n) NAME="${OPTARG}" ;;
        l) LINK="${OPTARG}" ;;
        d) DIRECTION="${OPTARG}" ;;
        p) PCM="${OPTARG}" ;;
        m) MIXER="${OPTARG}" ;;
        r) RATE="${OPTARG}" ;;
        k) CHANNELS="${OPTARG}" ;;
        S) DURATION="${OPTARG}" ;;
        t) TOL_PCT="${OPTARG}" ;;
        e) ERRORS="${OPTARG}" ;;
        D) DSP_ERE="${OPTARG}" ;;
        W) WAIT_SECONDS="${OPTARG}" ;;
        *) usage ;;
    esac
done

create_out_dir "${OUTPUT}"
mountpoint -q /sys/kernel/debug || mount -t debugfs none /sys/kernel/debug 2>/dev/null

skip_rest() {
    for s in "$@"; do
        report_skip "${NAME}-${s}"
        done_cases="${done_cases} ${s}"
    done
    exit 0
}

# kernel lines logged after the mark (the last line before the run): no
# timestamps, so suspend and printk.time=0 cannot hide a line, and a full
# ring keeps working; the mark goes through the environment because awk
# -v interprets backslashes. No mark found: nothing is returned and
# kwindow says so.
kmark() { dmesg | tail -n 1; }
ksince() {
    KMARK="$1" dmesg | KMARK="$1" awk '{ a[NR] = $0 } $0 == ENVIRON["KMARK"] { l = NR }
        END { if (l == "") { print "(kernel log window unknown: mark not found)" > "/dev/stderr"; exit }
              for (i = l + 1; i <= NR; i++) print a[i] }'
}

# every MIXER control goes back to its value before the run
saved=""
done_cases=""
restore() {
    [ -n "${pid}" ] && kill "${pid}" 2>/dev/null
    [ -n "${saved}" ] && audio_mixer "${C}" "${saved}" >/dev/null 2>&1
    saved=""
    for c in card link route start clock; do
        case " ${done_cases} " in *" ${c} "*) ;; *) report_fail "${NAME}-${c}" ;; esac
    done
}
trap restore EXIT
trap 'exit 1' INT TERM
pass() { report_pass "${NAME}-$1"; done_cases="${done_cases} $1"; }
fail() { report_fail "${NAME}-$1"; done_cases="${done_cases} $1"; }
pid=""
bad=""

case "${WAIT_SECONDS}" in ''|*[!0-9]*) WAIT_SECONDS=60; bad=1 ;; esac
C="$(audio_wait_card "${CARD}" "${WAIT_SECONDS}")"
if [ -z "${C}" ]; then
    dt="$(for f in /proc/device-tree/sound*/*/link-name; do [ -f "${f}" ] && tr -d '\0' < "${f}" && echo; done | grep -E "${LINK:-.}" | head -1)"
    if [ -n "${dt}" ] && [ -n "${LINK}" ]; then
        info_msg "${NAME}: the device tree has the link '${dt}' but no card '${CARD}' bound (topology or codec probe)"
        fail card
        skip_rest link route start clock
    fi
    info_msg "${NAME}: no card '${CARD}': the image carries no stub DAI links"
    skip_rest card link route start clock
fi
pass card

[ -n "${LINK}" ] || bad=1
case "${DIRECTION}" in playback|capture) ;; *) bad=1 ;; esac
for n in "${DURATION}" "${RATE}" "${CHANNELS}" "${TOL_PCT}"; do
    case "${n}" in ''|*[!0-9]*) bad=1 ;; esac
done
[ -n "${bad}" ] || [ "${DURATION}" -ge 4 ] || bad=1
if [ -n "${bad}" ]; then
    info_msg "${NAME}: bad parameters (LINK set, DIRECTION playback|capture, integer RATE, CHANNELS, TOL_PCT, WAIT_SECONDS, DURATION >= 4)"
    exit 1
fi
m="$(for f in /sys/class/sound/card${C}/device/of_node/*/link-name; do [ -f "${f}" ] && tr -d '\0' < "${f}" && echo; done | grep -E "${LINK}" | sort -u)"
if [ -z "${m}" ]; then
    info_msg "${NAME}: device tree: no DAI link matching '${LINK}' on card '${CARD}' (index ${C})"
    fail link
    skip_rest route start clock
fi
info_msg "${NAME}: links: $(echo ${m})"
pass link

if [ -z "${MIXER}" ]; then
    info_msg "${NAME}: no MIXER given, the route is not checked"
    report_skip "${NAME}-route"; done_cases="${done_cases} route"
else
    old_ifs="${IFS}"; IFS=';'
    for kv in ${MIXER}; do
        IFS="${old_ifs}"
        v="$(amixer -c "${C}" cget "name=${kv%=*}" 2>/dev/null | sed -n 's/^ *: values=//p')"
        [ -n "${v}" ] && saved="${saved}${kv%=*}=${v};"
        IFS=';'
    done
    IFS="${old_ifs}"
    if ! audio_mixer "${C}" "${MIXER}"; then
        if amixer -c "${C}" controls 2>/dev/null | grep -q "Mixer"; then
            info_msg "${NAME}: topology: the route '${MIXER}' does not apply (no graph for the port)"
        else
            info_msg "${NAME}: topology: the card has no DSP routing controls (no topology loaded)"
        fi
        fail route
        skip_rest start clock
    fi
    pass route
fi

D="$(audio_pcm "${C}" "${DIRECTION}" "${PCM}")"
if [ -z "${D}" ]; then
    info_msg "${NAME}: no ${DIRECTION} PCM '${PCM}' on card ${C}"
    fail start
    skip_rest clock
fi
s="p"; tool="aplay"
[ "${DIRECTION}" = capture ] && { s="c"; tool="arecord"; }
st="/proc/asound/card${C}/pcm${D}${s}/sub0/status"

# "<uptime> <hw_ptr>" of the running PCM (the status tstamp stays 0 unless
# the application enables timestamps)
sample() {
    h="$(awk '/^hw_ptr/ { print $3 }' "${st}" 2>/dev/null)"
    [ -n "${h}" ] && echo "$(cut -d' ' -f1 /proc/uptime) ${h}"
}

k0="$(kmark)"
bytes=$((RATE * CHANNELS * 2 * DURATION))
log="${OUTPUT}/${NAME}-${tool}.log"
# ask for 10 ms periods (the driver may grant larger ones; the granted
# hw_params are printed): hw_ptr lags by under one period at each sample,
# so the rate error over the 4 s window is under one period plus the
# 10 ms /proc/uptime resolution; an xrun fails start
pp="--period-time=10000 --buffer-time=160000"
if [ "${DIRECTION}" = capture ]; then
    # shellcheck disable=SC2086
    arecord -q ${pp} -D "hw:${C},${D}" -f S16_LE -r "${RATE}" -c "${CHANNELS}" -t raw -d "${DURATION}" /dev/null > "${log}" 2>&1 &
else
    # shellcheck disable=SC2086
    head -c "${bytes}" /dev/zero | aplay -q ${pp} -D "hw:${C},${D}" -f S16_LE -r "${RATE}" -c "${CHANNELS}" -t raw - > "${log}" 2>&1 &
fi
pid=$!
sleep 1
sed 's/^/  hw_params: /' "/proc/asound/card${C}/pcm${D}${s}/sub0/hw_params" 2>/dev/null | grep -E "period_size|buffer_size|rate"
a="$(sample)"
sleep $((DURATION - 2))
b="$(sample)"
i=0
while kill -0 "${pid}" 2>/dev/null && [ "${i}" -lt 40 ]; do sleep 0.25 2>/dev/null || sleep 1; i=$((i + 1)); done
if kill -0 "${pid}" 2>/dev/null; then
    kill "${pid}" 2>/dev/null; sleep 1; kill -9 "${pid}" 2>/dev/null; wait "${pid}" 2>/dev/null
    rc=124
else
    wait "${pid}"; rc=$?
fi
pid=""
cat "${log}"
new="$(ksince "${k0}")"
[ -z "${new}" ] && ! dmesg | grep -qxF -- "${k0}" && info_msg "${NAME}: kernel log window unknown (mark rotated out): kernel lines not checked"
errs="$( { echo "${new}" | grep -iE "${ERRORS}" | grep -iE 'fail|error|timeout|-[0-9]+$'; echo "${new}" | grep -E "${DSP_ERE}"; } | sort -u | head -8)"
[ -n "${errs}" ] && echo "${errs}" | sed 's/^/  dmesg: /'
info_msg "${NAME}: ${tool} rc=${rc}, status samples '${a}' '${b}'"
ha="${a#* }"; hb="${b#* }"

xrun="$(grep -iE 'underrun|overrun|xrun' "${log}" | head -2)"
[ -n "${xrun}" ] && info_msg "${NAME}: ${tool}: ${xrun}"
if [ "${rc}" -ne 0 ] || [ -n "${errs}" ] || [ -n "${xrun}" ]; then
    all="$(echo "${new}" | grep -iE 'fail|error|timeout' | head -12)"
    [ -n "${all}" ] && echo "${all}" | sed 's/^/  kernel: /'
    if [ -z "${b}" ] || [ "${ha}" = "${hb}" ]; then
        if echo "${all}" | grep -qE "${DSP_ERE}"; then
            info_msg "${NAME}: the stream did not run: the DSP refused the port configuration"
        elif echo "${all}" | grep -qi timeout; then
            info_msg "${NAME}: the stream did not run: the DSP did not answer"
        elif [ -n "${all}" ]; then
            info_msg "${NAME}: the stream did not run: refused in the kernel before the DSP"
        else
            info_msg "${NAME}: the stream did not run: port clock not running"
        fi
    else
        info_msg "${NAME}: the stream ran but ended with an error"
    fi
    fail start
    skip_rest clock
fi
pass start

fps="$(echo "${a} ${b}" | awk '{ dt = $3 - $1; df = $4 - $2; if (dt > 0) printf "%d", df / dt; else print 0 }')"
ok="$(awk -v f="${fps}" -v r="${RATE}" -v t="${TOL_PCT}" 'BEGIN { d = f - r; if (d < 0) d = -d; print (d * 100 <= r * t) ? 1 : 0 }')"
info_msg "${NAME}: frame rate ${fps}/s for ${RATE} (tolerance ${TOL_PCT} %)"
if [ "${ok}" = 1 ]; then
    pass clock
    info_msg "${NAME}: the software path to the port works; pads, external codec and wiring are not exercised"
else
    info_msg "${NAME}: port clock: frame rate ${fps}/s is not ${RATE}"
    fail clock
fi
exit 0
