#!/bin/sh
# run.sh [bare] [product]: run a product map on the virtual board
# (boards/virtual.yaml); results land in vm-out/ (or vm-out-bare/). "bare"
# runs without loading the virtual drivers, to show the tests do not
# pass on hardware that is not there.
#
# With KVM: boot Ubuntu's generic kernel in QEMU with virtme-ng on the
# host root file system and a software TPM (swtpm) attached. Without KVM
# (an arm64 cloud VM or CI runner, itself a QEMU/KVM guest): emulating
# the guest is too slow for udev, so the suite runs on the host's own
# kernel instead, the same drivers loaded into it and swtpm behind
# tpm_vtpm_proxy. PRD_VM=qemu or PRD_VM=native forces one. Needs
# tools/vm/setup-host.sh; native mode loads modules on the host.
set -eu
top=$(cd "$(dirname "$0")/../.." && pwd)
mode=${1:-full}
product=${2:-products/generic.yaml}
out=$top/vm-out; [ "$mode" = bare ] && out=$top/vm-out-bare
rm -rf "$out"; mkdir -p "$out"
mode_vm=${PRD_VM:-}
[ -n "$mode_vm" ] || { [ -w /dev/kvm ] && mode_vm=qemu || mode_vm=native; }
tpm=$(mktemp -d)
if [ "$mode_vm" = native ]; then
	modprobe tpm_vtpm_proxy
	swtpm chardev --vtpm-proxy --tpm2 --tpmstate dir="$tpm" --pid file="$tpm/pid" --daemon > "$out/swtpm.log" 2>&1 || true
	sh "$top/tools/vm/inside.sh" "$mode" "$product" "$out" > "$out/console.log" 2>&1 || true
	if [ "$mode" = full ]; then
		# leave the host as it was for the bare run
		for m in vkms vimc vicodec vivid snd_aloop; do modprobe -r "$m" 2>/dev/null || true; done
	fi
	tail -5 "$out/console.log"
	[ -s "$tpm/pid" ] && kill "$(cat "$tpm/pid")" 2>/dev/null || true
	rm -rf "$tpm"
	test -s "$out/results.jsonl"
	exit $?
fi
swtpm socket --tpm2 --tpmstate dir="$tpm" --ctrl type=unixio,path="$tpm/sock" --daemon
case "$(uname -m)" in
	aarch64) tdev=tpm-tis-device ;;
	*) tdev=tpm-tis ;;
esac
k=$(ls /boot/vmlinuz-*-generic | sort -V | tail -1)
# arm64 ships the kernel gzip compressed; virtme-ng reads the version (and
# so finds the modules) only in an uncompressed image
if [ "$(head -c 2 "$k" | od -An -tx1 | tr -d ' ')" = 1f8b ]; then
	img=$(mktemp)
	zcat "$k" > "$img"
	k=$img
fi
timeout 3600 vng --run "$k" --user root --rw --memory 3G --cpus 2 \
	--qemu-opts="-chardev socket,id=chrtpm,path=$tpm/sock -tpmdev emulator,id=tpm0,chardev=chrtpm -device $tdev,tpmdev=tpm0" \
	--exec "sh $top/tools/vm/inside.sh $mode $product $out" > "$out/console.log" 2>&1 || true
tail -5 "$out/console.log"
rm -rf "$tpm"
[ -n "${img:-}" ] && rm -f "$img"
test -s "$out/results.jsonl"
