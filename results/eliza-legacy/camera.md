# Camera

Tests for the Eliza camera requirements: upstream CAMSS (CSIPHY, CSID,
VFE RDI), CCI and the IMX577 sensor (imx412 driver) on CAM0 of an Eliza
CQS EVK, and libcamera on top (simple pipeline handler with the software
ISP). These ran on a lab EVK fitted with an IMX577 module over the board
console, not in LAVA (no LAVA EVK has a camera module).

## Layout

| path | what |
|---|---|
| `board/r1-run.sh` | raw capture: loads the camera modules, builds the media graph imx577 -> msm_csiphy0 -> msm_csid0 -> msm_vfe0_rdi0 at SRGGB10 4056x3040, streams 8 frames each of the sensor colour bars, live default exposure and live long exposure with high gain through `v4l2-ctl --stream-mmap`, logs CSID/VFE interrupt counts |
| `board/r1-png.py` | RAW10 unpack, per-channel statistics, 2x2 binning debayer to PNG (no ISP, no white balance) |
| `board/ppm2png.py` | per-frame RGB statistics of libcamera `cam --file` PPMs and the last frame as PNG |

`r1-run.sh` was written for the diagnostic image the camera was brought
up on: it checks the DT fingerprint at /proc/device-tree/lab-fingerprint
against its argument and installs camera modules from `ecam/mod.tgz` on
the ESP (checked against `ecam/SHA256SUMS`, build ids compared with the
loaded modules). On an image with the camera drivers built in, run the
media-ctl and v4l2-ctl part (from "MD=" on) by hand.

## How to run

Raw frames (requirement CAM-001), on the board, needs numpy:

    sh board/r1-run.sh <FP>            # log in /root/ecam/<FP>/r1.log, PNGs next to it

libcamera (requirement CAM-002), on the board:

    cam -l
    LIBCAMERA_SOFTISP_MODE=cpu cam -c 1 --capture=30 -s width=1920,height=1080,pixelformat=BGR888 --file=/tmp/c-#.ppm
    python3 -I board/ppm2png.py /tmp/cam.png $(ls /tmp/c-*.ppm | tail -2)
    LIBCAMERA_SOFTISP_MODE=cpu timeout -s INT 8 gst-launch-1.0 -e libcamerasrc ! video/x-raw,width=1920,height=1080 ! videoconvert ! jpegenc quality=90 ! multifilesink location=/tmp/gst-%03d.jpg max-files=3

The kernel needs CAMSS, the Qualcomm MIPI CSI2 PHY, CCI and imx412 as
modules (CONFIG_VIDEO_QCOM_CAMSS, CONFIG_PHY_QCOM_MIPI_CSI2,
CONFIG_I2C_QCOM_CCI, CONFIG_VIDEO_IMX412) and the EVK camera DT overlay
(IMX577 on CCI0 bus 0 at 0x1a, MCLK0, CSIPHY0 four lanes). The Eliza
CAMSS patches were carried on the EmbeddedAndroid/linux eliza/integration
branch; an equivalent series is posted upstream.

## Results

| ID | requirement | test | how to run | latest result |
|---|---|---|---|---|
| CAM-001 | Upstream camera driver: CAMSS, CCI and CSIPHY for CAM0 with the IMX577 | `board/r1-run.sh`, `board/r1-png.py` | `sh board/r1-run.sh <FP>` on the board console | pass (console runs, 2026-11-20, FP ECAMDIAG-261120-05 and retest ECAMDIAG-261120-09): imx412 binds the sensor as imx577 (chip ID read over CCI), media graph complete; 4056x3040 SRGGB10 raw frames, 8 buffers of 15418880 bytes each, 30.0 fps, CSID interrupts rise; long exposure frame channel means 65.2 to 66.5 (10-bit, black level 64), max 1023, frame to frame mean absolute difference 2.37; the FP 09 retest dequeued 8 buffers in each of the three captures with the loaded module build ids equal to the payload. Upstream status: the posted upstream series is not merged yet |
| CAM-002 | libcamera support, phase 1 | libcamera `cam` and GStreamer `libcamerasrc` commands above, `board/ppm2png.py` | commands above, on the board console | partial (console runs, 2026-11-20, FP ECAMDIAG-261120-05 and 09): `cam -l` lists 'imx577' (simple pipeline handler, software ISP input 4056x3040 RGGB10 CSI2P); CPU software ISP: 10/10 and 60/60 frames at 30.0 fps at 1920x1080 (converged frame RGB means 40.3/40.7/40.2), FP 09 retest 30 frames at 30.00 fps (means 48.0/47.6/47.9, frame difference 5.03); gst `libcamerasrc` 234 frames in 7.9 s with a clean EOS, CPU debayer 21.7 ms per frame. GPU ISP on the A722 not done: on that image EGL ran on llvmpipe (downstream kgsl, no Mesa freedreno). No imx577 sensor helper in libcamera 0.7.2, so the gain mapping is unknown and frames are dark. Later run on the integration image (FP QCDFP-261120-1792, 2026-11-20): full sensor 4048x3040 BGR888 capture, 30 frames at 30.00 fps |

R3 of the camera work (a second sensor, OV9282) is not a requirement in
the list and was not tested: it is not known to be fitted.
