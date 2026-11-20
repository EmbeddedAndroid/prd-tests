# SPDX-License-Identifier: GPL-2.0-only
# Copyright (c) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# Writes interleaved PCM as printf(1) octal escapes, one line per 64 frames,
# so that a POSIX shell can turn it into bytes (awk cannot portably write a
# NUL byte). Variables (awk -v):
#   rate      sample rate in Hz
#   channels  channels per frame
#   mask      channels that carry the signal, 1-based, comma separated;
#             "all" for every channel; the others are digital silence
#   segments  "<Hz>:<seconds> ..." played one after the other; 0 Hz is
#             silence
#   dbfs      sine peak level in dB relative to full scale (default -12)
#   bytes     bytes per sample: 2 (S16_LE), 3 (S24_3LE) or 4 (S24_LE or
#             S32_LE, see shift)
#   shift     left shift of a 24-bit sample in a 4-byte container: 0 for
#             S24_LE, 8 for S32_LE
BEGIN {
    if (bytes == "") bytes = 2
    if (shift == "") shift = 0
    if (dbfs == "") dbfs = -12
    bits = (bytes == 2) ? 16 : 24
    full = m_pow2(bits - 1) - 1
    amp = full * m_exp(dbfs / 20 * m_log(10))
    pi = m_pi()
    for (c = 1; c <= channels; c++)
        on[c] = (mask == "all")
    n = split(mask, m, ",")
    for (i = 1; i <= n; i++)
        on[m[i] + 0] = 1
    for (b = 0; b < 256; b++)
        oct[b] = sprintf("\\%03o", b)
    line = ""
    frames = 0
    phase = 0
    nseg = split(segments, seg, " ")
    for (s = 1; s <= nseg; s++) {
        split(seg[s], fd, ":")
        f = fd[1] + 0
        len = int(fd[2] * rate + 0.5)
        dphi = 2 * pi * f / rate
        for (i = 0; i < len; i++) {
            v = (f > 0) ? int(amp * m_sin(phase)) : 0
            phase += dphi
            if (phase > 2 * pi) phase -= 2 * pi
            for (c = 1; c <= channels; c++)
                line = line sample(on[c] ? v : 0)
            if (++frames % 64 == 0) {
                print line
                line = ""
            }
        }
    }
    if (line != "") print line
}

function sample(v,    u, out, k) {
    if (bytes == 4 && shift > 0) v = v * m_pow2(shift)
    u = (v < 0) ? v + m_pow2(8 * bytes) : v
    out = ""
    for (k = 0; k < bytes; k++) {
        out = out oct[u % 256]
        u = int(u / 256)
    }
    return out
}
