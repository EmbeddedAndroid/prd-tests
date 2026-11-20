#!/bin/sh
# inside.sh <full|bare> <product> <outdir>: run in the VM by run.sh. Load
# the virtual drivers (full), then the runner on boards/virtual.yaml.
mode=$1; product=$2; out=$3
top=$(cd "$(dirname "$0")/../.." && pwd)
cd "$top" || exit 1
mountpoint -q /sys/kernel/debug || mount -t debugfs none /sys/kernel/debug
if [ "$mode" = full ]; then
	modprobe vkms enable_writeback=1 enable_overlay=1 enable_cursor=1 || exit 1
	modprobe vimc || exit 1
	modprobe vicodec || exit 1
	modprobe vivid n_devs=1 node_types=0x1 || exit 1
	modprobe snd-aloop || exit 1
fi
udevadm trigger 2>/dev/null; udevadm settle 2>/dev/null
# a registry of this VM's own, not one a host user left behind
export GST_REGISTRY=/tmp/prd-gst-registry.bin
uname -a
ls /dev/dri /dev/media* /dev/video* /dev/snd /dev/tpm* 2>&1 | tr '\n' ' '; echo
python3 tools/run.py --board boards/virtual.yaml --product "$product" -o "$out"
echo "inside done rc $?"
