#!/usr/bin/env python3
"""mk-cmds-job.py <cmds file> <label> <fp> <device> <outdir>: diagnostic
job that runs every non-comment line of the commands file as one inline
test step (echoed first as "### <line>", failures do not stop the run)
after the shared open-boot deploy, firmware fingerprint check and login
(common/lava). Writes <outdir>/<label>.yaml and <label>-backstop.yaml.

Environment: IMAGE_URL, ROOT_PW, optional IMAGE_DIR,
ARTIFACT_HEADER, STOCK_IMAGE_URL, STOCK_IMAGE_DIR, STOCK_UEFI_VERSION."""
import os
import sys

here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(here, 'common', 'lava'))
import jobtpl  # noqa: E402

cmds, label, fp, dev, out = sys.argv[1:6]
vals = {'FP': fp, 'DEVICE': dev,
        'IMAGE_DIR': os.environ.get('IMAGE_DIR', 'qcom-multimedia-image-eliza-evk-emmc-open-fw')}
t = jobtpl.load('open-boot.yaml', vals)
acts = t['actions'][:5]
jobtpl.add_url(acts[0]['deploy']['qcomflash'], '{IMAGE_URL}')
steps = []
for line in open(cmds):
    line = line.rstrip('\n')
    if not line.strip() or line.lstrip().startswith('#'):
        continue
    steps.append("echo '### %s'" % line.replace("'", "'\\''"))
    steps.append('(%s) 2>&1 || true' % line)
test = {'test': {'definitions': [{
    'from': 'inline', 'name': 'diag-' + label, 'path': 'inline/diag.yaml',
    'repository': {'metadata': {'format': 'Lava-Test Test Definition 1.0', 'name': 'diag-' + label},
                   'run': {'steps': steps}}}],
    'timeout': {'minutes': 30, 'skip': True}}}
t['job_name'] = 'eliza-evk diag %s (%s)' % (label, fp)
t['metadata'] = {'firmware-fingerprint': fp, 'linux-fingerprint': fp, 'rootfs-fingerprint': fp,
                 'purpose': 'diagnostics ' + label}
t['actions'] = acts + [test]
os.makedirs(out, exist_ok=True)
p = os.path.join(out, '%s.yaml' % label)
jobtpl.dump(t, p)
jobtpl.dump(jobtpl.backstop(dev, label), os.path.join(out, '%s-backstop.yaml' % label))
print(p)
