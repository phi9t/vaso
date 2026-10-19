#!/usr/bin/env bash
set -euo pipefail

find_runfile() {
  local pattern="$1"
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -path "$pattern" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

if [[ "${VASO_IN_INSULA:-0}" != "1" ]]; then
  echo "run me inside the insula: run.sh --insula-cmd bazel test //synthetic:nccl_two_rank_native" >&2
  exit 2
fi

PREFIX_PATH="$(find_runfile '*/+nccl_native+nccl_native/prefix_path.txt')"
MANIFEST_JSON="$(find_runfile '*/+nccl_native+nccl_native/sdk_boundary.json')"
CUDA_PREFIX="$(dirname "$(find_runfile '*/+cuda_native+cuda_native/prefix/bin/nvcc')")/.."
PREFIX="$(tr -d '\n' < "$PREFIX_PATH")"

[[ -f "$PREFIX/include/nccl.h" ]] || {
  echo "NCCL native prefix lacks include/nccl.h: $PREFIX" >&2
  exit 1
}
[[ -e "$PREFIX/lib/libnccl.so" ]] || {
  echo "NCCL native prefix lacks lib/libnccl.so: $PREFIX" >&2
  exit 1
}
[[ -f "$MANIFEST_JSON" ]] || {
  echo "NCCL sdk-boundary manifest not staged" >&2
  exit 1
}

python3 - "$MANIFEST_JSON" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
if data["mechanism"] != "sdk-boundary":
    raise SystemExit("wrong NCCL native mechanism")
if data["package"] != "nccl":
    raise SystemExit("wrong package in NCCL native manifest")
if data["expected_version"] != "2.30.7":
    raise SystemExit("wrong expected NCCL package version")
if data["actual_header_version"] != "2.30.7":
    raise SystemExit("wrong NCCL header version")
if not data.get("dependency_prefixes", {}).get("cuda"):
    raise SystemExit("NCCL native manifest lacks CUDA dependency prefix")
PY

if [[ -n "${TEST_TMPDIR:-}" ]]; then
  TMPDIR="$TEST_TMPDIR"
elif [[ -z "${TMPDIR:-}" ]]; then
  TMPDIR="$PWD"
fi
export TMPDIR
mkdir -p "$TMPDIR"
work="$(mktemp -d -p "$TMPDIR" nccl-two-rank.XXXXXX)"
trap 'rm -rf "$work"' EXIT

cat > "$work/nccl_two_rank.cc" <<'CC'
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>
#include <thread>
#include <chrono>

#include <cuda_runtime.h>
#include <nccl.h>

#define CUDA_CHECK(call) do { \
  cudaError_t status = (call); \
  if (status != cudaSuccess) { \
    std::fprintf(stderr, "CUDA failure at %s:%d: %s\n", __FILE__, __LINE__, cudaGetErrorString(status)); \
    return 1; \
  } \
} while (0)

#define NCCL_CHECK(call) do { \
  ncclResult_t status = (call); \
  if (status != ncclSuccess) { \
    std::fprintf(stderr, "NCCL failure at %s:%d: %s\n", __FILE__, __LINE__, ncclGetErrorString(status)); \
    return 1; \
  } \
} while (0)

static int read_device_count() {
  int runtime_version = 0;
  cudaError_t runtime_status = cudaRuntimeGetVersion(&runtime_version);
  if (runtime_status == cudaSuccess) {
    std::fprintf(stderr, "CUDA runtime version: %d\n", runtime_version);
  } else {
    std::fprintf(stderr, "CUDA runtime version query failed: %s\n", cudaGetErrorString(runtime_status));
  }
  int driver_version = 0;
  cudaError_t driver_status = cudaDriverGetVersion(&driver_version);
  if (driver_status == cudaSuccess) {
    std::fprintf(stderr, "CUDA driver API version: %d\n", driver_version);
  } else {
    std::fprintf(stderr, "CUDA driver API version query failed: %s\n", cudaGetErrorString(driver_status));
  }
  int devices = 0;
  cudaError_t status = cudaGetDeviceCount(&devices);
  if (status != cudaSuccess) {
    std::fprintf(stderr, "CUDA device count failed: %s\n", cudaGetErrorString(status));
    std::exit(1);
  }
  return devices;
}

static bool read_id(const std::string &path, ncclUniqueId *id) {
  std::ifstream in(path, std::ios::binary);
  if (!in) {
    return false;
  }
  in.read(reinterpret_cast<char *>(id), sizeof(*id));
  return in.good();
}

int main(int argc, char **argv) {
  if (argc == 2 && std::strcmp(argv[1], "--device-count") == 0) {
    int devices = read_device_count();
    std::printf("cuda:device-count:%d\n", devices);
    return devices >= 2 ? 0 : 3;
  }
  if (argc != 4) {
    std::fprintf(stderr, "usage: %s <rank> <nranks> <id-file>\n", argv[0]);
    return 2;
  }

  int rank = std::atoi(argv[1]);
  int nranks = std::atoi(argv[2]);
  std::string id_path = argv[3];
  if (nranks != 2 || rank < 0 || rank >= nranks) {
    std::fprintf(stderr, "expected two ranks, got rank=%d nranks=%d\n", rank, nranks);
    return 2;
  }

  int devices = read_device_count();
  if (devices < nranks) {
    std::fprintf(stderr, "need at least %d CUDA devices, saw %d\n", nranks, devices);
    return 3;
  }
  CUDA_CHECK(cudaSetDevice(rank));

  ncclUniqueId id;
  if (rank == 0) {
    NCCL_CHECK(ncclGetUniqueId(&id));
    std::string tmp = id_path + ".tmp";
    {
      std::ofstream out(tmp, std::ios::binary | std::ios::trunc);
      out.write(reinterpret_cast<const char *>(&id), sizeof(id));
      if (!out.good()) {
        std::fprintf(stderr, "failed to write NCCL unique id\n");
        return 1;
      }
    }
    if (std::rename(tmp.c_str(), id_path.c_str()) != 0) {
      std::perror("rename NCCL unique id");
      return 1;
    }
  } else {
    bool loaded = false;
    for (int attempt = 0; attempt != 600; ++attempt) {
      if (read_id(id_path, &id)) {
        loaded = true;
        break;
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    if (!loaded) {
      std::fprintf(stderr, "timed out waiting for NCCL unique id\n");
      return 1;
    }
  }

  ncclComm_t comm;
  cudaStream_t stream;
  float value = static_cast<float>(rank + 1);
  float result = 0.0f;
  float *device_value = nullptr;

  NCCL_CHECK(ncclCommInitRank(&comm, nranks, id, rank));
  CUDA_CHECK(cudaStreamCreate(&stream));
  CUDA_CHECK(cudaMalloc(&device_value, sizeof(float)));
  CUDA_CHECK(cudaMemcpy(device_value, &value, sizeof(float), cudaMemcpyHostToDevice));
  NCCL_CHECK(ncclAllReduce(device_value, device_value, 1, ncclFloat, ncclSum, comm, stream));
  CUDA_CHECK(cudaStreamSynchronize(stream));
  CUDA_CHECK(cudaMemcpy(&result, device_value, sizeof(float), cudaMemcpyDeviceToHost));
  CUDA_CHECK(cudaFree(device_value));
  CUDA_CHECK(cudaStreamDestroy(stream));
  NCCL_CHECK(ncclCommDestroy(comm));

  if (std::fabs(result - 3.0f) > 0.001f) {
    std::fprintf(stderr, "rank %d expected 3.0, got %.6f\n", rank, result);
    return 1;
  }
  std::printf("nccl:rank:%d:sum:%.1f\n", rank, result);
  return 0;
}
CC

cuda_libdir="$CUDA_PREFIX/lib64"
if [[ ! -d "$cuda_libdir" ]]; then
  cuda_libdir="$CUDA_PREFIX/lib"
fi

/usr/bin/c++ -std=c++17 "$work/nccl_two_rank.cc" -o "$work/nccl_two_rank" \
  -I"$PREFIX/include" -I"$CUDA_PREFIX/include" \
  -L"$PREFIX/lib" -L"$cuda_libdir" \
  -lnccl -lcudart \
  -Wl,-rpath,"$PREFIX/lib" -Wl,-rpath,"$cuda_libdir"

driver_lib="${VASO_CUDA_DRIVER_LIB:-/run/nvidia-driver/lib}"
ld_entries=()
[[ -d "$driver_lib" ]] && ld_entries+=("$driver_lib")
ld_entries+=("$PREFIX/lib" "$cuda_libdir")
[[ -n "${LD_LIBRARY_PATH:-}" ]] && ld_entries+=("$LD_LIBRARY_PATH")
export LD_LIBRARY_PATH="$(IFS=:; echo "${ld_entries[*]}")"
export NCCL_DEBUG="${NCCL_DEBUG:-WARN}"

"$work/nccl_two_rank" --device-count
id_file="$work/nccl_unique_id.bin"
timeout_s="${NCCL_TWO_RANK_TIMEOUT:-180}"

timeout "$timeout_s" "$work/nccl_two_rank" 0 2 "$id_file" >"$work/rank0.log" 2>&1 &
pid0=$!
timeout "$timeout_s" "$work/nccl_two_rank" 1 2 "$id_file" >"$work/rank1.log" 2>&1 &
pid1=$!

status0=0
status1=0
wait "$pid0" || status0=$?
wait "$pid1" || status1=$?

cat "$work/rank0.log"
cat "$work/rank1.log"

if [[ "$status0" -ne 0 || "$status1" -ne 0 ]]; then
  echo "NCCL two-rank smoke failed: rank0=$status0 rank1=$status1" >&2
  exit 1
fi

grep -q 'nccl:rank:0:sum:3.0' "$work/rank0.log"
grep -q 'nccl:rank:1:sum:3.0' "$work/rank1.log"
printf 'nccl:two-rank:all-reduce:ok\n'
