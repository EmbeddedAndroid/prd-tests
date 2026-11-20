#!/bin/sh
# setup-host.sh: install what tools/vm/run.sh needs on an Ubuntu 24.04
# host (arm64 or x86_64): QEMU and virtme-ng, Ubuntu's generic kernel with
# its extra modules (vkms, vimc, vicodec, vivid, snd-aloop) whatever
# kernel the host itself runs, swtpm, and the user space the tests call.
# The VM boots that kernel on the host's root file system, so everything
# is installed here.
set -eu
sudo apt-get update -qq
case "$(uname -m)" in
	aarch64) q=qemu-system-arm ;;
	*) q=qemu-system-x86 ;;
esac
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
	virtme-ng "$q" qemu-utils linux-image-generic \
	python3 python3-yaml swtpm swtpm-tools tpm2-tools \
	v4l-utils alsa-utils weston seatd mesa-utils libgl1-mesa-dri \
	gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
	gstreamer1.0-plugins-bad libcamera-tools libcamera-ipa gstreamer1.0-libcamera \
	kmod udev busybox-static binutils >/dev/null
k=$(ls /boot/vmlinuz-*-generic | sort -V | tail -1)
k=${k#/boot/vmlinuz-}
# and the extra modules of the running kernel, for the native mode
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
	"linux-modules-extra-$k" "linux-modules-extra-$(uname -r)" >/dev/null
# virtme-ng reads the kernel image, which Ubuntu installs root-only
sudo chmod a+r /boot/vmlinuz-* 2>/dev/null || true
