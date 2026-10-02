from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from vaso.config import VasoConfig


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str
    required: bool = True

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class InsulaDoctorConfig:
    estate_root: Path
    line: str | None = None
    rootfs_lock: Path | None = None
    min_estate_free_gib: int = 100
    docker_root: Path = Path("/var/lib/docker")
    min_docker_free_gib: int = 100
    driver_library_dirs: tuple[Path, ...] = (
        Path("/usr/lib/x86_64-linux-gnu"),
        Path("/usr/lib64"),
        Path("/lib/x86_64-linux-gnu"),
    )
    device_globs: tuple[str, ...] = ("/dev/nvidia*",)
    require_driver: bool = True


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


def doctor_passed(checks: Iterable[DoctorCheck]) -> bool:
    return all(check.ok or not check.required for check in checks)


def _is_writable_dir(path: Path) -> tuple[bool, str]:
    if not path.is_dir():
        return False, f"{path} is missing; create it on the estate volume"
    probe = path / f".vaso-doctor-write-test-{os.getpid()}"
    try:
        probe.write_text("ok\n", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return False, f"{path} is not writable: {exc}"
    return True, str(path)


def _check_writable(path: Path, name: str) -> DoctorCheck:
    ok, detail = _is_writable_dir(path)
    return DoctorCheck(name, ok, detail)


def _disk_headroom(path: Path, gib: int, *, required: bool) -> DoctorCheck:
    if not path.exists():
        return DoctorCheck(
            "disk:docker_root_headroom" if not required else "disk:estate_headroom",
            False,
            f"{path} is missing; check host storage placement",
            required=required,
        )
    usage = shutil.disk_usage(path)
    free_gib = usage.free / (1024**3)
    ok = free_gib >= gib
    detail = f"{path} free={free_gib:.1f}GiB required={gib}GiB"
    if not ok and required:
        detail += "; move the estate or free disk before running the insula"
    elif not ok:
        detail += "; warning only for docker root"
    return DoctorCheck(
        "disk:docker_root_headroom" if not required else "disk:estate_headroom",
        ok,
        detail,
        required=required,
    )


def _find_driver_lib(name: str, dirs: tuple[Path, ...]) -> Path | None:
    for directory in dirs:
        candidate = directory / name
        if candidate.exists():
            return candidate
    return None


def _driver_checks(config: InsulaDoctorConfig) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    for soname in ("libcuda.so.1", "libnvidia-ml.so.1"):
        path = _find_driver_lib(soname, config.driver_library_dirs)
        checks.append(
            DoctorCheck(
                f"driver:{soname}",
                path is not None,
                str(path) if path else f"{soname} not found; install NVIDIA driver libraries or fix driver bind paths",
                required=config.require_driver,
            )
        )
    devices = sorted(
        device
        for pattern in config.device_globs
        for device in glob.glob(pattern)
        if Path(device).exists()
    )
    checks.append(
        DoctorCheck(
            "driver:devices",
            bool(devices),
            ",".join(devices) if devices else "/dev/nvidia* not found; expose NVIDIA device nodes or request a CPU-only run",
            required=config.require_driver,
        )
    )
    return checks


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rootfs_manifest_checks(config: InsulaDoctorConfig) -> list[DoctorCheck]:
    if not config.line:
        return []
    line_dir = config.estate_root / "rootfs-lines" / config.line
    rootfs = line_dir / "rootfs"
    manifest_path = line_dir / "rootfs-bundle.json"
    checks = [
        DoctorCheck(
            "rootfs:line_rootfs",
            (rootfs / "bin" / "bash").is_file() or (rootfs / "bin" / "sh").is_file(),
            f"{rootfs}/bin/bash or bin/sh; build the rootfs line before running the insula",
        ),
        DoctorCheck(
            "rootfs_manifest:exists",
            manifest_path.is_file(),
            f"{manifest_path}; rebuild the rootfs line if it is missing",
        ),
    ]
    if not manifest_path.is_file():
        return checks
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        checks.append(DoctorCheck("rootfs_manifest:json", False, f"{manifest_path} is invalid JSON: {exc}"))
        return checks
    checks.append(
        DoctorCheck(
            "rootfs_manifest:line",
            manifest.get("line") == config.line,
            f"manifest line={manifest.get('line')!r} expected={config.line!r}; rebuild the rootfs line",
        )
    )
    if config.rootfs_lock is not None:
        if not config.rootfs_lock.is_file():
            checks.append(
                DoctorCheck(
                    "rootfs_manifest:lock_sha256",
                    False,
                    f"{config.rootfs_lock} is missing; cannot verify rootfs manifest lock",
                )
            )
        else:
            expected = _sha256(config.rootfs_lock)
            actual = manifest.get("lock", {}).get("sha256") if isinstance(manifest.get("lock"), dict) else None
            checks.append(
                DoctorCheck(
                    "rootfs_manifest:lock_sha256",
                    actual == expected,
                    f"manifest lock sha256={actual!r} expected={expected}; rebuild the rootfs line",
                )
            )
    return checks


def _lease_checks(estate_root: Path) -> list[DoctorCheck]:
    lease_dir = estate_root / "agents" / "leases"
    checks = [_check_writable(lease_dir, "lease_dir:writable")]
    corrupt: list[str] = []
    if lease_dir.is_dir():
        for path in sorted(lease_dir.glob("*.json")):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                corrupt.append(str(path))
                continue
            if not isinstance(state.get("leases", []), list):
                corrupt.append(str(path))
    checks.append(
        DoctorCheck(
            "lease_dir:json",
            not corrupt,
            "lease JSON states are readable" if not corrupt else f"remove or repair corrupt lease state: {', '.join(corrupt)}",
        )
    )
    return checks


def run_insula_doctor(config: InsulaDoctorConfig) -> list[DoctorCheck]:
    estate = config.estate_root
    writable_rels = [
        "vaso",
        "vaso/cache/bazel/repository-cache",
        "vaso/tmp",
        "home/kvothe",
        "sources",
    ]
    if config.line:
        writable_rels.extend(
            [
                f"vaso/lines/{config.line}/cache/bazel/output-base",
                f"vaso/lines/{config.line}/cache/bazel/disk-cache",
                f"vaso/lines/{config.line}/state/native",
                f"vaso/lines/{config.line}/state/stamps",
            ]
        )
    checks = [_check_writable(estate / rel, f"writable:{rel}") for rel in writable_rels]
    checks.extend(_driver_checks(config))
    checks.append(_disk_headroom(estate, config.min_estate_free_gib, required=True))
    checks.append(_disk_headroom(config.docker_root, config.min_docker_free_gib, required=False))
    checks.extend(_rootfs_manifest_checks(config))
    checks.extend(_lease_checks(estate))
    return checks


def _parse_insula(args: argparse.Namespace) -> int:
    driver_dirs = (
        tuple(Path(value) for value in args.driver_lib_dir)
        if args.driver_lib_dir
        else InsulaDoctorConfig.driver_library_dirs
    )
    config = InsulaDoctorConfig(
        estate_root=Path(args.estate_root),
        line=args.line,
        rootfs_lock=Path(args.rootfs_lock) if args.rootfs_lock else None,
        min_estate_free_gib=args.min_estate_free_gib,
        docker_root=Path(args.docker_root),
        min_docker_free_gib=args.min_docker_free_gib,
        driver_library_dirs=driver_dirs,
        device_globs=tuple(args.device_glob),
        require_driver=args.require_driver,
    )
    checks = run_insula_doctor(config)
    if args.json:
        print(json.dumps({"checks": [check.to_json() for check in checks]}, indent=2, sort_keys=True))
    else:
        for check in checks:
            if check.ok:
                prefix = "ok"
            elif check.required:
                prefix = "FAIL"
            else:
                prefix = "WARN"
            print(f"{prefix}: {check.name}: {check.detail}")
    return 0 if doctor_passed(checks) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="vaso host and insula diagnostics")
    sub = parser.add_subparsers(dest="command", required=True)
    insula = sub.add_parser("insula")
    insula.add_argument("--estate-root", required=True)
    insula.add_argument("--line")
    insula.add_argument("--rootfs-lock")
    insula.add_argument("--min-estate-free-gib", type=int, default=100)
    insula.add_argument("--docker-root", default="/var/lib/docker")
    insula.add_argument("--min-docker-free-gib", type=int, default=100)
    insula.add_argument("--driver-lib-dir", action="append", default=[])
    insula.add_argument("--device-glob", action="append", default=["/dev/nvidia*"])
    driver = insula.add_mutually_exclusive_group()
    driver.add_argument("--require-driver", dest="require_driver", action="store_true", default=True)
    driver.add_argument("--no-require-driver", dest="require_driver", action="store_false")
    insula.add_argument("--json", action="store_true")
    insula.set_defaults(func=_parse_insula)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
