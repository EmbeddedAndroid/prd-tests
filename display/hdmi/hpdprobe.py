# hpdprobe.py: drive TLMM gpio184 (EVK HDMI 5V enable per the posted eliza-evk DT) high,
# sample gpio47 (HDMI HPD) from debugfs, then drive gpio184 low and release it.
import fcntl, os, struct, time, glob, re
def chip_for(label):
    for p in sorted(glob.glob("/dev/gpiochip*")):
        fd = os.open(p, os.O_RDWR)
        b = bytearray(68); fcntl.ioctl(fd, 0x8044B401, b)
        lab = b[32:64].split(b"\0")[0].decode()
        if lab == label: return fd, p
        os.close(fd)
    raise SystemExit("no chip " + label)
def hpd():
    for l in open("/sys/kernel/debug/gpio"):
        if re.match(r"^ *gpio47 ", l): return l.strip()
fd, p = chip_for("f100000.pinctrl")
print("chip", p, "before:", hpd())
req = bytearray(592); struct.pack_into("I", req, 0, 184)
req[256:256+8] = b"hpdprobe"
struct.pack_into("Q", req, 288, 1 << 3)          # GPIO_V2_LINE_FLAG_OUTPUT
struct.pack_into("I", req, 296, 1)               # one attribute
struct.pack_into("IIQQ", req, 320, 2, 0, 1, 1)   # OUTPUT_VALUES: line0 = 1
struct.pack_into("I", req, 560, 1)
fcntl.ioctl(fd, 0xC250B407, req)
lfd = struct.unpack_from("i", req, 588)[0]
try:
    for t in (0.2, 0.5, 1.0, 2.0, 3.0):
        time.sleep(t); print("5V on +%.1fs:" % t, hpd())
finally:
    d = bytearray(struct.pack("QQ", 0, 1)); fcntl.ioctl(lfd, 0xC010B40F, d); time.sleep(0.3)
    print("5V off:", hpd()); os.close(lfd)
for l in open("/sys/kernel/debug/gpio"):
    if re.match(r"^ *gpio184 ", l): print("after release:", l.strip())
