# Eliza PRD tests

Every test written so far for an Eliza (Qualcomm Eliza SoC) product
requirement, collected in one place for review, with the latest recorded
result of each. The board is the Eliza CQS EVK (eMMC) running open boot
firmware (TF-A, OP-TEE, U-Boot) and meta-qcom images; most tests run in
LAVA on the `eliza-evk-emmc` device type at
https://lava.infra.foundries.io, the rest on a board console.

Every requirement the evaluation looked at is listed in its area's
README, including the ones with no test (GAP, with the reason). Results
are quoted as measured; nothing is rounded up.

## Layout

| path | what |
|---|---|
| `audio/` | ASoC and AudioReach PAL test definitions, LAVA job generator, results |
| `video/` | iris (V4L2) and GStreamer probes, overlay builder, LAVA job generator, results |
| `camera/` | CAMSS raw capture and libcamera checks, results |
| `display/` | HDMI KMS and GPIO checks, DisplayPort status job, Weston gate, results |
| `common/lava/` | shared LAVA templates (open-boot deploy and login, stock restore backstop) and `jobtpl.py` |
| `tools/scrub.sh` | refuses internal hosts, addresses and secrets; run in CI on every push |

## Images the results were measured on

Every image embeds a build fingerprint (FP) that is incremented on every
build and read back before a result counts: on BL2, BL31, OP-TEE and
U-Boot in the cold boot log, in the kernel release (`uname -r`) and in
/etc/qcdfp (or the DT /lab-fingerprint on diagnostic images).

| area | image | FP |
|---|---|---|
| audio, ASoC path | meta-qcom qcom-multimedia-image, eliza-evk-emmc-open-fw | QCDFP-261119-1770 |
| audio, PAL path | meta-qcom qcom-multimedia-proprietary-image, eliza-evk-emmc-open-fw | QCDFP-261119-1771 |
| audio, regression on an integration image | qcom-multimedia-image | QCDFP-261119-1783 |
| video | qcom-multimedia-image | QCDFP-261119-1770 |
| camera | diagnostic kernel image (camera DT and modules on the QCDFP-261114-1527 kernel and rootfs); later the integration image | ECAMDIAG-261120-05, ECAMDIAG-261120-09; QCDFP-261120-1792 |
| display, HDMI | HDMI test kernel | EHDFP-261120-0005 (EDID on EHDFP-261120-0003) |
| display, Weston | integration image (drm/msm, Mesa freedreno) | QCDFP-261120-1792 |
| display, DisplayPort | qcom-multimedia-image | QCDFP-261114-1511 |

## How to run

LAVA tests: each area has a generator that writes a job from
`common/lava/open-boot.yaml` plus its test actions, and a stock restore
job from `common/lava/backstop.yaml` to submit right after it. The
generators need python3 and PyYAML. Templates carry `{PLACEHOLDER}`s; a
generator fills each one from the environment variable of the same name
when it is set and leaves the rest for you to fill in:

| placeholder / variable | meaning |
|---|---|
| `{IMAGE_URL}` | URL of the qcomflash tarball of the image under test, reachable by the LAVA dispatcher |
| `{IMAGE_DIR}` | top directory inside that tarball (default `qcom-multimedia-image-eliza-evk-emmc-open-fw`, or the proprietary image's) |
| `{FP}` | build fingerprint of that image (generator argument) |
| `{DEVICE}` | LAVA device name of an `eliza-evk-emmc` board (generator argument) |
| `{ROOT_PW}` | root login password of the image |
| `{TD_URL}` | URL of the audio test-definitions tarball (`audio/scripts/mk-td-tar.sh`) |
| `{OVERLAY_URL}` | URL of the video probe overlay (`video/scripts/stage-overlay.sh`) |
| `{STOCK_IMAGE_URL}`, `{STOCK_IMAGE_DIR}` | the lab's stock health-check qcomflash tarball and its top directory, for the backstop |
| `{STOCK_UEFI_VERSION}` | UEFI version string the stock image prints, matched by the backstop |
| `ARTIFACT_HEADER` | optional `Name: value` HTTP header added to every deploy URL of the image under test (for artifact stores that need one) |
| `STOCK_ARTIFACT_HEADER` | optional `Name: value` HTTP header for the stock image URL in the backstop |
| `ACDB_URL`, `ACDB_MD5` | optional, audio PAL only: tar with the board ACDB (not in this repository) and its md5 |

Console tests (camera, HDMI, Weston): the commands are in the area
README; they need a board with the hardware fitted (IMX577 camera module,
HDMI monitor).

## Summary

pass = measured and meets what the test checks; partial = measured in
part, or measured without a target to meet; fail = the test ran and the
feature is absent; not run = a test exists but did not run or could not
exercise the feature (for example no DP sink fitted); GAP = no test.

| area | requirements | pass | partial | fail | not run | GAP |
|---|---|---|---|---|---|---|
| audio | 71 (+13 deprecated, not evaluated) | 5 | 16 | 5 | 0 | 45 |
| video | 41 | 0 | 21 | 2 | 0 | 18 |
| camera | 2 | 1 | 1 | 0 | 0 | 0 |
| display | 89 | 3 | 3 | 0 | 10 | 73 |

Audio counts one status per requirement over both software paths: pass
needs every tested path to pass; one path passing with the other not run
counts as partial.

Not here: the platform regression suites run on the same images (kernel
health, firmware fingerprints, remoteproc, OP-TEE xtest, KVM, GPU,
inference) are not mapped to requirement ids by any evaluation.

## Licensing

`audio/test-definitions/` is a subset of the Linaro test-definitions
tree and stays under its GPL-2.0 licence (`audio/test-definitions/COPYING`).
`display/hdmi/pwnpat.c` is GPL-2.0 (SPDX header).
