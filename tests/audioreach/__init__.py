# SPDX-License-Identifier: GPL-2.0
"""Optional vendor module: Qualcomm AudioReach (PAL/AGM usecases and the
QACT calibration transport).

These features have no standard Linux interface. The board profile
enables the module with modules.audioreach: true on images that ship
the AudioReach user space; without it every test here fails with the
feature named, which is the result on an upstream-only image."""
import os
import re

from prd.core import have, read, sh, test


def need(env, feature):
    if not env.module('audioreach'):
        return ('fail', '%s: no standard Linux interface provides it and the board profile does '
                'not enable the AudioReach module (modules.audioreach)' % feature)
    return None


def discover(env):
    return None


def pal_conf():
    rc, out = sh("find /etc -maxdepth 3 -iname 'resourcemanager*.xml' 2>/dev/null | head -3")
    return sorted(set(l for l in out.split() if l.startswith('/')))


@test('audioreach.usecase')
def t_usecase(env, pattern, feature):
    """A PAL usecase or processing module named in the resource manager configuration"""
    r = need(env, feature)
    if r:
        return r
    files = pal_conf()
    if not files:
        return 'fail', '%s: no PAL resource manager configuration on this image' % feature
    hits = set()
    for f in files:
        hits |= set(m for m in re.findall(r'[A-Z0-9_]*(?:%s)[A-Z0-9_]*' % pattern, read(f), re.I))
    if not hits:
        return 'fail', '%s: no entry matching %s in %s' % (feature, pattern, ' '.join(files))
    return 'partial', '%s: PAL configuration entries %s; not exercised' % (feature, sorted(hits)[:8])


@test('audioreach.qact')
def t_qact(env, feature='QACT calibration transport'):
    """QACT: on-device calibration transport for the host tool"""
    r = need(env, feature)
    if r:
        return r
    tools = [t for t in ('ats_server', 'qact_server', 'acdb_rtac', 'diag-router') if have(t)]
    dev = os.path.exists('/dev/diag')
    if not tools and not dev:
        return 'fail', '%s: no ATS/QACT server and no diag device on the device' % feature
    return 'partial', '%s: %s%s; host session not exercised' % (
        feature, tools, ', /dev/diag' if dev else '')


@test('audioreach.acdb')
def t_acdb(env):
    """Audio calibration database: an ACDB file for the board is installed"""
    r = need(env, 'Audio calibration database')
    if r:
        return r
    rc, out = sh("find /etc/acdbdata /vendor/etc/acdbdata -name '*.acdb' 2>/dev/null | head -8")
    files = out.split()
    if not files:
        return 'fail', 'no ACDB file under /etc/acdbdata'
    return 'pass', 'ACDB files: %s' % ' '.join(files), {'files': len(files)}
