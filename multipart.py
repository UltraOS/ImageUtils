#!/usr/bin/python3
"""
Builder for multi-partition disk images used by the partition-addressing tests.

Unlike image_utils.ultra.DiskImage (which only ever creates a single
partition), this module lays out several partitions so the loader's
disk/partition addressing (hdN-partN, partuuid, diskuuid, EBR logicals, ...)
can be exercised end to end. The MBR/EBR partition tables are written by hand
for full control over partition types and indices; GPT images are created with
sgdisk so we can pin explicit disk/partition GUIDs.
"""
import os
import struct
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional

from . import generator as g

SECTOR_SIZE = 512
SECTORS_PER_MIB = (1024 * 1024) // SECTOR_SIZE

# The one and only partition alignment we use, in MiB. Keeping everything on a
# 1 MiB boundary means every file system starts at a whole-MiB offset, which
# also leaves a comfortable gap before the first partition for the BIOS stage2.
ALIGN_MIB = 1

MBR_EXTENDED = 0x05
MBR_FAT_LBA = 0x0C
GPT_ESP_TYPE = "EF00"

OFFSET_TO_MBR_PARTITION_LIST = 0x01BE
OFFSET_TO_MBR_MAGIC = 510
MBR_MAGIC = 0xAA55


class Partition:
    def __init__(
        self, files: Optional[Dict[str, str]] = None,
        size_mib: int = 3, fat32: bool = False,
        esp: bool = False, unique_guid: Optional[str] = None,
    ):
        # Mapping of "path/within/fs" -> host file path.
        self.files = files or {}
        self.size_mib = size_mib
        self.fat32 = fat32
        self.esp = esp
        # Only meaningful for GPT.
        self.unique_guid = unique_guid


def _stage_files(files: Dict[str, str]) -> str:
    staging = tempfile.mkdtemp()

    for arc_name, host_path in files.items():
        dst = os.path.join(staging, arc_name)
        os.makedirs(os.path.dirname(dst) or staging, exist_ok=True)
        shutil.copy(host_path, dst)

    return staging


def _make_fat(part: Partition) -> str:
    """Create a populated FAT file system image, return its temp path."""
    fs_img = tempfile.mkstemp()[1]
    g.file_resize_to_mib(fs_img, part.size_mib)
    g.make_fat(fs_img, part.size_mib, part.fat32)

    staging = _stage_files(part.files)
    try:
        g.fat_fill(fs_img, staging)
    finally:
        shutil.rmtree(staging)

    return fs_img


def _dd_embed_sectors(image: str, sector: int, fs_img: str) -> None:
    subprocess.check_call([
        "dd", f"if={fs_img}", f"of={image}", f"bs={SECTOR_SIZE}",
        f"seek={sector}", "conv=notrunc", "status=none"
    ])


def _pack_mbr_entry(part_type: int, first_block: int,
                    block_count: int) -> bytes:
    # status, CHS begin (ignored by hyper), type, CHS end, first LBA, LBA count
    return struct.pack("<B3sB3sII", 0x00, b"\x00\x00\x00", part_type,
                       b"\x00\x00\x00", first_block, block_count)


def _write_at(image: str, byte_off: int, data: bytes) -> None:
    with open(image, "r+b") as f:
        f.seek(byte_off)
        f.write(data)


def build_mbr_image(
    path: str, primaries: List[Partition],
    logicals: Optional[List[Partition]] = None,
    installer_path: Optional[str] = None,
) -> None:
    """
    Lay out `primaries` as MBR primary partitions (indices 0..N-1) followed by,
    if `logicals` is given, an extended partition holding them as an EBR chain
    (indices 4, 5, ...). If `installer_path` is set, the BIOS stage2 is
    embedded afterwards.
    """
    logicals = logicals or []
    assert len(primaries) <= (3 if logicals else 4)

    mbr_entries: List[bytes] = []
    fs_embeds = []  # (sector, fs_img)

    cursor_mib = ALIGN_MIB
    for part in primaries:
        start_sector = cursor_mib * SECTORS_PER_MIB
        count = part.size_mib * SECTORS_PER_MIB
        # Under UEFI, OVMF happily boots \EFI\BOOT from any FAT volume, so a
        # plain FAT-LBA type works for every MBR partition (the ESP flag only
        # matters for GPT).
        mbr_entries.append(_pack_mbr_entry(MBR_FAT_LBA, start_sector, count))
        fs_embeds.append((start_sector, _make_fat(part)))
        cursor_mib += part.size_mib

    ebr_writes = []  # (byte_off, data)
    if logicals:
        ext_start_mib = cursor_mib
        ext_start_sector = ext_start_mib * SECTORS_PER_MIB

        # Each logical consumes a 1 MiB gap (holding its EBR) plus its data.
        ext_span_mib = sum(ALIGN_MIB + p.size_mib for p in logicals)
        mbr_entries.append(_pack_mbr_entry(
            MBR_EXTENDED, ext_start_sector, ext_span_mib * SECTORS_PER_MIB))

        for i, part in enumerate(logicals):
            ebr_sector = cursor_mib * SECTORS_PER_MIB
            data_sector = (cursor_mib + ALIGN_MIB) * SECTORS_PER_MIB
            count = part.size_mib * SECTORS_PER_MIB

            # Entry 0: the logical partition itself, relative to this EBR.
            entry0 = _pack_mbr_entry(
                MBR_FAT_LBA, data_sector - ebr_sector, count)

            # Entry 1: link to the next EBR, relative to the extended
            # partition's start (the DOS convention, which coincides with what
            # hyper reads for the first hop).
            if i + 1 < len(logicals):
                next_ebr_mib = cursor_mib + ALIGN_MIB + part.size_mib
                next_ebr_sector = next_ebr_mib * SECTORS_PER_MIB
                remaining = sum(ALIGN_MIB + p.size_mib
                                for p in logicals[i + 1:]) * SECTORS_PER_MIB
                entry1 = _pack_mbr_entry(
                    MBR_EXTENDED, next_ebr_sector - ext_start_sector,
                    remaining)
            else:
                entry1 = _pack_mbr_entry(0x00, 0, 0)

            ebr = bytearray(SECTOR_SIZE)
            ebr[OFFSET_TO_MBR_PARTITION_LIST:
                OFFSET_TO_MBR_PARTITION_LIST + 32] = entry0 + entry1
            struct.pack_into("<H", ebr, OFFSET_TO_MBR_MAGIC, MBR_MAGIC)
            ebr_writes.append((ebr_sector * SECTOR_SIZE, bytes(ebr)))

            fs_embeds.append((data_sector, _make_fat(part)))
            cursor_mib += ALIGN_MIB + part.size_mib

    total_mib = cursor_mib + 1  # a spare MiB at the tail
    g.file_resize_to_mib(path, total_mib)

    # A minimal but valid MBR: zeroed boot code, our partition table, magic.
    while len(mbr_entries) < 4:
        mbr_entries.append(_pack_mbr_entry(0x00, 0, 0))
    _write_at(path, OFFSET_TO_MBR_PARTITION_LIST, b"".join(mbr_entries))
    _write_at(path, OFFSET_TO_MBR_MAGIC, struct.pack("<H", MBR_MAGIC))

    for byte_off, data in ebr_writes:
        _write_at(path, byte_off, data)

    for sector, fs_img in fs_embeds:
        _dd_embed_sectors(path, sector, fs_img)
        os.remove(fs_img)

    if installer_path is not None:
        subprocess.check_call([installer_path, path])


def _sgdisk_first_sector(image: str, part_num: int) -> int:
    out = subprocess.check_output(
        ["sgdisk", "-i", str(part_num), image]).decode("ascii")
    for line in out.splitlines():
        if line.startswith("First sector:"):
            # "First sector: 2048 (at 1024.0 KiB)"
            return int(line.split()[2])
    raise RuntimeError(f"couldn't find first sector for partition {part_num}")


def build_gpt_image(
    path: str, partitions: List[Partition], disk_guid: str,
) -> None:
    """Create a GPT image with pinned disk/partition GUIDs via sgdisk."""
    total_mib = ALIGN_MIB + sum(p.size_mib for p in partitions) + 1
    g.file_resize_to_mib(path, total_mib)

    sgdisk_args = ["sgdisk", "-o", "-U", disk_guid]
    for i, part in enumerate(partitions):
        num = i + 1
        sgdisk_args += ["-a", str(SECTORS_PER_MIB),
                        "-n", f"{num}:0:+{part.size_mib}M"]
        if part.esp:
            sgdisk_args += ["-t", f"{num}:{GPT_ESP_TYPE}"]
        if part.unique_guid:
            sgdisk_args += ["-u", f"{num}:{part.unique_guid}"]
    sgdisk_args.append(path)
    subprocess.check_call(sgdisk_args)

    for i, part in enumerate(partitions):
        sector = _sgdisk_first_sector(path, i + 1)
        fs_img = _make_fat(part)
        _dd_embed_sectors(path, sector, fs_img)
        os.remove(fs_img)
