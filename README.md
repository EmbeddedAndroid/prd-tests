# prd-tests

Product requirement tests on standard Linux interfaces. The tests are
rows of two products: `products/generic.yaml`, the standard upstream
interfaces every open Linux stack should pass, and `products/qcom.yaml`,
the interfaces and KPIs only Qualcomm stacks have. A board profile
supplies the few facts a test cannot discover and the numbers the board
must reach; one runner executes the rows on a device and reports each
as pass, partial, fail or skip with the reason and the measurements. A
product's own requirements map onto those rows (see "Products and
requirement maps").

The tests use the kernel and user space interfaces any board exposes:
DRM/KMS (atomic API, dumb buffers, writeback) and Wayland/EGL for
display, V4L2 stateful codecs and GStreamer for video, the media
controller, V4L2 capture and libcamera for camera, ALSA (PCMs, mixer,
compress offload), UCM and PipeWire for audio, and the remoteproc, TEE,
TPM, thermal and driver model sysfs interfaces. Each test discovers what
the device has (connectors by type, codecs by format, sensors by entity
function, cards by PCM) and names nothing board specific. Vendor-only
paths live in optional modules a board profile enables
(`tests/audioreach`, `tests/camx`).

## Layout

| path | what |
|---|---|
| `prd/` | runner library: test registry, result rules, target evaluation (`core.py`); raw-ioctl KMS and V4L2 clients (`kms.py`, `v4l2.py`); python3 standard library only |
| `tests/<interface>/` | the generic tests of one interface: `display`, `video`, `camera`, `audio` (with the shell `test-definitions` cases), `platform`; optional vendor modules `audioreach` and `camx` |
| `boards/<board>.yaml` | board profiles: LAVA deploy data, display roles, audio routes and cases, camera graph setup, enabled modules, hardware the board does not have |
| `products/generic.yaml`, `products/qcom.yaml` | the rows: ID to test, arguments and target; standard interfaces and Qualcomm-specific ones |
| `products/counts/<map>.yaml` | public count of a requirement map kept outside the repository: how many of its requirements map to each row |
| `tools/run.py` | the runner (on the device) |
| `tools/mk-overlay.sh` | tars runner, tests, profiles and maps for a LAVA overlay at `/prd` |
| `tools/fetch-vectors.sh` | downloads the public video conformance vectors for the overlay |
| `tools/mk-lava-job.py` | writes the LAVA job for a board and a product (plus a stock restore job where the board needs one) |
| `tools/selftest.py` | checks the result rules and that every product map entry names an existing test with valid arguments |
| `tools/check-generic.py` | refuses board, SoC, card, codec, sensor, connector instance, GPIO and register names under `tests/` (CI) |
| `tools/matrix.py` | test x platform matrix of a product run on several boards (LAVA jobs or results.jsonl) |
| `tools/check-map.py` | checks requirement maps and count files against the rows (CI: the public side) |
| `tools/coverage.py` | per requirement results of a requirement map from runs of the rows |
| `tools/vm/` | the virtual board: run the suite on Linux virtual drivers in QEMU (`run.sh`), its host setup and the CI verdict (`expect.py`) |
| `tools/scrub.sh` | refuses internal hosts, addresses and secrets; runs in CI on every push |
| `diagnostics/<board>/` | board bring-up probes (GPIO and register level); not requirement tests |
| `results/` | recorded results per product and board, with the LAVA jobs |

## Results

Every requirement gets exactly one of:

- pass: measured and meets what the test checks, and the target if the
  row or the board profile gives one;
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

    python3 tools/run.py --board boards/eliza-evk.yaml --product products/generic.yaml \
        --product products/qcom.yaml -o out
    python3 tools/run.py --board ... --product ... --area display          # one area
    python3 tools/run.py --board ... --product ... GDS-023 GDS-026          # some IDs
    python3 tools/run.py --list                                             # every test
    python3 tools/run.py --board boards/eliza-evk.yaml --test display.edid connector=HDMI-A

KMS tests need DRM master: the runner stops the compositor unit named in
the profile (default `weston`) for them and starts it again for the
Wayland tests and at exit.

`display.pipe_width` reads which hardware pipes the driver gave a plane
from the DRM debugfs atomic state (`/sys/kernel/debug/dri/<minor>/state`,
debugfs mounted on demand); the row names the driver's pipe lines, so
the test itself stays generic.

In LAVA (host side, python3 with PyYAML):

    sh tools/fetch-vectors.sh vectors           # optional: video conformance vectors
    sh tools/mk-overlay.sh prd.tar vectors      # upload prd.tar where the dispatcher can fetch it
    export IMAGE_URL=... PRD_URL=... ROOT_PW=...   # optional ARTIFACT_HEADER, PRD_HEADER
    python3 tools/mk-lava-job.py --board boards/eliza-evk.yaml --product products/generic.yaml \
        --product products/qcom.yaml --tag run1 --device <lava device> --fp <build fingerprint> -o jobs

The job flashes the image with qdl, checks the build fingerprint where
the profile says the image embeds one (firmware banners, kernel release,
rootfs file), unpacks the overlay at `/prd`, runs the products and
reports each row as a LAVA test case. For boards whose test
image replaces the lab's stock firmware (`lava.backstop`), a second job
restores the stock image on the same device: submit it right after the
test job (needs `STOCK_IMAGE_URL`, `STOCK_IMAGE_DIR`,
`STOCK_UEFI_VERSION`, optional `STOCK_ARTIFACT_HEADER`).

`python3 tools/selftest.py` checks the runner rules and the rows, and
`python3 tools/check-map.py` the count files; run both after editing a
test, a profile or a product.

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

`run.sh` boots Ubuntu's generic kernel in QEMU (virtme-ng) on the host
root file system, with KVM where the host has it; an arm64 host without
KVM (cloud VMs and CI runners) emulates the guest. `PRD_VM=native` (set
for setup-host.sh too) loads the drivers into the host kernel instead,
on a throwaway VM whose kernel has them; run.sh names any it lacks, as
Ubuntu's cloud kernels may. The verdict of a full run: every requirement
passes, is partial, or skips with the board's reason, unless
`boards/virtual.yaml` lists it under `known_fail` with the reason
(features vkms and snd-aloop do not have). A bare run must not pass
anything that needs the drivers, which shows the tests are not vacuous.
`.github/workflows/virtual.yml` runs both on x86_64 and arm64 runners
for every push; `scrub.yml` runs the scrub, the generic names gate,
the map checker and the selftest.

## Products and requirement maps

`products/generic.yaml` has one row per test and arguments on the
standard interfaces (DRM/KMS with writeback, Wayland/EGL, V4L2 codecs
and capture, the media controller, ALSA, GStreamer, libcamera, TEE,
TPM, thermal). `products/qcom.yaml` has the Qualcomm-specific rows:
remote processors and DSP restart, FastRPC, AudioReach graphs and
calibration, compress offload, vendor display properties and
modifiers, the downstream GL driver, CamX, low power islands, features
no standard interface exposes, and the quality KPIs. Rows say what to
measure; a board profile gives the numbers that board must reach
(`targets`, below). CI runs `generic.yaml` on the virtual board;
both run in LAVA on the real boards:

    python3 tools/matrix.py --product products/generic.yaml --product products/qcom.yaml \
        EVK=<job> RB3=<job> VM=vm-out/results.jsonl

Both set `missing: skip`: a missing interface is a skip ("interface
absent"), since the matrix asks which interfaces work. Row IDs are
never reused.

A product's requirements live in a requirement map: requirement ID,
title, the one row it maps to, and optionally a target of its own
(`none` when the requirement states no number) and a note. A map that
carries internal requirement IDs stays outside this repository; its
public trace is `products/counts/<map>.yaml`, the number of
requirements per row, which `tools/check-map.py` checks against the
rows in CI. With the map at hand:

    python3 tools/check-map.py --map <map.yaml>
    python3 tools/coverage.py --map <map.yaml> <job> [<job> ...]

`coverage.py` reports every requirement from the runs of the rows:
under the map's default `missing: fail` a part skipped because its
interface is missing fails (the requirement needs it), then the map's
target and note apply. From a run's results.jsonl it gives the same
status and reason as running the requirements themselves
(`tools/selftest.py` checks this on every result rule); from a LAVA
log a composite reason broken by a console line inside its " || "
separator is reported "unparsed".

The current matrix is in `results/matrix.md`; totals per platform:

| platform | generic (128): pass / partial / fail / skip | qcom (79): pass / partial / fail / skip |
|---|---|---|
| virtual x86_64 | 39 / 9 / 23 / 57 | not run |
| virtual arm64 | 39 / 9 / 23 / 57 | not run |
| no drivers | 2 / 1 / 4 / 121 | not run |
| Eliza EVK | 56 / 5 / 24 / 43 | 3 / 9 / 48 / 19 |
| RB3 Gen 2 | 50 / 8 / 10 / 60 | 1 / 3 / 46 / 29 |
| IQ-9075 EVK | 48 / 6 / 15 / 59 | 2 / 7 / 47 / 23 |
| IQ-8275 EVK | 51 / 6 / 12 / 59 | 2 / 2 / 45 / 30 |
| Glymur CRD | 54 / 6 / 11 / 57 | 2 / 5 / 42 / 30 |
| VENTUNO Q | 36 / 17 / 14 / 61 | 1 / 2 / 45 / 31 |

## Adding a board

Copy a profile in `boards/` and fill in what applies. A profile for the
same board on another image can instead name the first one in `base`
and give only what differs: its keys are merged over the base profile,
dictionaries key by key.

- `lava`: device type, image directory in the qcomflash tarball,
  storage, firehose programmer, rawprogram and patch files, shell
  prompt; `firmware_fingerprint` and `rootfs_fingerprint` if the image
  embeds a build fingerprint; `backstop` if a stock restore is needed;
  `login_commands` run after login; `timeout_skip` to go on to the next
  action when one times out; `priority` (default low; medium where the
  boards are shared with jobs at medium).
- `display`: the connector type of the `primary` display and the
  `external` ones (`HDMI-A`, `DP`, `DSI`, `eDP`), the compositor unit
  and its Wayland socket (found under /run/user otherwise);
  `gpu_busy`, a GPU busy counter file, for GPU drivers that report no
  DRM client usage stats.
- `audio`: card, playback and capture PCMs with their mixer routes,
  mute/unmute, a second playback front end, a lower volume setting,
  `loopback` when the speaker is heard by the microphones, the audio
  DSP's remoteproc name, and the parameters of each shell `cases` entry
  a product uses. Without them the acoustic tests and cases report
  Missing: skip where `absent` lists the key (`audio-loopback`,
  `audio-routes`), fail otherwise.
- `camera.raw`: media-ctl links and formats for a raw capture.
- `camx`: the camera service unit (`service`, default
  `qti-cam-server`) and the `cameras` the CamX stack exposes (`id`,
  `width`, `height`, `fps`, `format`), used by the `camx` module.
- `modules`: optional vendor modules (`audioreach`, `camx`).
- `targets`: numbers this board must reach, by row ID, in the target
  syntax below; they replace the row's own target.
- `run_last`: tests that leave the device changed for the rest of the
  run (a test name, optionally with `key=value` arguments, for example
  `audio.case case=compress`); requirements using them run after the
  others.
- `absent`: hardware the board does not have, keyed as the tests name
  it (`DSI`, `DP-sink`, `HDMI-A-sink`, `backlight`, `touch`,
  `displays-<n>`, `audio-analyzer`, `camera-sensor`, `camx`, `camera-2`,
  `tpm`, ...), each
  with what would enable the tests.

Nothing else changes: the same rows run on the new board.

## Adding rows and a requirement map

A row in `products/generic.yaml` (standard interface) or
`products/qcom.yaml` (Qualcomm-specific) has a `title`, a `test` and
its `args` (or `tests`, a list, when one requirement needs several),
and optionally a `target` on the test's measurements that holds on
every board:

    GDS-023:
      title: "Output colour depth (max bpc)"
      test: display.connector_property
      args: {commit: true, connector: primary, feature: Colour depth, names: [max bpc]}

A target value is a minimum (`10`), a range (`{min: 5, max: 9}`), an
exact value (`{eq: 10}`) or a list the measured list must contain;
`none` marks a requirement without a number. A row with several
`tests` fails when any of them fails, passes when all pass, skips when
all skip, and is partial otherwise. `areas` maps ID prefixes to the
`--area` names. Run `tools/selftest.py` to check the rows.

A requirement map is YAML: `product`, `board` (the profile whose
targets apply), `missing` (default `fail`) and `requirements`, each
with `id` (the ID a public product used, if any), `title`, `row` and
optional `target` and `note`. Add its count to
`products/counts/` (`map`, `requirements`, `rows`: count per row) and
run `tools/check-map.py --map <map.yaml>`.

## Licensing

GPL-2.0 (SPDX headers). `tests/audio/test-definitions/` is a subset of
the Linaro test-definitions tree and stays under its GPL-2.0 licence
(`tests/audio/test-definitions/COPYING`).
