import json
import os

from . import path_guesser as pg
import subprocess
from typing import Optional


def _is_usable_flash_descriptor(desc: dict, edk2_arch: str) -> bool:
    if "uefi" not in desc.get("interface-types", []):
        return False

    mapping = desc.get("mapping", {})
    if mapping.get("device") != "flash":
        return False
    if mapping.get("executable", {}).get("format") != "raw":
        return False

    if "requires-smm" in desc.get("features", []):
        return False

    targets = desc.get("targets", [])
    return any(t.get("architecture") == edk2_arch for t in targets)


def _find_flash_firmware(
    descriptor_dir: str, edk2_arch: str
) -> Optional[str]:
    try:
        names = sorted(os.listdir(descriptor_dir))
    except OSError:
        return None

    for name in names:
        if not name.endswith(".json"):
            continue

        try:
            with open(os.path.join(descriptor_dir, name)) as file:
                desc = json.load(file)
        except (OSError, ValueError):
            continue

        if not _is_usable_flash_descriptor(desc, edk2_arch):
            continue

        path = desc["mapping"]["executable"].get("filename")
        if path and pg.valid_path_or_none(path):
            return path

    return None


def get_path_to_qemu_uefi_firmware(arch: str) -> Optional[str]:
    edk2_arch = {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "x64": "x86_64",
        "aarch64": "aarch64",
        "arm64": "aarch64",
        "arm": "arm",
        "aarch32": "arm",
    }[arch.lower()]

    prefixes = [
        "/usr",
    ]

    try:
        bp = subprocess.run(["brew", "--prefix", "qemu"],
                            stdout=subprocess.PIPE,
                            universal_newlines=True)
        if bp.returncode == 0:
            prefixes.append(bp.stdout.strip())
    except FileNotFoundError:
        pass

    for prefix in prefixes:
        res = _find_flash_firmware(
            os.path.join(prefix, "share/qemu/firmware"), edk2_arch
        )
        if res is not None:
            return res

    return None


def guess_canonical_file_name_for_binary(path: str) -> str:
    out_name = os.path.basename(path)

    try:
        file_type = subprocess.check_output(["file", path], text=True).lower()
    except Exception:
        return out_name

    if "aarch64" in file_type:
        out_name = "BOOTAA64.EFI"
    elif "x86-64" in file_type:
        out_name = "BOOTX64.EFI"
    elif "Intel 80386" in file_type:
        out_name = "BOOTIA32.EFI"

    return out_name
