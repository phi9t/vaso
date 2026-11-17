#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path


LINE = os.environ["VASO_CUDA_LINE"]
CUDA_HOME = Path("/usr/local/cuda")
LOCK = json.loads(Path("/build/cuda_ecosystem.lock.json").read_text(encoding="utf-8"))
COMPONENTS = LOCK["lines"][LINE]["components"]
WORK = Path("/build/work")
DOWNLOADS = Path("/build/downloads")


def run(argv: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    print("+ " + " ".join(argv), flush=True)
    subprocess.run(argv, cwd=cwd, env=env, check=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download_checked(url: str, sha256: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        run(["curl", "-fsSL", "--retry", "3", "--retry-delay", "2", "-o", str(dest), url])
    actual = sha256_file(dest)
    if actual != sha256:
        raise SystemExit(f"{dest.name}: sha256 mismatch: expected {sha256}, got {actual}")
    return dest


def copy_contents(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    run(["cp", "-a", f"{src}/.", str(dst)])


def write_deterministic_nvcc_wrapper(checkout: Path) -> Path:
    wrapper = WORK / "vaso-nvcc"
    wrapper.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
real_nvcc="${REAL_NVCC:-/usr/local/cuda/bin/nvcc}"
needs_seed=0
for arg in "$@"; do
  case "$arg" in
    -c|-dc|-dw|-dlink)
      needs_seed=1
      ;;
  esac
done
if [ "$needs_seed" = 0 ]; then
  exec "$real_nvcc" "$@"
fi
out=""
src=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "-o" ]; then
    out="$arg"
    prev=""
    continue
  fi
  case "$arg" in
    -o)
      prev="-o"
      ;;
    *.cu)
      src="$arg"
      ;;
  esac
done
seed_input="${out:-${src:-nvcc}}"
seed="$(python3 - "$seed_input" "${NCCL_ROOT_FOR_SEED:-/build/work/nccl}" <<'NVCC_SEED_PY'
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

value = sys.argv[1]
try:
    path = Path(value).resolve()
    for root_text in sys.argv[2:]:
        try:
            value = path.relative_to(Path(root_text).resolve()).as_posix()
            break
        except ValueError:
            pass
except Exception:
    pass
print("vaso-nccl-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16])
NVCC_SEED_PY
)"
exec "$real_nvcc" --objdir-as-tempdir "-frandom-seed=$seed" "$@"
""",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper


def install_redist(component_name: str) -> None:
    component = COMPONENTS[component_name]
    source = component["source"]
    archive = download_checked(source["url"], source["sha256"], DOWNLOADS / Path(source["url"]).name)
    extract_dir = WORK / f"extract-{component_name}"
    shutil.rmtree(extract_dir, ignore_errors=True)
    extract_dir.mkdir(parents=True)
    run(["tar", "-xJf", str(archive), "-C", str(extract_dir)])
    children = [p for p in extract_dir.iterdir() if p.is_dir()]
    if len(children) != 1:
        raise SystemExit(f"{component_name}: expected one archive root, found {[p.name for p in children]}")
    root = children[0]
    if (root / "include").is_dir():
        copy_contents(root / "include", CUDA_HOME / "include")
    for lib_name in ("lib", "lib64"):
        lib = root / lib_name
        if lib.is_dir():
            copy_contents(lib, CUDA_HOME / "lib64")


def install_nccl() -> None:
    component = COMPONENTS["nccl"]
    source = component["source"]
    checkout = WORK / "nccl"
    shutil.rmtree(checkout, ignore_errors=True)
    run(["git", "clone", "--branch", source["tag"], "--depth", "1", source["url"], str(checkout)])
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
    if actual != source["commit"]:
        raise SystemExit(f"nccl commit mismatch: expected {source['commit']}, got {actual}")
    env = os.environ.copy()
    env["CUDA_HOME"] = str(CUDA_HOME)
    env["REAL_NVCC"] = str(CUDA_HOME / "bin/nvcc")
    env["NCCL_ROOT_FOR_SEED"] = str(checkout)
    env["NVCC"] = str(write_deterministic_nvcc_wrapper(checkout))
    gencode = "-gencode=arch=compute_90,code=sm_90 -gencode=arch=compute_100,code=sm_100"
    jobs = os.environ.get("VASO_BUILD_JOBS", str(os.cpu_count() or 1))
    if not jobs.isdecimal() or int(jobs) < 1:
        raise SystemExit("VASO_BUILD_JOBS must be a positive integer")
    run(["make", f"-j{jobs}", "src.build", f"NVCC_GENCODE={gencode}"], cwd=checkout, env=env)
    copy_contents(checkout / "build/include", CUDA_HOME / "include")
    copy_contents(checkout / "build/lib", CUDA_HOME / "lib64")


def install_tensorrt() -> None:
    source = COMPONENTS["tensorrt"]["source"]
    debs: list[Path] = []
    holds: list[str] = []
    for package in source["packages"]:
        url = package["url"]
        deb = download_checked(url, package["sha256"], DOWNLOADS / Path(url).name)
        debs.append(deb)
        holds.append(package["package"].split("=", 1)[0])
    run(["apt-get", "install", "-y", "--no-install-recommends", *[str(p) for p in debs]])
    run(["apt-mark", "hold", *holds])


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    for component in ("cudnn", "cusparselt", "cudss", "nvshmem"):
        install_redist(component)
    install_nccl()
    install_tensorrt()
    run(["ldconfig"])
    shutil.rmtree(DOWNLOADS, ignore_errors=True)
    shutil.rmtree(WORK, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
