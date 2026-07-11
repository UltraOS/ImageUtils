#!/usr/bin/python3
"""
Builder for the TFTP root served to a loader over a (simulated) PXE boot.

PXE has no partitions or file system: the loader pulls every file straight
off a TFTP server, which is just a plain directory. This lays that directory
out with the boot image the firmware fetches plus whatever files (config,
kernels, modules) the boot config refers to, leaving the caller to decide
what those are.
"""
import os
import shutil
import tempfile
from typing import Dict


def build_tftp_root(boot_image: str, boot_name: str, config: str,
                    files: Dict[str, str]) -> str:
    """
    Create a TFTP root directory and return its path. `boot_image` is copied in
    as `boot_name` (the file the firmware fetches); `config` is written out as
    hyper.cfg; `files` maps an archive path within the root to the host file to
    place there.
    """
    root = tempfile.mkdtemp()

    for arc_name, host_path in files.items():
        dst = os.path.join(root, arc_name)
        os.makedirs(os.path.dirname(dst) or root, exist_ok=True)
        shutil.copy(host_path, dst)

    with open(os.path.join(root, "hyper.cfg"), "w") as f:
        f.write(config)

    shutil.copy(boot_image, os.path.join(root, boot_name))
    return root
