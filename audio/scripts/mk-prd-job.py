#!/usr/bin/env python3
"""mk-prd-job.py <base|ovl> <tag> <fp> <device> <outdir> [cases]

Audio PRD job for an eliza-evk-emmc-open-fw package: the shared open-boot
deploy, cold firmware fingerprint monitor and login (common/lava), Linux
and rootfs fingerprint checks, then one inline definition per PRD test
case from the test-definitions tarball (scripts/mk-td-tar.sh), unpacked
at /td. base = upstream ASoC card (aplay/arecord + mixer), ovl =
AudioReach PAL through PipeWire. Writes <outdir>/<tag>.yaml and
<outdir>/<tag>-backstop.yaml (stock restore on the same device).
cases: comma list to keep (default all).

Environment: IMAGE_URL (qcomflash package), TD_URL (td tarball),
ROOT_PW, optional ARTIFACT_HEADER, STOCK_IMAGE_URL,
STOCK_IMAGE_DIR, STOCK_UEFI_VERSION (backstop); ovl only, optional:
ACDB_URL and ACDB_MD5 (tar holding etc/acdbdata/eliza_cqs_evk/acdb_cal.acdb,
the board calibration, not part of this repository)."""
import os
import sys

here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(here), 'common', 'lava'))
import jobtpl  # noqa: E402
import yaml  # noqa: E402

var, tag, fp, dev, out = sys.argv[1:6]
only = sys.argv[6].split(',') if len(sys.argv) > 6 else None
img = 'qcom-multimedia-image' if var == 'base' else 'qcom-multimedia-proprietary-image'
vals = {'FP': fp, 'DEVICE': dev, 'IMAGE_DIR': os.environ.get('IMAGE_DIR', img + '-eliza-evk-emmc-open-fw')}
t = jobtpl.load('open-boot.yaml', vals)
acts = t['actions'][:5]
q = acts[0]['deploy']['qcomflash']
jobtpl.add_url(q, '{IMAGE_URL}')
q['overlays']['td'] = {'format': 'tar', 'path': '/'}
jobtpl.add_url(q['overlays']['td'], '{TD_URL}')
ACDB = os.environ.get('ACDB_URL')
if ACDB and var == 'ovl':
    q['overlays']['acdb'] = {'format': 'tar', 'path': '/'}
    jobtpl.add_url(q['overlays']['acdb'], ACDB)

# every test starts from the MultiMedia2-only speaker route
SPK = ('WSA_CODEC_DMA_RX_0 Audio Mixer MultiMedia1=0;WSA_CODEC_DMA_RX_0 Audio Mixer MultiMedia5=0;'
       'WSA_CODEC_DMA_RX_0 Audio Mixer MultiMedia2=1;WSA RX0 MUX=AIF1_PB;WSA RX1 MUX=AIF1_PB;WSA_RX0 INP0=RX0;'
       'WSA_RX1 INP0=RX1;WSA_RX0 Digital Volume=81;WSA_RX1 Digital Volume=81;WSA_COMP1 Switch=0;WSA_COMP2 Switch=0;'
       'SpkrLeft PA Volume=12;SpkrRight PA Volume=12;SpkrLeft COMP Switch=1;SpkrLeft BOOST Switch=1;'
       'SpkrLeft DAC Switch=1;SpkrLeft PBR Switch=1;SpkrLeft VISENSE Switch=0;SpkrLeft WSA MODE=0;'
       'SpkrRight COMP Switch=1;SpkrRight BOOST Switch=1;SpkrRight DAC Switch=1;SpkrRight PBR Switch=1;'
       'SpkrRight VISENSE Switch=0;SpkrRight WSA MODE=0')


def mic(a, b, fe='MultiMedia4'):
    return ('VA DEC0 MUX=VA_DMIC;VA DMIC MUX0=DMIC%d;VA_AIF1_CAP Mixer DEC0=1;VA_DEC0 Volume=100;'
            'VA DEC1 MUX=VA_DMIC;VA DMIC MUX1=DMIC%d;VA_AIF1_CAP Mixer DEC1=1;VA_DEC1 Volume=100;'
            '%s Mixer VA_CODEC_DMA_TX_0=1' % (a, b, fe))


MUTE = 'WSA_RX0 Digital Volume=0;WSA_RX1 Digital Volume=0'
CARD = 'eliza-cqs-evk'
COMMON = {'CARD': CARD, 'PLAY_PCM': 'MultiMedia2', 'CAPTURE_PCM': 'MultiMedia4',
          'PLAY_MIXER': SPK, 'CAPTURE_MIXER': mic(0, 1), 'TONE_DBFS': '-6', 'FREQ': '1600'}
PAL = {'AUDIO_BACKEND': 'pipewire', 'CARD': CARD, 'PLAY_PCM': 'pal_sink_speaker_ll',
       'CAPTURE_PCM': 'pal_source_speaker_mic_raw', 'PLAY_MIXER': '', 'CAPTURE_MIXER': '',
       'RATE': '48000', 'FREQ': '1600', 'TONE_DBFS': '-6', 'TOLERANCE_HZ': '25', 'WAIT_SECONDS': '60'}
INV = [
    ('soundwire-status', 'soundwire-status', {'MIN_MASTERS': '1', 'MIN_PERIPHERALS': '2'}),
    ('audio-interfaces', 'audio-interfaces', {'CARD': CARD, 'INTERFACES': 'tdm i2s analog-mic i2c dp'}),
]
# (td dir, case name, params); order: inventory, signal, compress last
BASE = INV + [
    ('audio-drivers', 'audio-drivers', {'CARD': CARD, 'CARD_DRIVER': 'snd-sc8280xp', 'REMOTEPROC': 'adsp'}),
    ('alsa-loopback', 'alsa-loopback', dict(COMMON, MUTE_MIXER=MUTE, SPEAKERS='right:2', SWEEP='1600 2600 3640')),
    ('alsa-capture', 'alsa-capture-dmic23', dict(COMMON, CAPTURE_MIXER=mic(2, 3), MICS='dmic2 dmic3', REMOTEPROC='')),
    ('alsa-capture', 'alsa-capture-dmic67', dict(COMMON, CAPTURE_MIXER=mic(6, 7), MICS='dmic6 dmic7', REMOTEPROC='')),
    ('alsa-volume', 'alsa-volume', dict(COMMON, VOLUME_CTLS='WSA_RX0 Digital Volume;WSA_RX1 Digital Volume',
                                        VOLUMES='81 80 71 61', MUTE_VALUE='0', STEP_DB='1', TOL_DB='1', MIN_DB='10')),
    ('alsa-device-switch', 'alsa-device-switch', {
        'CARD': CARD, 'PLAY_PCM': 'MultiMedia2', 'CAPTURE_PCM': 'MultiMedia4', 'PLAY_MIXER': SPK,
        'ROUTE_A': mic(0, 1), 'ROUTE_OFF': 'VA DMIC MUX0=DMIC4;VA DMIC MUX1=DMIC5',
        'ROUTE_B': 'VA DMIC MUX0=DMIC2;VA DMIC MUX1=DMIC3', 'FREQ': '1600', 'TONE_DBFS': '-6'}),
    ('alsa-upmix', 'alsa-upmix', dict(COMMON, CONTROL_CHANNEL='1')),
    ('alsa-latency', 'alsa-latency', dict(COMMON, MUTE_MIXER=MUTE, PERIOD_FRAMES='240', BUFFER_PERIODS='2')),
    ('alsa-formats', 'alsa-formats', dict(COMMON, PLAY_PCM2='MultiMedia1',
                                          PLAY_MIXER2='WSA_CODEC_DMA_RX_0 Audio Mixer MultiMedia1=1')),
    # after a compress stream that played, MultiMedia2 playback is silent
    # afterwards (589233, 589252, 589327; not after a failed open: 589389)
    ('alsa-compress', 'alsa-compress', {'CARD': CARD, 'COMPR_DEVICE': '4', 'CAPTURE_PCM': 'MultiMedia4',
        'PLAY_MIXER': SPK + ';WSA_CODEC_DMA_RX_0 Audio Mixer MultiMedia5=1', 'CAPTURE_MIXER': mic(0, 1),
        'MUTE_MIXER': MUTE, 'TONE_DBFS': '-6', 'FREQ': '1600'}),
]
OVL = INV + [
    ('audio-drivers', 'audio-drivers', {'CARD': CARD, 'CARD_DRIVER': '', 'REMOTEPROC': 'adsp'}),
    # before any PAL graph: a dynamic module crash must not hide FastRPC
    ('fastrpc', 'fastrpc-adsp', {'TESTS': 'adsp:adsp:0:0 adsp-unsigned-pd:adsp:0:1', 'NODES': ''}),
    ('alsa-loopback', 'alsa-loopback-pal', dict(PAL, MUTE_MIXER='silence', SPEAKERS='right:2', PLAY_CHANNELS='2',
                                                CAPTURE_CHANNELS='2', SWEEP='1600 2600 3640', MIN_DB='20',
                                                SECONDS_PER_TONE='5')),
    ('alsa-loopback', 'alsa-loopback-pal-db', dict(PAL, PLAY_PCM='pal_sink_speaker_db', MUTE_MIXER='silence',
                                                   SPEAKERS='right:2', PLAY_CHANNELS='2', CAPTURE_CHANNELS='2',
                                                   SWEEP='1600 2600 3640', MIN_DB='20', SECONDS_PER_TONE='5')),
    ('alsa-capture', 'alsa-capture-pal', dict(PAL, MICS='speaker-mic', MIN_DB='10', SECONDS_PER_TONE='3',
                                              REMOTEPROC='', BUSY_SECONDS='10')),
    ('alsa-volume', 'alsa-volume-pal', dict(PAL, PLAY_PCM='pal_sink_speaker_db', VOLUMES='0 -1 -10 -20', STEP_DB='1', TOL_DB='1', MIN_DB='10',
                                         SECONDS_PER_TONE='5')),
    ('alsa-concurrency', 'alsa-concurrency-pal', dict(PAL, PLAY_A='pal_sink_speaker_ll', PLAY_B='pal_sink_speaker_db',
                                                      FREQ_A='1600', FREQ_B='2600', SECONDS_PER_TONE='6')),
    ('pal-ecns', 'pal-ecns', {'PLAY_NODE': 'pal_sink_speaker_ll', 'PROC_NODE': 'pal_source_speaker_mic',
                              'RAW_NODE': 'pal_source_speaker_mic_raw', 'SUPPRESS_DB': '10', 'PREFIX': 'ecns',
                              'FREQ': '1600'}),
]
cases = BASE if var == 'base' else OVL
# PRD_REPEAT_AFTER=<case>:<case2> (diagnostics): run a copy of <case2>,
# named <case2>-after-<case>, right after <case>
if os.environ.get('PRD_REPEAT_AFTER'):
    a, b = os.environ['PRD_REPEAT_AFTER'].split(':')
    i = [c[1] for c in cases].index(a)
    d = [c for c in cases if c[1] == b][0]
    cases = cases[:i + 1] + [(d[0], '%s-after-%s' % (b, a), d[2])] + cases[i + 1:]
if only:
    cases = [c for c in cases if c[1] in only]
tdroot = os.path.join(here, 'test-definitions')
pre = ['cat /etc/qcdfp; uname -r; cat /td/REV',
       "lava-test-case linux-fingerprint --shell 'uname -r | grep -q %s'" % fp,
       "lava-test-case rootfs-fingerprint --shell 'grep -q %s /etc/qcdfp'" % fp,
       'while [ $(cut -d. -f1 /proc/uptime) -lt 90 ]; do sleep 5; done; cat /proc/uptime; cat /proc/asound/cards',
       # the test shell runs under set -e: diagnostics must never fail
       '{ for t in aplay arecord amixer alsabat cplay lame pw-play pw-record wpctl agmplay agmcap agmcompressplay '
       'fastrpc_test pal_test_app; do printf "%s=%s\\n" $t "$(command -v $t)"; done; } || true',
       '{ ls -R /etc/acdbdata 2>&1 | head -20; ls /etc/ | grep -iE "resourcemanager|usecase|mixer_paths|card-defs" ; '
       'md5sum /etc/acdbdata/*/* 2>/dev/null | head -10; } || true']
if var == 'ovl':
    pre.append('md5sum /etc/acdbdata/eliza_cqs_evk/acdb_cal.acdb || true')
    if ACDB and os.environ.get('ACDB_MD5'):
        pre.append("lava-test-case acdb-eliza-installed --shell 'md5sum /etc/acdbdata/eliza_cqs_evk/acdb_cal.acdb | grep -q ^%s'"
                   % os.environ['ACDB_MD5'])
    pre.append('{ export PIPEWIRE_RUNTIME_DIR=/run/pipewire; systemctl --no-pager is-active pipewire wireplumber; '
               'sleep 10; pw-cli ls Node 2>&1 | grep -E "node.name|media.class"; } || true')
tests = []
for i, (d, name, params) in enumerate(cases):
    y = yaml.safe_load(open(os.path.join(tdroot, 'automated/linux', d, d + '.yaml')))
    pr = dict(y.get('params') or {}); pr.update(params)
    exp = ['export %s=%s' % (k, "'" + str(v).replace("'", "'\\''") + "'") for k, v in pr.items()]
    if var == 'ovl':
        exp.insert(0, 'export PIPEWIRE_RUNTIME_DIR=/run/pipewire')
    # the rack has a 1040 Hz interferer and its harmonics: test tones sit at
    # half-integer multiples and the peak search stays within 10 %
    exp.insert(0, 'export AUDIO_PEAK_SPAN=1.1')
    diag = []
    if var == 'ovl':
        diag = ["{ journalctl -b -u pipewire --no-pager 2>/dev/null | grep -iE 'error|fail' | tail -8; } || true"]
    steps = (pre if i == 0 else []) + exp + ['cd /td'] + y['run']['steps'] + diag + \
        ["{ for r in /sys/class/remoteproc/remoteproc*; do echo \"$(cat $r/name) $(cat $r/state)\"; done; "
         "dmesg | grep -iE 'remoteproc|crash|fatal|smmu|fault|CMD timeout|q6apm|audioreach|fastrpc|hw_params|soundwire' | tail -12; } || true"]
    tests.append({'test': {'definitions': [{'from': 'inline', 'name': name, 'path': 'inline/%s.yaml' % name,
        'repository': {'metadata': {'format': 'Lava-Test Test Definition 1.0', 'name': name},
                       'run': {'steps': steps}}}], 'timeout': {'minutes': 15}}})
t['job_name'] = 'eliza-evk audio PRD %s %s %s (%s)' % (var, tag, fp, 'ASoC path' if var == 'base' else 'AudioReach PAL path')
if ACDB:
    t['job_name'] += ' Eliza ACDB'
t['metadata'] = {'image': 'meta-qcom %s eliza-evk-emmc-open-fw' % img, 'firmware-fingerprint': fp,
                 'linux-fingerprint': fp, 'rootfs-fingerprint': fp,
                 'purpose': 'audio PRD evaluation (%s): one definition per PRD test case' % var}
t['actions'] = acts + tests
t['timeouts']['job']['minutes'] = 180
os.makedirs(out, exist_ok=True)
p = os.path.join(out, '%s.yaml' % tag)
jobtpl.dump(t, p)
jobtpl.dump(jobtpl.backstop(dev, tag), os.path.join(out, '%s-backstop.yaml' % tag))
print(p)
