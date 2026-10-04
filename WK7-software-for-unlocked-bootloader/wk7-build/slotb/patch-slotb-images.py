#!/usr/bin/env python3
"""patch-slotb-images.py: make the two small slot-B patches from YOUR speaker's own images (no LG files ship here).

  patch-slotb-images.py vbmeta_b.img boot_b_header.bin OUTDIR

- vbmeta_b.img (64 KiB, read from /dev/block/bootdevice/by-name/vbmeta_b): sets the AVB header flags to 1
  (hashtree disabled, byte 123), so slot B still mounts after files are added to system_b.
- boot_b_header.bin (first 2048 bytes of boot_b): appends " androidboot.selinux=permissive" to the kernel
  command line in the boot image header (slot B only), so the boot hook may start the chroot.
Writes OUTDIR/vbmeta_b_hashtree_disabled.img and OUTDIR/boot_b_header_permissive.bin. Writing them back to
the speaker is a separate, deliberate step (see INSTALL.md). Refuses anything that doesn't look as expected.
"""
import os
import sys

PERMISSIVE = b" androidboot.selinux=permissive"


def patch_vbmeta(data):
    if data[:4] != b"AVB0":
        sys.exit("vbmeta: no AVB0 magic")
    if data[123] not in (0, 1) or any(data[120:123]):
        sys.exit("vbmeta: unexpected flags %r" % data[120:124])
    out = bytearray(data)
    out[120:124] = (1).to_bytes(4, "big")
    return bytes(out)


def patch_boot_header(data):
    if data[:8] != b"ANDROID!" or len(data) < 2048:
        sys.exit("boot header: no ANDROID! magic or shorter than 2048 bytes")
    cmd = data[64:64 + 512].split(b"\0")[0]
    if PERMISSIVE.strip() in cmd:
        return data
    new = cmd + PERMISSIVE
    if len(new) >= 512:
        sys.exit("boot header: command line too long")
    out = bytearray(data)
    out[64:64 + 512] = new + b"\0" * (512 - len(new))
    return bytes(out)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    vb, bh, outdir = sys.argv[1:]
    os.makedirs(outdir, exist_ok=True)
    open(os.path.join(outdir, "vbmeta_b_hashtree_disabled.img"), "wb").write(patch_vbmeta(open(vb, "rb").read()))
    open(os.path.join(outdir, "boot_b_header_permissive.bin"), "wb").write(patch_boot_header(open(bh, "rb").read(2048)))
    print("written to", outdir)
