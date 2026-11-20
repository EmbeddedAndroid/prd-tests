#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""mk-lava-job.py: LAVA job that flashes an image on a board and runs a
product's requirements with tools/run.py from the overlay.

    mk-lava-job.py --board boards/<board>.yaml --product products/<p>.yaml
                   --tag <name> [--device <lava device>] [--fp <fingerprint>]
                   [--area display,audio] [-o jobs] [ID ...]

Deploy and boot come from the board profile's lava section (device type,
storage, firehose programmer, rawprogram and patch files, image
directory, shell prompt, optional priority, default low; lava.flash
lists several qdl steps where the image flashes more than one storage;
lava.username, login_first (commands right after login, before the
shell is set up) and prompts for images that log in differently). With
lava.firmware_fingerprint the job checks
the build fingerprint on the cold boot firmware banners, the kernel
release and lava.rootfs_fingerprint before any test; with lava.backstop
a second job restores the lab's stock image on the same device
(<tag>-backstop.yaml), to be submitted right after the test job.

Environment: IMAGE_URL (qcomflash tarball), PRD_URL (tools/mk-overlay.sh
tarball), ROOT_PW, optional IMAGE_DIR, ARTIFACT_HEADER and PRD_HEADER
("Name: value" HTTP header for the image and the overlay URL); backstop:
STOCK_IMAGE_URL, STOCK_IMAGE_DIR, STOCK_UEFI_VERSION (a pattern, with
backslash escapes for regex characters), optional STOCK_ARTIFACT_HEADER. Needs python3 with PyYAML."""
import argparse
import os
import re
import sys

import yaml

TOP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def header(var):
    h = os.environ.get(var)
    if not h:
        return None
    k, _, v = h.partition(':')
    return {k.strip(): v.strip()}


def env(name, default=None):
    v = os.environ.get(name, default)
    if v is None:
        sys.exit('%s is not set' % name)
    return v


def deploy(image_url, image_dir, hdr, overlays=None, rootfs=None):
    q = {'url': image_url}
    if hdr:
        q['headers'] = hdr
    q['format'] = 'ext4'
    q['overlays'] = {'lava': True}
    q['overlays'].update(overlays or {})
    return {'deploy': {'to': 'qdl', 'rootfs_image': rootfs or '%s/rootfs.img' % image_dir,
                       'qcomflash': q, 'timeout': {'minutes': 40}}}


def qdl_boot(st, image_dir, minutes=15):
    """One qdl flash step: st carries firehose, rawprogram, patch, storage
    and an optional path below the image directory."""
    path = os.path.join(image_dir, st['path']) if st.get('path') else image_dir
    return {'boot': {'method': 'qdl', 'firehose_program': st['firehose'],
                     'rawprogram': ' '.join(st['rawprogram']), 'patch': ' '.join(st['patch']),
                     'path': path, 'storage': st['storage'], 'timeout': {'minutes': minutes}}}


def flash_steps(lv):
    """lava.flash (several storages, flashed in order) or the single step
    given by lava.storage, firehose, rawprogram and patch."""
    return lv.get('flash') or [lv]


def login(lv, reset=True):
    b = {'method': 'minimal',
         'auto_login': {'login_prompt': 'login:', 'username': lv.get('username', 'root'),
                        'password_prompt': 'Password', 'password': env('ROOT_PW'),
                        'login_commands': [x.format(**os.environ) for x in lv.get('login_first', [])] +
                        ['dmesg -n 1'] +
                        lv.get('login_commands', [])},
         'prompts': lv.get('prompts', []) + [lv['prompt'], 'root@(.*):[/~]#'],
         'timeout': {'minutes': 10}}
    if not reset:
        b['reset'] = False
    return skip(lv, {'boot': b})


def skip(lv, act):
    """lava.timeout_skip: a timed out deploy, boot or monitor goes on to
    the next action instead of ending the job."""
    if lv.get('timeout_skip'):
        list(act.values())[0]['timeout']['skip'] = True
    return act


def fp_monitor(fp):
    pat = ('(?P<test_case_id>OP-TEE version(?=[^\r\n]*?{FP})|U-Boot SPL(?=[^\r\n]*?{FP})|'
           'U-Boot(?=[^\r\n]*?{FP})|BL31(?=[^\r\n]*?{FP})|BL2(?=[^\r\n]*?{FP}))'
           '[^\\r\\n]*?(?P<result>{FP})').replace('{FP}', fp)
    return {'test': {'monitors': [{'name': 'firmware-fingerprint-cold', 'start': 'SBL1, End',
                                   'end': 'Booting Linux on physical CPU', 'pattern': pat,
                                   'fixupdict': {fp: 'pass'},
                                   'expected': ['bl2', 'bl31', 'op_tee_version', 'u_boot']}],
                     'timeout': {'minutes': 4}}}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--board', required=True)
    ap.add_argument('--product', required=True)
    ap.add_argument('--tag', required=True)
    ap.add_argument('--device')
    ap.add_argument('--fp')
    ap.add_argument('--area')
    ap.add_argument('-o', '--outdir', default='jobs')
    ap.add_argument('ids', nargs='*')
    a = ap.parse_args()
    board = yaml.safe_load(open(a.board))
    lv = board['lava']
    bname = os.path.splitext(os.path.basename(a.board))[0]
    pname = os.path.splitext(os.path.basename(a.product))[0]
    image_dir = os.environ.get('IMAGE_DIR', lv['image_dir'])
    if lv.get('firmware_fingerprint') and not a.fp:
        sys.exit('board %s embeds a build fingerprint: --fp is required' % bname)
    prd = {'format': 'tar', 'path': '/', 'url': env('PRD_URL')}
    if header('PRD_HEADER'):
        prd['headers'] = header('PRD_HEADER')
    acts = [skip(lv, deploy(env('IMAGE_URL'), image_dir, header('ARTIFACT_HEADER'), {'prd': prd},
                           lv.get('rootfs_image'))),
            ] + [skip(lv, qdl_boot(st, image_dir)) for st in flash_steps(lv)]
    if lv.get('firmware_fingerprint'):
        acts += [skip(lv, {'boot': {'method': 'minimal', 'timeout': {'minutes': 1}}}),
                 skip(lv, fp_monitor(a.fp)), login(lv, reset=False)]
    else:
        acts.append(login(lv))
    sel = (' --area %s' % a.area if a.area else '') + ''.join(' %s' % i for i in a.ids)
    steps = ['cat /prd/REV; uname -r; cat %s 2>/dev/null || true' % lv.get('rootfs_fingerprint', '/etc/os-release')]
    if a.fp:
        steps.append("lava-test-case linux-fingerprint --shell 'uname -r | grep -q %s'" % a.fp)
        if lv.get('rootfs_fingerprint'):
            steps.append("lava-test-case rootfs-fingerprint --shell 'grep -q %s %s'" % (
                a.fp, lv['rootfs_fingerprint']))
    steps += ['while [ $(cut -d. -f1 /proc/uptime) -lt 60 ]; do sleep 5; done; cat /proc/uptime',
              "lava-test-case prd-runner --shell 'cd /prd && python3 tools/run.py "
              "--board boards/%s.json --product products/%s.json -o /tmp/prd-out%s'" % (bname, pname, sel),
              '/prd/utils/send-to-lava.sh /tmp/prd-out/result.txt']
    name = '%s-%s' % (pname, bname)
    acts.append({'test': {'definitions': [{
        'from': 'inline', 'name': name, 'path': 'inline/%s.yaml' % name,
        'repository': {'metadata': {'format': 'Lava-Test Test Definition 1.0', 'name': name},
                       'run': {'steps': steps}}}], 'timeout': {'minutes': 150}}})
    job = {'device_type': lv['device_type'],
           'job_name': '%s requirements on %s %s%s' % (pname, bname, a.tag, ' (%s)' % a.fp if a.fp else ''),
           'priority': lv.get('priority', 'low'), 'visibility': 'public',
           'timeouts': {'job': {'minutes': 210}, 'action': {'minutes': 10}, 'connection': {'minutes': 2}},
           'context': {'test_character_delay': 10},
           'metadata': {'board-profile': bname, 'product': pname,
                        'overlay': os.path.basename(os.environ['PRD_URL'].split('?')[0])},
           'actions': acts}
    if a.fp:
        job['metadata']['build-fingerprint'] = a.fp
    if a.device:
        job['device'] = a.device
    os.makedirs(a.outdir, exist_ok=True)
    p = os.path.join(a.outdir, '%s.yaml' % a.tag)
    dump(job, p)
    print(p)
    if lv.get('backstop'):
        if not a.device:
            sys.exit('a backstop needs --device (it must run on the same board)')
        bs = {'device_type': lv['device_type'], 'device': a.device,
              'job_name': '%s stock restore backstop after %s' % (bname, a.tag),
              'priority': lv.get('priority', 'low'), 'visibility': 'public',
              'timeouts': {'job': {'minutes': 60}, 'action': {'minutes': 10},
                           'connection': {'minutes': 2}},
              'metadata': {'purpose': 'stock restore backstop after %s' % a.tag},
              'actions': [{'deploy': {'to': 'qdl', 'qcomflash': {'url': env('STOCK_IMAGE_URL')},
                                      'timeout': {'minutes': 30}}},
                          ] + [qdl_boot(st, env('STOCK_IMAGE_DIR'), 20) for st in flash_steps(lv)] + [
                          {'boot': {'method': 'minimal', 'timeout': {'minutes': 3}}},
                          {'test': {'timeout': {'minutes': 10}, 'monitors': [{
                              'name': 'stock-restore', 'start': 'SBL1, End', 'end': 'UEFI Total',
                              'pattern': r'(?P<test_case_id>UEFI Ver)\s*:\s*(?P<result>%s)'
                              % env('STOCK_UEFI_VERSION'),
                              # keyed by the text the board prints, not the pattern
                              'fixupdict': {re.sub(r'\\(.)', r'\1', env('STOCK_UEFI_VERSION')): 'pass'},
                              'expected': ['uefi_ver']}]}}]}
        if header('STOCK_ARTIFACT_HEADER'):
            bs['actions'][0]['deploy']['qcomflash']['headers'] = header('STOCK_ARTIFACT_HEADER')
        p = os.path.join(a.outdir, '%s-backstop.yaml' % a.tag)
        dump(bs, p)
        print(p)


def dump(obj, path):
    with open(path, 'w') as f:
        yaml.safe_dump(obj, f, sort_keys=False, width=100000)


if __name__ == '__main__':
    main()
