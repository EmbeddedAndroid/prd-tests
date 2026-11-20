"""ppm2png.py [-s N] <out.png> <ppm>...: per-frame RGB stats for libcamera
cam --file PPMs (P6, 8-bit) and the last frame as PNG, NxN mean downscaled
(default 2)."""
import struct
import sys
import zlib

import numpy as np


def png(path, rgb):
    h, w, _ = rgb.shape
    raw = b"".join(b"\0" + rgb[y].tobytes() for y in range(h))

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 6)))
        f.write(chunk(b"IEND", b""))


def ppm(path):
    d = open(path, "rb").read()
    tok, i = [], 0
    while len(tok) < 4:
        while d[i:i + 1].isspace():
            i += 1
        j = i
        while not d[j:j + 1].isspace():
            j += 1
        tok.append(d[i:j])
        i = j
    w, h = int(tok[1]), int(tok[2])
    return np.frombuffer(d[i + 1:i + 1 + w * h * 3], np.uint8).reshape(h, w, 3)


args = sys.argv[1:]
n = 2
if args[0] == "-s":
    n, args = int(args[1]), args[2:]
prev = None
for p in args[1:]:
    a = ppm(p)
    st = " ".join(f"{c} min {a[:, :, k].min()} max {a[:, :, k].max()} mean {a[:, :, k].mean():.1f}"
                  for k, c in enumerate("RGB"))
    d = "" if prev is None else f" | mean abs diff vs previous {np.abs(a.astype(np.int16) - prev).mean():.2f}"
    print(f"{p.rsplit('/', 1)[-1]}: {a.shape[1]}x{a.shape[0]} {st}{d}")
    prev = a.astype(np.int16)
h, w = a.shape[0] // n * n, a.shape[1] // n * n
small = a[:h, :w].reshape(h // n, n, w // n, n, 3).mean((1, 3)).astype(np.uint8)
png(args[0], small)
print(f"wrote {args[0]} ({w // n}x{h // n}) from {args[-1]}")
