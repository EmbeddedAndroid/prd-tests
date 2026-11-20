# gpiohold.py <line> [<line>...]: drive TLMM lines high one at a time for 3 s each,
# print the line state and HPD (gpio47) from debugfs while held, then drive low and release.
import fcntl, os, struct, sys, time, glob, re
def chip_for(label):
    for p in sorted(glob.glob("/dev/gpiochip*")):
        fd = os.open(p, os.O_RDWR); b = bytearray(68); fcntl.ioctl(fd, 0x8044B401, b)
        if b[32:64].split(b"\0")[0].decode() == label: return fd
        os.close(fd)
def dbg(n):
    for l in open("/sys/kernel/debug/gpio"):
        if re.match(r"^ *gpio%d " % n, l): return l.strip()
fd = chip_for("f100000.pinctrl")
for n in map(int, sys.argv[1:]):
    print("line", n, "before:", dbg(n), "|", dbg(47))
    req = bytearray(592); struct.pack_into("I", req, 0, n); req[256:264] = b"hdmiprob"
    struct.pack_into("Q", req, 288, 1 << 3); struct.pack_into("I", req, 296, 1)
    struct.pack_into("IIQQ", req, 320, 2, 0, 1, 1); struct.pack_into("I", req, 560, 1)
    fcntl.ioctl(fd, 0xC250B407, req); lfd = struct.unpack_from("i", req, 588)[0]
    try:
        for t in (0.3, 1.0, 3.0):
            time.sleep(t); print("  held +%.1fs:" % t, dbg(n), "|", dbg(47))
    finally:
        fcntl.ioctl(lfd, 0xC010B40F, bytearray(struct.pack("QQ", 0, 1))); time.sleep(0.3)
        print("  low:", dbg(n), "|", dbg(47)); os.close(lfd)
    print("  released:", dbg(n))
