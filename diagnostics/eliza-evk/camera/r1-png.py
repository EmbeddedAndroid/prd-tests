"""r1-png.py <raw> <W> <H> <outprefix>: SRGGB10P (MIPI RAW10 packed)
frames from v4l2-ctl --stream-to -> per-frame stats and two PNGs of the
last frame.

Processing (no ISP): unpack RAW10; per-channel stats on the Bayer planes;
2x2 binning debayer (R, mean(Gr, Gb), B) to W/2 x H/2, then 2x2 mean to
W/4 x H/4; black level 64 subtracted.
  <out>-fixed.png: value/(1023-64) * 255, no gain, no white balance.
  <out>-stretch.png: per-image gain so the 99.5th percentile maps to 255,
  gamma 1/2.2, no white balance.
"""
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


def unpack(buf, w, h, stride):
    a = np.frombuffer(buf, np.uint8)[: stride * h].reshape(h, stride)[:, : w * 5 // 4]
    a = a.reshape(h, w // 4, 5).astype(np.uint16)
    out = np.empty((h, w // 4, 4), np.uint16)
    for i in range(4):
        out[:, :, i] = (a[:, :, i] << 2) | ((a[:, :, 4] >> (2 * i)) & 3)
    return out.reshape(h, w)


def main():
    path, w, h, pre = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
    data = open(path, "rb").read()
    stride = None
    for s in range(w * 5 // 4, w * 5 // 4 + 512):
        if len(data) % (s * h) == 0:
            stride = s
            break
    if stride is None:
        print(f"{path}: size {len(data)} is not a multiple of any stride x {h}")
        return
    n = len(data) // (stride * h)
    print(f"{path}: {n} frames, stride {stride}")
    prev = None
    for i in range(n):
        f = unpack(data[i * stride * h:(i + 1) * stride * h], w, h, stride)
        planes = {"R": f[0::2, 0::2], "Gr": f[0::2, 1::2], "Gb": f[1::2, 0::2], "B": f[1::2, 1::2]}
        st = " ".join(f"{k} min {p.min()} max {p.max()} mean {p.mean():.1f}" for k, p in planes.items())
        d = "" if prev is None else f" | mean abs diff vs previous {np.abs(f.astype(np.int32) - prev).mean():.2f}"
        print(f"frame {i}: {st}{d}")
        prev = f.astype(np.int32)
        last = f
    f = np.clip(last.astype(np.float32) - 64, 0, None)
    rgb = np.stack([f[0::2, 0::2], (f[0::2, 1::2] + f[1::2, 0::2]) / 2, f[1::2, 1::2]], -1)
    hh, ww = rgb.shape[0] // 2 * 2, rgb.shape[1] // 2 * 2
    rgb = rgb[:hh, :ww].reshape(hh // 2, 2, ww // 2, 2, 3).mean((1, 3))
    png(pre + "-fixed.png", np.clip(rgb / (1023 - 64) * 255, 0, 255).astype(np.uint8))
    g = np.percentile(rgb, 99.5)
    g = g if g > 0 else 1.0
    png(pre + "-stretch.png", (np.clip(rgb / g, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8))
    print(f"wrote {pre}-fixed.png {pre}-stretch.png ({ww // 2}x{hh // 2}), stretch gain 1/{g:.1f}")


main()
