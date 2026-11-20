#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""check-generic.py [dir]: fail when a file under tests/ names a board,
SoC, sound card, connector instance, sensor, codec part, DSP instance,
GPIO or register address. Those belong in boards/*.yaml and
products/*.yaml; tests discover the device. Prints every hit with file
and line; exit status 1 on any.

Words that only name a kernel interface or a standard (DRM connector
types such as HDMI-A, the SoundWire bus, V4L2 fourccs) are allowed;
connector instances (HDMI-A-1) and numbered GPIOs are not. Shellcheck
directives (SC2046) and all-ones masks are not register addresses."""
import os
import re
import sys

TOP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANNED = [
    # boards, SoCs and product names
    r'\b(eliza|lemans|monaco|kodiak|glymur|hamoa|purwa|kaanapali|lanai|pakala|seca|nord|shikra|mahua)\b',
    r'\b(qcs|qcm|qrb|sm|sc|sa|sdm|msm|apq|ipq)[0-9]{3,4}[a-z]*\b',
    r'\brb[1-9]\b|rb3gen2|ventuno|\biq-?[0-9]{4}\b|\bcqs\b|\bevk\b',
    # sound card, codec, DSP instance and vendor driver names
    r'\bwsa[0-9]*\b|\bwcd[0-9]*\b|\blpass\b|\bq6[a-z]*\b|\b[acsm]dsp[0-9]*\b|snd-sc|sc8280xp',
    r'\bmultimedia[0-9]+\b|\bva_dmic|\bwsa_|\bdmic[0-9]+\b',
    # camera sensors
    r'\bimx[0-9]{3}\b|\bov[0-9]{4}\b',
    # connector instances, GPIOs, register addresses
    r'\b(HDMI-[AB]|DP|eDP|DSI|DPI|VGA|DVI-[IDA])-[0-9]+\b',
    r'\bgpio[0-9]+\b|\bGPIO[0-9]+\b',
    r'\b0x(?!f+\b)(?!ff000000\b)[0-9a-fA-F]{6,}\b',
]
PAT = re.compile('|'.join('(?:%s)' % p for p in BANNED), re.I)
SKIP_LINE = re.compile(r'shellcheck disable|SPDX-License-Identifier|Copyright')
SKIP_FILE = ('COPYING', 'COPYRIGHTS')


def main():
    top = sys.argv[1] if len(sys.argv) > 1 else TOP
    hits = 0
    for d, dirs, files in os.walk(os.path.join(top, 'tests')):
        dirs[:] = [x for x in dirs if x != '__pycache__']
        for f in sorted(files):
            if f in SKIP_FILE or f.endswith('.json'):
                continue
            p = os.path.join(d, f)
            try:
                lines = open(p, encoding='utf-8').read().splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for n, line in enumerate(lines, 1):
                if SKIP_LINE.search(line):
                    continue
                m = PAT.search(line)
                if m:
                    hits += 1
                    print('%s:%d: "%s": %s' % (os.path.relpath(p, top), n, m.group(0), line.strip()[:120]))
    print('check-generic: %d names in tests/' % hits)
    return 1 if hits else 0


if __name__ == '__main__':
    sys.exit(main())
