#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (c) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# Helpers for the signal-level ALSA tests: card and PCM lookup, mixer
# settings, tone generation (tone.awk) and level/peak analysis
# (spectrum.awk), both on math.awk so that any POSIX awk works (BusyBox
# awk is often built without the math library). Needs aplay, arecord,
# amixer and od.
# Source after sh-test-lib; AUDIO_LIB is this directory.

AUDIO_LIB="${AUDIO_LIB:-$(cd "$(dirname "$0")/../../lib/audio" && pwd)}"

# audio_tools_missing: names of the tools these helpers need that are absent
audio_tools_missing() {
    local t
    local tools="aplay arecord amixer od awk"
    [ "${AUDIO_BACKEND}" = pipewire ] && tools="pipewire wireplumber pw-cli pw-play pw-record od awk"
    for t in ${tools}; do
        command -v "${t}" > /dev/null 2>&1 || printf '%s ' "${t}"
    done
}

# audio_card <id or name>: index of the card, or of the first card
audio_card() {
    if [ -z "$1" ]; then
        awk '/^ *[0-9]+ \[/ { print $1; exit }' /proc/asound/cards
    else
        awk -v c="$1" '/^ *[0-9]+ \[/ {
            id = $2; sub(/^\[/, "", id); gsub(/[]: ]/, "", id)
            n = $0; sub(/.* - /, "", n)
            if (id == c || n == c) { print $1; exit } }' /proc/asound/cards
    fi
}

# audio_pcm <card> <playback|capture> <PCM name>: device number of the
# first PCM of that direction whose name is the given one (any if empty)
audio_pcm() {
    local tool=aplay
    [ "$2" = capture ] && tool=arecord
    "${tool}" -l 2>/dev/null | awk -v c="$1" -v p="$3" '
        $1 == "card" && $2 == c ":" {
            d = $0; sub(/.*device /, "", d); sub(/:.*/, "", d)
            if (p == "" || index($0, ": " p " ") || index($0, ": " p "\t") || index($0, ": " p " (")) { print d; exit } }'
}

# audio_mixer <card> "<control>=<value>;...": apply every setting; returns 1
# if any of them fails
audio_mixer() {
    local rc=0
    local s
    local old_ifs="${IFS}"
    IFS=';'
    for s in $2; do
        IFS="${old_ifs}"
        [ -n "${s}" ] || { IFS=';'; continue; }
        if ! amixer -c "$1" -q cset "name=${s%=*}" "${s##*=}"; then
            info_msg "cannot set '${s%=*}' to ${s##*=}"
            rc=1
        fi
        IFS=';'
    done
    IFS="${old_ifs}"
    return "${rc}"
}

# audio_fmt_args <format>: "<bytes> <shift> <bits> <od type>" for S16_LE,
# S24_LE, S24_3LE and S32_LE
audio_fmt_args() {
    case "$1" in
        S16_LE) echo "2 0 16 d2" ;;
        S24_LE) echo "4 0 24 d4" ;;
        S32_LE) echo "4 8 32 d4" ;;
        *) return 1 ;;
    esac
}

# audio_tone <file> <format> <rate> <channels> <mask> "<Hz>:<s> ..." [dBFS]:
# raw interleaved PCM with a sine on the channels of mask (1-based list or
# "all") and silence on the others
audio_tone() {
    local bytes shift bits od_t
    read -r bytes shift bits od_t <<EOT
$(audio_fmt_args "$2")
EOT
    [ -n "${bytes}" ] || return 1
    : > "$1"
    awk -v rate="$3" -v channels="$4" -v mask="$5" -v segments="$6" \
        -v dbfs="${7:--12}" -v bytes="${bytes}" -v shift="${shift}" \
        -f "${AUDIO_LIB}/math.awk" -f "${AUDIO_LIB}/tone.awk" | while read -r line; do
            # shellcheck disable=SC2059
            printf "${line}"
        done > "$1"
}

# audio_analyze <file> <format> <rate> <channels> [skip s] [len s] [Hz]:
# spectrum.awk lines for a raw capture. The peak is searched from AUDIO_FMIN
# (default 300 Hz): below that, small speakers reproduce little and boards
# pick up hum and fan noise that can be louder than the test signal. With
# a frequency, the search is kept to AUDIO_PEAK_SPAN (default 1.23, a third
# of an octave) either side of it: a stream played at the wrong rate still
# moves the peak, but a steady interferer elsewhere in the room cannot
# take it.
audio_analyze() {
    local bytes shift bits od_t lo hi
    read -r bytes shift bits od_t <<EOT
$(audio_fmt_args "$2")
EOT
    lo="${AUDIO_FMIN:-300}"
    hi=""
    if [ -n "$7" ]; then
        read -r lo hi <<EOT
$(awk -v f="$7" -v s="${AUDIO_PEAK_SPAN:-1.23}" -v m="${lo}" 'BEGIN { l = f / s; if (l < m) l = m; print l, f * s }')
EOT
    fi
    od -An -v -t "${od_t}" "$1" | awk -v rate="$3" -v channels="$4" \
        -v bits="${bits}" -v skip="${5:-0}" -v len="${6:-}" -v freq="${7:-}" \
        -v fmin="${lo}" -v fmax="${hi}" \
        -f "${AUDIO_LIB}/math.awk" -f "${AUDIO_LIB}/spectrum.awk"
}

# audio_field <line> <key>: value of key=<value> in an analysis line
audio_field() {
    echo "$1" | tr ' ' '\n' | sed -n "s/^$2=//p"
}

# AUDIO_BACKEND selects how the tests reach the speakers and microphones:
#   alsa      (default) aplay/arecord on ALSA devices (hw:<card>,<pcm>)
#   pipewire  pw-play/pw-record on PipeWire nodes, for cards that only
#             userspace drives (AudioReach PAL through its PipeWire plugin);
#             S16_LE only. The device arguments are then node names.
AUDIO_BACKEND="${AUDIO_BACKEND:-alsa}"

# audio_le <value> <bytes>: little-endian bytes of an integer, for printf
audio_le() {
    local v="$1"
    local n="$2"
    while [ "${n}" -gt 0 ]; do
        # shellcheck disable=SC2059
        printf "\\$(printf %03o $((v & 255)))"
        v=$((v >> 8))
        n=$((n - 1))
    done
}

# audio_wav <raw S16_LE> <wav> <rate> <channels>: PCM WAVE file of the data
audio_wav() {
    local n
    n="$(wc -c < "$1")"
    {
        printf 'RIFF'; audio_le $((n + 36)) 4; printf 'WAVEfmt '
        audio_le 16 4; audio_le 1 2; audio_le "$4" 2; audio_le "$3" 4
        audio_le $(($3 * $4 * 2)) 4; audio_le $(($4 * 2)) 2; audio_le 16 2
        printf 'data'; audio_le "${n}" 4
        cat "$1"
    } > "$2"
}

# audio_wav_raw <wav> <raw>: the samples of a WAVE file (after its data chunk
# header, wherever the writer put it)
audio_wav_raw() {
    local off
    off="$(od -A n -v -t x1 -N 4096 "$1" | tr -s ' \n' '  ' | awk '{
        for (i = 1; i + 3 <= NF; i++)
            if ($i == "64" && $(i + 1) == "61" && $(i + 2) == "74" && $(i + 3) == "61") { print i + 7; exit } }')"
    [ -n "${off}" ] || return 1
    tail -c +"$((off + 1))" "$1" > "$2"
}

# audio_pw_start [node ...]: start PipeWire and WirePlumber unless running,
# and wait up to AUDIO_PW_WAIT (30) s for the nodes to appear
audio_pw_start() {
    local i=0
    local n
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/pw}"
    mkdir -p "${XDG_RUNTIME_DIR}"
    if ! pidof pipewire > /dev/null 2>&1; then
        pipewire > "${XDG_RUNTIME_DIR}/pipewire.log" 2>&1 &
        sleep 2
    fi
    if ! pidof wireplumber > /dev/null 2>&1; then
        wireplumber > "${XDG_RUNTIME_DIR}/wireplumber.log" 2>&1 &
    fi
    for n in "$@"; do
        while ! pw-cli ls Node 2>/dev/null | grep -q "node.name = \"${n}\""; do
            [ "${i}" -lt "${AUDIO_PW_WAIT:-30}" ] || return 1
            sleep 1
            i=$((i + 1))
        done
    done
}

# audio_record <dev> <file> <format> <rate> <channels> <seconds>: raw capture
audio_record() {
    if [ "${AUDIO_BACKEND}" = pipewire ]; then
        local pid rc
        pw-record --target "$1" --rate "$4" --channels "$5" --format s16 "$2.wav" &
        pid=$!
        sleep "$6"
        kill -INT "${pid}" 2>/dev/null
        wait "${pid}"
        rc=$?
        # pw-record has no duration option and its exit status after the
        # interrupt varies between versions: judge the samples it wrote.
        echo "pw-record exit ${rc}"
        audio_wav_raw "$2.wav" "$2" && [ -s "$2" ]
        return
    fi
    arecord -q -D "$1" -t raw -f "$3" -r "$4" -c "$5" -d "$6" "$2"
}

# audio_play <dev> <file> <format> <rate> <channels>: play raw PCM
audio_play() {
    if [ "${AUDIO_BACKEND}" = pipewire ]; then
        audio_wav "$2" "$2.wav" "$4" "$5" && pw-play --target "$1" "$2.wav"
        return
    fi
    aplay -q -D "$1" -t raw -f "$3" -r "$4" -c "$5" "$2"
}

# audio_play_record <play dev> <play file> <play format> <play rate>
#   <play channels> <capture dev> <capture file> <capture format>
#   <capture rate> <capture channels> <seconds>: record for <seconds>
#   while the file plays, starting the playback 0.5 s into the recording.
#   Returns the player's exit code, or 2 if the recorder failed or did not
#   end.
audio_play_record() {
    local rpid i rc
    audio_record "$6" "$7" "$8" "$9" "${10}" "${11}" > "$7.log" 2>&1 &
    rpid=$!
    sleep 0.5
    audio_play "$1" "$2" "$3" "$4" "$5" > "$2.log" 2>&1
    rc=$?
    i=0
    while kill -0 "${rpid}" 2>/dev/null && [ "${i}" -lt $((${11} + 10)) ]; do
        sleep 1
        i=$((i + 1))
    done
    if kill -0 "${rpid}" 2>/dev/null; then
        kill "${rpid}"
        wait "${rpid}"
        info_msg "arecord $6 still running after ${11} s"
        return 2
    fi
    wait "${rpid}" || { cat "$7.log"; return 2; }
    [ "${rc}" -eq 0 ] || cat "$2.log"
    return "${rc}"
}

# audio_detect <analysis line> <Hz> <baseline dBFS> <min dB> <tolerance Hz>:
# 0 when the line's peak is within tolerance of Hz and its level at Hz is at
# least min dB above the baseline
audio_detect() {
    local peak level
    peak="$(audio_field "$1" peak)"
    level="$(audio_field "$1" level)"
    awk -v p="${peak}" -v f="$2" -v l="${level}" -v b="$3" -v m="$4" -v t="$5" \
        'BEGIN { d = p - f; if (d < 0) d = -d; exit !(d <= t && l - b >= m) }'
}

# audio_since <uptime s>: kernel log lines after that time
audio_since() {
    dmesg | awk -v t="$1" '{ s = $0; sub(/^\[ */, "", s); if (s + 0 > t) print }'
}

# audio_kernel_errors <uptime s>: count of abort, WARN, BUG and oops lines
audio_kernel_errors() {
    audio_since "$1" | grep -cE "WARNING:|BUG:|Oops|Internal error|Unable to handle|external abort"
}

# audio_run_for <seconds> <command> [args]: run the command, kill it after
# the given seconds (not every system has timeout(1)); returns its exit
# code, or 124 when it was killed
audio_run_for() {
    local s="$1"
    local pid i=0
    shift
    "$@" &
    pid=$!
    while kill -0 "${pid}" 2>/dev/null && [ "${i}" -lt "$((s * 5))" ]; do
        sleep 0.2
        i=$((i + 1))
    done
    if kill -0 "${pid}" 2>/dev/null; then
        kill "${pid}" 2>/dev/null
        sleep 0.5
        kill -9 "${pid}" 2>/dev/null
        wait "${pid}" 2>/dev/null
        return 124
    fi
    wait "${pid}"
}

# audio_wait_card <id or name> <seconds>: wait for the card to appear;
# prints its index (empty when it never did)
audio_wait_card() {
    local i=0
    while [ -z "$(audio_card "$1")" ] && [ "${i}" -lt "$2" ]; do
        sleep 1
        i=$((i + 1))
    done
    audio_card "$1"
}

# audio_tone_check <raw> <Hz> <skip s> <len s> <silence raw> <min dB>: for an
# S16_LE stereo capture at AUDIO_RATE (48000), "<pass|fail> <dB over
# silence>" on the louder of the two channels: pass when the tone is
# detected there (audio_detect, 25 Hz tolerance) against the silence
# capture's level on that channel over the same length. With
# AUDIO_CHECK_ANY=1 (for controls that must not hear the tone) it passes
# when either channel detects it. A capture with no frames in the window
# (under 256 frames) reports "none": callers must not read that as "not
# detected". The
# analysis lines go to stderr.
audio_tone_check() {
    local r="${AUDIO_RATE:-48000}"
    local best="" bl="" ch line l b d hit=fail best_r=fail
    for ch in 1 2; do
        line="$(audio_analyze "$1" S16_LE "${r}" 2 "$3" "$4" "$2" | sed -n "${ch}p")"
        # spectrum.awk needs 256 frames for a level
        if [ -z "${line}" ] || [ "$(audio_field "${line}" frames)" -lt 256 ]; then
            echo "$1 +$3 s: no frames" 1>&2
            echo "none -200"
            return
        fi
        l="$(audio_field "${line}" level)"
        b="$(audio_field "$(audio_analyze "$5" S16_LE "${r}" 2 0.5 "$4" "$2" | sed -n "${ch}p")" level)"
        echo "$1 +$3 s: ${line} (silence ${b})" 1>&2
        audio_detect "${line}" "$2" "${b:--200}" "$6" 25 && [ "${AUDIO_CHECK_ANY:-0}" = 1 ] && hit=pass
        if [ -z "${bl}" ] || awk -v a="${l}" -v c="${bl}" 'BEGIN { exit !(a > c) }'; then
            best="${line}"; bl="${l}"; d="$(awk -v l="${l}" -v b="${b:--200}" 'BEGIN { printf "%.1f", l - b }')"
            if audio_detect "${line}" "$2" "${b:--200}" "$6" 25; then best_r=pass; else best_r=fail; fi
        fi
    done
    [ "${hit}" = pass ] && best_r=pass
    echo "${best_r} ${d}"
}

# audio_report_control <case> <positive result> <"result dB" from
# audio_tone_check with AUDIO_CHECK_ANY=1>: report a control that must NOT
# hear the tone. No capture ("none") fails; a control whose positive case
# did not pass proves nothing and is skipped; otherwise pass when the tone
# was not detected.
audio_report_control() {
    local res="${3%% *}"
    local db="${3##* }"
    if [ "${res}" = none ] || [ -z "${res}" ]; then
        info_msg "$1: no capture for the control"
        report_fail "$1"
    elif [ "$2" != pass ]; then
        info_msg "$1: the positive case did not pass, the control proves nothing"
        report_skip "$1"
    elif [ "${res}" = pass ]; then
        add_metric "$1" fail "${db}" dB
    else
        add_metric "$1" pass "${db}" dB
    fi
}

# audio_settle: on the pipewire backend, wait AUDIO_SETTLE (3) s: pw-play
# returns while the sink is still playing out its buffer, and the next
# capture would hear the tail
audio_settle() {
    [ "${AUDIO_BACKEND}" = pipewire ] && sleep "${AUDIO_SETTLE:-3}"
    return 0
}
