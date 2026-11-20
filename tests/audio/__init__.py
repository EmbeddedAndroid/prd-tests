# SPDX-License-Identifier: GPL-2.0
"""Audio tests on ALSA (cards, PCMs, mixer controls, compress offload),
UCM and PipeWire.

Acoustic tests play a tone on the board's speaker route and record it
on its microphone route (both from the board profile); levels are the
Goertzel power at the tone, compared with a reference that cannot hold
the tone: a capture with the speaker route muted (DSP restart, pause),
the lead-in before playback (concurrent streams) or the noise floor
before the start (latencies), so a quiet room cannot pass.
Without a loopback route in the profile they raise Missing with key
audio-loopback (skip only where the profile lists it as absent).

Board profile keys (audio.*): card (id or name; default the first card
with a playback and a capture PCM), play / capture ({pcm: name prefix or
device: number, mixer: 'ctl=value;...'}), mute / unmute (mixer strings;
without them the control capture is taken without playback), play2 (a
second playback front end: pcm or device, mixer, off), volume_step (mixer string of a
lower volume), loopback ({freq, min_db}: present when the speaker is
heard by the microphones), dsp (remoteproc name of the audio DSP),
cases ({name: {dir, params, hw}}: the parameters of a shell test-definition
case under tests/audio/test-definitions; hw names the hardware the case
needs, so a case that passes nothing is missing hardware: skip where the
profile lists hw as absent, fail otherwise), case_env (environment for
every case)."""
import math
import os
import pty
import re
import signal
import struct
import subprocess
import tempfile
import time

from prd.core import Missing, have, read, sh, test

RATE = 48000
TD = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test-definitions')


def cards():
    out = []
    for line in read('/proc/asound/cards').splitlines():
        m = re.match(r'\s*(\d+) \[(\S+)\s*\]: (\S+) - (.*)', line)
        if m:
            out.append({'index': int(m.group(1)), 'id': m.group(2), 'driver': m.group(3),
                        'name': m.group(4).strip()})
    return out


def pcms(card):
    """{(dir, dev): name} from /proc/asound/pcm."""
    out = {}
    for line in read('/proc/asound/pcm').splitlines():
        m = re.match(r'(\d+)-(\d+): ([^:]*) : [^:]* :(.*)', line)
        if m and int(m.group(1)) == card:
            name = m.group(3).strip()
            if 'playback' in m.group(4):
                out[('p', int(m.group(2)))] = name
            if 'capture' in m.group(4):
                out[('c', int(m.group(2)))] = name
    return out


def mixer(card, settings):
    """Apply 'ctl=value;...'; returns the controls that were refused."""
    bad = []
    for s in (settings or '').split(';'):
        if not s.strip():
            continue
        name, val = s.rsplit('=', 1)
        r = subprocess.run(['amixer', '-c', str(card), '-q', 'cset', 'name=' + name, val],
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if r.returncode:
            bad.append(name)
    return bad


def tone(path, freq, secs, dbfs=-6.0, ch=2, rate=RATE):
    amp = 32767 * 10 ** (dbfs / 20)
    w = 2 * math.pi * freq / rate
    one = [struct.pack('<h', int(amp * math.sin(w * i))) * ch for i in range(rate)]
    n = int(secs * rate)
    with open(path, 'wb') as f:
        f.write(b''.join(one) * (n // rate) + b''.join(one[:n % rate]))
    return path


def read_s16(path, ch=2):
    d = open(path, 'rb').read()
    n = len(d) // (2 * ch)
    s = struct.unpack('<%dh' % (n * ch), d[:n * ch * 2])
    return [s[c::ch] for c in range(ch)]


def goertzel_db(x, freq, rate=RATE):
    """Power at freq in dBFS (0 dB = full-scale sine)."""
    n = len(x)
    if n < 64:
        return -200.0
    k = 2 * math.cos(2 * math.pi * freq / rate)
    s1 = s2 = 0.0
    for v in x:
        s0 = v + k * s1 - s2
        s2, s1 = s1, s0
    p = s1 * s1 + s2 * s2 - k * s1 * s2
    return 20 * math.log10(2 * math.sqrt(max(p, 0)) / n / 32768 + 1e-12)


def level(path, freq, skip=0.0, length=None):
    a = int(skip * RATE)
    b = a + int(length * RATE) if length else None
    return max(goertzel_db(c[a:b], freq) for c in read_s16(path))


class Audio:
    def __init__(self, env):
        self.env = env
        self.cfg = env.cfg('audio', default={}) or {}
        self.tmp = tempfile.mkdtemp(prefix='prd-audio-')
        want = self.cfg.get('card')
        for i in range(60 if want else 1):
            cs = cards()
            if not want or any(want in (c['id'], c['name']) for c in cs):
                break
            time.sleep(1)
        if not cs:
            raise Missing('no sound card registered (/proc/asound/cards is empty)', hw='sound')
        if want:
            c = next((c for c in cs if want in (c['id'], c['name'])), None)
            if not c:
                raise Missing('sound card %s not registered (cards: %s)' % (
                    want, ', '.join(c['id'] for c in cs)), hw='sound')
        else:
            c = next((c for c in cs if any(k[0] == 'p' for k in pcms(c['index'])) and
                      any(k[0] == 'c' for k in pcms(c['index']))), cs[0])
        self.card = c['index']
        self.info = c
        self.pcm = pcms(self.card)
        self.pdev = self.find('p', self.cfg.get('play'))
        self.cdev = self.find('c', self.cfg.get('capture'))
        rc, out = sh('amixer -c %d controls' % self.card)
        self.ctls = re.findall(r"name='([^']*)'", out)
        self._sil = None
        print('ALSA card %d %s (%s), %d PCMs, %d controls, play dev %s capture dev %s' % (
            self.card, c['id'], c['driver'], len(self.pcm), len(self.ctls), self.pdev, self.cdev),
            flush=True)

    def find(self, d, spec):
        """PCM device for a profile entry: {device: N} or {pcm: name prefix}."""
        spec = spec or {}
        if 'device' in spec:
            return spec['device'] if (d, spec['device']) in self.pcm else None
        for (dd, dev), name in sorted(self.pcm.items()):
            if dd == d and (not spec.get('pcm') or name.startswith(spec['pcm'])):
                return dev
        return None

    def route(self):
        bad = mixer(self.card, (self.cfg.get('play') or {}).get('mixer'))
        return bad + mixer(self.card, (self.cfg.get('capture') or {}).get('mixer'))

    def need_loop(self):
        if not self.cfg.get('loopback'):
            raise Missing('no speaker-to-microphone loopback route in the board profile '
                          '(audio.loopback)', hw='audio-loopback')
        if self.pdev is None or self.cdev is None:
            raise Missing('playback or capture PCM not found on card %d' % self.card, hw='audio-loopback')
        self.route()
        lb = self.cfg['loopback']
        return lb.get('freq', 1000), lb.get('min_db', 15.0)

    def f(self, name):
        return os.path.join(self.tmp, name)

    def record(self, path, secs, dev=None):
        return subprocess.Popen(['arecord', '-q', '-D', 'hw:%d,%d' % (self.card, self.cdev if dev is None else dev),
                                 '-t', 'raw', '-f', 'S16_LE', '-r', str(RATE), '-c', '2', '-d',
                                 str(int(math.ceil(secs))), path],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def play(self, path, dev=None, extra=None):
        return subprocess.Popen(['aplay', '-q', '-D', 'hw:%d,%d' % (self.card, self.pdev if dev is None else dev),
                                 '-t', 'raw', '-f', 'S16_LE', '-r', str(RATE), '-c', '2'] +
                                (extra or []) + [path],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def play_record(self, src, secs, lead=0.5):
        cap = self.f('cap-%d.raw' % int(time.time() * 1000))
        r = self.record(cap, secs)
        time.sleep(lead)
        p = self.play(src)
        out = p.communicate()[0].decode(errors='replace') + r.communicate()[0].decode(errors='replace')
        return cap, p.returncode, out

    def silence(self, freq):
        """The control: a capture while the tone plays with the speaker route
        muted, or without playback where the profile has no mute route."""
        if self._sil is None:
            if self.cfg.get('mute'):
                mixer(self.card, self.cfg.get('mute'))
                cap, rc, out = self.play_record(tone(self.f('sil.raw'), freq, 3), 4)
                mixer(self.card, self.cfg.get('unmute'))
            else:
                cap = self.f('sil-cap.raw')
                self.record(cap, 4).communicate()
            self._sil = level(cap, freq, 1.0, 2.0)
        return self._sil

    def detect(self, cap, freq, min_db, skip, length):
        lv = level(cap, freq, skip, length)
        base = self.silence(freq)
        return lv - base >= min_db, lv, base


def discover(env):
    return Audio(env)


def A(env):
    return env.ctx('audio')


# ---- inventory
@test('audio.cards')
def t_cards(env):
    """Sound cards with playback and capture PCMs are registered"""
    a = A(env)
    np_ = sum(1 for k in a.pcm if k[0] == 'p')
    nc = sum(1 for k in a.pcm if k[0] == 'c')
    return ('pass' if np_ and nc else 'fail', 'card %d %s (%s): %d playback, %d capture PCMs, '
            '%d mixer controls' % (a.card, a.info['id'], a.info['driver'], np_, nc, len(a.ctls)),
            {'playback': np_, 'capture': nc, 'controls': len(a.ctls)})


def hw_ranges(a, d, dev):
    tool = 'aplay' if d == 'p' else 'arecord'
    rc, out = sh('timeout 5 %s -D hw:%d,%d -t raw --dump-hw-params -d 1 /dev/null 2>&1' % (
        tool, a.card, dev), timeout=10)
    fm = re.search(r'FORMAT:\s*(.*)', out)
    ch = re.search(r'CHANNELS:\s*(.*)', out)
    rt = re.search(r'RATE:\s*(.*)', out)
    return (fm.group(1).split() if fm else [], ch.group(1).strip() if ch else '',
            rt.group(1).strip() if rt else '', out)


def range_vals(s):
    """'[8000 192000]' or '48000' or '{1 2}' -> (min, max)."""
    nums = [int(x) for x in re.findall(r'\d+', s)]
    return (min(nums), max(nums)) if nums else (0, 0)


@test('audio.pcm_caps')
def t_pcm_caps(env, direction='playback'):
    """PCM capabilities: sample formats, channel and rate ranges of the board's PCM"""
    a = A(env)
    d = 'p' if direction == 'playback' else 'c'
    dev = a.pdev if d == 'p' else a.cdev
    if dev is None:
        raise Missing('no %s PCM on card %d' % (direction, a.card), hw='audio-%s' % direction)
    fm, ch, rt, out = hw_ranges(a, d, dev)
    if not fm:
        return 'fail', 'hw params of hw:%d,%d not readable: %s' % (a.card, dev, out.strip()[-200:])
    cmin, cmax = range_vals(ch)
    rmin, rmax = range_vals(rt)
    return ('pass', '%s hw:%d,%d (%s): formats %s, channels %s, rate %s' % (
        direction, a.card, dev, a.pcm[(d, dev)], ' '.join(fm), ch, rt),
        {'formats': fm, 'channels': cmax, 'min_rate': rmin, 'max_rate': rmax})


@test('audio.controls')
def t_controls(env, patterns, feature):
    """A mixer control for a processing feature exists on the card"""
    a = A(env)
    hit = [c for c in a.ctls if any(re.search(p, c, re.I) for p in patterns)]
    if not hit:
        return 'fail', '%s: no control matching %s among the %d controls of card %s' % (
            feature, '/'.join(patterns), len(a.ctls), a.info['id'])
    return 'partial', '%s: controls %s; effect not measured' % (feature, hit[:8])


CODEC = {1: 'PCM', 2: 'MP3', 3: 'AMR', 4: 'AMRWB', 5: 'AMRWB+', 6: 'AAC', 7: 'WMA', 8: 'REAL',
         9: 'VORBIS', 10: 'FLAC', 11: 'IEC61937', 12: 'G723_1', 13: 'G729', 14: 'BESPOKE',
         15: 'ALAC', 16: 'APE', 17: 'OPUS'}


def compr_caps(card, dev):
    """SNDRV_COMPRESS_GET_CAPS on /dev/snd/comprC<card>D<dev>: codec names."""
    import fcntl
    size = 6 * 4 + 32 * 4 + 11 * 4
    req = (3 << 30) | (size << 16) | (ord('C') << 8) | 0x10
    # the open mode selects the direction: write for playback offload
    try:
        fd = os.open('/dev/snd/comprC%dD%d' % (card, dev), os.O_WRONLY)
    except OSError:
        fd = os.open('/dev/snd/comprC%dD%d' % (card, dev), os.O_RDONLY)
    try:
        b = bytearray(size)
        fcntl.ioctl(fd, req, b, True)
    finally:
        os.close(fd)
    n = struct.unpack_from('<I', b, 0)[0]
    return [CODEC.get(c, str(c)) for c in struct.unpack_from('<%dI' % n, b, 24)]


@test('audio.compress_codec')
def t_compress_codec(env, codec, label=None):
    """Compressed offload: a codec is advertised by a compress device of the card"""
    a = A(env)
    caps = {}
    for f in sorted(os.listdir('/dev/snd')):
        m = re.match(r'comprC(\d+)D(\d+)$', f)
        if m and int(m.group(1)) == a.card:
            try:
                caps[int(m.group(2))] = compr_caps(a.card, int(m.group(2)))
            except OSError as e:
                caps[int(m.group(2))] = ['GET_CAPS error %s' % e]
    label = label or codec
    if not caps:
        raise Missing('%s: card %s has no compress offload device' % (label, a.info['id']),
                      hw='compress-offload')
    desc = '; '.join('comprC%dD%d: %s' % (a.card, dv, ' '.join(c)) for dv, c in caps.items())
    if not any(codec in c for c in caps.values()):
        return 'fail', '%s is not among the offload codecs advertised (%s)' % (label, desc)
    return ('partial', '%s advertised (%s); decode of a %s stream not exercised' % (
        label, desc, label), {'codecs': sorted(set(x for c in caps.values() for x in c))})


@test('audio.ucm')
def t_ucm(env):
    """UCM: the card has a use case configuration with verbs and devices"""
    a = A(env)
    if not have('alsaucm'):
        return 'fail', 'alsaucm not on the image'
    rc, out = sh('alsaucm -c hw:%d list _verbs 2>&1' % a.card, timeout=20)
    verbs = re.findall(r'^\s*\d+:\s*(\S+)', out, re.M)
    if rc or not verbs:
        raise Missing('no UCM configuration for card %s: %s' % (a.info['id'], out.strip()[-200:]),
                      hw='ucm')
    rc, dv = sh('alsaucm -c hw:%d set _verb %s list _devices 2>&1' % (a.card, verbs[0]), timeout=20)
    devs = re.findall(r'^\s*\d+:\s*(\S+)', dv, re.M)
    return ('pass' if devs else 'partial', 'UCM verbs %s; devices of %s: %s' % (
        verbs, verbs[0], devs or dv.strip()[-120:]), {'verbs': verbs, 'devices': devs})


@test('audio.pipewire')
def t_pipewire(env):
    """PipeWire: the sound server runs and exposes audio sinks and sources"""
    if not have('pw-cli'):
        raise Missing('PipeWire (pw-cli) not on the image', hw='pipewire')
    e = 'PIPEWIRE_RUNTIME_DIR=%s' % env.cfg('audio', 'pipewire_runtime', default='/run/pipewire')
    rc, out = sh('%s pw-cli ls Node 2>&1' % e, timeout=20)
    if rc and 'connect' in out.lower():
        raise Missing('no PipeWire daemon to connect to: %s' % out.strip()[-120:], hw='pipewire')
    sinks = len(re.findall(r'media.class = "Audio/Sink"', out))
    srcs = len(re.findall(r'media.class = "Audio/Source"', out))
    if rc:
        return 'fail', 'pw-cli rc %d: %s' % (rc, out.strip()[-200:])
    return ('pass' if sinks and srcs else 'fail', 'PipeWire nodes: %d audio sinks, %d audio sources'
            % (sinks, srcs), {'sinks': sinks, 'sources': srcs})


# ---- shell test-definition cases
def case_result(path):
    res = {}
    for line in read(path).splitlines():
        p = line.split()
        if len(p) >= 2:
            res[p[0]] = p[1]
    return res


@test('audio.case')
def t_case(env, case):
    """A shell test-definition case with the board's card, PCM and route parameters"""
    cs = env.cfg('audio', 'cases', default={}) or {}
    if case not in cs:
        raise Missing('the board profile defines no "%s" case (audio.cases)' % case,
                      hw='audio-routes')
    import json
    d = cs[case].get('dir', case)
    base = os.path.join(TD, 'automated', 'linux', d)
    y = os.path.join(base, d + '.yaml')
    try:
        import yaml
        spec = yaml.safe_load(open(y))
    except ImportError:
        spec = json.load(open(os.path.join(base, d + '.json')))
    params = dict(spec.get('params') or {})
    params.update(env.cfg('audio', 'case_env', default={}) or {})
    params.update(cs[case].get('params') or {})
    env_ = dict(os.environ)
    env_.update({k: str(v) for k, v in params.items()})
    out = os.path.join(base, 'output')
    sh('rm -rf %s' % out)
    steps = [s for s in spec['run']['steps'] if 'send-to-lava' not in s]
    script = 'cd %s\n%s\n' % (TD, '\n'.join(steps))
    # own process group: a timeout kills the aplay/arecord the case started
    p = subprocess.Popen(['sh', '-c', script], env=env_, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, start_new_session=True)
    try:
        out_, _ = p.communicate(timeout=600)
        log, rc = out_.decode('utf-8', 'replace'), p.returncode
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        out_, _ = p.communicate()
        log, rc = out_.decode('utf-8', 'replace') + '\n(timed out after 600 s)', 124
    print(log[-6000:], flush=True)
    res = case_result(os.path.join(out, 'result.txt'))
    if not res:
        return 'fail', '%s produced no results (rc %d): %s' % (case, rc, log.strip()[-200:])
    r = case_status(case, res)
    hw = cs[case].get('hw')
    if hw and 'pass' not in res.values():
        raise Missing(r[1], hw=hw)
    return r


def case_status(case, res):
    """Status of a shell case from its sub-case results: fail when any
    sub-case fails or none passes."""
    n = {s: sum(1 for v in res.values() if v == s) for s in ('pass', 'fail', 'skip')}
    detail = '%s: %d pass / %d fail / %d skip sub-cases%s' % (
        case, n['pass'], n['fail'], n['skip'],
        '; failed: ' + ' '.join(k for k, v in res.items() if v == 'fail') if n['fail'] else '')
    meas = {'pass': n['pass'], 'fail': n['fail']}
    if n['fail']:
        return 'fail', detail, meas
    if not n['pass']:
        return 'fail', detail + ': no sub-case ran (%s)' % ' '.join(res), meas
    return 'pass', detail, meas


# ---- acoustic
@test('audio.ssr')
def t_ssr(env):
    """DSP restart: the audio DSP is stopped and restarted and playback works after"""
    a = A(env)
    name = env.cfg('audio', 'dsp')
    rp = None
    for d in sorted(os.listdir('/sys/class/remoteproc')) if os.path.isdir('/sys/class/remoteproc') else []:
        if name and read('/sys/class/remoteproc/%s/name' % d).strip() == name:
            rp = '/sys/class/remoteproc/' + d
    if not rp:
        raise Missing('no remoteproc named %s for the audio DSP (board audio.dsp)' % name,
                      hw='audio-dsp')
    freq, min_db = a.need_loop()
    t = tone(a.f('ssr.raw'), freq, 3)
    cap, rc, out = a.play_record(t, 4)
    ok0, lv0, base = a.detect(cap, freq, min_db, 1.0, 2.0)
    if not ok0:
        return 'fail', 'tone not detected before the restart (%.1f dB over %.1f)' % (lv0, base)
    t0 = time.time()
    sh('echo stop > %s/state; sleep 2' % rp)
    st = read(rp + '/state').strip()
    if st != 'offline':
        return 'fail', 'DSP stop refused: state %s after stop' % st
    sh('echo start > %s/state' % rp)
    st = ''
    for i in range(60):
        st = read(rp + '/state').strip()
        if st == 'running' and any(c['index'] == a.card for c in cards()) and \
                pcms(a.card).get(('p', a.pdev)):
            break
        time.sleep(1)
    up = time.time() - t0
    if st != 'running':
        return 'fail', 'DSP state %s 60 s after restart' % st
    env._ctx.pop('audio', None)
    a = A(env)
    a.need_loop()
    cap, rc, out = a.play_record(t, 4)
    ok1, lv1, _ = a.detect(cap, freq, min_db, 1.0, 2.0)
    if not ok1:
        _, dm = sh('dmesg | grep -iE "remoteproc|hw_params|error" | tail -6')
        return 'fail', ('DSP back after %.0f s but the tone is not detected (%.1f dB, before %.1f; '
                        'aplay rc %d); kernel: %s' % (up, lv1, lv0, rc,
                                                     dm.strip().replace('\n', ' | ')),
                        {'recovery_s': round(up, 1)})
    return 'pass', 'DSP restarted in %.0f s; tone %.1f dB before, %.1f dB after (muted %.1f)' % (
        up, lv0, lv1, base), {'recovery_s': round(up, 1)}


@test('audio.concurrent_play')
def t_concurrent_play(env):
    """Two playback streams mixed to one output at the same time, both heard"""
    a = A(env)
    freq, min_db = a.need_loop()
    p2 = a.cfg.get('play2') or {}
    d1 = a.find('p', p2) if p2 else None
    if d1 is None:
        raise Missing('no second playback front end in the board profile (audio.play2)',
                      hw='audio-play2')
    bad = mixer(a.card, p2.get('mixer'))
    if bad:
        return 'fail', 'cannot route the second front end: %s' % bad
    music, alert = tone(a.f('m.raw'), 1000, 4), tone(a.f('a.raw'), 2500, 2)
    cap = a.f('ma.raw')
    r = a.record(cap, 5)
    time.sleep(0.5)
    q1 = a.play(music)
    time.sleep(1.0)
    q2 = a.play(alert, dev=d1)
    o2 = q2.communicate()[0].decode(errors='replace')
    o1 = q1.communicate()[0].decode(errors='replace')
    r.communicate()
    mixer(a.card, p2.get('off'))
    m_lv, a_lv = level(cap, 1000, 1.7, 1.5), level(cap, 2500, 1.7, 1.5)
    bm, ba = level(cap, 1000, 0.0, 0.4), level(cap, 2500, 0.0, 0.4)
    ok = m_lv - bm >= min_db and a_lv - ba >= min_db
    why = '1000 Hz stream %.1f dB (lead-in %.1f), 2500 Hz stream %.1f dB (lead-in %.1f) while both play' % (
        m_lv, bm, a_lv, ba)
    if q1.returncode or q2.returncode:
        why += '; aplay rc %d/%d: %s' % (q1.returncode, q2.returncode, (o1 + o2).strip()[:200])
    return ('pass' if ok else 'fail', why, {'streams': 2 if ok else 0})


@test('audio.pause_resume')
def t_pause_resume(env, pause_s=30):
    """Pause and resume: a stream paused for a long time resumes and plays"""
    a = A(env)
    freq, min_db = a.need_loop()
    src = tone(a.f('pause.raw'), freq, 6)
    cap = a.f('pause-cap.raw')
    r = a.record(cap, pause_s + 10)
    time.sleep(0.5)
    # aplay -i only reads keys from a terminal
    master, slave = pty.openpty()
    p = subprocess.Popen(['aplay', '-q', '-i', '-D', 'hw:%d,%d' % (a.card, a.pdev), '-t', 'raw',
                          '-f', 'S16_LE', '-r', str(RATE), '-c', '2', src],
                         stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    os.close(slave)
    time.sleep(2.0)
    os.write(master, b' ')
    t0 = time.time()
    time.sleep(pause_s)
    os.write(master, b' ')
    held = time.time() - t0
    try:
        out = p.communicate(timeout=20)[0].decode(errors='replace')
    except subprocess.TimeoutExpired:
        p.kill()
        out = 'aplay did not finish after resume'
    r.communicate()
    os.close(master)
    before = level(cap, freq, 1.0, 1.0)
    paused = level(cap, freq, 5.0, pause_s / 2)
    after = level(cap, freq, pause_s + 3.5, 2.0)
    base = a.silence(freq)
    ok = before - base >= min_db and after - base >= min_db and paused - base < min_db / 2
    return ('pass' if ok else 'fail', 'tone before pause %.1f dB, during the %.0f s pause %.1f dB, '
            'after resume %.1f dB (muted %.1f)%s' % (before, held, paused, after, base,
                                                     '' if p.returncode == 0 else '; aplay: ' + out.strip()[:200]),
            {'pause_s': round(held)})


@test('audio.soft_step')
def t_soft_step(env, min_ramp_ms=10):
    """Soft volume stepping: a volume change ramps instead of jumping"""
    a = A(env)
    freq, min_db = a.need_loop()
    step = a.cfg.get('volume_step')
    if not step:
        raise Missing('no lower volume setting in the board profile (audio.volume_step)',
                      hw='audio-volume')
    src = tone(a.f('ss.raw'), freq, 6)
    cap = a.f('ss-cap.raw')
    r = a.record(cap, 7)
    time.sleep(0.5)
    p = a.play(src)
    time.sleep(3.0)
    mixer(a.card, step)
    p.communicate()
    r.communicate()
    mixer(a.card, a.cfg.get('unmute'))
    win = int(0.005 * RATE)
    chans = read_s16(cap)
    c = max(chans, key=lambda x: goertzel_db(x[int(1.5 * RATE):int(2.5 * RATE)], freq))
    lv = [goertzel_db(c[i:i + win], freq) for i in range(int(2.0 * RATE), int(5.0 * RATE), win)]
    hi, lo = sorted(lv[:100])[50], sorted(lv[-100:])[50]
    drop = hi - lo
    if drop < 20:
        return 'fail', 'the volume step lowered the tone by only %.1f dB (%.1f -> %.1f)' % (drop, hi, lo)
    top, bot = hi - 0.1 * drop, lo + 0.1 * drop
    i0 = next((i for i, v in enumerate(lv) if v < top), None)
    i1 = next((i for i, v in enumerate(lv) if i0 is not None and i >= i0 and v < bot), None)
    if i0 is None or i1 is None:
        return 'fail', 'no transition found in the capture'
    ramp = (i1 - i0) * 5
    return ('pass' if ramp >= min_ramp_ms else 'fail',
            'volume step (%.1f dB drop) took %d ms from 10%% to 90%% of the change%s' % (
                drop, ramp, '' if ramp >= min_ramp_ms else ': an instant step, no soft stepping'),
            {'ramp_ms': ramp})


class Stream:
    """arecord streaming into this process; read timestamps map capture
    frames to CLOCK_MONOTONIC."""

    def __init__(self, a, secs):
        self.p = subprocess.Popen(['arecord', '-q', '-D', 'hw:%d,%d' % (a.card, a.cdev), '-t', 'raw',
                                   '-f', 'S16_LE', '-r', str(RATE), '-c', '2', '--period-size=240',
                                   '-d', str(secs)], stdout=subprocess.PIPE)
        self.data = bytearray()
        self.marks = []

    def pump(self, until):
        fd = self.p.stdout.fileno()
        while time.monotonic() < until:
            b = os.read(fd, 1920)
            if not b:
                break
            self.data += b
            self.marks.append((len(self.data) // 4, time.monotonic()))

    def finish(self):
        rest = self.p.stdout.read()
        if rest:
            self.data += rest
        self.p.wait()

    def frame_time(self, frame):
        return next((t for n, t in self.marks if n >= frame), None)

    def onset(self, freq, min_db, after_frame=0, win=240):
        s = struct.unpack('<%dh' % (len(self.data) // 2), bytes(self.data[:len(self.data) // 4 * 4]))
        chans = [s[0::2], s[1::2]]
        c = max(chans, key=lambda x: goertzel_db(x[after_frame:], freq))
        noise = sorted(goertzel_db(c[i:i + win], freq)
                       for i in range(max(0, after_frame - RATE // 4), max(win, after_frame) - win + 1, win))
        nf = noise[len(noise) // 2]
        for i in range(after_frame, len(c) - win, win // 2):
            if goertzel_db(c[i:i + win], freq) > nf + min_db:
                return i, nf
        return None, nf


@test('audio.output_latency')
def t_output_latency(env, kind='restart'):
    """Output latency: playback start to tone at the microphone (cold or warm)"""
    a = A(env)
    freq, min_db = a.need_loop()
    src = tone(a.f('lat.raw'), freq, 1.0)
    if kind == 'restart':
        a.play(src).communicate()
    else:
        time.sleep(12)          # let the audio graph power down
    s = Stream(a, 6)
    s.pump(time.monotonic() + 1.0)
    f0 = len(s.data) // 4
    t0 = time.monotonic()
    p = a.play(src)
    s.pump(time.monotonic() + 3.0)
    p.communicate()
    s.finish()
    on, nf = s.onset(freq, min_db, f0)
    if on is None:
        return 'fail', 'tone onset not found in the capture (noise %.1f dB)' % nf
    lat = (s.frame_time(on) - t0) * 1000
    return ('pass', '%s output latency %.0f ms (aplay start to tone at the microphone; capture '
            'read resolution 5 ms, acoustic path under 1 ms)' % (kind, lat),
            {'latency_ms': round(lat, 1)})


def proc_hw_params(path):
    t = read(path)
    g = lambda k: int((re.search(r'%s: (\d+)' % k, t) or [0, 0])[1])
    return g('rate'), g('period_size'), g('buffer_size')


@test('audio.buffer_latency')
def t_buffer_latency(env, direction='playback'):
    """Continuous latency: frames buffered between the application and the converter"""
    a = A(env)
    if a.pdev is None or a.cdev is None:
        raise Missing('playback or capture PCM not found', hw='audio-loopback')
    if direction == 'playback':
        p = a.play(tone(a.f('co.raw'), 1000, 3, dbfs=-40))
        time.sleep(1.0)
        rate, per, buf = proc_hw_params('/proc/asound/card%d/pcm%dp/sub0/hw_params' % (a.card, a.pdev))
        p.communicate()
        frames = buf
    else:
        r = a.record(a.f('ci.raw'), 3)
        time.sleep(1.0)
        rate, per, buf = proc_hw_params('/proc/asound/card%d/pcm%dc/sub0/hw_params' % (a.card, a.cdev))
        r.communicate()
        frames = per
    if not rate:
        return 'fail', 'no hw_params while streaming'
    ms = frames * 1000.0 / rate
    return ('pass', '%s: period %d, buffer %d frames at %d Hz: %.1f ms buffered with the default '
            'ALSA configuration; DSP and codec delay not included' % (direction, per, buf, rate, ms),
            {'latency_ms': round(ms, 1)})


@test('audio.capture_start')
def t_capture_start(env):
    """Cold capture start: time from opening an idle card to the first captured frame"""
    a = A(env)
    if a.cdev is None:
        raise Missing('no capture PCM', hw='audio-capture')
    a.route()
    time.sleep(12)
    t0 = time.monotonic()
    p = subprocess.Popen(['arecord', '-q', '-D', 'hw:%d,%d' % (a.card, a.cdev), '-t', 'raw', '-f',
                          'S16_LE', '-r', str(RATE), '-c', '2', '--period-size=240', '-d', '2'],
                         stdout=subprocess.PIPE)
    b = p.stdout.read(960)
    t1 = time.monotonic()
    p.stdout.read()
    p.wait()
    if not b:
        return 'fail', 'arecord produced no data'
    ms = (t1 - t0) * 1000
    return ('pass', 'first 5 ms of capture data reached the reader %.0f ms after arecord started '
            'on an idle card (process start, open, graph start and one period)' % ms,
            {'latency_ms': round(ms, 1)})


@test('audio.roundtrip')
def t_roundtrip(env):
    """Round trip latency: speaker to microphone measured by alsabat"""
    a = A(env)
    a.need_loop()
    if not have('alsabat'):
        return 'fail', 'alsabat not on the image'
    rc, out = sh('alsabat -P hw:%d,%d -C hw:%d,%d -c 2 -r 48000 -f S16_LE --roundtriplatency 2>&1 '
                 '| tail -12' % (a.card, a.pdev, a.card, a.cdev), timeout=120)
    m = re.search(r'average latency:?\s*([\d.]+)\s*ms', out, re.I) or \
        re.search(r'latency[^\d]*([\d.]+)\s*ms', out, re.I)
    if not m:
        return 'fail', 'alsabat gave no latency (rc %d): %s' % (rc, out.strip().replace('\n', ' | ')[-300:])
    return 'pass', 'alsabat round trip %s ms' % m.group(1), {'latency_ms': float(m.group(1))}


@test('audio.switch_latency')
def t_switch_latency(env):
    """Route switch latency: output route enabled while streaming until the tone is heard"""
    a = A(env)
    freq, min_db = a.need_loop()
    if not a.cfg.get('mute'):
        raise Missing('no mute and unmute route in the board profile (audio.mute)', hw='audio-mute')
    src = tone(a.f('sw.raw'), freq, 4)
    mixer(a.card, a.cfg.get('mute'))
    s = Stream(a, 6)
    s.pump(time.monotonic() + 0.5)
    p = a.play(src)
    s.pump(time.monotonic() + 1.0)
    f0 = len(s.data) // 4
    t0 = time.monotonic()
    mixer(a.card, a.cfg.get('unmute'))
    s.pump(time.monotonic() + 2.0)
    p.communicate()
    s.finish()
    on, nf = s.onset(freq, min_db, f0)
    if on is None:
        return 'fail', 'tone not found after the route change (noise %.1f dB)' % nf
    ms = (s.frame_time(on) - t0) * 1000
    return ('pass', 'output route enabled while streaming, tone heard after %.0f ms' % ms,
            {'latency_ms': round(ms, 1)})


@test('audio.clock_drift')
def t_clock_drift(env, seconds=20):
    """Audio clock against the system clock: wall time to play a fixed length"""
    a = A(env)
    if a.pdev is None:
        raise Missing('no playback PCM', hw='audio-playback')
    a.route()
    src = tone(a.f('av.raw'), 1000, seconds, dbfs=-30)
    t0 = time.monotonic()
    p = a.play(src)
    out = p.communicate()[0].decode(errors='replace')
    el = time.monotonic() - t0
    drift = (el - seconds) * 1000
    if p.returncode:
        return 'fail', 'aplay rc %d: %s' % (p.returncode, out[:200])
    return ('pass', '%d.000 s of audio played in %.3f s wall time (open, prepare and drain '
            'included: %+.0f ms)' % (seconds, el, drift), {'drift_ms': round(drift, 1)})


@test('audio.quality')
def t_quality(env, what):
    """Audio quality KPI (THD+N, SNR, response...): needs a calibrated analyzer"""
    raise Missing('%s needs a calibrated audio analyzer on the codec outputs or inputs' % what,
                  hw='audio-analyzer')
