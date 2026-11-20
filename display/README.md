# Display

Tests for the Eliza display requirements on the Eliza CQS EVK (eMMC):
the native HDMI output, DisplayPort over USB-C, and Weston on upstream
KMS. The requirement list has 89 rows; most need a panel, a second
display or a feature that upstream drm/msm does not have, and have no
test yet.

## Layout

| path | what |
|---|---|
| `hdmi/pwnpat.c` | KMS proof pattern: lights the first connected HDMI-A connector through a dumb buffer (no compositor, no libdrm) with its preferred mode or a given WxH, draws a large text, the kernel release (build fingerprint) and the UTC time, refreshed every second; prints the connectors, the mode and "SETCRTC ok" |
| `hdmi/hpdprobe.py` | drives the HDMI 5 V enable (TLMM GPIO184) high, samples HPD (GPIO47) from debugfs, then drops 5 V |
| `hdmi/gpiohold.py` | drives TLMM lines high one at a time for 3 s and prints the line and HPD state |
| `dp/dp-status.cmds` | DisplayPort connector status, EDID, modes, Type-C roles and HPD kernel lines, one command per line |
| `dp/mk-cmds-job.py` | writes a LAVA job that runs each line of a commands file as a step (shared open-boot template) plus its stock restore backstop |

`pwnpat.c` builds with any aarch64 C compiler (no libraries beyond libc,
for example `aarch64-linux-gnu-gcc -O2 -static -o pwnpat hdmi/pwnpat.c`;
the exact flags of the binary that ran were not recorded). Usage:
`pwnpat [seconds] [WxH]`.

The GPIO probes use the GPIO character device uAPI v2 and debugfs, and
must run before the HDMI driver claims GPIO184 (or with it unbound).

## Kernel

HDMI on Eliza needs the drm/msm HDMI series for Eliza: an upstream RFC
(the Eliza HDMI and HDMI PHY patches, posted 2026-08-28) plus two fixes
made on the board: the PHY/PLL programming order with settle delays and
the TX ack wait, and a PLL determine_rate that keeps the programmed rate.
Without them the PHY never reports TX ack and no picture appears. The
board-tested kernel was 7.3.0-EHDFP-261120-0005. No kernel config change
is needed (CONFIG_DRM_MSM_HDMI). The DT enables HDMI in the default EVK
DTB (5 V enable on TLMM GPIO184, DDC GPIO6/7, CEC GPIO46, HPD GPIO47).

## Weston gate

How DSP-001 (and DSP-002, on the proprietary GPU stack image) is
checked, on the board console with a monitor on HDMI:

    systemctl is-active weston
    ls -l /dev/dri; cat /sys/class/drm/card*-HDMI-A-1/status
    dmesg | grep -iE "adreno|gmu|zap|sqe|msm_dpu|hdmi"
    # Weston's own log (it logs only with --log):
    mkdir -p /run/systemd/system/weston.service.d
    printf '[Service]\nExecStart=\nExecStart=/usr/bin/weston --modules=systemd-notify.so --log=/tmp/weston.log\n' \
        > /run/systemd/system/weston.service.d/log.conf
    systemctl daemon-reload; systemctl restart weston; sleep 6
    grep -E "HDMI-A-1|rendering device|GL vendor|GL renderer|Using GL renderer" /tmp/weston.log
    XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-1 eglinfo -B -p wayland
    XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-1 weston-simple-egl &
    grep -h "drm-driver\|drm-engine-gpu" /proc/$(pidof weston-simple-egl)/fdinfo/*

Pass: the weston log shows HDMI-A-1 connected and the GL renderer on the
GPU (not pixman or llvmpipe), and drm-engine-gpu advances for the EGL
client. The run on 1792 also took a photo of the monitor with the EVK
camera.

## DisplayPort

No LAVA EVK has a DisplayPort sink: the USB-C partner on every board is
the lab's USB connection. `dp/dp-status.cmds` shows the connector state;
every DP requirement is blocked until a DP monitor or an EDID dummy plug
is fitted to a LAVA EVK.

    export IMAGE_URL=... ROOT_PW=... STOCK_IMAGE_URL=... STOCK_IMAGE_DIR=... STOCK_UEFI_VERSION=...
    python3 dp/mk-cmds-job.py dp/dp-status.cmds dp-status <FP> <device> jobs

(The recorded DP runs used the same commands inside the platform suite job
with the stock restore at its end, instead of a separate backstop job.)

## Results

pass/partial = measured; not run = a test exists but did not run or
could not exercise the feature; GAP = no test, with the reason. The HDMI
results come from console runs on one lab EVK with a 1280x720 HDMI
monitor , not from LAVA.

| ID | requirement | test | how to run | latest result |
|---|---|---|---|---|
| DSP-003 | Color Depth | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-004 | MIPI DSI link max bit rate | none | - | GAP: no DSI panel on the EVK; needs a panel, its DT and panel driver |
| DSP-005 | MIPI controller max bit clock | none | - | GAP: no DSI panel on the EVK; needs a panel, its DT and panel driver |
| DSP-006 | Video Mode Panel Support | none | - | GAP: no DSI panel on the EVK; needs a panel, its DT and panel driver |
| DSP-007 | HDMI Support | `hdmi/pwnpat.c` | on the board console: `pwnpat 60` (or `pwnpat 60 WxH`), then `cat /sys/class/drm/card*-HDMI-A-1/status /sys/class/drm/card*-HDMI-A-1/modes`, `dmesg \| grep "phy init"` | pass (console run on a lab EVK, kernel 7.3.0-EHDFP-261120-0005, 2026-11-20): HDMI-A-1 connected, 1280x720@60 set by the KMS client ("SETCRTC ok"), PHY "phy init 74250000 Hz: sram 0x1 tx ack 0x3 0x3 0x3 0x3", pattern with the kernel release and UTC clock photographed on the monitor by the EVK camera. Needs the HDMI series under "Kernel" below; not yet in an upstream tree |
| DSP-008 | Max Resolution and refresh rates - 2D | `hdmi/pwnpat.c` | on the board console: `pwnpat 60` (or `pwnpat 60 WxH`), then `cat /sys/class/drm/card*-HDMI-A-1/status /sys/class/drm/card*-HDMI-A-1/modes`, `dmesg \| grep "phy init"` | partial (console run on a lab EVK, kernel 7.3.0-EHDFP-261120-0005, 2026-11-20): the attached monitor tops out at 1280x720, which works; higher resolutions not tested (no such sink) |
| DSP-009 | HDCP for HDMI | none | - | GAP: not tested; the driver logs "HDCP is not supported without qfprom" at probe |
| DSP-010 | Video Format Timing | `hdmi/pwnpat.c` | on the board console: `pwnpat 60` (or `pwnpat 60 WxH`), then `cat /sys/class/drm/card*-HDMI-A-1/status /sys/class/drm/card*-HDMI-A-1/modes`, `dmesg \| grep "phy init"` | partial (console run on a lab EVK, kernel 7.3.0-EHDFP-261120-0005, 2026-11-20): three timings set and photographed: 1280x720@60 (74.25 MHz), 640x480@75 (31.5 MHz), 720x480@60 (27.027 MHz), PHY TX ack on all four lanes each time; the other EDID modes were not set |
| DSP-011 | Output pixel format | none | - | GAP: not tested on the HDMI output |
| DSP-012 | Color Depth | none | - | GAP: not tested on the HDMI output |
| DSP-013 | EDID/DDC | `hdmi/pwnpat.c` (connector modes), EDID read from sysfs | on the board console: `pwnpat 60` (or `pwnpat 60 WxH`), then `cat /sys/class/drm/card*-HDMI-A-1/status /sys/class/drm/card*-HDMI-A-1/modes`, `dmesg \| grep "phy init"`, `od -An -tx1 /sys/class/drm/card*-HDMI-A-1/edid` | pass (console run, kernel EHDFP-261120-0003, 2026-11-20): 256 EDID bytes read over DDC, monitor name "HDMI monitor", mode list from the EDID (1280x720 down to 720x400; 15 modes on the connector in the 0005 runs) |
| DSP-014 | HDMI Detection | `hdmi/hpdprobe.py`, `hdmi/gpiohold.py` | on the board console, before the HDMI driver binds: `python3 hdmi/gpiohold.py 184` (or `hdmi/hpdprobe.py`) | partial (console run, 2026-11-20): with the 5 V enable (TLMM GPIO184) driven high, HPD (GPIO47) reads high within 0.3 s and low again when 5 V drops; the connector reports "connected" with a sink. A cable unplug/replug hot plug event was not tested |
| DSP-015 | Configuration #1: Highest resolution primary display only | none | - | GAP: not evaluated against the resolution target; a single HDMI display at 1280x720 works (DSP-007) |
| DSP-016 | Configuration #2: Highest resolution primary + external [interface] | none | - | GAP: needs more than one display (only one HDMI monitor and no DSI panel) |
| DSP-017 | Configuration #3: Highest resolution primary + secondary + external | none | - | GAP: needs more than one display (only one HDMI monitor and no DSI panel) |
| DSP-018 | Number of video layers | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-019 | Color Space Conversion | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-020 | Dithering | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-021 | Source Cropping | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-022 | Memory Color | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-023 | Writeback path | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-024 | Picture Control Block (DSPP) | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-025 | Arbitrary alignment of video/graphic layers | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-026 | No constraints on blend ordering | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-027 | Per-Pixel/Source/Destination/Constant/alpha blending | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-028 | Display Stream Compression (DSC) | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-029 | Brightness Control | none | - | GAP: backlight control needs a panel with a backlight; none on the EVK |
| DSP-030 | Dynamic calculation of DSI bitclk according to Display Panel parameters | none | - | GAP: no DSI panel on the EVK; needs a panel, its DT and panel driver |
| DSP-031 | Variable Refresh Rate | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-032 | Display Support on Apps Bootloader (UEFI) | none | - | GAP: no boot splash in the open boot stack (U-Boot) yet |
| DSP-033 | Max display resolution in the various voltage corners | none | - | GAP: not tested; needs the target configuration and KPI numbers |
| DSP-034 | Display Source Processing - Scaling | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-035 | Dual DSI for a single panel | none | - | GAP: no DSI panel on the EVK; needs a panel, its DT and panel driver |
| DSP-036 | Maximum frame width | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-037 | Universal Bandwidth Compression | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-038 | Dynamic Display detect/config | none | - | GAP: bootloader panel detection: no DSI panel and no bootloader display path yet |
| DSP-039 | Display Post Processing Content Adaptive Backlight Control (DSPP) | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-040 | Display Post Processing Panel Color Correction (DSPP) | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-041 | Display Post Processing Gamut mapping (DSPP) | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-042 | Display Post Processing Inverse Gamma Correction (DSPP) | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-043 | Display Post Processing Panel Gamma Correction (DSPP) | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-044 | Display Post Processing Dither (DSPP) | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-045 | Display Post Processing Picture Adjustment (DSPP) | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-046 | Enable support for panels used for internal platforms | none | - | GAP: internal platform panels are not on the EVK |
| DSP-047 | Destination Scaler & Sharpening | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-048 | 10-bit DSI Output | none | - | GAP: no DSI panel on the EVK; needs a panel, its DT and panel driver |
| DSP-049 | Concurrent write back | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-050 | Support for Adaptive V-Sync Refresh (Q-Sync) | none | - | GAP: downstream tool or feature with no upstream equivalent today |
| DSP-051 | SSC Support on MIPI | none | - | GAP: no DSI panel on the EVK; needs a panel, its DT and panel driver |
| DSP-052 | DisplayPort resolution support | `dp/dp-status.cmds` | `dp/mk-cmds-job.py dp/dp-status.cmds dp-status <FP> <device> jobs` | not run: blocked (2026-11-14, FP QCDFP-261114-1511, [570955](https://lava.infra.foundries.io/scheduler/job/570955) [570957](https://lava.infra.foundries.io/scheduler/job/570957) [570959](https://lava.infra.foundries.io/scheduler/job/570959) [570961](https://lava.infra.foundries.io/scheduler/job/570961)): card0-DP-1 reads disconnected on all four LAVA EVKs, no HPD event, 0 EDID bytes, no modes; the USB-C partner is the lab USB connection, no DP sink is fitted |
| DSP-053 | DisplayPort MST support | `dp/dp-status.cmds` | `dp/mk-cmds-job.py dp/dp-status.cmds dp-status <FP> <device> jobs` | not run: blocked (2026-11-14, FP QCDFP-261114-1511, [570955](https://lava.infra.foundries.io/scheduler/job/570955) [570957](https://lava.infra.foundries.io/scheduler/job/570957) [570959](https://lava.infra.foundries.io/scheduler/job/570959) [570961](https://lava.infra.foundries.io/scheduler/job/570961)): card0-DP-1 reads disconnected on all four LAVA EVKs, no HPD event, 0 EDID bytes, no modes; the USB-C partner is the lab USB connection, no DP sink is fitted |
| DSP-054 | DisplayPort + USB concurrency | `dp/dp-status.cmds` | `dp/mk-cmds-job.py dp/dp-status.cmds dp-status <FP> <device> jobs` | not run: blocked (2026-11-14, FP QCDFP-261114-1511, [570955](https://lava.infra.foundries.io/scheduler/job/570955) [570957](https://lava.infra.foundries.io/scheduler/job/570957) [570959](https://lava.infra.foundries.io/scheduler/job/570959) [570961](https://lava.infra.foundries.io/scheduler/job/570961)): card0-DP-1 reads disconnected on all four LAVA EVKs, no HPD event, 0 EDID bytes, no modes; the USB-C partner is the lab USB connection, no DP sink is fitted |
| DSP-055 | DisplayPort Link Rate | `dp/dp-status.cmds` | `dp/mk-cmds-job.py dp/dp-status.cmds dp-status <FP> <device> jobs` | not run: blocked (2026-11-14, FP QCDFP-261114-1511, [570955](https://lava.infra.foundries.io/scheduler/job/570955) [570957](https://lava.infra.foundries.io/scheduler/job/570957) [570959](https://lava.infra.foundries.io/scheduler/job/570959) [570961](https://lava.infra.foundries.io/scheduler/job/570961)): card0-DP-1 reads disconnected on all four LAVA EVKs, no HPD event, 0 EDID bytes, no modes; the USB-C partner is the lab USB connection, no DP sink is fitted |
| DSP-056 | HDR10 for embedded panel | none | - | GAP: HDR metadata support is not in upstream drm/msm today; needs a definition of what "supported" means upstream |
| DSP-057 | HDR10 for external displays | none | - | GAP: HDR metadata support is not in upstream drm/msm today; needs a definition of what "supported" means upstream |
| DSP-058 | HDR10 for external displays through HDMI | none | - | GAP: HDR metadata support is not in upstream drm/msm today; needs a definition of what "supported" means upstream |
| DSP-059 | HDCP for DisplayPort | `dp/dp-status.cmds` | `dp/mk-cmds-job.py dp/dp-status.cmds dp-status <FP> <device> jobs` | not run: blocked (2026-11-14, FP QCDFP-261114-1511, [570955](https://lava.infra.foundries.io/scheduler/job/570955) [570957](https://lava.infra.foundries.io/scheduler/job/570957) [570959](https://lava.infra.foundries.io/scheduler/job/570959) [570961](https://lava.infra.foundries.io/scheduler/job/570961)): card0-DP-1 reads disconnected on all four LAVA EVKs, no HPD event, 0 EDID bytes, no modes; the USB-C partner is the lab USB connection, no DP sink is fitted |
| DSP-060 | DisplayPort basic audio pass-through support | `dp/dp-status.cmds` | `dp/mk-cmds-job.py dp/dp-status.cmds dp-status <FP> <device> jobs` | not run: blocked (2026-11-14, FP QCDFP-261114-1511, [570955](https://lava.infra.foundries.io/scheduler/job/570955) [570957](https://lava.infra.foundries.io/scheduler/job/570957) [570959](https://lava.infra.foundries.io/scheduler/job/570959) [570961](https://lava.infra.foundries.io/scheduler/job/570961)): card0-DP-1 reads disconnected on all four LAVA EVKs, no HPD event, 0 EDID bytes, no modes; the USB-C partner is the lab USB connection, no DP sink is fitted |
| DSP-061 | Number of RGB layers | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-062 | Maximum Layers mixed/blended in HW | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-063 | Inline-Rotation | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-064 | Display Panel Calibration Support | none | - | GAP: downstream tool or feature with no upstream equivalent today |
| DSP-065 | Display Tuning Support | none | - | GAP: downstream tool or feature with no upstream equivalent today |
| DSP-066 | Compression support over DisplayPort | `dp/dp-status.cmds` | `dp/mk-cmds-job.py dp/dp-status.cmds dp-status <FP> <device> jobs` | not run: blocked (2026-11-14, FP QCDFP-261114-1511, [570955](https://lava.infra.foundries.io/scheduler/job/570955) [570957](https://lava.infra.foundries.io/scheduler/job/570957) [570959](https://lava.infra.foundries.io/scheduler/job/570959) [570961](https://lava.infra.foundries.io/scheduler/job/570961)): card0-DP-1 reads disconnected on all four LAVA EVKs, no HPD event, 0 EDID bytes, no modes; the USB-C partner is the lab USB connection, no DP sink is fitted |
| DSP-067 | Display Source Processing - Sharpening (SSPP) | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-068 | Input surface formats | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-069 | Output surfaces formats | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-070 | HDR10+ for embedded panel | none | - | GAP: HDR metadata support is not in upstream drm/msm today; needs a definition of what "supported" means upstream |
| DSP-071 | HDR10+ for external panel | none | - | GAP: HDR metadata support is not in upstream drm/msm today; needs a definition of what "supported" means upstream |
| DSP-072 | Mixed color gamut content support | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-073 | Legacy Touch Support (I2C, I3C) | none | - | GAP: needs the touch controller on the reference board |
| DSP-074 | Rounded corners/notches/cut-out | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-075 | Display interface configuration | none | - | GAP: not tested; needs the target configuration and KPI numbers |
| DSP-076 | ROI based concurrent writeback | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-077 | Number of RGB layers | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-078 | Number of RGB layers | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-079 | DPU based video playback enhancements | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-080 | Custom Blend Layers | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-081 | Dolby Vision HDR | none | - | GAP: HDR metadata support is not in upstream drm/msm today; needs a definition of what "supported" means upstream |
| DSP-082 | HDR Vivid | none | - | GAP: HDR metadata support is not in upstream drm/msm today; needs a definition of what "supported" means upstream |
| DSP-083 | Histogram Calculator | none | - | GAP: upstream DPU driver code exists for this display engine generation; no test written yet (now testable on the HDMI output) |
| DSP-084 | Power and Performance optimal composition policies | none | - | GAP: not tested; needs the target configuration and KPI numbers |
| DSP-085 | DP as Primary Display | `dp/dp-status.cmds` | `dp/mk-cmds-job.py dp/dp-status.cmds dp-status <FP> <device> jobs` | not run: blocked (2026-11-14, FP QCDFP-261114-1511, [570955](https://lava.infra.foundries.io/scheduler/job/570955) [570957](https://lava.infra.foundries.io/scheduler/job/570957) [570959](https://lava.infra.foundries.io/scheduler/job/570959) [570961](https://lava.infra.foundries.io/scheduler/job/570961)): card0-DP-1 reads disconnected on all four LAVA EVKs, no HPD event, 0 EDID bytes, no modes; the USB-C partner is the lab USB connection, no DP sink is fitted |
| DSP-086 | DP output format | `dp/dp-status.cmds` | `dp/mk-cmds-job.py dp/dp-status.cmds dp-status <FP> <device> jobs` | not run: blocked (2026-11-14, FP QCDFP-261114-1511, [570955](https://lava.infra.foundries.io/scheduler/job/570955) [570957](https://lava.infra.foundries.io/scheduler/job/570957) [570959](https://lava.infra.foundries.io/scheduler/job/570959) [570961](https://lava.infra.foundries.io/scheduler/job/570961)): card0-DP-1 reads disconnected on all four LAVA EVKs, no HPD event, 0 EDID bytes, no modes; the USB-C partner is the lab USB connection, no DP sink is fitted |
| DSP-002 | Weston Compositor for Downstream | Weston gate (manual) | manual gate on the board console (see "Weston gate" below) | not run: the proprietary GPU stack image (FP QCDFP-261120-1793) was built 2026-11-20 but the gate has not run on it. The earlier proprietary image (FP QCDFP-261114-1527) failed at Weston GL init ("EGL_BAD_ALLOC") because its kgsl driver could not load the GPU firmware |
| DSP-087 | DSI Output Format | none | - | GAP: no DSI panel on the EVK; needs a panel, its DT and panel driver |
| DSP-088 | HDMI v2.1 optional features | none | - | GAP: not tested on the HDMI output |
| DSP-089 | Upstream Linux Display Drivers | none | - | GAP: code reading only: Eliza DPU and DP support is in qcom-next or carried as lab kernel patches; HDMI is an RFC based series, not upstream |
| DSP-001 | Weston Compositor for Upstream | Weston gate (manual) | manual gate on the board console (see "Weston gate" below) | pass (console run, FP QCDFP-261120-1792, 2026-11-20): Weston on upstream drm/msm KMS drives HDMI-A-1 at 1280x720@60 with the GL renderer on Mesa freedreno ("GL renderer: Adreno (TM) 722", "Using rendering device: /dev/dri/renderD128"); weston-simple-egl runs on the GPU (fdinfo drm-engine-gpu advances); photographed by the EVK camera. Display and GPU are separate DRM devices on this image by design |
