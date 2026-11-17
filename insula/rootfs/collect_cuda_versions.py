#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


LINE = __import__("os").environ["VASO_CUDA_LINE"]
CUDA_HOME = Path("/usr/local/cuda")
LOCK = json.loads(Path("/build/cuda_ecosystem.lock.json").read_text(encoding="utf-8"))
COMPONENTS = LOCK["lines"][LINE]["components"]
DEFINE_RE = re.compile(r"^\s*#\s*define\s+([A-Za-z0-9_]+)\s+([0-9]+)")


def macros(*paths: Path) -> dict[str, int]:
    found: dict[str, int] = {}
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = DEFINE_RE.match(line)
            if match:
                found[match.group(1)] = int(match.group(2))
    return found


def command(argv: list[str]) -> str | None:
    try:
        return subprocess.check_output(argv, text=True, stderr=subprocess.STDOUT).strip()
    except Exception:
        return None


def dpkg_packages(prefixes: tuple[str, ...]) -> dict[str, str]:
    out = command(["dpkg-query", "-W", "-f=${binary:Package}=${Version}\n"]) or ""
    result: dict[str, str] = {}
    for line in out.splitlines():
        if "=" not in line:
            continue
        name, version = line.split("=", 1)
        if name.startswith(prefixes):
            result[name] = version
    return dict(sorted(result.items()))


def version(parts: list[int]) -> str | None:
    if any(part is None for part in parts):
        return None
    return ".".join(str(part) for part in parts)


def main() -> int:
    include = CUDA_HOME / "include"
    report: dict[str, object] = {
        "schema_version": 1,
        "line": LINE,
        "components": {},
    }
    cuda_macros = macros(include / "cuda.h")
    nvcc = command([str(CUDA_HOME / "bin/nvcc"), "--version"])
    nvcc_version = None
    if nvcc:
        match = re.search(r"release\s+[^,]+,\s+V([0-9.]+)", nvcc)
        if match:
            nvcc_version = match.group(1)
    report["components"]["cuda_toolkit"] = {
        "expected": COMPONENTS["cuda_toolkit"]["version"],
        "cuda_header": cuda_macros.get("CUDA_VERSION"),
        "nvcc": nvcc_version,
    }

    cudnn = macros(include / "cudnn_version.h")
    report["components"]["cudnn"] = {
        "expected": COMPONENTS["cudnn"]["version"],
        "header": version([cudnn.get("CUDNN_MAJOR"), cudnn.get("CUDNN_MINOR"), cudnn.get("CUDNN_PATCHLEVEL")]),
    }
    cusparselt = macros(include / "cusparseLt.h")
    report["components"]["cusparselt"] = {
        "expected": COMPONENTS["cusparselt"]["version"],
        "header_macros": {k: v for k, v in sorted(cusparselt.items()) if k.startswith("CUSPARSELT")},
    }
    cudss = macros(include / "cudss.h")
    report["components"]["cudss"] = {
        "expected": COMPONENTS["cudss"]["version"],
        "header_macros": {k: v for k, v in sorted(cudss.items()) if k.startswith("CUDSS")},
    }
    nvshmem = macros(include / "nvshmem_version.h", include / "nvshmem.h")
    report["components"]["nvshmem"] = {
        "expected": COMPONENTS["nvshmem"]["version"],
        "header_macros": {k: v for k, v in sorted(nvshmem.items()) if k.startswith("NVSHMEM")},
    }
    nccl = macros(include / "nccl.h")
    report["components"]["nccl"] = {
        "expected": COMPONENTS["nccl"]["version"],
        "header": version([nccl.get("NCCL_MAJOR"), nccl.get("NCCL_MINOR"), nccl.get("NCCL_PATCH")]),
    }
    trt = macros(Path("/usr/include/x86_64-linux-gnu/NvInferVersion.h"), Path("/usr/include/NvInferVersion.h"))
    report["components"]["tensorrt"] = {
        "expected": COMPONENTS["tensorrt"]["version"],
        "header": version([
            trt.get("NV_TENSORRT_MAJOR"),
            trt.get("NV_TENSORRT_MINOR"),
            trt.get("NV_TENSORRT_PATCH"),
            trt.get("NV_TENSORRT_BUILD"),
        ]),
        "packages": dpkg_packages(("libnvinfer", "libnvonnxparsers", "python3-libnvinfer", "tensorrt")),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
