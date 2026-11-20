# Audio

Tests for the Eliza audio requirements on the Eliza CQS EVK (eMMC), on
two software paths:

- ASoC: the upstream ASoC card (aplay/arecord and mixer controls) on the
  open image `qcom-multimedia-image`, FP QCDFP-261119-1770.
- PAL: the AudioReach PAL/AGM path through PipeWire on
  `qcom-multimedia-proprietary-image`, FP QCDFP-261119-1771, with the
  Eliza audio calibration database (ACDB) installed as an overlay. The
  ACDB is not distributable and is not in this repository.

Both images are meta-qcom `eliza-evk-emmc-open-fw` builds (open TF-A,
OP-TEE, U-Boot). Every job checks the build fingerprint on BL2, BL31,
OP-TEE, U-Boot, the kernel release and /etc/qcdfp before any test runs.

Each requirement case is a test definition under
`test-definitions/automated/linux/` (a subset of the Linaro
test-definitions tree, GPL-2.0, see `test-definitions/COPYING`). The
audio cases play a sine on the speaker and capture it on the on-board
DMICs (acoustic loopback), then check the spectral peak, level and
timing.

## Layout

| path | what |
|---|---|
| `test-definitions/automated/linux/<dir>/` | one definition per case group (yaml + script) |
| `test-definitions/automated/lib/audio/` | shared tone, spectrum and control helpers |
| `scripts/mk-prd-job.py` | writes the LAVA job (ASoC `base` or PAL `ovl`) with one inline definition per case, plus a stock restore backstop |
| `scripts/mk-ci-regression.sh` | the regression job: cases in `scripts/regression-cases.json` (passed on both EVKs) |
| `scripts/mk-td-tar.sh` | tars the test definitions for the job's `/td` overlay |

Case name to definition directory (the case names are what the job
generator and the results use):

| case | directory |
|---|---|
| `alsa-capture-dmic23`, `alsa-capture-dmic67`, `alsa-capture-pal` | `alsa-capture` |
| `alsa-loopback`, `alsa-loopback-pal`, `alsa-loopback-pal-db` | `alsa-loopback` |
| `alsa-volume`, `alsa-volume-pal` | `alsa-volume` |
| `alsa-concurrency-pal` | `alsa-concurrency` |
| `fastrpc-adsp` | `fastrpc` |
| `acdb-eliza-installed` | a step of the first case in `mk-prd-job.py` (needs `ACDB_MD5`) |
| every other case | the directory of the same name |

## How to run

Needs python3 with PyYAML and a LAVA instance with `eliza-evk-emmc`
devices.

    sh scripts/mk-td-tar.sh /tmp/prdtd.tar      # upload it, then:
    export IMAGE_URL=... TD_URL=... ROOT_PW=...
    export STOCK_IMAGE_URL=... STOCK_IMAGE_DIR=... STOCK_UEFI_VERSION=...
    python3 scripts/mk-prd-job.py base prd-base <FP> <device> jobs
    python3 scripts/mk-prd-job.py ovl prd-ovl <FP> <device> jobs   # PAL; optional ACDB_URL, ACDB_MD5
    lavacli jobs submit jobs/prd-base.yaml; lavacli jobs submit jobs/prd-base-backstop.yaml

A trailing comma list of case names keeps only those cases (as in the
table below). `sh scripts/mk-ci-regression.sh base <FP> <device>` writes
the regression job. Placeholders and environment variables are listed in
the top-level README.

Run the two EVKs one after the other, never at the same time: boards in
the same rack hear each other's speakers. Test tones sit at 1600, 2600
and 3640 Hz because the rack has a 1040 Hz interferer and its harmonics.

The generator is the version the evaluation used last (it reproduces the
regression job [591307](https://lava.infra.foundries.io/scheduler/job/591307)
step for step). The final ASoC report jobs (589422, 589537) ran an
earlier revision whose speaker route did not explicitly clear the
MultiMedia1 and MultiMedia5 mixer inputs; nothing else differs.

## Known board issue

The left WSA8845 speaker amplifier (SoundWire 1:0:0217:0204:00:0) never
attaches on any Eliza EVK in the lab (open, hardware level). A stream
prepare that touches it fails ("Program transport params failed"), so
MultiMedia2 cases fail at random in some runs; after an MP3 offload
stream, later streams usually fail, so `alsa-compress` runs last. A red
MultiMedia2 case is not by itself a regression without a second run.

## Results

Status per path: pass = every case passed on both EVKs in the final runs
(2026-11-19); partial = some cases pass or the effect is only partly
verified; fail = the case ran and the feature is absent; not run = no
case for that path. Requirement status: GAP = no test written for any
path.

Final runs: ASoC [589422](https://lava.infra.foundries.io/scheduler/job/589422)
and [589537](https://lava.infra.foundries.io/scheduler/job/589537),
regression [589601](https://lava.infra.foundries.io/scheduler/job/589601)
(78 pass / 10 fail, the SoundWire intermittents above); PAL
[590568](https://lava.infra.foundries.io/scheduler/job/590568) and
[590676](https://lava.infra.foundries.io/scheduler/job/590676), regression
[591028](https://lava.infra.foundries.io/scheduler/job/591028) (44 pass /
0 fail). PAL with the SM8750 ACDB instead of the Eliza one:
[590789](https://lava.infra.foundries.io/scheduler/job/590789),
[591094](https://lava.infra.foundries.io/scheduler/job/591094) (same
statuses except the three concurrency requirements, which pass there).
First regression run on an integration image (FP QCDFP-261119-1783):
[591307](https://lava.infra.foundries.io/scheduler/job/591307), 84 pass /
0 fail / 14 skip; `alsa-compress` skipped because that image has no
`cplay` or `lame`.

| ID | requirement | test | how to run | latest result |
|---|---|---|---|---|
| AUD-001 | Multiband DRC (MBDRC) | none | - | GAP: no test written; PAL: not run: MBDRC: no per-module hook without QACT; Eliza ACDB loaded, module not observable |
| AUD-002 | Volume Control - Basic | ASoC: `alsa-volume`<br>PAL: `alsa-volume-pal` | `scripts/mk-prd-job.py base <tag> <FP> <device> jobs alsa-volume`<br>`scripts/mk-prd-job.py ovl <tag> <FP> <device> jobs alsa-volume-pal` | ASoC: pass (9 pass / 0 fail cases, 2026-11-19, [589422](https://lava.infra.foundries.io/scheduler/job/589422) [589537](https://lava.infra.foundries.io/scheduler/job/589537)): passes on both final boards; failed in other 1770 runs (589233, 589327) when a MultiMedia2 open hit the SoundWire prepare failure of the never-attached left WSA8845 (AUD-003)<br>PAL: partial (7 pass / 2 fail cases, 2026-11-19, [590568](https://lava.infra.foundries.io/scheduler/job/590568) [590676](https://lava.infra.foundries.io/scheduler/job/590676)): PAL volume: 0 to -1 dB steps 0.9 dB, but -1 to -10 measures ~3 dB and -10 to -20 ~0.9 dB on both boards (same with the SM8750 ACDB in LAVA 588219): the device PP compresses the range |
| AUD-004 | Mono Noise Suppression | PAL: `pal-ecns` | `scripts/mk-prd-job.py ovl <tag> <FP> <device> jobs pal-ecns` | PAL: partial (1 pass / 2 fail cases, 2026-11-19, [590568](https://lava.infra.foundries.io/scheduler/job/590568) [590676](https://lava.infra.foundries.io/scheduler/job/590676)): processed (ECNS) node near-silent (-129 dBFS on a -28 dBFS tone, bursts lost too); PAL logs ACDB CKV error 17 / no calibration on the ECNS graph |
| AUD-005 | Dynamic Range Control (DRC) | none | - | GAP: no test written; PAL: not run: TX DRC: no per-module hook without QACT; Eliza ACDB loaded, module not observable |
| AUD-006 | Dual-Mic ECNS | none | - | GAP: no test written; PAL: not run: Dual-mic ECNS: PAL exposes one speaker-mic ECNS usecase only |
| AUD-007 | Multi-Mic ECNS | none | - | GAP: no test written; PAL: not run: Multi-mic ECNS: no multi-mic usecase in the PAL config |
| AUD-008 | TDM/PCM | ASoC: `audio-interfaces`<br>PAL: `audio-interfaces` | `scripts/mk-prd-job.py base <tag> <FP> <device> jobs audio-interfaces`<br>`scripts/mk-prd-job.py ovl <tag> <FP> <device> jobs audio-interfaces` | ASoC: fail (0 pass / 1 fail cases, 2026-11-19, [589422](https://lava.infra.foundries.io/scheduler/job/589422) [589537](https://lava.infra.foundries.io/scheduler/job/589537)): no TDM DAI link on the card (upstream ASoC DT)<br>PAL: fail (0 pass / 1 fail cases, 2026-11-19, [590568](https://lava.infra.foundries.io/scheduler/job/590568) [590676](https://lava.infra.foundries.io/scheduler/job/590676)): interface not present on the card |
| AUD-009 | Digital Mics | ASoC: `alsa-capture-dmic23`, `alsa-capture-dmic67`, `alsa-loopback`<br>PAL: `alsa-capture-pal` | `scripts/mk-prd-job.py base <tag> <FP> <device> jobs alsa-capture-dmic23,alsa-capture-dmic67,alsa-loopback`<br>`scripts/mk-prd-job.py ovl <tag> <FP> <device> jobs alsa-capture-pal` | ASoC: pass (11 pass / 0 fail cases, 2026-11-19, [589422](https://lava.infra.foundries.io/scheduler/job/589422) [589537](https://lava.infra.foundries.io/scheduler/job/589537))<br>PAL: pass (3 pass / 0 fail cases, 2026-11-19, [590568](https://lava.infra.foundries.io/scheduler/job/590568) [590676](https://lava.infra.foundries.io/scheduler/job/590676)) |
| AUD-010 | AV Sync | none | - | GAP: no test written; PAL: not run: AV sync: PRD row is TBD; parked until the requirement defines it |
| AUD-011 | Device switch | ASoC: `alsa-device-switch` | `scripts/mk-prd-job.py base <tag> <FP> <device> jobs alsa-device-switch` | ASoC: pass (5 pass / 0 fail cases, 2026-11-19, [589422](https://lava.infra.foundries.io/scheduler/job/589422) [589537](https://lava.infra.foundries.io/scheduler/job/589537)): passes on both final boards; failed in other 1770 runs (589141, 589143, 589327) when a MultiMedia2 open hit the SoundWire prepare failure of the never-attached left WSA8845 (AUD-003)<br>PAL: not run: Device switch on PAL: only speaker devices fitted; headset/DP nodes exist, no device to switch to |
| AUD-012 | Subsystem Restart | none | - | GAP: no PRD case written; the DSP restart cases inside alsa-capture (REMOTEPROC) were skipped in these runs; first pass: the restart after audio is refused by the open firmware LPASS reset handling (fix belongs in OP-TEE) (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-013 | Tunnel Mode | ASoC: `alsa-compress` | `scripts/mk-prd-job.py base <tag> <FP> <device> jobs alsa-compress` | ASoC: pass (6 pass / 0 fail cases, 2026-11-19, [589422](https://lava.infra.foundries.io/scheduler/job/589422) [589537](https://lava.infra.foundries.io/scheduler/job/589537)): MP3 offload plays in real time; after an offload stream later streams often fail or are silent (589233, 589252, 589327, 589416), so it runs last<br>PAL: not run: Tunnel on PAL: pal_sink_speaker_compress node exists, no client test yet; passes on the Base path<br>upstream retest (sound for-next, 2026-11-18): MP3 offload drains in 5 of 6 runs with the q6apm-dai compress map fix ([582345](https://lava.infra.foundries.io/scheduler/job/582345) [582349](https://lava.infra.foundries.io/scheduler/job/582349) [582353](https://lava.infra.foundries.io/scheduler/job/582353) [582357](https://lava.infra.foundries.io/scheduler/job/582357) [582361](https://lava.infra.foundries.io/scheduler/job/582361) [582365](https://lava.infra.foundries.io/scheduler/job/582365)), 0 of 6 without ([582343](https://lava.infra.foundries.io/scheduler/job/582343) [582347](https://lava.infra.foundries.io/scheduler/job/582347) [582351](https://lava.infra.foundries.io/scheduler/job/582351) [582355](https://lava.infra.foundries.io/scheduler/job/582355) [582359](https://lava.infra.foundries.io/scheduler/job/582359) [582363](https://lava.infra.foundries.io/scheduler/job/582363)); the failing fix run could not open the compress device |
| AUD-014 | Audio Calibration Database (ACDB) | PAL: `alsa-capture-pal`, `alsa-loopback-pal`, `soundwire-status` | `scripts/mk-prd-job.py ovl <tag> <FP> <device> jobs alsa-capture-pal,alsa-loopback-pal,soundwire-status` | PAL: pass (10 pass / 0 fail cases, 2026-11-19, [590568](https://lava.infra.foundries.io/scheduler/job/590568) [590676](https://lava.infra.foundries.io/scheduler/job/590676)): Eliza ACDB installed and loaded by AGM; speaker and mic graphs play and record; the ECNS graph finds no calibration (CKV error 17, see AUD-015) |
| AUD-016 | Qualcomm Audio Calibration Tool (QACT) | none | - | GAP: no test written; PAL: not run: QACT: needs the QACT host tool and its device transport |
| AUD-017 | music playback + alert sounds | none | - | GAP: no test; concurrency on the ASoC path allows one front end per back end (see AUD-018), on the PAL path see AUD-018 (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-019 | audio recording + ringer | PAL: `alsa-loopback-pal` | `scripts/mk-prd-job.py ovl <tag> <FP> <device> jobs alsa-loopback-pal` | PAL: pass (6 pass / 0 fail cases, 2026-11-19, [590568](https://lava.infra.foundries.io/scheduler/job/590568) [590676](https://lava.infra.foundries.io/scheduler/job/590676)) |
| AUD-020 | multichannel decode + ringer/alert | PAL: `alsa-concurrency-pal` | `scripts/mk-prd-job.py ovl <tag> <FP> <device> jobs alsa-concurrency-pal` | PAL: partial (6 pass / 1 fail cases, 2026-11-19, [590568](https://lava.infra.foundries.io/scheduler/job/590568) [590676](https://lava.infra.foundries.io/scheduler/job/590676)): both streams play and mix; with the Eliza ACDB stream A (ll) drops ~9 dB and B (db) ~3 dB while both play (590568, 590676, 591190, 591233; SM8750 ACDB: 0.1 dB, 590789, 591094); on the second EVK the attenuated 1600 Hz tone lost the spectral peak to the rack's ~1567 Hz interferer; intermittent: concurrency-both-a 1/2 |
| AUD-018 | Simultanious streams | PAL: `alsa-concurrency-pal` | `scripts/mk-prd-job.py ovl <tag> <FP> <device> jobs alsa-concurrency-pal` | PAL: partial (6 pass / 1 fail cases, 2026-11-19, [590568](https://lava.infra.foundries.io/scheduler/job/590568) [590676](https://lava.infra.foundries.io/scheduler/job/590676)): both streams play and mix; with the Eliza ACDB stream A (ll) drops ~9 dB and B (db) ~3 dB while both play (590568, 590676, 591190, 591233; SM8750 ACDB: 0.1 dB, 590789, 591094); on the second EVK the attenuated 1600 Hz tone lost the spectral peak to the rack's ~1567 Hz interferer; intermittent: concurrency-both-a 1/2 |
| AUD-021 | Extended Pause | none | - | GAP: no test written; PAL: not run: Extended pause: s2idle resets the EVK (earlier run, not rerun here) |
| AUD-022 | ALAC | none | - | GAP: no test written; PAL: not run: ALAC: no DSP ALAC client/test yet |
| AUD-023 | Analog Mics | ASoC: `audio-interfaces`<br>PAL: `audio-interfaces` | `scripts/mk-prd-job.py base <tag> <FP> <device> jobs audio-interfaces`<br>`scripts/mk-prd-job.py ovl <tag> <FP> <device> jobs audio-interfaces` | ASoC: fail (0 pass / 1 fail cases, 2026-11-19, [589422](https://lava.infra.foundries.io/scheduler/job/589422) [589537](https://lava.infra.foundries.io/scheduler/job/589537)): no analog codec on the card (EVK speakers/mics are WSA884x + DMIC only)<br>PAL: fail (0 pass / 1 fail cases, 2026-11-19, [590568](https://lava.infra.foundries.io/scheduler/job/590568) [590676](https://lava.infra.foundries.io/scheduler/job/590676)): interface not present on the card |
| AUD-024 | Speaker Protection | none | - | GAP: no test; needs WSA VI feedback capture and the protection module in the graph (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-025 | Cold Start Output Latency | none | - | GAP: no test; latency KPI without a target number yet (loopback timing on the EVK is possible) (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-026 | Continuous Output Latency | none | - | GAP: no test; latency KPI without a target number yet (loopback timing on the EVK is possible) (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-027 | Restart Latency | none | - | GAP: no test; latency KPI without a target number yet (loopback timing on the EVK is possible) (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-028 | Output device switch latency | none | - | GAP: no test; latency KPI without a target number yet (loopback timing on the EVK is possible) (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-029 | Cold Start Input Latency | none | - | GAP: no test; latency KPI without a target number yet (loopback timing on the EVK is possible) (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-030 | Continuous Input Latency | none | - | GAP: no test; latency KPI without a target number yet (loopback timing on the EVK is possible) (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-031 | Continuous Roundtrip Latency | none | - | GAP: no test; latency KPI without a target number yet (loopback timing on the EVK is possible) (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-032 | Playback PEAQ | none | - | GAP: no test; quality KPI, needs a calibrated audio analyzer on a fitted board (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-033 | Playback THD+N | none | - | GAP: no test; quality KPI, needs a calibrated audio analyzer on a fitted board (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-034 | Playback Noise | none | - | GAP: no test; quality KPI, needs a calibrated audio analyzer on a fitted board (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-035 | Playback Dynamic Range | none | - | GAP: no test; quality KPI, needs a calibrated audio analyzer on a fitted board (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-036 | Playback SNR | none | - | GAP: no test; quality KPI, needs a calibrated audio analyzer on a fitted board (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-037 | Playback Click and Pop | none | - | GAP: no test; quality KPI, needs a calibrated audio analyzer on a fitted board (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-038 | Playback Maximum Loudness / Output Power | none | - | GAP: no test; quality KPI, needs a calibrated audio analyzer on a fitted board (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-039 | Playback Frequency Response | none | - | GAP: no test; quality KPI, needs a calibrated audio analyzer on a fitted board (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-040 | Record THD+N | none | - | GAP: no test; quality KPI, needs a calibrated audio analyzer on a fitted board (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-041 | Record SNR | none | - | GAP: no test; quality KPI, needs a calibrated audio analyzer on a fitted board (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-042 | Record Click and Pop | none | - | GAP: no test; quality KPI, needs a calibrated audio analyzer on a fitted board (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-043 | Record Frequency Response | none | - | GAP: no test; quality KPI, needs a calibrated audio analyzer on a fitted board (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-003 | Audio SoundWire | ASoC: `soundwire-status`<br>PAL: `soundwire-status` | `scripts/mk-prd-job.py base <tag> <FP> <device> jobs soundwire-status`<br>`scripts/mk-prd-job.py ovl <tag> <FP> <device> jobs soundwire-status` | ASoC: partial (5 pass / 1 fail cases, 2026-11-19, [589422](https://lava.infra.foundries.io/scheduler/job/589422) [589537](https://lava.infra.foundries.io/scheduler/job/589537)): left WSA8845 (sdw 1:0:0217:0204:00:0) never attaches on any Eliza EVK (open, hardware level); a stream prepare that touches it fails ("Program transport params failed")<br>PAL: partial (6 pass / 1 fail cases, 2026-11-19, [590568](https://lava.infra.foundries.io/scheduler/job/590568) [590676](https://lava.infra.foundries.io/scheduler/job/590676)): the never-attached left WSA8845 (sdw 1:0:0217:0204:00:0; open, hardware-level) |
| AUD-044 | I2C | ASoC: `audio-interfaces`<br>PAL: `audio-interfaces` | `scripts/mk-prd-job.py base <tag> <FP> <device> jobs audio-interfaces`<br>`scripts/mk-prd-job.py ovl <tag> <FP> <device> jobs audio-interfaces` | ASoC: fail (0 pass / 1 fail cases, 2026-11-19, [589422](https://lava.infra.foundries.io/scheduler/job/589422) [589537](https://lava.infra.foundries.io/scheduler/job/589537)): no I2C audio device on the card<br>PAL: fail (0 pass / 1 fail cases, 2026-11-19, [590568](https://lava.infra.foundries.io/scheduler/job/590568) [590676](https://lava.infra.foundries.io/scheduler/job/590676)): interface not present on the card |
| AUD-045 | I2S | ASoC: `audio-interfaces`<br>PAL: `audio-interfaces` | `scripts/mk-prd-job.py base <tag> <FP> <device> jobs audio-interfaces`<br>`scripts/mk-prd-job.py ovl <tag> <FP> <device> jobs audio-interfaces` | ASoC: fail (0 pass / 1 fail cases, 2026-11-19, [589422](https://lava.infra.foundries.io/scheduler/job/589422) [589537](https://lava.infra.foundries.io/scheduler/job/589537)): no I2S/MI2S DAI link on the card<br>PAL: fail (0 pass / 1 fail cases, 2026-11-19, [590568](https://lava.infra.foundries.io/scheduler/job/590568) [590676](https://lava.infra.foundries.io/scheduler/job/590676)): interface not present on the card |
| AUD-046 | DisplayPort | PAL: `audio-interfaces` | `scripts/mk-prd-job.py ovl <tag> <FP> <device> jobs audio-interfaces` | PAL: fail (0 pass / 1 fail cases, 2026-11-19, [590568](https://lava.infra.foundries.io/scheduler/job/590568) [590676](https://lava.infra.foundries.io/scheduler/job/590676)): interface not present on the card |
| AUD-047 | LPCM (Decoder) | ASoC: `alsa-formats`, `alsa-loopback`<br>PAL: `alsa-loopback-pal`, `alsa-loopback-pal-db` | `scripts/mk-prd-job.py base <tag> <FP> <device> jobs alsa-formats,alsa-loopback`<br>`scripts/mk-prd-job.py ovl <tag> <FP> <device> jobs alsa-loopback-pal,alsa-loopback-pal-db` | ASoC: partial (44 pass / 3 fail cases, 2026-11-19, [589422](https://lava.infra.foundries.io/scheduler/job/589422) [589537](https://lava.infra.foundries.io/scheduler/job/589537)): two PCMs on one WSA backend (concurrent-playback) do not both play; MultiMedia2 opens fail intermittently on the never-attached left WSA8845 (AUD-003); intermittent: plug-playback-96000-S16_LE-1ch 1/2, plug-playback-96000-S16_LE-2ch 1/2<br>PAL: pass (12 pass / 0 fail cases, 2026-11-19, [590568](https://lava.infra.foundries.io/scheduler/job/590568) [590676](https://lava.infra.foundries.io/scheduler/job/590676)) |
| AUD-048 | AAC-LC | none | - | GAP: no test; DSP offload decode proven with MP3 on the ASoC path (AUD-013), this codec not exercised (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-049 | HE-AAC v1 (AAC+) | none | - | GAP: no test; DSP offload decode proven with MP3 on the ASoC path (AUD-013), this codec not exercised (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-050 | HE-AAC v2 (eAAC+) | none | - | GAP: no test; DSP offload decode proven with MP3 on the ASoC path (AUD-013), this codec not exercised (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-051 | AAC-ELD | none | - | GAP: no test; DSP offload decode proven with MP3 on the ASoC path (AUD-013), this codec not exercised (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-052 | MP3 | none | - | GAP: no test written; PAL: not run: MP3 on PAL: pal_sink_speaker_compress node exists, no client test yet; MP3 offload passes on the Base (ASoC) path |
| AUD-053 | Power Island | none | - | GAP: no test; island entry never measured (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-015 | Single-mic ECNS | PAL: `pal-ecns` | `scripts/mk-prd-job.py ovl <tag> <FP> <device> jobs pal-ecns` | PAL: partial (1 pass / 2 fail cases, 2026-11-19, [590568](https://lava.infra.foundries.io/scheduler/job/590568) [590676](https://lava.infra.foundries.io/scheduler/job/590676)): processed (ECNS) node near-silent (-129 dBFS on a -28 dBFS tone, bursts lost too); PAL logs ACDB CKV error 17 / no calibration on the ECNS graph |
| AUD-054 | Ogg Vorbis | none | - | GAP: no test; DSP offload decode proven with MP3 on the ASoC path (AUD-013), this codec not exercised (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-055 | LPCM (Encoder) | ASoC: `alsa-capture-dmic23`, `alsa-formats`<br>PAL: `alsa-capture-pal` | `scripts/mk-prd-job.py base <tag> <FP> <device> jobs alsa-capture-dmic23,alsa-formats`<br>`scripts/mk-prd-job.py ovl <tag> <FP> <device> jobs alsa-capture-pal` | ASoC: pass (29 pass / 0 fail cases, 2026-11-19, [589422](https://lava.infra.foundries.io/scheduler/job/589422) [589537](https://lava.infra.foundries.io/scheduler/job/589537))<br>PAL: pass (3 pass / 0 fail cases, 2026-11-19, [590568](https://lava.infra.foundries.io/scheduler/job/590568) [590676](https://lava.infra.foundries.io/scheduler/job/590676)) |
| AUD-056 | Upmixing | ASoC: `alsa-upmix` | `scripts/mk-prd-job.py base <tag> <FP> <device> jobs alsa-upmix` | ASoC: pass (3 pass / 0 fail cases, 2026-11-19, [589422](https://lava.infra.foundries.io/scheduler/job/589422) [589537](https://lava.infra.foundries.io/scheduler/job/589537))<br>PAL: not run: Upmix on PAL: not tested (Base path upmix tested via ALSA plug) |
| AUD-057 | Limiter | none | - | GAP: no test written; PAL: not run: Limiter: no per-module hook without QACT; Eliza ACDB loaded, module not observable |
| AUD-058 | Infinite Impulse Response (IIR) Tuning Filters | none | - | GAP: no test written; PAL: not run: IIR: no per-module hook without QACT; Eliza ACDB loaded, module not observable |
| AUD-059 | Hexagon DSP | PAL: `fastrpc-adsp` | `scripts/mk-prd-job.py ovl <tag> <FP> <device> jobs fastrpc-adsp` | PAL: partial (1 pass / 1 fail cases, 2026-11-19, [590568](https://lava.infra.foundries.io/scheduler/job/590568) [590676](https://lava.infra.foundries.io/scheduler/job/590676)): signed PD on the ADSP runs; unsigned PD fails |
| AUD-060 | Low latency Audio | ASoC: `alsa-latency`<br>PAL: `alsa-loopback-pal` | `scripts/mk-prd-job.py base <tag> <FP> <device> jobs alsa-latency`<br>`scripts/mk-prd-job.py ovl <tag> <FP> <device> jobs alsa-loopback-pal` | ASoC: partial (2 pass / 6 fail cases, 2026-11-19, [589422](https://lava.infra.foundries.io/scheduler/job/589422) [589537](https://lava.infra.foundries.io/scheduler/job/589537)): capture refuses periods below 40 ms (granted 1920 frames); 5 ms playback opens when the SoundWire prepare succeeds; no alsabat for round-trip latency; intermittent: lowlatency-reference-detected 1/2, lowlatency-playback-period 1/2, lowlatency-playback-no-xrun 1/2<br>PAL: pass (6 pass / 0 fail cases, 2026-11-19, [590568](https://lava.infra.foundries.io/scheduler/job/590568) [590676](https://lava.infra.foundries.io/scheduler/job/590676)) |
| AUD-061 | DSP Framework | PAL: `fastrpc-adsp` | `scripts/mk-prd-job.py ovl <tag> <FP> <device> jobs fastrpc-adsp` | PAL: partial (1 pass / 1 fail cases, 2026-11-19, [590568](https://lava.infra.foundries.io/scheduler/job/590568) [590676](https://lava.infra.foundries.io/scheduler/job/590676)): signed PD on the ADSP runs; unsigned PD fails |
| AUD-062 | Soft Stepping | none | - | GAP: no test written; PAL: not run: Soft stepping: ramp test not written yet |
| AUD-063 | Capture | ASoC: `alsa-capture-dmic23`, `alsa-capture-dmic67`<br>PAL: `alsa-capture-pal` | `scripts/mk-prd-job.py base <tag> <FP> <device> jobs alsa-capture-dmic23,alsa-capture-dmic67`<br>`scripts/mk-prd-job.py ovl <tag> <FP> <device> jobs alsa-capture-pal` | ASoC: pass (10 pass / 0 fail cases, 2026-11-19, [589422](https://lava.infra.foundries.io/scheduler/job/589422) [589537](https://lava.infra.foundries.io/scheduler/job/589537))<br>PAL: pass (3 pass / 0 fail cases, 2026-11-19, [590568](https://lava.infra.foundries.io/scheduler/job/590568) [590676](https://lava.infra.foundries.io/scheduler/job/590676)) |
| AUD-064 | Dynamic Download of modules | PAL: `fastrpc-adsp` | `scripts/mk-prd-job.py ovl <tag> <FP> <device> jobs fastrpc-adsp` | PAL: partial (1 pass / 1 fail cases, 2026-11-19, [590568](https://lava.infra.foundries.io/scheduler/job/590568) [590676](https://lava.infra.foundries.io/scheduler/job/590676)): signed PD on the ADSP runs; unsigned PD fails |
| AUD-065 | Speaker Protection Framework | none | - | GAP: no test; needs WSA VI feedback capture and the protection module in the graph (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-066 | Real time data and resource monitoring | none | - | GAP: no test written; PAL: not run: RT monitoring: needs QACT |
| AUD-067 | Centralized Protection System | none | - | GAP: no test; smart PA feature, requirement still open (not in the set evaluated with tests; first-pass feasibility only) |
| AUD-068 | EC Reference | none | - | GAP: no test written; PAL: not run: EC reference: needs an ECNS graph that produces data (see AUD-015) |
| AUD-069 | Multi-instance of generic stream playback | PAL: `alsa-concurrency-pal` | `scripts/mk-prd-job.py ovl <tag> <FP> <device> jobs alsa-concurrency-pal` | PAL: partial (6 pass / 1 fail cases, 2026-11-19, [590568](https://lava.infra.foundries.io/scheduler/job/590568) [590676](https://lava.infra.foundries.io/scheduler/job/590676)): both streams play and mix; with the Eliza ACDB stream A (ll) drops ~9 dB and B (db) ~3 dB while both play (590568, 590676, 591190, 591233; SM8750 ACDB: 0.1 dB, 590789, 591094); on the second EVK the attenuated 1600 Hz tone lost the spectral peak to the rack's ~1567 Hz interferer; intermittent: concurrency-both-a 1/2 |
| AUD-070 | Upstream Linux Audio Drivers | ASoC: `audio-drivers`<br>PAL: `audio-drivers` | `scripts/mk-prd-job.py base <tag> <FP> <device> jobs audio-drivers`<br>`scripts/mk-prd-job.py ovl <tag> <FP> <device> jobs audio-drivers` | ASoC: pass (4 pass / 0 fail cases, 2026-11-19, [589422](https://lava.infra.foundries.io/scheduler/job/589422) [589537](https://lava.infra.foundries.io/scheduler/job/589537))<br>PAL: partial (3 pass / 1 fail cases, 2026-11-19, [590568](https://lava.infra.foundries.io/scheduler/job/590568) [590676](https://lava.infra.foundries.io/scheduler/job/590676)): the AudioReach overlay loads the out-of-tree audioreach_driver (taint O); every other audio module is in tree |
| AUD-071 | Upstream and provided ongoing support for Linux-firmware and use firmware recipe from openembedded-core | none | - | GAP: no test; the topology is in linux-firmware, ADSP firmware upstreaming pending (not in the set evaluated with tests; first-pass feasibility only) |

Deprecated in the requirement list, not evaluated: AUD-072 (SVA: 1 user, 1 keyword), AUD-073 (SVA : Multi keyword, mult user), AUD-074 (SVA: Handset mic (users, keywords determined by which of the above features is enabled)), AUD-075 (SVA: Voice Activation during audio playback (users, keywords determined by which of the above features is enabled)), AUD-076 (SVA: Buffering after detection), AUD-077 (SVA: OpenDSP framework), AUD-078 (SVA: Echo Cancellation), AUD-079 (SVA: Headset Mic), AUD-080 (AllPlay), AUD-081 (Resampler (Sample Rate Converter)), AUD-082 (eNPU), AUD-083 (WSA884x), AUD-084 (WCD939x)
