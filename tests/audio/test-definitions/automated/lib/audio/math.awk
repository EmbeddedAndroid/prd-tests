# SPDX-License-Identifier: GPL-2.0-only
# Copyright (c) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# Arithmetic-only cos, sin, sqrt, natural log and exp, for awk builds
# without the math library (BusyBox without FEATURE_AWK_LIBM). Accurate to
# about 1e-12 over the ranges the audio helpers use.

function m_pi() { return 3.14159265358979323846 }

function m_cos(x,    t, s, k, x2) {
    x = x - 2 * m_pi() * int(x / (2 * m_pi()))
    if (x > m_pi()) x -= 2 * m_pi()
    if (x < -m_pi()) x += 2 * m_pi()
    x2 = x * x; t = 1; s = 1
    for (k = 1; k < 18; k++) {
        t = -t * x2 / ((2 * k - 1) * (2 * k))
        s += t
    }
    return s
}

function m_sin(x) { return m_cos(x - m_pi() / 2) }

function m_sqrt(x,    r, k) {
    if (x <= 0) return 0
    r = (x > 1) ? x : 1
    for (k = 0; k < 200; k++) {
        r = 0.5 * (r + x / r)
        if (r * r - x < 1e-12 * x && x - r * r < 1e-12 * x) break
    }
    return r
}

function m_log(x,    e, y, y2, t, s, k) {
    if (x <= 0) return -1e300
    e = 0
    while (x >= 1) { x /= 2; e++ }
    while (x < 0.5) { x *= 2; e-- }
    y = (x - 1) / (x + 1); y2 = y * y; t = y; s = 0
    for (k = 1; k < 60; k += 2) { s += t / k; t *= y2 }
    return 2 * s + e * 0.69314718055994530942
}

function m_exp(x,    n, t, s, k) {
    n = 0
    while (x > 0.5) { x /= 2; n++ }
    while (x < -0.5) { x /= 2; n++ }
    t = 1; s = 1
    for (k = 1; k < 25; k++) { t = t * x / k; s += t }
    for (k = 0; k < n; k++) s = s * s
    return s
}

function m_pow2(n,    r) {
    r = 1
    while (n-- > 0) r *= 2
    return r
}
