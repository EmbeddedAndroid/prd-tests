# Eliza EVK board diagnostics

Bring-up probes for the Eliza CQS EVK. They read or drive board GPIOs
and registers, so they are board diagnostics, not requirement tests: the
display requirements are tested at the connector level by
tests/display (connector status, EDID, mode set).

| file | what |
|---|---|
| `hpdprobe.py` | drives the HDMI 5 V enable (TLMM GPIO184) high, samples HPD (GPIO47) from debugfs, then drops 5 V |
| `gpiohold.py` | drives TLMM lines high one at a time for 3 s and prints the line and HPD state |
| `pwnpat.c` | KMS proof pattern on the first connected HDMI-A connector: large text, the kernel release and the UTC time, refreshed every second (no compositor, no libdrm); `pwnpat [seconds] [WxH]` |
| `camera/r1-run.sh` | raw IMX577 capture on CAM0 through CSIPHY0, CSID0 and VFE0 RDI0 on a diagnostic kernel with the camera modules on the ESP |
| `camera/r1-png.py`, `camera/ppm2png.py` | convert raw and PPM captures to PNG (needs numpy) |

The GPIO probes use the GPIO character device uAPI v2 and debugfs and
must run before the HDMI driver claims GPIO184 (or with it unbound).
`pwnpat.c` builds with any aarch64 C compiler, for example
`aarch64-linux-gnu-gcc -O2 -static -o pwnpat pwnpat.c`.
