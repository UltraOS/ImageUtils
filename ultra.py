from . import generator as g
from typing import Optional
import tempfile
import subprocess
import os
import shutil


def _optimal_fs_size_mb(fs_type):
    if fs_type == "FAT12":
        return 3

    if fs_type == "FAT16":
        return 32

    if fs_type == "FAT32":
        return 64

    raise RuntimeError(f"Unknown filesystem type {fs_type}")


class Module:
    format_string = \
        """
module:
    name = "{name}"
    type = "{type}"
    path = "{path}"
    size = "{size}"
"""

    def __init__(
        self, name: str, is_file: bool,
        path: Optional[str] = None, size: Optional[int] = None
    ):
        self.name = name
        self.is_file = is_file
        self.path = path
        self.size = size

    def __str__(self) -> str:
        if self.name == "__KERNEL__":
            return "kernel-as-module = true"

        module_type = "file" if self.is_file else "memory"
        path = self.path or ""
        size = self.size or "auto"

        return Module.format_string.format(
            name=self.name, type=module_type, path=path, size=size
        )


class DiskImage:
    # always align partitions at 1 MiB
    part_align_mibs = 1

    def __init__(
        self, fs_root_dir: str, br_type: str,
        fs_type: str, fs_size_mb: Optional[int] = None,
        hyper_config: Optional[str] = None,
        hyper_uefi_binary_path: Optional[str] = None,
        hyper_iso_br_path: Optional[str] = None,
        hyper_installer_path: Optional[str] = None,
        out_path: Optional[str] = None,
    ):
        self.__fs_root_dir = fs_root_dir
        self.__br_type = br_type.upper()
        self.__fs_type = fs_type.upper()
        self.__path = out_path if out_path else tempfile.mkstemp()[1]

        if hyper_config is not None:
            with open(os.path.join(self.__path, "hyper.cfg"), "w") as f:
                f.write(hyper_config)

        is_iso = self.fs_type == "ISO9660"

        if not is_iso:
            if fs_size_mb is None:
                fs_size_mb = _optimal_fs_size_mb(self.fs_type)

            image_size = fs_size_mb + DiskImage.part_align_mibs

            # Make sure the backup header is intact
            if br_type == "GPT":
                image_size += 1

            g.file_resize_to_mib(self.__path, image_size)

            if self.__br_type == "MBR" or self.__br_type == "GPT":
                g.image_partition(self.__path, self.__br_type, self.__fs_type,
                                  DiskImage.part_align_mibs, fs_size_mb)

        uefi_root_path = None
        if hyper_uefi_binary_path is not None:
            uefi_root_path = tempfile.TemporaryDirectory()
            efi_boot_path = os.path.join(uefi_root_path.name, "EFI/BOOT")
            os.makedirs(efi_boot_path)
            shutil.copy(hyper_uefi_binary_path, efi_boot_path)

        g.make_fs(self.__path, self.__fs_type, DiskImage.part_align_mibs,
                  fs_size_mb, self.__fs_root_dir,
                  uefi_root_path.name if uefi_root_path else None,
                  hyper_iso_br_path)

        if uefi_root_path is not None:
            uefi_root_path.cleanup()

        should_install = False

        if hyper_installer_path is not None and self.__br_type != "GPT":
            should_install = True

        # Hybrid boot depends on having stage2 pointed to by el-torito
        if is_iso:
            should_install = should_install and hyper_iso_br_path is not None

        if should_install:
            assert hyper_installer_path
            subprocess.check_call([hyper_installer_path, self.__path])

    @property
    def br_type(self):
        return self.__br_type

    def is_cd(self):
        return self.__br_type == "CD"

    @property
    def fs_type(self):
        return self.__fs_type

    @property
    def path(self):
        return self.__path

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        os.remove(self.__path)
