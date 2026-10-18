#!/usr/bin/env bash
# Materialize a digest-pinned CUDA ecosystem bwrap rootfs bundle for the vaso insula.
#
# Output:
#   $VASO_ESTATE_ROOT/rootfs-lines/<line>/rootfs/
#   $VASO_ESTATE_ROOT/rootfs-lines/<line>/rootfs-bundle.json
set -euo pipefail

EXP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK="${EXP_DIR}/rootfs/cuda_ecosystem.lock.json"
LLVM_PLAN="${EXP_DIR}/rootfs/llvm_rootfs_cache.py"
LINE=""
DEST=""
EXTRA_APT=(
  ca-certificates
  curl
  git
  gfortran
  python3
  python3-venv
  cmake
  ninja-build
  pkg-config
  bzip2
  xz-utils
  patchelf
  file
  unzip
  zip
)

usage() {
  cat >&2 <<'EOF'
usage: rootfs/build_rootfs.sh --line cu129|cu130 [--lock path] [--dest path]

Default output is $VASO_ESTATE_ROOT/rootfs-lines/<line>/.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --line)
      LINE="${2:-}"
      shift 2
      ;;
    --lock)
      LOCK="${2:-}"
      shift 2
      ;;
    --dest)
      DEST="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

case "$LINE" in
  cu129|cu130) ;;
  *)
    echo "--line must be cu129 or cu130" >&2
    usage
    exit 2
    ;;
esac

if [[ ! -f "$LOCK" ]]; then
  echo "lock not found: $LOCK" >&2
  exit 2
fi

if [[ -n "${VASO_ESTATE_ROOT:-}" ]]; then
  ESTATE_ROOT="$VASO_ESTATE_ROOT"
elif [[ -n "${VASO_AGENT_IO_ROOT:-}" && "$VASO_AGENT_IO_ROOT" == */agents/* ]]; then
  ESTATE_ROOT="${VASO_AGENT_IO_ROOT%%/agents/*}"
else
  PREFER_DIR="$(df -P "$EXP_DIR" | awk 'NR==2{print $6}')"
  ESTATE_ROOT="$(python3 "$EXP_DIR/tools/estate.py" --required-gib 1 --prefer "$PREFER_DIR")"
fi
ESTATE_ROOT="$(mkdir -p "$ESTATE_ROOT" && cd "$ESTATE_ROOT" && pwd -P)"
export VASO_ESTATE_ROOT="$ESTATE_ROOT"

if [[ -z "$DEST" ]]; then
  DEST="$ESTATE_ROOT/rootfs-lines/$LINE"
fi
DEST_PARENT="$(dirname "$DEST")"
mkdir -p "$DEST_PARENT"
DEST="$(mkdir -p "$DEST" && cd "$DEST" && pwd -P)"
ROOTFS="$DEST/rootfs"
MANIFEST="$DEST/rootfs-bundle.json"

if [[ "$ROOTFS" == "$ESTATE_ROOT/rootfs" || "$MANIFEST" == "$ESTATE_ROOT/rootfs-bundle.json" ]]; then
  echo "refusing to write the live estate rootfs or estate rootfs-bundle.json" >&2
  exit 2
fi

WORK_ROOT="${VASO_AGENT_IO_ROOT:-$ESTATE_ROOT/agents/rootfs-builder}"
mkdir -p "$WORK_ROOT/tmp" "$WORK_ROOT/rootfs-build-contexts"
export TMPDIR="${TMPDIR:-$WORK_ROOT/tmp}"
mkdir -p "$TMPDIR"

LOCK_SHA256="$(sha256sum "$LOCK" | awk '{print $1}')"
LOCK_SHA8="${LOCK_SHA256:0:8}"
BUILD_CTX="$WORK_ROOT/rootfs-build-contexts/${LINE}-${LOCK_SHA8}-$$"
if [[ -e "$BUILD_CTX" ]]; then
  echo "build context already exists: $BUILD_CTX" >&2
  exit 2
fi
mkdir -p "$BUILD_CTX"

CID=""
LLVM_CID=""
LLVM_BUILD_CTX=""
cleanup() {
  if [[ -n "$CID" ]]; then
    docker rm -f "$CID" >/dev/null 2>&1 || true
  fi
  if [[ -n "$LLVM_CID" ]]; then
    docker rm -f "$LLVM_CID" >/dev/null 2>&1 || true
  fi
  if [[ -n "$LLVM_BUILD_CTX" ]]; then
    rm -rf "$LLVM_BUILD_CTX"
  fi
  rm -rf "$BUILD_CTX"
}
trap cleanup EXIT

python3 - "$LOCK" "$LINE" > "$BUILD_CTX/lock.env" <<'PY'
import json
import shlex
import sys

lock_path, line_name = sys.argv[1:3]
with open(lock_path, encoding="utf-8") as fh:
    lock = json.load(fh)
line = lock["lines"][line_name]
base = line["base_image"]
toolkit = line["components"]["cuda_toolkit"]
values = {
    "BASE_REF": base["reference"],
    "BASE_TAG": base["tag"],
    "BASE_DIGEST": base["digest"],
    "CUDA_VERSION": toolkit["version"],
    "NVCC_VERSION": toolkit["verify"]["expect"]["nvcc"],
}
for key, value in values.items():
    print(f"{key}={shlex.quote(str(value))}")
PY
. "$BUILD_CTX/lock.env"

python3 "$LLVM_PLAN" --lock "$LOCK" --estate-root "$ESTATE_ROOT" --format env > "$BUILD_CTX/llvm.env"
. "$BUILD_CTX/llvm.env"

cp "$LOCK" "$BUILD_CTX/cuda_ecosystem.lock.json"

cat > "$BUILD_CTX/install_cuda_ecosystem.py" <<'PY'
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
    gencode = "-gencode=arch=compute_90,code=sm_90 -gencode=arch=compute_100,code=sm_100"
    jobs = str(os.cpu_count() or 1)
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
PY

cat > "$BUILD_CTX/collect_cuda_versions.py" <<'PY'
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
PY

download_checked_host() {
  local url="$1"
  local sha256="$2"
  local dest="$3"
  mkdir -p "$(dirname "$dest")"
  local actual=""
  if [[ -f "$dest" ]]; then
    actual="$(sha256sum "$dest" | awk '{print $1}')"
  fi
  if [[ "$actual" != "$sha256" ]]; then
    local partial="${dest}.download.$$"
    rm -f "$partial"
    curl -fsSL --retry 3 --retry-delay 2 -o "$partial" "$url"
    actual="$(sha256sum "$partial" | awk '{print $1}')"
    if [[ "$actual" != "$sha256" ]]; then
      rm -f "$partial"
      echo "$dest: sha256 mismatch: expected $sha256, got $actual" >&2
      exit 1
    fi
    mv "$partial" "$dest"
  fi
  actual="$(sha256sum "$dest" | awk '{print $1}')"
  if [[ "$actual" != "$sha256" ]]; then
    echo "$dest: sha256 mismatch: expected $sha256, got $actual" >&2
    exit 1
  fi
}

ensure_llvm_cache() {
  local marker="$LLVM_CACHE_DIR/.vaso-llvm-cache-complete"
  if [[ -f "$marker" && -x "$LLVM_CACHE_DIR/bin/clang" ]]; then
    echo "== llvm cache hit: $LLVM_CACHE_DIR =="
    return
  fi

  local downloads="$ESTATE_ROOT/rootfs-lines/_llvm/downloads"
  local tarball="$downloads/llvm-project-${LLVM_COMMIT}.tar.gz"
  local cache_tmp="${LLVM_CACHE_DIR}.build.$$"
  local jobs
  jobs="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"
  if [[ "$jobs" -gt "$LLVM_MAX_JOBS" ]]; then
    jobs="$LLVM_MAX_JOBS"
  fi

  echo "== llvm cache miss: building $LLVM_CACHE_KEY with $jobs jobs =="
  download_checked_host "$LLVM_URL" "$LLVM_SHA256" "$tarball"

  LLVM_BUILD_CTX="$WORK_ROOT/rootfs-build-contexts/llvm-${LLVM_CACHE_KEY}-$$"
  rm -rf "$LLVM_BUILD_CTX" "$cache_tmp"
  mkdir -p "$LLVM_BUILD_CTX/llvm-patches" "$cache_tmp"
  cp "$LOCK" "$LLVM_BUILD_CTX/cuda_ecosystem.lock.json"
  cp "$LLVM_PLAN" "$LLVM_BUILD_CTX/llvm_rootfs_cache.py"
  cp "$tarball" "$LLVM_BUILD_CTX/llvm-project.tar.gz"

  python3 - "$LOCK" > "$LLVM_BUILD_CTX/llvm-patches.tsv" <<'PY'
import json
import sys
from pathlib import Path

lock = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for index, patch in enumerate(lock["components"]["llvm"].get("patches", [])):
    print(
        "\t".join([
            f"{index:02d}",
            patch["name"],
            patch["url"],
            patch["sha256"],
            patch.get("strip", "-p1"),
        ])
    )
PY

  while IFS=$'\t' read -r index name url sha256 strip; do
    local patch_file="$downloads/$name"
    download_checked_host "$url" "$sha256" "$patch_file"
    cp "$patch_file" "$LLVM_BUILD_CTX/llvm-patches/${index}-${name}"
    printf '%s\t%s\n' "${index}-${name}" "$strip" >> "$LLVM_BUILD_CTX/llvm-patch-strip.tsv"
  done < "$LLVM_BUILD_CTX/llvm-patches.tsv"

  cat > "$LLVM_BUILD_CTX/Dockerfile" <<DOCKER
FROM ${LLVM_BUILD_BASE_REF}
DOCKER
  cat >> "$LLVM_BUILD_CTX/Dockerfile" <<'DOCKER'
ENV DEBIAN_FRONTEND=noninteractive
ENV TMPDIR=/build/tmp
ARG LLVM_SHA256
ARG LLVM_STRIP_PREFIX
ARG LLVM_MAX_JOBS
LABEL org.vaso.llvm.cache="true"
COPY cuda_ecosystem.lock.json /build/cuda_ecosystem.lock.json
COPY llvm_rootfs_cache.py /build/llvm_rootfs_cache.py
COPY llvm-project.tar.gz /build/llvm-project.tar.gz
COPY llvm-patches /build/llvm-patches
COPY llvm-patch-strip.tsv /build/llvm-patch-strip.tsv
RUN set -eux; \
    mkdir -p "$TMPDIR"; \
    apt-get update; \
    llvm_pkgs="$(dpkg-query -W -f='${binary:Package}\n' 'clang*' 'lld*' 'llvm*' 'libclang*' 'libllvm*' 'mlir*' 2>/dev/null || true)"; \
    if [ -n "$llvm_pkgs" ]; then apt-mark unhold $llvm_pkgs || true; apt-get purge -y --allow-change-held-packages $llvm_pkgs; fi; \
    apt-get install -y --no-install-recommends ca-certificates build-essential cmake ninja-build python3 patch xz-utils bzip2; \
    rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    echo "${LLVM_SHA256}  /build/llvm-project.tar.gz" | sha256sum -c -; \
    mkdir -p /build/src; \
    tar -xzf /build/llvm-project.tar.gz -C /build/src; \
    mv "/build/src/${LLVM_STRIP_PREFIX}" /build/src/llvm-project; \
    while IFS="$(printf '\t')" read -r patch_name strip; do patch -d /build/src/llvm-project "$strip" < "/build/llvm-patches/$patch_name"; done < /build/llvm-patch-strip.tsv; \
    python3 /build/llvm_rootfs_cache.py --lock /build/cuda_ecosystem.lock.json --estate-root /build --format cmake-args > /build/cmake.args; \
    cmake -S /build/src/llvm-project/llvm -B /build/llvm-build -G Ninja $(cat /build/cmake.args); \
    cmake --build /build/llvm-build --parallel "${LLVM_MAX_JOBS}"; \
    cmake --install /build/llvm-build; \
    /usr/lib/llvm-23/bin/clang --version; \
    test -x /usr/lib/llvm-23/bin/clang++; \
    test -x /usr/lib/llvm-23/bin/ld.lld; \
    test -x /usr/lib/llvm-23/bin/mlir-tblgen; \
    test -f /usr/lib/llvm-23/lib/cmake/llvm/LLVMConfig.cmake; \
    test -f /usr/lib/llvm-23/lib/cmake/mlir/MLIRConfig.cmake; \
    rm -rf /build/llvm-build /build/src /build/llvm-project.tar.gz /build/llvm-patches /build/tmp
DOCKER

  docker build \
    --build-arg "LLVM_SHA256=$LLVM_SHA256" \
    --build-arg "LLVM_STRIP_PREFIX=$LLVM_STRIP_PREFIX" \
    --build-arg "LLVM_MAX_JOBS=$jobs" \
    -t "$LLVM_DOCKER_IMAGE" \
    "$LLVM_BUILD_CTX" 1>&2

  LLVM_CID="$(docker create --name "vaso-llvm-${LLVM_CACHE_KEY}-extract-$$" "$LLVM_DOCKER_IMAGE")"
  docker cp "$LLVM_CID:$LLVM_INSTALL_PREFIX/." "$cache_tmp"
  docker rm -f "$LLVM_CID" >/dev/null
  LLVM_CID=""

  local clang_version_output
  clang_version_output="$(docker run --rm "$LLVM_DOCKER_IMAGE" "$LLVM_INSTALL_PREFIX/bin/clang" --version)"

  python3 - "$cache_tmp" "$LOCK" "$LLVM_CACHE_KEY" "$clang_version_output" <<'PY'
import json
import re
import sys
from pathlib import Path

cache, lock_path, cache_key, out = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
lock = json.loads(lock_path.read_text(encoding="utf-8"))
llvm = lock["components"]["llvm"]
expect = llvm["verify"]["expect"]
match = re.search(r"clang version\s+([^\s]+)", out)
if not match or match.group(1) != expect["clang_version"] or expect["commit"] not in out:
    raise SystemExit(f"clang version mismatch for {cache_key}: {out}")
for rel in llvm["build"]["install_artifacts"]:
    if not (cache / rel).exists():
        raise SystemExit(f"LLVM cache missing required artifact: {rel}")
(cache / ".vaso-llvm-build.json").write_text(
    json.dumps(
        {
            "schema_version": 1,
            "cache_key": cache_key,
            "commit": llvm["commit"],
            "version": llvm["version"],
            "source_sha256": llvm["source"]["sha256"],
            "patch_sha256s": {patch["name"]: patch["sha256"] for patch in llvm.get("patches", [])},
            "install_prefix": llvm["install_layout"]["prefix"],
            "clang_version_output": out.strip(),
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
(cache / ".vaso-llvm-cache-complete").write_text(cache_key + "\n", encoding="utf-8")
PY

  if [[ -e "$LLVM_CACHE_DIR" ]]; then
    mv "$LLVM_CACHE_DIR" "${LLVM_CACHE_DIR}.incomplete.$(date -u +%Y%m%dT%H%M%SZ)"
  fi
  mkdir -p "$(dirname "$LLVM_CACHE_DIR")"
  mv "$cache_tmp" "$LLVM_CACHE_DIR"
  rm -rf "$LLVM_BUILD_CTX"
  LLVM_BUILD_CTX=""
}

cat > "$BUILD_CTX/Dockerfile" <<DOCKER
FROM ${BASE_REF}
ENV DEBIAN_FRONTEND=noninteractive
ENV TMPDIR=/build/tmp
ARG VASO_CUDA_LINE
LABEL org.vaso.cuda.line="${LINE}" \\
      org.vaso.cuda.lock_sha256="${LOCK_SHA256}" \\
      org.vaso.llvm.cache_key="${LLVM_CACHE_KEY}" \\
      org.vaso.cuda.base_digest="${BASE_DIGEST}"
COPY cuda_ecosystem.lock.json /build/cuda_ecosystem.lock.json
COPY install_cuda_ecosystem.py /build/install_cuda_ecosystem.py
COPY collect_cuda_versions.py /build/collect_cuda_versions.py
RUN set -eux; \\
    mkdir -p "\$TMPDIR"; \\
    apt-get update; \\
    purge_pkgs="\$(dpkg-query -W -f='\${binary:Package}\\n' 'libnccl*' 'libcudnn*' 2>/dev/null || true)"; \\
    if [ -n "\$purge_pkgs" ]; then apt-mark unhold \$purge_pkgs || true; apt-get purge -y --allow-change-held-packages \$purge_pkgs; fi; \\
    llvm_pkgs="\$(dpkg-query -W -f='\${binary:Package}\\n' 'clang*' 'lld*' 'llvm*' 'libclang*' 'libllvm*' 'mlir*' 2>/dev/null || true)"; \\
    if [ -n "\$llvm_pkgs" ]; then apt-mark unhold \$llvm_pkgs || true; apt-get purge -y --allow-change-held-packages \$llvm_pkgs; fi; \\
    find /usr/local -path '/usr/local/cuda*/compat/libcuda.so*' -delete; \\
    find /usr/local -path '/usr/local/cuda*/compat/libnvidia-ml.so*' -delete; \\
    apt-get install -y --no-install-recommends ${EXTRA_APT[*]}; \\
    rm -rf /var/lib/apt/lists/*
RUN VASO_CUDA_LINE="\$VASO_CUDA_LINE" python3 /build/install_cuda_ecosystem.py
RUN VASO_CUDA_LINE="\$VASO_CUDA_LINE" python3 /build/collect_cuda_versions.py > /usr/local/cuda/vaso-cuda-versions.json
RUN rm -rf /build
DOCKER

DERIVED="vaso-rootfs-${LINE}:${LOCK_SHA8}"

echo "== 1. pull digest-pinned base image =="
docker pull "$BASE_REF" 1>&2

echo "== 2. verify Ubuntu >= 24.04 =="
UBUNTU_VER="$(docker run --rm --entrypoint bash "$BASE_REF" -lc '. /etc/os-release; echo $VERSION_ID' 2>/dev/null | tr -d '"')"
echo "line=$LINE image=$BASE_REF ubuntu=$UBUNTU_VER"
python3 - "$UBUNTU_VER" <<'PY'
import sys
ver = sys.argv[1].strip()
major = int(ver.split(".")[0]) if ver else 0
if major < 24:
    sys.exit(f"rootfs image must be Ubuntu >= 24.04, got {ver!r}")
PY

echo "== 3a. build or reuse shared LLVM cache =="
ensure_llvm_cache

echo "== 3. install locked CUDA ecosystem into derived image =="
docker build \
  --build-arg "VASO_CUDA_LINE=$LINE" \
  -t "$DERIVED" \
  "$BUILD_CTX" 1>&2

echo "== 4. export the derived image filesystem into a plain rootfs tree =="
rm -rf "$ROOTFS"
mkdir -p "$ROOTFS"
CID="$(docker create --name "vaso-rootfs-${LINE}-${LOCK_SHA8}-extract-$$" "$DERIVED")"
docker export "$CID" | tar -x -C "$ROOTFS"
docker rm -f "$CID" >/dev/null
CID=""

echo "== 4a. copy shared LLVM cache into the rootfs =="
find "$ROOTFS/usr/lib" "$ROOTFS/usr/lib64" "$ROOTFS/lib" "$ROOTFS/lib64" \( -name 'libLLVM*' -o -name 'libMLIR*' \) \( -type f -o -type l \) -delete 2>/dev/null || true
find "$ROOTFS/usr/lib" -maxdepth 1 -name 'llvm-*' -exec rm -rf {} + 2>/dev/null || true
find "$ROOTFS/usr/bin" -maxdepth 1 \( -name 'clang*' -o -name 'ld.lld*' -o -name 'lld*' \) \( -type f -o -type l \) -delete 2>/dev/null || true
LLVM_ROOTFS_PREFIX="$ROOTFS${LLVM_INSTALL_PREFIX}"
rm -rf "$LLVM_ROOTFS_PREFIX"
mkdir -p "$LLVM_ROOTFS_PREFIX"
cp -a "$LLVM_CACHE_DIR/." "$LLVM_ROOTFS_PREFIX/"

if [[ -s /etc/resolv.conf ]]; then
  cp /etc/resolv.conf "$ROOTFS/etc/resolv.conf"
fi

RUN_UID="$(id -u)"
RUN_GID="$(id -g)"
RUN_USER="$(id -un)"
RUN_GROUP="$(id -gn)"
if ! awk -F: -v gid="$RUN_GID" '$3 == gid { found = 1 } END { exit !found }' "$ROOTFS/etc/group"; then
  printf '%s:x:%s:\n' "$RUN_GROUP" "$RUN_GID" >> "$ROOTFS/etc/group"
fi
if ! awk -F: -v uid="$RUN_UID" '$3 == uid { found = 1 } END { exit !found }' "$ROOTFS/etc/passwd"; then
  printf '%s:x:%s:%s:%s:/home/kvothe:/bin/bash\n' \
    "$RUN_USER" "$RUN_UID" "$RUN_GID" "$RUN_USER" >> "$ROOTFS/etc/passwd"
fi

for d in home/kvothe workspace vaso vaso/state vaso/cache vaso/runs vaso/traces \
         vaso/tmp opt/vaso run/vaso run/nvidia-driver; do
  mkdir -p "$ROOTFS/$d"
done

echo "== 5. record provenance manifest =="
BASE_IMAGE_ID="$(docker image inspect "$BASE_REF" --format '{{.Id}}')"
DERIVED_IMAGE_ID="$(docker image inspect "$DERIVED" --format '{{.Id}}')"
python3 - "$MANIFEST" "$LOCK" "$LOCK_SHA256" "$LINE" "$BASE_REF" "$BASE_DIGEST" \
  "$BASE_IMAGE_ID" "$DERIVED" "$DERIVED_IMAGE_ID" "$UBUNTU_VER" "$ROOTFS" \
  "$LLVM_CACHE_KEY" "${EXTRA_APT[@]}" <<'PY'
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

(
    out,
    lock_path,
    lock_sha256,
    line,
    base_ref,
    base_digest,
    base_image_id,
    derived,
    derived_image_id,
    ubuntu,
    rootfs,
    llvm_cache_key,
) = sys.argv[1:13]
extra = sys.argv[13:]
lock = json.loads(Path(lock_path).read_text(encoding="utf-8"))


def resolve_rootfs_path(rootfs_dir: Path, path: str) -> Path:
    rootfs_dir = rootfs_dir.resolve()
    parts = list(Path(path).parts[1:])
    current = rootfs_dir
    seen = 0
    while parts:
        part = parts.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if current != rootfs_dir:
                current = current.parent
            continue
        current = current / part
        while current.is_symlink():
            seen += 1
            if seen > 64:
                raise RuntimeError(f"too many symlinks while resolving {path}")
            target = Path(os.readlink(current))
            if target.is_absolute():
                current = rootfs_dir
                parts = list(target.parts[1:]) + parts
            else:
                current = current.parent
                parts = list(target.parts) + parts
    return current


versions_path = resolve_rootfs_path(Path(rootfs), "/usr/local/cuda/vaso-cuda-versions.json")
versions = json.loads(versions_path.read_text(encoding="utf-8")) if versions_path.exists() else {}
rootfs_dir = Path(rootfs).resolve()


def run_in_rootfs(argv):
    bwrap = shutil.which("bwrap")
    if bwrap:
        cmd = [
            bwrap,
            "--die-with-parent",
            "--unshare-user",
            "--uid",
            str(os.getuid()),
            "--gid",
            str(os.getgid()),
            "--clearenv",
            "--ro-bind",
            str(rootfs_dir),
            "/",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/vaso/tmp",
            "--setenv",
            "TMPDIR",
            "/vaso/tmp",
            "--setenv",
            "PATH",
            "/usr/lib/llvm-23/bin:/usr/bin:/bin",
            "--",
            *argv,
        ]
    else:
        binary = resolve_rootfs_path(rootfs_dir, argv[0])
        cmd = [str(binary), *argv[1:]]
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()
    except Exception:
        return None


llvm = lock.get("components", {}).get("llvm", {})
if isinstance(llvm, dict):
    expect = llvm.get("verify", {}).get("expect", {})
    prefix = expect.get("prefix", llvm.get("install_layout", {}).get("prefix", "/usr/lib/llvm-23"))
    clang_out = run_in_rootfs([str(Path(prefix) / "bin/clang"), "--version"])
    actual = None
    if clang_out:
        match = re.search(r"clang version\s+([^\s]+)", clang_out)
        if match:
            actual = match.group(1)
    versions.setdefault("components", {})["llvm"] = {
        "expected": llvm.get("version"),
        "actual": actual,
        "commit": llvm.get("commit") if clang_out and str(llvm.get("commit")) in clang_out else None,
        "prefix": prefix,
        "cache_key": llvm_cache_key,
        "version_output": clang_out,
    }
    versions_path.write_text(json.dumps(versions, indent=2, sort_keys=True) + "\n", encoding="utf-8")
manifest = {
    "schema_version": 2,
    "line": line,
    "base_image": base_ref,
    "base_image_digest": base_digest,
    "base_image_id": base_image_id,
    "derived_image": derived,
    "derived_image_id": derived_image_id,
    "ubuntu_version": ubuntu,
    "lock": {
        "path": str(Path(lock_path).resolve()),
        "sha256": lock_sha256,
        "schema_version": lock["schema_version"],
    },
    "component_versions": {
        name: component["version"]
        for name, component in sorted(lock["lines"][line]["components"].items())
    } | {
        name: component["version"]
        for name, component in sorted(lock.get("components", {}).items())
    },
    "verified_versions": versions,
    "extra_apt_packages": extra,
    "sandbox_leaf_targets": [
        "/home/kvothe", "/workspace", "/vaso", "/vaso/state", "/vaso/cache",
        "/vaso/runs", "/vaso/traces", "/vaso/tmp", "/opt/vaso",
        "/run/vaso", "/run/nvidia-driver",
    ],
}
with open(out, "w", encoding="utf-8") as fh:
    json.dump(manifest, fh, indent=2, sort_keys=True)
    fh.write("\n")
print(f"wrote {out}")
PY

echo "== done: rootfs at $ROOTFS =="
