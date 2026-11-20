#!/bin/sh
# run.sh [bare] [product]: boot Ubuntu's generic kernel in QEMU with virtme-ng on
# the host root file system and run a product map on the virtual board
# (boards/virtual.yaml) inside it; results land in vm-out/ (or
# vm-out-bare/). "bare" boots without loading the virtual drivers, to
# show the tests do not pass on hardware that is not there. A software
# TPM (swtpm) is attached to the VM. Needs tools/vm/setup-host.sh.
set -eu
top=$(cd "$(dirname "$0")/../.." && pwd)
mode=${1:-full}
product=${2:-products/generic.yaml}
out=$top/vm-out; [ "$mode" = bare ] && out=$top/vm-out-bare
rm -rf "$out"; mkdir -p "$out"
tpm=$(mktemp -d)
swtpm socket --tpm2 --tpmstate dir="$tpm" --ctrl type=unixio,path="$tpm/sock" --daemon
case "$(uname -m)" in
	aarch64) tdev=tpm-tis-device ;;
	*) tdev=tpm-tis ;;
esac
kvm=; mach=
if [ ! -w /dev/kvm ]; then
	kvm=--disable-kvm
	# without KVM there is no host GIC to mirror
	[ "$(uname -m)" = aarch64 ] && mach="-machine gic-version=max"
fi
k=$(ls /boot/vmlinuz-*-generic | sort -V | tail -1)
timeout 3600 vng --run "$k" --user root --rw --memory 3G --cpus 2 $kvm \
	--qemu-opts="$mach -chardev socket,id=chrtpm,path=$tpm/sock -tpmdev emulator,id=tpm0,chardev=chrtpm -device $tdev,tpmdev=tpm0" \
	--exec "sh $top/tools/vm/inside.sh $mode $product $out" > "$out/console.log" 2>&1 || true
tail -5 "$out/console.log"
rm -rf "$tpm"
test -s "$out/results.jsonl"
