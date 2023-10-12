import os.path

from . import path_guesser as pg
import subprocess
from typing import Optional


def get_path_to_qemu_uefi_firmware(arch: str) -> Optional[str]:
    edk2_arch = "x86_64" if "amd64" else arch

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

    res = pg.valid_path_with_prefixes_or_none(
        prefixes,
        f"share/qemu/firmware/60-edk2-{edk2_arch}.json"
    )
    if res is None:
        return None

    import json
    with open(res) as file:
        path_json = json.load(file)

    guess = path_json.get("mapping", {}) \
                     .get("executable", {}) \
                     .get("filename", {})

    return pg.valid_path_or_none(guess) if guess else None


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
