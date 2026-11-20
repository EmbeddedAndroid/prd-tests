#!/bin/sh
# r1-run.sh <FP>: R1 on an Eliza CQS EVK with an IMX577 on CAM0
# (diagnostic UKI: board kernel and rootfs, camera DT, camera modules in
# ecam/ on the ESP). Installs the camera modules from the efi payload,
# loads them, and captures IMX577 frames through CSIPHY0 -> CSID0 -> VFE0 RDI0.
# Output: /root/ecam/<FP>/ (log, media graph, raw frames, PNGs).
FP=$1
O=/root/ecam/$FP
mkdir -p $O
exec > $O/r1.log 2>&1
echo "== FP"
fp=$(tr -d '\0' < /proc/device-tree/lab-fingerprint)
echo "dt fingerprint $fp, want $FP; kernel $(uname -r)"
[ "$fp" = "$FP" ] || { echo "FP MISMATCH: STOP"; exit 1; }

echo "== payload"
E=$(awk '$3 == "vfat" { print $2 }' /proc/mounts | while read m; do [ -f $m/ecam/mod.tgz ] && echo $m; done | head -1)
if [ -z "$E" ]; then
	mkdir -p /mnt/efi; mount -o ro /dev/disk/by-partlabel/efi /mnt/efi && E=/mnt/efi
fi
[ -f "$E/ecam/mod.tgz" ] || { echo "NO PAYLOAD: STOP"; exit 1; }
cat $E/ecam/SHA256SUMS
(cd $E/ecam && sha256sum -c SHA256SUMS) || { echo "PAYLOAD CHECKSUM: STOP"; exit 1; }
K=/lib/modules/$(uname -r)
tar xzf $E/ecam/mod.tgz -C $K
depmod -a
for m in imx412 qcom_camss phy_qcom_mipi_csi2 i2c_qcom_cci diag_cam_idprobe; do
	echo "$m: $(modinfo -F filename $m)"
done

# bid_ok <module>: the loaded module's build id is in its payload file
bid_ok() {
	python3 -I -c "import sys
n = open('/sys/module/%s/notes/.note.gnu.build-id' % sys.argv[1], 'rb').read()
ok = n in open(sys.argv[2], 'rb').read()
print(sys.argv[1], n[16:].hex(), 'match' if ok else 'MISMATCH')
sys.exit(0 if ok else 1)" $1 "$(modinfo -F filename $1)"
}
t0=0
fresh=1
for m in i2c_qcom_cci phy_qcom_mipi_csi2 qcom_camss imx412; do
	[ -d /sys/module/$m ] && bid_ok $m || fresh=0
done
if [ $fresh = 1 ]; then
	echo "== boot-loaded modules are this payload: no reload"
else
echo "== unload"
# camera modules autoloaded at boot come from the previous payload in
# updates/; wireplumber holds the video nodes and pins qcom_camss
for p in /proc/[0-9]*; do
	[ "$(cat $p/comm 2>/dev/null)" = wireplumber ] && kill ${p#/proc/} && echo "killed wireplumber ${p#/proc/}"
done
sleep 1
for m in qcom_camss imx412 phy_qcom_mipi_csi2 i2c_qcom_cci diag_cam_idprobe; do
	[ -d /sys/module/$m ] && { rmmod $m; echo "rmmod $m: $?"; }
done
for m in qcom_camss imx412 phy_qcom_mipi_csi2 i2c_qcom_cci; do
	[ -d /sys/module/$m ] && { echo "$m STILL LOADED: STOP"; exit 1; }
done

echo "== load"
t0=$(cut -d' ' -f1 /proc/uptime)
for m in i2c_qcom_cci phy_qcom_mipi_csi2 qcom_camss imx412; do
	modprobe $m; echo "modprobe $m: $?"
done
echo "== loaded module build ids vs payload files"
for m in i2c_qcom_cci phy_qcom_mipi_csi2 qcom_camss imx412; do
	bid_ok $m || { echo "BUILD ID MISMATCH: STOP"; exit 1; }
done
fi
sleep 3
dmesg | awk -v t="$t0" '{ s = $0; sub(/^\[ */, "", s); if (s + 0 > t + 0) print }' | tail -40
ls /sys/bus/i2c/drivers/imx412/ 2>&1
grep -E "csid|vfe|csiphy|cci" /proc/interrupts

MD=
for m in /dev/media*; do
	media-ctl -d $m -p 2>/dev/null | grep -q "Qualcomm Camera Subsystem" && MD=$m
done
[ -n "$MD" ] || { echo "NO CAMSS MEDIA DEVICE: STOP"; exit 1; }
media-ctl -d $MD -p > $O/media-graph.txt 2>&1
echo "camss $MD"
grep -E "^- entity|device node" $O/media-graph.txt
SENS=$(sed -n 's/^- entity [0-9]*: \(imx[0-9]* [0-9]*-001a\) .*/\1/p' $O/media-graph.txt | head -1)
[ -n "$SENS" ] || { echo "NO SENSOR ENTITY: STOP"; exit 0; }
echo "sensor entity: $SENS"
SSD=$(media-ctl -d $MD -e "$SENS")
v4l2-ctl -d $SSD -l
v4l2-ctl -d $SSD --list-ctrls-menus | sed -n '/test_pattern/,/^$/p' | head -12

W=4056 H=3040
mc() { echo "+ media-ctl $*"; media-ctl -d $MD "$@"; }
mc --reset
mc -V "\"$SENS\":0[fmt:SRGGB10_1X10/${W}x${H}]"
mc -V "\"msm_csiphy0\":0[fmt:SRGGB10_1X10/${W}x${H}]"
mc -V "\"msm_csid0\":0[fmt:SRGGB10_1X10/${W}x${H}]"
mc -V "\"msm_vfe0_rdi0\":0[fmt:SRGGB10_1X10/${W}x${H}]"
mc -l "\"msm_csiphy0\":1->\"msm_csid0\":0[1]"
mc -l '"msm_csid0":1->"msm_vfe0_rdi0":0[1]'
media-ctl -d $MD -p > $O/media-graph-linked.txt 2>&1
VD=$(media-ctl -d $MD -e msm_vfe0_video0)
echo "video node $VD"

# cap <name> <frames>: v4l2-ctl stream into $O/<name>.raw, killed after 30 s
cap() {
	rm -f $O/$1.raw
	irq0=$(grep -E "csid|vfe" /proc/interrupts)
	t0=$(cut -d' ' -f1 /proc/uptime)
	v4l2-ctl -d $VD --set-fmt-video=width=$W,height=$H,pixelformat=pRAA \
		--stream-mmap=4 --stream-count=$2 --stream-to=$O/$1.raw --verbose > $O/v4l2-$1.txt 2>&1 &
	p=$!
	( sleep 30; kill -9 $p 2>/dev/null && echo "TIMEOUT after 30 s: $1" ) &
	tk=$!
	wait $p; echo "v4l2-ctl $1 exit $?"
	kill $tk 2>/dev/null
	grep -E "bytesperline|Size Image|Width/Height|cap dqbuf" $O/v4l2-$1.txt | head -8
	echo "$(grep -c 'cap dqbuf' $O/v4l2-$1.txt) buffers dequeued"
	ls -l $O/$1.raw 2>&1
	echo "irq before:"; echo "$irq0"; echo "irq after:"; grep -E "csid|vfe" /proc/interrupts
	dmesg | awk -v t="$t0" '{ s = $0; sub(/^\[ */, "", s); if (s + 0 > t + 0) print }' | tail -15
}

echo "== C1: sensor colour bars (test_pattern=1)"
v4l2-ctl -d $SSD -c test_pattern=1
cap bars 8
v4l2-ctl -d $SSD -c test_pattern=0
echo "== C2: live, driver default exposure/gain"
v4l2-ctl -d $SSD -C exposure,analogue_gain
cap live 8
echo "== C3: live, long exposure + high gain"
v4l2-ctl -d $SSD -c exposure=3000,analogue_gain=900 2>&1
v4l2-ctl -d $SSD -C exposure,analogue_gain
cap bright 8

echo "== frames"
for n in bars live bright; do
	[ -s $O/$n.raw ] && python3 -I $E/ecam/r1-png.py $O/$n.raw $W $H $O/$n
done
ls -l $O
echo "== done"
