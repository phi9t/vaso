from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass

from vaso.config import VasoConfig


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def run_doctor(config: VasoConfig) -> list[DoctorCheck]:
    rootfs = config.state_root / "rootfs"
    bwrap = shutil.which("bwrap")
    rootfs_shell = (rootfs / "bin" / "sh").is_file() or (rootfs / "bin" / "bash").is_file()
    return [
        DoctorCheck("bwrap_available", bwrap is not None, bwrap or "not found"),
        DoctorCheck("rootfs_exists", rootfs.is_dir(), str(rootfs)),
        DoctorCheck("rootfs_has_shell", rootfs_shell, f"{rootfs}/bin/sh or {rootfs}/bin/bash"),
        DoctorCheck(
            "host_home_writable",
            (config.state_root / "home" / "kvothe").is_dir(),
            str(config.state_root / "home" / "kvothe"),
        ),
    ]
