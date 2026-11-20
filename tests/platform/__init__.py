# SPDX-License-Identifier: GPL-2.0
"""Platform tests: remote processors, TEE, TPM, thermal, driver and
firmware provenance, low power statistics, and the explicit "no
standard interface" result.

Board profile keys used: absent.<key> for hardware the board lacks
(tpm), platform.sleep_stats (directory of low power statistics, for
example a debugfs sleep stats directory)."""
import glob
import os
import re
import time

from prd.core import Missing, have, read, sh, test


def discover(env):
    return None


def rprocs():
    out = {}
    for d in sorted(glob.glob('/sys/class/remoteproc/remoteproc*')):
        out[read(d + '/name').strip()] = d
    return out


@test('platform.remoteproc')
def t_remoteproc(env, names=None, restart=False):
    """Remote processors: named processors run, optionally survive a stop/start"""
    rp = rprocs()
    if not rp:
        raise Missing('no remote processor in /sys/class/remoteproc', hw='remoteproc')
    want = names or sorted(rp)
    missing = [n for n in want if n not in rp]
    states = {n: read(rp[n] + '/state').strip() for n in want if n in rp}
    down = [n for n, s in states.items() if s != 'running']
    meas = {'running': sorted(n for n, s in states.items() if s == 'running')}
    if missing or down:
        return 'fail', 'remoteproc %s; missing %s, not running %s' % (states, missing, down), meas
    if not restart:
        return 'pass', 'remoteproc %s' % states, meas
    times = {}
    for n in want:
        t0 = time.time()
        sh('echo stop > %s/state; sleep 1' % rp[n])
        if read(rp[n] + '/state').strip() != 'offline':
            times[n] = None
            continue
        sh('echo start > %s/state' % rp[n])
        st = ''
        for i in range(60):
            st = read(rp[n] + '/state').strip()
            if st == 'running':
                break
            time.sleep(0.5)
        times[n] = round(time.time() - t0, 1) if st == 'running' else None
    bad = [n for n, t in times.items() if t is None]
    meas['restart_s'] = max(t for t in times.values() if t is not None) if len(bad) < len(times) else None
    return ('fail' if bad else 'pass', 'stop/start recovery seconds %s%s' % (
        times, '; stop refused or not running again: %s' % bad if bad else ''), meas)


@test('platform.in_tree')
def t_in_tree(env, cls):
    """Drivers in tree: the modules bound to a device class carry no out-of-tree taint"""
    devs = sorted(glob.glob('/sys/class/%s/*' % cls))
    if not devs:
        raise Missing('no device in /sys/class/%s' % cls, hw=cls)
    mods, builtin = {}, set()
    for d in devs:
        p = os.path.realpath(d)
        while p.startswith('/sys/devices') and p != '/sys/devices':
            drv = os.path.join(p, 'driver')
            if os.path.exists(drv):
                dn = os.path.basename(os.path.realpath(drv))
                m = os.path.join(drv, 'module')
                if os.path.exists(m):
                    mn = os.path.basename(os.path.realpath(m))
                    mods[mn] = read('/sys/module/%s/taint' % mn).strip()
                else:
                    builtin.add(dn)
            elif os.path.isdir('/sys/module/%s' % os.path.basename(p).replace('-', '_')) and \
                    os.path.dirname(p).endswith('/platform'):
                # a platform device a module creates for itself, without a
                # driver to bind (a virtual device): named after its module
                mn = os.path.basename(p).replace('-', '_')
                mods[mn] = read('/sys/module/%s/taint' % mn).strip()
            p = os.path.dirname(p)
    if not mods and not builtin:
        return 'fail', 'no driver is bound to the %d %s devices' % (len(devs), cls)
    oot = sorted(m for m, t in mods.items() if 'O' in t or 'P' in t)
    taint = read('/proc/sys/kernel/tainted').strip()
    return ('fail' if oot else 'pass', '%s drivers: modules %s, built-in %s; out-of-tree or '
            'proprietary: %s; kernel taint %s (in-tree here means part of the running kernel '
            'tree, not necessarily merged upstream)' % (
                cls, ' '.join('%s%s' % (m, '[%s]' % t if t else '') for m, t in sorted(mods.items()))
                or 'none', ' '.join(sorted(builtin)) or 'none', ' '.join(oot) or 'none', taint),
            {'modules': sorted(mods), 'out_of_tree': oot})


@test('platform.firmware_packaged')
def t_firmware_packaged(env, paths, package='linux-firmware'):
    """Firmware provenance: the firmware files come from the named package"""
    files = sorted(set(f for p in paths for f in glob.glob(p)))
    if not files:
        return 'fail', 'no firmware file matches %s' % ' '.join(paths)
    owners = []
    for f in files:
        o = ''
        for q in ('opkg search %s', 'rpm -qf %s', 'dpkg -S %s'):
            rc, out = sh((q % f) + ' 2>/dev/null')
            if rc == 0 and out.strip():
                o = out.strip().splitlines()[0]
                break
        owners.append((f, o))
    ok = [f for f, o in owners if package in o]
    unk = [f for f, o in owners if not o]
    desc = '; '.join('%s <- %s' % (f, o or 'no package database entry') for f, o in owners[:10])
    if len(ok) == len(files):
        return 'pass', '%d files from %s: %s' % (len(ok), package, desc)
    return ('partial' if ok else 'fail', '%d of %d files from %s (%d without a package entry): %s'
            % (len(ok), len(files), package, len(unk), desc), {'packaged': len(ok), 'files': len(files)})


@test('platform.tee')
def t_tee(env, run_xtest=False):
    """TEE: a TEE device is registered and the client interface answers"""
    devs = sorted(glob.glob('/dev/tee[0-9]*'))
    if not devs:
        raise Missing('no /dev/tee* device', hw='tee')
    rc, dm = sh("dmesg | grep -iE 'optee|tee:' | head -4")
    if not run_xtest:
        return 'pass', 'TEE devices %s; %s' % (devs, dm.strip().replace('\n', ' | ')[:300])
    if not have('xtest'):
        return 'fail', 'xtest not on the image'
    rc, out = sh('xtest -l 1 2>&1 | tail -4', timeout=900)
    m = re.search(r'(\d+) subtests? of which (\d+) failed', out)
    return ('pass' if m and m.group(2) == '0' else 'fail', 'xtest level 1: %s' % out.strip().replace('\n', ' | '),
            {'failed': int(m.group(2)) if m else None})


@test('platform.tpm')
def t_tpm(env):
    """TPM: a TPM 2.0 device answers a random number request"""
    d = sorted(glob.glob('/sys/class/tpm/tpm*'))
    if not d:
        raise Missing('no TPM in /sys/class/tpm', hw='tpm')
    ver = read(d[0] + '/tpm_version_major').strip()
    if have('tpm2_getrandom'):
        rc, out = sh('tpm2_getrandom --hex 16 2>&1', timeout=30)
        ok = rc == 0 and re.fullmatch(r'[0-9a-f]{32}', out.strip()) is not None
        return ('pass' if ok else 'fail', '%s version %s; tpm2_getrandom: %s' % (
            os.path.basename(d[0]), ver, out.strip()[:80]))
    return 'partial', '%s version %s; tpm2-tools not on the image, no command sent' % (
        os.path.basename(d[0]), ver)


@test('platform.thermal')
def t_thermal(env, min_zones=1):
    """Thermal: zones report plausible temperatures and have trip points and cooling devices"""
    zones = sorted(glob.glob('/sys/class/thermal/thermal_zone*'))
    if not zones:
        raise Missing('no thermal zone in /sys/class/thermal', hw='thermal')
    bad, trips, ok = [], 0, 0
    for z in zones:
        t = read(z + '/temp').strip()
        try:
            v = int(t) / 1000.0
            if -40 <= v <= 125:
                ok += 1
            else:
                bad.append('%s %s %.1f C' % (os.path.basename(z), read(z + '/type').strip(), v))
        except ValueError:
            bad.append('%s unreadable' % os.path.basename(z))
        trips += len(glob.glob(z + '/trip_point_*_temp'))
    cool = len(glob.glob('/sys/class/thermal/cooling_device*'))
    return ('pass' if ok >= min_zones and not bad else 'fail',
            '%d zones (%d plausible), %d trip points, %d cooling devices%s' % (
                len(zones), ok, trips, cool, '; implausible: %s' % bad if bad else ''),
            {'zones': len(zones), 'trips': trips, 'cooling': cool})


@test('platform.sleep_stat')
def t_sleep_stat(env, pattern, seconds=10):
    """Low power statistics: a named low power mode's count advances at idle"""
    p = env.cfg('platform', 'sleep_stats')
    if not p:
        raise Missing('the board profile names no low power statistics directory '
                      '(platform.sleep_stats)', hw='sleep-stats')
    if not os.path.isdir(p):
        return 'fail', 'no low power statistics directory %s' % p
    ents = [e for e in sorted(os.listdir(p)) if re.search(pattern, e, re.I)]
    if not ents:
        return 'fail', 'no entry matching %s in %s (%s)' % (pattern, p, ' '.join(sorted(os.listdir(p))))
    cnt = lambda t: int((re.search(r'Count:\s*(\d+)', t) or [0, -1])[1])
    s0 = {e: cnt(read(os.path.join(p, e))) for e in ents}
    if all(v < 0 for v in s0.values()):
        return 'fail', 'entries %s carry no "Count:" field' % ents
    time.sleep(seconds)
    moved = [e for e in ents if cnt(read(os.path.join(p, e))) > s0[e]]
    return ('pass' if moved else 'fail', 'entries %s; count advanced over %d s idle: %s' % (
        ents, seconds, moved or 'none'))


@test('platform.no_interface')
def t_no_interface(env, feature, interface, look=None):
    """A feature no standard Linux interface exposes: fails, naming what is missing"""
    found = []
    for g in look or []:
        found += glob.glob(g)
    if found:
        return 'partial', '%s: candidate interface present (%s); not exercised' % (feature, found[:4])
    return 'fail', '%s: %s' % (feature, interface)
