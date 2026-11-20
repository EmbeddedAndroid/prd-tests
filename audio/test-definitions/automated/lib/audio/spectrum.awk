# SPDX-License-Identifier: GPL-2.0-only
# Copyright (c) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# Level and spectral peak of each channel of interleaved PCM read as signed
# integers (od -An -v -t d2 for S16_LE, -t d4 for S24_LE/S32_LE), one or
# more per line. Needs math.awk. Variables (awk -v):
#   rate      sample rate in Hz
#   channels  channels per frame
#   bits      significant bits: 16, 24 (S24_LE: low 24 bits of 32) or 32
#   skip      seconds to ignore at the start (default 0)
#   len       seconds to analyse (default: the rest)
#   freq      frequency of interest in Hz (optional)
#   fmin, fmax  peak search range in Hz (default 100 and 0.45 x rate)
# Prints one line per channel:
#   ch=<n> frames=<n> rms=<dBFS> level=<dBFS at freq> peak=<Hz> peaklevel=<dBFS>
# rms is the level of a sine with the channel's RMS; level is the sine peak
# amplitude at freq from a Hann-windowed DFT over the whole window; peak is
# the largest bin of a Hann-windowed FFT averaged over blocks of up to 8192
# frames, refined by parabolic interpolation. -200 means digital zeros.
BEGIN {
    if (skip == "") skip = 0
    if (fmin == "") fmin = 100
    if (fmax == "") fmax = 0.45 * rate
    if (bits == 24) { wrap = m_pow2(24); half = m_pow2(23); full = m_pow2(23) }
    else { full = m_pow2(bits - 1) }
    first = int(skip * rate) * channels
    last = (len == "") ? -1 : first + int(len * rate) * channels
    pi = m_pi()
    k = 0
}
{
    for (i = 1; i <= NF; i++) {
        if (k >= first && (last < 0 || k < last)) {
            v = $i + 0
            if (wrap) {
                if (v < 0) v += m_pow2(32)
                v = v % wrap
                if (v >= half) v -= wrap
            }
            c = (k % channels) + 1
            x[c, cnt[c]++] = v
        }
        k++
    }
}
END {
    for (c = 1; c <= channels; c++) {
        n = cnt[c] + 0
        if (n < 256) {
            printf "ch=%d frames=%d rms=-200 level=-200 peak=0 peaklevel=-200\n", c, n
            continue
        }
        s = 0; s2 = 0
        for (i = 0; i < n; i++) { s += x[c, i]; s2 += x[c, i] * x[c, i] }
        mean = s / n
        var = s2 / n - mean * mean
        rms = (var > 0) ? m_sqrt(var) : 0
        lvl = (freq != "") ? db(goertzel(c, n, freq + 0, mean)) : -200
        peak(c, n, mean)
        printf "ch=%d frames=%d rms=%.1f level=%.1f peak=%.0f peaklevel=%.1f\n",
            c, n, db(rms * m_sqrt(2) / full), lvl, pf, pl
    }
}

function hann(n,    i, w0, c1, s1, cr, sr, t) {
    if (n == hn) return
    # w[i] = 0.5 - 0.5 cos(2 pi i / (n - 1)), cosines by rotation
    c1 = m_cos(2 * pi / (n - 1)); s1 = m_sin(2 * pi / (n - 1))
    cr = 1; sr = 0; hsum = 0
    for (i = 0; i < n; i++) {
        hw[i] = 0.5 - 0.5 * cr
        hsum += hw[i]
        t = cr * c1 - sr * s1; sr = sr * c1 + cr * s1; cr = t
    }
    hn = n
}

# Goertzel magnitude of the windowed, mean-removed channel at f, as a sine
# peak amplitude in full-scale units
function goertzel(c, n, f, mean,    i, cw, sw, coef, s0, s1, s2, re, im) {
    hann(n)
    cw = m_cos(2 * pi * f / rate); sw = m_sin(2 * pi * f / rate)
    coef = 2 * cw; s1 = 0; s2 = 0
    for (i = 0; i < n; i++) {
        s0 = (x[c, i] - mean) * hw[i] + coef * s1 - s2
        s2 = s1; s1 = s0
    }
    re = s1 - s2 * cw; im = s2 * sw
    w = m_sqrt(re * re + im * im)
    return 2 * w / hsum / full
}

# peak: sets pf (Hz) and pl (dBFS) from the block-averaged FFT
function peak(c, n, mean,    b, nb, blk, i, j, lo, hi, best, bi, a, y0, y1, y2, d, wsum) {
    b = 256
    while (b * 2 <= n && b < 8192) b *= 2
    nb = int(n / b); if (nb > 4) nb = 4
    for (i = 0; i < b / 2; i++) mag[i] = 0
    # Hann window of the block length
    wsum = 0
    for (i = 0; i < b; i++) {
        bw[i] = 0.5 - 0.5 * m_cos(2 * pi * i / b)
        wsum += bw[i]
    }
    for (blk = 0; blk < nb; blk++) {
        for (i = 0; i < b; i++) {
            re[i] = (x[c, blk * b + i] - mean) * bw[i]
            im[i] = 0
        }
        fft(b)
        for (i = 0; i < b / 2; i++) mag[i] += m_sqrt(re[i] * re[i] + im[i] * im[i]) / nb
    }
    lo = int(fmin * b / rate); if (lo < 1) lo = 1
    hi = int(fmax * b / rate); if (hi > b / 2 - 2) hi = b / 2 - 2
    best = -1; bi = lo
    for (i = lo; i <= hi; i++) if (mag[i] > best) { best = mag[i]; bi = i }
    d = 0
    if (best > 0 && mag[bi - 1] > 0 && mag[bi + 1] > 0) {
        y0 = m_log(mag[bi - 1]); y1 = m_log(best); y2 = m_log(mag[bi + 1])
        if (y0 - 2 * y1 + y2 != 0) d = 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2)
    }
    pf = (bi + d) * rate / b
    pl = db(2 * best / wsum / full)
}

# in-place iterative radix-2 FFT of re[0..b-1], im[0..b-1]
function fft(b,    i, j, k, m, len, half, wr, wi, cr, ci, tr, ti, t, ang) {
    j = 0
    for (i = 0; i < b - 1; i++) {
        if (i < j) {
            t = re[i]; re[i] = re[j]; re[j] = t
            t = im[i]; im[i] = im[j]; im[j] = t
        }
        m = b / 2
        while (m >= 1 && j >= m) { j -= m; m /= 2 }
        j += m
    }
    for (len = 2; len <= b; len *= 2) {
        half = len / 2
        ang = -2 * pi / len
        wr = m_cos(ang); wi = m_sin(ang)
        for (i = 0; i < b; i += len) {
            cr = 1; ci = 0
            for (k = 0; k < half; k++) {
                tr = re[i + k + half] * cr - im[i + k + half] * ci
                ti = re[i + k + half] * ci + im[i + k + half] * cr
                re[i + k + half] = re[i + k] - tr
                im[i + k + half] = im[i + k] - ti
                re[i + k] += tr
                im[i + k] += ti
                t = cr * wr - ci * wi; ci = cr * wi + ci * wr; cr = t
            }
        }
    }
}

function db(a) {
    return (a > 0) ? 20 * m_log(a) / m_log(10) : -200
}
