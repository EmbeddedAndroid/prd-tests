#!/bin/sh
# run.sh [bare] [product]: run a product map on the virtual board
# (boards/virtual.yaml); results land in vm-out/ (or vm-out-bare/). "bare"
# runs without loading the virtual drivers, to show the tests do not
# pass on hardware that is not there.
#
# Default: boot Ubuntu's generic kernel in QEMU with virtme-ng on the
# host root file system and a software TPM (swtpm) attached. PRD_VM=native
# (for an arm64 cloud VM or CI runner without KVM, where an emulated guest
# is too slow for udev; such a host is a QEMU/KVM guest itself) runs the
# suite on the host's own kernel instead, with the same drivers loaded and
# swtpm behind tpm_vtpm_proxy. Native mode stops PipeWire and WirePlumber
# and unbinds the framebuffer console for the run: use it on a throwaway
# VM, not a workstation. Needs tools/vm/setup-host.sh.
set -eu
top=$(cd "$(dirname "$0")/../.." && pwd)
mode=${1:-full}
product=${2:-products/generic.yaml}
out=$top/vm-out; [ "$mode" = bare ] && out=$top/vm-out-bare
rm -rf "$out"; mkdir -p "$out"
mode_vm=${PRD_VM:-qemu}
tpm=$(mktemp -d)
if [ "$mode_vm" = native ]; then
	# a desktop session's media daemons hold the cameras and the sound
	# card; seatd may have left its socket behind
	for p in wireplumber pipewire pipewire-pulse; do pkill -x "$p" 2>/dev/null || true; done
	pgrep -x seatd >/dev/null || rm -f /run/seatd.sock
	before=$(for m in vkms vimc vicodec vivid snd_aloop; do [ ! -d "/sys/module/$m" ] || echo "$m"; done)
	if [ "$mode" = bare ]; then
		for m in vkms vimc vicodec vivid snd_aloop; do
			if [ -d "/sys/module/$m" ]; then
				echo "run.sh: $m is still loaded, a bare run would not be bare" >&2
				exit 1
			fi
		done
	fi
	modprobe tpm_vtpm_proxy
	swtpm chardev --vtpm-proxy --tpm2 --tpmstate dir="$tpm" --pid file="$tpm/pid" --daemon > "$out/swtpm.log" 2>&1 || true
	sh "$top/tools/vm/inside.sh" "$mode" "$product" "$out" > "$out/console.log" 2>&1 || true
	if [ "$mode" = full ]; then
		# unload what this run loaded; the framebuffer console holds vkms
		# until it is unbound, and is bound again after
		unbound=
		for vt in /sys/class/vtconsole/vtcon*; do
			if grep -q "frame buffer" "$vt/name" 2>/dev/null && [ "$(cat "$vt/bind")" = 1 ]; then
				echo 0 > "$vt/bind" 2>/dev/null && unbound="$unbound $vt"
			fi
		done
		# the compositor may still be closing the card for a moment
		for m in vkms vimc vicodec vivid snd_aloop; do
			echo "$before" | grep -qx "$m" && continue
			i=0
			while [ -d "/sys/module/$m" ] && [ $i -lt 10 ]; do
				modprobe -r "$m" 2>/dev/null || sleep 1
				i=$((i + 1))
			done
		done
		for vt in $unbound; do echo 1 > "$vt/bind" 2>/dev/null || true; done
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
# QEMU's 9p server holds a host descriptor for every file open in the
# guest; at a 1024 soft limit guest programs fail with EMFILE
ulimit -n "$(ulimit -Hn)"
timeout 3600 vng --run "$k" --user root --rw --memory 3G --cpus 2 \
	--qemu-opts="-chardev socket,id=chrtpm,path=$tpm/sock -tpmdev emulator,id=tpm0,chardev=chrtpm -device $tdev,tpmdev=tpm0" \
	--exec "sh $top/tools/vm/inside.sh $mode $product $out" > "$out/console.log" 2>&1 || true
tail -5 "$out/console.log"
rm -rf "$tpm"
[ -n "${img:-}" ] && rm -f "$img"
test -s "$out/results.jsonl"
