import subprocess
import platform
import os
import shutil
import tempfile
from typing import Optional


def _dd_mebibyte_postfix() -> str:
    if platform.system() == "Darwin":
        return "m"

    return "M"


def file_resize_to_mib(path: str, mib: int) -> None:
    subprocess.check_call(["dd", "if=/dev/zero", f"of={path}",
                           f"bs=1{_dd_mebibyte_postfix()}", f"count={mib}"])


def _linux_image_partition(
    path: str, br_type: str, fs_type: str, align_mib: int, part_len_mib: int
) -> None:
    label = "gpt" if br_type == "GPT" else "msdos"
    subprocess.check_call(["parted", "-s", path, "mklabel", label])

    part_type = "primary" if br_type == "MBR" else "test-partition"

    fs_type = fs_type.lower()
    if fs_type == "fat12":
        fs_type = "fat16"  # parted doesn't support fat12 labels

    subprocess.check_call(["parted", "-s", path,
                           "mkpart", part_type,
                           fs_type, f"{align_mib}M",
                           f"{align_mib + part_len_mib}M"])


def _darwin_image_partition_gpt(
    path: str, _: str, align_mib: int, part_len_mib: int
) -> None:
    part_begin = (align_mib * 1024 * 1024) // 512
    part_end = part_begin + ((part_len_mib * 1024 * 1024) // 512)

    gdisk_fmt = "n\n"  # New partition
    gdisk_fmt += "1\n"  # At index 1
    gdisk_fmt += f"{part_begin}\n"  # Starts here
    gdisk_fmt += f"{part_end}\n"  # Ends here
    gdisk_fmt += "\n"  # With default GUID (some Apple thing)
    gdisk_fmt += "w\n"  # Write the new changes
    gdisk_fmt += "y\n"  # Yes, overwrite everything

    gdp = subprocess.Popen(["gdisk", path], stdin=subprocess.PIPE)

    assert gdp.stdin
    gdp.stdin.write(gdisk_fmt.encode("ascii"))
    gdp.stdin.close()

    gdp.wait(5)
    if gdp.returncode != 0:
        raise RuntimeError("gdisk exited with error")


# Darwin doesn't have 'parted', instead it ships with
# a weird version of 'fdisk'
def _darwin_image_partition_mbr(
    path: str, fs_type: str, align_mib: int, part_len_mib: int
) -> None:
    fs_type_to_id = {
        "FAT12": 0x01,
        "FAT16": 0x04,
        "FAT32": 0x0C
    }

    part_begin = (align_mib * 1024 * 1024) // 512
    part_len_mib = (part_len_mib * 1024 * 1024) // 512
    fs_id = "{:02X}".format(fs_type_to_id[fs_type])

    fdisk_fmt = f"{part_begin},{part_len_mib},{fs_id}\n"

    fdp = subprocess.Popen(["fdisk", "-yr", path], stdin=subprocess.PIPE)

    assert fdp.stdin
    fdp.stdin.write(fdisk_fmt.encode("ascii"))
    fdp.stdin.close()

    fdp.wait(5)
    if fdp.returncode != 0:
        raise RuntimeError("fdisk exited with error")


def _darwin_image_partition(
    path: str, br_type: str, fs_type: str, align_mib: int, part_len_mib: int
) -> None:
    if br_type == "MBR":
        _darwin_image_partition_mbr(path, fs_type, align_mib, part_len_mib)
    else:
        _darwin_image_partition_gpt(path, fs_type, align_mib, part_len_mib)


SYSTEM_TO_IMAGE_PARTITION = {
    "Linux": _linux_image_partition,
    "Darwin": _darwin_image_partition,
}


def image_partition(
    path: str, br_type: str, fs_type: str, align_mib: int, part_len_mib: int
) -> None:
    part_fn = SYSTEM_TO_IMAGE_PARTITION[platform.system()]
    part_fn(path, br_type, fs_type, align_mib, part_len_mib)


def fat_recursive_copy(raw_fs_path: str, file_path: str) -> None:
    subprocess.check_call([
        "mcopy", "-Q", "-i", raw_fs_path, "-s", file_path, "::"
    ])


def fat_fill(raw_fs_path: str, root_dir_path: str) -> None:
    for f in os.listdir(root_dir_path):
        full_path = os.path.abspath(os.path.join(root_dir_path, f))
        fat_recursive_copy(raw_fs_path, full_path)


def make_fat(raw_fs_path: str, _: int, force_fat32: bool) -> None:
    cr_args = ["mformat", "-i", raw_fs_path]
    if force_fat32:
        cr_args.append("-F")

    subprocess.check_call(cr_args)


def make_iso(
    image_path: str, root_path: str, uefi_root_path: Optional[str] = None,
    iso_br_path: Optional[str] = None
) -> None:
    # Make the disk itself
    xorriso_args = ["xorriso", "-as", "mkisofs"]

    if iso_br_path is not None:
        br_file = "boot_record"
        br_path = os.path.join(root_path, br_file)
        shutil.copy(iso_br_path, br_path)

        xorriso_args.extend([
            "-b", br_file, "-no-emul-boot", "-boot-load-size", "4",
            "-boot-info-table"
        ])

    if uefi_root_path is not None:
        # Make the EFI ESP partition
        fat_image = os.path.join(root_path, "efi_esp")
        file_resize_to_mib(fat_image, 1)
        make_fat(fat_image, 1, False)
        fat_fill(fat_image, uefi_root_path)

        xorriso_args.extend([
            "--efi-boot", "efi_esp", "-efi-boot-part", "--efi-boot-image"
        ])

    xorriso_args.extend([
        "--protective-msdos-label", root_path, "-o", image_path
    ])
    subprocess.check_call(xorriso_args)


def image_embed(image_path: str, mib_offset: int, fs_image_path: str) -> None:
    subprocess.check_call([
        "dd", f"if={fs_image_path}", f"seek={mib_offset}",
        f"bs=1{_dd_mebibyte_postfix()}", f"of={image_path}",
        "conv=notrunc"
    ])


def make_fs(
    image_path: str, fs_type: str, image_mib_offset: int,
    size: Optional[int], root_path: str,
    uefi_root_path: Optional[str] = None,
    iso_br_path: Optional[str] = None
) -> None:
    if fs_type == "ISO9660":
        return make_iso(image_path, root_path, uefi_root_path, iso_br_path)

    with tempfile.NamedTemporaryFile() as tf:
        assert size
        file_resize_to_mib(tf.name, size)

        if fs_type.startswith("FAT"):
            make_fat(tf.name, size, fs_type == "FAT32")
            fat_fill(tf.name, root_path)
            if uefi_root_path:
                fat_fill(tf.name, uefi_root_path)
        else:
            raise RuntimeError(f"Unknown filesystem type {fs_type}")

        image_embed(image_path, image_mib_offset, tf.name)
