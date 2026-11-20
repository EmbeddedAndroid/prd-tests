#!/usr/bin/env python3
"""mk-video-job.py <tag> <fp> <device> <outdir>: video PRD probe job for an
eliza-evk-emmc-open-fw qcom-multimedia-image package. Deploy, cold
firmware fingerprint monitor and login come from the shared open-boot
template (common/lava); the probe runs from the /video-prd overlay built
by stage-overlay.sh. Writes <outdir>/<tag>.yaml and
<outdir>/<tag>-backstop.yaml (stock restore on the same device).

Environment: IMAGE_URL (qcomflash package), OVERLAY_URL (overlay tar),
ROOT_PW, optional IMAGE_DIR, ARTIFACT_HEADER, STOCK_IMAGE_URL,
STOCK_IMAGE_DIR, STOCK_UEFI_VERSION."""
import os
import sys

here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(here), 'common', 'lava'))
import jobtpl  # noqa: E402

tag, fp, dev, out = sys.argv[1:5]
vals = {'FP': fp, 'DEVICE': dev,
        'IMAGE_DIR': os.environ.get('IMAGE_DIR', 'qcom-multimedia-image-eliza-evk-emmc-open-fw')}
t = jobtpl.load('open-boot.yaml', vals)
acts = t['actions'][:5]
q = acts[0]['deploy']['qcomflash']
jobtpl.add_url(q, '{IMAGE_URL}')
q['overlays']['vp'] = {'format': 'tar', 'path': '/'}
jobtpl.add_url(q['overlays']['vp'], '{OVERLAY_URL}')
steps = [
    'cat /etc/qcdfp; uname -a',
    "lava-test-case linux-fingerprint --shell 'uname -r | grep -q %s'" % fp,
    "lava-test-case rootfs-fingerprint --shell 'grep -q %s /etc/qcdfp'" % fp,
    'while [ $(cut -d. -f1 /proc/uptime) -lt 60 ]; do sleep 5; done; cat /proc/uptime',
    'sha256sum /video-prd/video-probe.sh /video-prd/vectors/* || true',
    'sh /video-prd/video-probe.sh || true',
]
test = {'test': {'definitions': [{'from': 'inline', 'name': 'video-prd', 'path': 'inline/video-prd.yaml',
    'repository': {'metadata': {'format': 'Lava-Test Test Definition 1.0', 'name': 'video-prd'},
                   'run': {'steps': steps}}}], 'timeout': {'minutes': 40}}}
t['job_name'] = 'eliza-evk video PRD probe %s %s (upstream qcom-iris, GStreamer v4l2)' % (tag, fp)
t['metadata'] = {'image': 'meta-qcom qcom-multimedia-image eliza-evk-emmc-open-fw', 'firmware-fingerprint': fp,
                 'linux-fingerprint': fp, 'rootfs-fingerprint': fp,
                 'purpose': 'video PRD assessment: iris formats/controls inventory, short decode/encode checks (read-only)'}
t['actions'] = acts + [test]
t['timeouts']['job']['minutes'] = 120
os.makedirs(out, exist_ok=True)
p = os.path.join(out, '%s.yaml' % tag)
jobtpl.dump(t, p)
jobtpl.dump(jobtpl.backstop(dev, tag), os.path.join(out, '%s-backstop.yaml' % tag))
print(p)
