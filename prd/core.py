# SPDX-License-Identifier: GPL-2.0
"""core.py: test registry, results and target evaluation (python3
standard library only, so it runs on any image with python3).

A test is a function registered with @test('<interface>.<name>'). It
takes the run environment and the keyword arguments the product map
gives it, discovers what the device has, and returns
(status, reason[, measurements]) where status is pass, fail or partial
and measurements is a dict of numbers or strings. It raises:

  Skip(reason)                the device lacks the hardware or attached
                              sink the test needs (an HDMI monitor, a
                              camera sensor); the result is skip.
  Missing(feature, hw=key)    the interface the test needs is not
                              exposed at all. When the board profile
                              lists key under 'absent', the board does
                              not have that hardware and the result is
                              skip with the profile's reason; otherwise
                              the feature is missing and the result is
                              fail, or skip in a product that sets
                              "missing: skip" (one that asks which
                              interfaces work, not which features a
                              product needs).

Any other exception is a fail with the exception text; so is a test
that has no result after 900 s.

The runner then applies the requirement's target to the measurements
(the board profile's targets.<ID> where it gives one, else the
requirement's own): a pass whose measurements miss the target, or do
not measure it, is partial; so is a pass for a requirement whose
target is "none" (the requirement states no number to meet). partial
is reported to LAVA as "unknown" so it is never counted as a pass."""
import importlib
import json
import os
import signal
import subprocess
import sys
import time
import traceback

LAVA = {'pass': 'pass', 'fail': 'fail', 'skip': 'skip', 'partial': 'unknown'}
REGISTRY = {}
# reason prefix of a missing interface under "missing: skip"
ABSENT = 'interface absent: '
NO_TARGET = '; measured, but the requirement states no target to meet'
MISSED = '; target not met: '


class Skip(Exception):
    pass


class Timeout(Exception):
    pass


def _alarm(signum, frame):
    raise Timeout('no result within the time limit')


class Missing(Exception):
    def __init__(self, feature, hw=None):
        Exception.__init__(self, feature)
        self.hw = hw


def test(name):
    def reg(fn):
        REGISTRY[name] = fn
        fn.test_name = name
        return fn
    return reg


def sh(cmd, timeout=None):
    """Run a shell command, return (rc, combined output); rc 124 on timeout."""
    try:
        p = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout)
    except subprocess.TimeoutExpired as e:
        return 124, (e.stdout or b'').decode('utf-8', 'replace')
    return p.returncode, p.stdout.decode('utf-8', 'replace')


def read(path, default=''):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


def have(tool):
    return any(os.access(os.path.join(d, tool), os.X_OK)
               for d in os.environ.get('PATH', '/usr/bin:/bin').split(':'))


def load_data(path):
    """Board profiles and product maps are YAML in the tree; the overlay
    carries a JSON copy next to each so the board needs no YAML module.
    A file with a "base" key is that file (same directory) with this
    one's keys merged over it, dictionaries key by key."""
    j = os.path.splitext(path)[0] + '.json'
    if path.endswith('.json') or (os.path.exists(j) and not os.path.exists(path)):
        with open(j) as f:
            d = json.load(f)
    else:
        try:
            import yaml
            with open(path) as f:
                d = yaml.safe_load(f)
        except ImportError:
            with open(j) as f:
                d = json.load(f)
    if isinstance(d, dict) and 'base' in d:
        b = load_data(os.path.join(os.path.dirname(path), d.pop('base') + os.path.splitext(path)[1]))
        d = merge(b, d)
    return d


def merge(a, b):
    if not isinstance(a, dict) or not isinstance(b, dict):
        return b
    r = dict(a)
    for k, v in b.items():
        r[k] = merge(a.get(k), v)
    return r


class Env:
    """What a test sees: the board profile and one cached discovery
    object per interface (built on first use by the interface module's
    discover(env))."""

    def __init__(self, board):
        self.board = board or {}
        self._ctx = {}

    def cfg(self, *path, default=None):
        d = self.board
        for k in path:
            if not isinstance(d, dict) or k not in d:
                return default
            d = d[k]
        return d

    def module(self, name):
        return bool(self.cfg('modules', name, default=False))

    def ctx(self, iface):
        if iface not in self._ctx:
            mod = sys.modules['tests.%s' % iface]
            try:
                self._ctx[iface] = mod.discover(self)
            except (Skip, Missing) as e:
                self._ctx[iface] = e
        c = self._ctx[iface]
        if isinstance(c, (Skip, Missing)):
            raise c
        return c


def check_target(meas, target):
    """Return the list of target misses (empty when every target is met)."""
    miss = []
    for k, want in (target or {}).items():
        if k not in meas or meas[k] is None:
            miss.append('%s not measured' % k)
            continue
        got = meas[k]
        if isinstance(want, dict):
            if 'min' in want and not got >= want['min']:
                miss.append('%s %s < %s' % (k, got, want['min']))
            if 'max' in want and not got <= want['max']:
                miss.append('%s %s > %s' % (k, got, want['max']))
            if 'eq' in want and got != want['eq']:
                miss.append('%s %s != %s' % (k, got, want['eq']))
        elif isinstance(want, list):
            have_ = got if isinstance(got, (list, tuple, set)) else str(got).split()
            lack = [w for w in want if w not in have_]
            if lack:
                miss.append('%s lacks %s' % (k, ' '.join(map(str, lack))))
        elif isinstance(want, bool) or isinstance(want, str):
            if got != want:
                miss.append('%s %s != %s' % (k, got, want))
        elif not got >= want:
            miss.append('%s %s < %s' % (k, got, want))
    return miss


def fmt_meas(meas):
    out = []
    for k, v in sorted(meas.items()):
        if isinstance(v, (list, tuple, set)):
            v = ','.join(map(str, v)) or '-'
        out.append('%s=%s' % (k, v))
    return ' '.join(out)


def combine(parts):
    """Status and reason of a requirement with several tests from their
    (test, status, reason): fail when any part fails, pass when all pass,
    skip when all skip, partial otherwise."""
    st = [p[1] for p in parts]
    reason = ' || '.join('%s %s: %s' % p for p in parts)
    if 'fail' in st:
        return 'fail', reason
    if all(s == 'skip' for s in st):
        return 'skip', reason
    if all(s == 'pass' for s in st):
        return 'pass', reason
    return 'partial', reason


def settle(status, reason, meas, target, note=None):
    """Apply a target ("none", or a dict for check_target) and a note to
    a test's status and reason."""
    if status == 'pass' and target == 'none':
        status = 'partial'
        reason += NO_TARGET
    elif status == 'pass' and target:
        try:
            miss = check_target(meas, target)
        except Exception as e:  # a measurement that cannot be compared misses
            miss = ['target %r not comparable: %s' % (target, e)]
        if miss:
            status = 'partial'
            reason += MISSED + '; '.join(miss)
    if note:
        reason += ' (%s)' % note
    return status, reason


def as_missing_fail(tests, status, reason):
    """A result recorded under "missing: skip" as a product with the
    default policy sees it: a part skipped with ABSENT is a fail with
    the reason the test gave. tests is the list of the requirement's
    test names (one, or the parts in order). A result with no such part
    is returned as it is: a target or note settle() applied stays. With
    such a part the runner never got a pass, so no target was applied.
    ValueError when a composite reason does not split into its parts."""
    if len(tests) == 1:
        if status == 'skip' and reason.startswith(ABSENT):
            return 'fail', reason[len(ABSENT):]
        return status, reason
    parts = []
    moved = False
    rest = reason
    for i, t in enumerate(tests):
        head = '%s ' % t
        if not rest.startswith(head):
            raise ValueError('reason does not start with part %s: %r' % (t, rest[:80]))
        rest = rest[len(head):]
        st, _, rest = rest.partition(': ')
        if i + 1 < len(tests):
            nxt = ' || %s ' % tests[i + 1]
            j = rest.find(nxt)
            if j < 0:
                raise ValueError('no part %s in %r' % (tests[i + 1], rest[:80]))
            why, rest = rest[:j], rest[j + 4:]
        else:
            why = rest
        if st == 'skip' and why.startswith(ABSENT):
            st, why, moved = 'fail', why[len(ABSENT):], True
        parts.append((t, st, why))
    return combine(parts) if moved else (status, reason)


class Runner:
    def __init__(self, env, outdir='output'):
        self.env = env
        # products that ask whether each interface works, not whether the
        # product's features are there, report a missing interface as skip
        self.missing = 'fail'
        self.outdir = outdir
        os.makedirs(outdir, exist_ok=True)
        self.res = open(os.path.join(outdir, 'result.txt'), 'w')
        self.js = open(os.path.join(outdir, 'results.jsonl'), 'w')
        self.counts = {}

    def call(self, name, args, limit=900):
        fn = REGISTRY.get(name)
        if fn is None:
            return 'fail', 'no test named %s in this tree' % name, {}
        old = signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(limit)
        try:
            r = fn(self.env, **(args or {}))
        except Skip as e:
            return 'skip', str(e), {}
        except Missing as e:
            why = self.env.cfg('absent', e.hw) if e.hw else None
            if why:
                return 'skip', '%s; board: %s' % (e, why), {}
            if self.missing == 'skip':
                return 'skip', ABSENT + str(e), {}
            return 'fail', str(e), {}
        except Exception as e:  # a crash in a test is a fail, with the reason
            traceback.print_exc(file=sys.stdout)
            return 'fail', 'test raised %s: %s' % (type(e).__name__, e), {}
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)
        status, reason = r[0], r[1]
        meas = dict(r[2]) if len(r) > 2 and r[2] else {}
        if status not in ('pass', 'fail', 'partial'):
            return 'fail', 'test returned bad status %r: %s' % (status, reason), meas
        return status, reason, meas

    def composite(self, parts):
        """Several tests for one requirement: fail when any part fails,
        pass when all pass, skip when all skip, partial otherwise."""
        res = [self.call(p['test'], p.get('args')) for p in parts]
        meas = {}
        for r in res:
            meas.update(r[2])
        st, reason = combine([(p['test'], r[0], r[1]) for p, r in zip(parts, res)])
        return st, reason, meas

    def requirement(self, rid, req):
        name = req.get('test') or '+'.join(p['test'] for p in req.get('tests', []))
        t0 = time.time()
        print('=== %s %s: %s' % (rid, req.get('title', ''), name), flush=True)
        if req.get('tests'):
            status, reason, meas = self.composite(req['tests'])
        else:
            status, reason, meas = self.call(name, req.get('args'))
        target = self.env.cfg('targets', rid, default=req.get('target'))
        status, reason = settle(status, reason, meas, target, req.get('note'))
        self.counts[status] = self.counts.get(status, 0) + 1
        dt = time.time() - t0
        print('PRD %s %s: %s%s (%.1f s)' % (rid, status, reason,
                                           ' [%s]' % fmt_meas(meas) if meas else '', dt), flush=True)
        self.res.write('%s %s\n' % (rid, LAVA[status]))
        self.res.flush()
        self.js.write(json.dumps({'id': rid, 'test': name, 'status': status, 'reason': reason,
                                  'meas': {k: (sorted(v) if isinstance(v, set) else v)
                                           for k, v in meas.items()},
                                  'seconds': round(dt, 1)}) + '\n')
        self.js.flush()

    def done(self):
        self.res.close()
        self.js.close()
        print('PRD SUMMARY ' + ' '.join('%s=%d' % kv for kv in sorted(self.counts.items())),
              flush=True)


def import_tests(top, ifaces=None):
    """Import tests/<iface>/__init__.py for every interface directory."""
    if top not in sys.path:
        sys.path.insert(0, top)
    importlib.import_module('tests')
    for d in sorted(os.listdir(os.path.join(top, 'tests'))):
        if ifaces and d not in ifaces:
            continue
        if os.path.exists(os.path.join(top, 'tests', d, '__init__.py')):
            importlib.import_module('tests.%s' % d)
