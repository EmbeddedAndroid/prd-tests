# prd-tests

Product requirement tests on standard Linux interfaces. A product's
requirements are mapped to generic tests; a board profile supplies the
few facts a test cannot discover; one runner executes the map on a
device and reports every requirement as pass, partial, fail or skip
with the reason and the measurements.

The tests use the kernel and user space interfaces any board exposes:
DRM/KMS (atomic API, dumb buffers, writeback) and Wayland/EGL for
display, V4L2 stateful codecs and GStreamer for video, the media
controller, V4L2 capture and libcamera for camera, ALSA (PCMs, mixer,
compress offload), UCM and PipeWire for audio, and the remoteproc, TEE,
TPM, thermal and driver model sysfs interfaces. Each test discovers what
the device has (connectors by type, codecs by format, sensors by entity
function, cards by PCM) and names nothing board specific. Vendor-only
paths live in optional modules a board profile enables
(`tests/audioreach`).

## Layout

| path | what |
|---|---|
| `prd/` | runner library: test registry, result rules, target evaluation (`core.py`); raw-ioctl KMS and V4L2 clients (`kms.py`, `v4l2.py`); python3 standard library only |
| `tests/<interface>/` | the generic tests of one interface: `display`, `video`, `camera`, `audio` (with the shell `test-definitions` cases), `platform`; optional vendor module `audioreach` |
| `boards/<board>.yaml` | board profiles: LAVA deploy data, display roles, audio routes and cases, camera graph setup, enabled modules, hardware the board does not have |
| `products/<product>.yaml` | product maps: requirement ID to test, arguments and target |
| `tools/run.py` | the runner (on the device) |
| `tools/mk-overlay.sh` | tars runner, tests, profiles and maps for a LAVA overlay at `/prd` |
| `tools/fetch-vectors.sh` | downloads the public video conformance vectors for the overlay |
| `tools/mk-lava-job.py` | writes the LAVA job for a board and a product (plus a stock restore job where the board needs one) |
| `tools/selftest.py` | checks the result rules and that every product map entry names an existing test with valid arguments |
| `tools/check-generic.py` | refuses board, SoC, card, codec, sensor, connector instance, GPIO and register names under `tests/` (CI) |
| `tools/matrix.py` | test x platform matrix of a product run on several boards (LAVA jobs or results.jsonl) |
| `tools/vm/` | the virtual board: run the suite on Linux virtual drivers in QEMU (`run.sh`), its host setup and the CI verdict (`expect.py`) |
| `tools/scrub.sh` | refuses internal hosts, addresses and secrets; runs in CI on every push |
| `diagnostics/<board>/` | board bring-up probes (GPIO and register level); not requirement tests |
| `results/` | recorded results per product and board, with the LAVA jobs |

## Results

Every requirement gets exactly one of:

- pass: measured and meets what the test checks, and the target if the
  map gives one;
- partial: works, but the target is not met or not measured, or the
  requirement states no number (`target: none`), or only part of the
  feature could be exercised; reported to LAVA as `unknown`, never as
  a pass;
- fail: the test ran and the feature is missing or broken, including
  features no standard interface exposes (`platform.no_interface` names
  what is missing);
- skip: the board lacks hardware the test needs (a sink, a panel, an
  analyzer), or the board profile states that something the test needs
  is not characterised for that board (audio routes); the reason says
  what would enable it.

A test that finds an interface missing raises `Missing(feature, hw=key)`.
That is a fail, unless the board profile lists `key` under `absent`, in
which case the board does not have that hardware and the result is skip
with the profile's reason. So the same test fails on a board that should
have a DSI panel and skips on a board known to have none.

The runner prints one line per requirement:

    PRD DSP-023 pass: captured 320x240 quadrants ... [formats=XR24,AR24]

and writes `<outdir>/result.txt` (the format `send-to-lava.sh` reads,
one LAVA test case per requirement) and `<outdir>/results.jsonl` (status,
reason and measurements per requirement).

## Running

On a device with python3 (no other module needed when the overlay's
JSON copies of the YAML files are used):

    python3 tools/run.py --board boards/eliza-evk.yaml --product products/eliza.yaml -o out
    python3 tools/run.py --board ... --product ... --area display          # one area
    python3 tools/run.py --board ... --product ... DSP-023 DSP-026          # some IDs
    python3 tools/run.py --list                                             # every test
    python3 tools/run.py --board boards/eliza-evk.yaml --test display.edid connector=HDMI-A

KMS tests need DRM master: the runner stops the compositor unit named in
the profile (default `weston`) for them and starts it again for the
Wayland tests and at exit.

In LAVA (host side, python3 with PyYAML):

    sh tools/fetch-vectors.sh vectors           # optional: video conformance vectors
    sh tools/mk-overlay.sh prd.tar vectors      # upload prd.tar where the dispatcher can fetch it
    export IMAGE_URL=... PRD_URL=... ROOT_PW=...   # optional ARTIFACT_HEADER, PRD_HEADER
    python3 tools/mk-lava-job.py --board boards/eliza-evk.yaml --product products/eliza.yaml \
        --tag run1 --device <lava device> --fp <build fingerprint> -o jobs

The job flashes the image with qdl, checks the build fingerprint where
the profile says the image embeds one (firmware banners, kernel release,
rootfs file), unpacks the overlay at `/prd`, runs the product map and
reports each requirement as a LAVA test case. For boards whose test
image replaces the lab's stock firmware (`lava.backstop`), a second job
restores the stock image on the same device: submit it right after the
test job (needs `STOCK_IMAGE_URL`, `STOCK_IMAGE_DIR`,
`STOCK_UEFI_VERSION`, optional `STOCK_ARTIFACT_HEADER`).

`python3 tools/selftest.py` checks the runner rules and the maps; run it
after editing a test, a profile or a map.

## Virtual board and CI

`boards/virtual.yaml` describes a board made of Linux virtual drivers:
vkms (KMS with overlay planes and writeback), vimc (a media controller
camera pipeline), vicodec (an FWHT stateful codec), vivid (a V4L2
capture device), snd-aloop (an ALSA loopback card), a software TPM
(swtpm) and Weston on vkms with the Mesa software renderer.

    sh tools/vm/setup-host.sh          # Ubuntu 24.04, arm64 or x86_64
    sudo sh tools/vm/run.sh full       # results in vm-out/
    python3 tools/vm/expect.py vm-out full
    sudo sh tools/vm/run.sh bare       # same suite, no virtual drivers
    python3 tools/vm/expect.py vm-out-bare bare

With KVM, `run.sh` boots Ubuntu's generic kernel in QEMU (virtme-ng) on
the host root file system; without KVM (arm64 cloud VMs and CI runners,
which are QEMU/KVM guests themselves) it loads the same drivers into the
host kernel, because an emulated guest is too slow. The verdict of a
full run: every requirement passes, is partial, or skips with the
board's reason, unless `boards/virtual.yaml` lists it under `known_fail`
with the reason (features vkms and snd-aloop do not have). A bare run
must not pass anything that needs the drivers, which shows the tests are
not vacuous. `.github/workflows/virtual.yml` runs both on x86_64 and
arm64 runners for every push; `scrub.yml` runs the scrub, the generic
names gate and the selftest.

## Generic product and matrix

`products/generic.yaml` holds one requirement per test with arguments
any board can answer, and sets `missing: skip`: there a missing
interface is a skip ("interface absent"), since the question is which
interfaces work. A product map that demands a feature (`products/
eliza.yaml`) keeps the default, where a missing interface is a fail
unless the board profile lists the hardware as absent. Run the generic
product on several boards and tabulate it:

    python3 tools/matrix.py --product products/generic.yaml EVK=<job> RB3=<job> VM=vm-out/results.jsonl

The current matrix is in `results/matrix.md`.
Totals of the current run (pass/partial/fail/skip of 72):

| platform | pass | partial | fail | skip |
|---|---|---|---|---|
| virtual x86_64 (QEMU + KVM) | 35 | 5 | 12 (all `known_fail`) | 20 |
| virtual arm64 (native) | 35 | 5 | 12 (all `known_fail`) | 20 |
| virtual, no drivers | 2 | 0 | 3 | 67 |
| Eliza EVK | 31 | 5 | 16 | 20 |
| RB3 Gen 2 | 36 | 5 | 3 | 28 |
| IQ-9075 EVK | 36 | 5 | 4 | 27 |
| IQ-8275 EVK | 36 | 4 | 4 | 28 |
| Glymur CRD | 37 | 4 | 4 | 27 |
| VENTUNO Q | 30 | 4 | 5 | 33 |

## Adding a board

Copy a profile in `boards/` and fill in what applies:

- `lava`: device type, image directory in the qcomflash tarball,
  storage, firehose programmer, rawprogram and patch files, shell
  prompt; `firmware_fingerprint` and `rootfs_fingerprint` if the image
  embeds a build fingerprint; `backstop` if a stock restore is needed;
  `login_commands` run after login; `timeout_skip` to go on to the next
  action when one times out; `priority` (default low; medium where the
  boards are shared with jobs at medium).
- `display`: the connector type of the `primary` display and the
  `external` ones (`HDMI-A`, `DP`, `DSI`, `eDP`), the compositor unit
  and its Wayland socket (found under /run/user otherwise).
- `audio`: card, playback and capture PCMs with their mixer routes,
  mute/unmute, a second playback front end, a lower volume setting,
  `loopback` when the speaker is heard by the microphones, the audio
  DSP's remoteproc name, and the parameters of each shell `cases` entry
  a product uses. Without them the acoustic tests and cases report
  Missing: skip where `absent` lists the key (`audio-loopback`,
  `audio-routes`), fail otherwise.
- `camera.raw`: media-ctl links and formats for a raw capture.
- `modules`: optional vendor modules (`audioreach`).
- `run_last`: tests that leave the device changed for the rest of the
  run (a test name, optionally with `key=value` arguments, for example
  `audio.case case=compress`); requirements using them run after the
  others.
- `absent`: hardware the board does not have, keyed as the tests name
  it (`DSI`, `DP-sink`, `HDMI-A-sink`, `backlight`, `touch`,
  `displays-<n>`, `audio-analyzer`, `camera-sensor`, `tpm`, ...), each
  with what would enable the tests.

Nothing else changes: the same product map runs on the new board.

## Adding a product

Write `products/<product>.yaml`: neutral requirement IDs (no internal
tracker IDs), each with a `title`, a `test` and its `args` (or `tests`,
a list, when one requirement needs several), and a `target` on the
test's measurements when the requirement states one:

    DSP-003:
      title: "Color Depth"
      test: display.connector_property
      args: {connector: primary, names: [max bpc], feature: Colour depth, commit: true}
      target: {max_bpc: 10}

`missing: skip` at the top makes a missing interface a skip instead of
a fail (see above). A target value is a minimum (`10`), a range (`{min: 5, max: 9}`), an
exact value (`{eq: 10}`) or a list the measured list must contain;
`none` marks a requirement without a number. A requirement with
several `tests` fails when any of them fails, passes when all pass,
skips when all skip, and is partial otherwise. `areas` maps ID prefixes to
the `--area` names. Run `tools/selftest.py` to check the map.

## Licensing

GPL-2.0 (SPDX headers). `tests/audio/test-definitions/` is a subset of
the Linaro test-definitions tree and stays under its GPL-2.0 licence
(`tests/audio/test-definitions/COPYING`).
