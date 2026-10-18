#!/usr/bin/env python3
"""Plan and run the D3 native PyTorch gate commands."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


TORCH_VERSION_PREFIX = "2.14.0"
JAX_VERSION_PREFIX = "0.10.2"
EXPERIMENT_ROOT = Path(__file__).resolve().parents[2]
HOST_TMP_ROOTS = (
    Path(os.sep) / "tmp",
    Path(os.sep) / "var" / "tmp",
)


@dataclass(frozen=True)
class Gate:
    name: str
    group: str
    command: list[str]
    blocking: bool
    stdout: Path
    stderr: Path

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "group": self.group,
            "command": self.command,
            "blocking": self.blocking,
            "stdout": str(self.stdout),
            "stderr": str(self.stderr),
        }


def _torch_code(body: str) -> str:
    return "\n".join(line.rstrip() for line in textwrap.dedent(body).strip().splitlines()) + "\n"


IMPORT_CUDA = _torch_code(
    f"""
    import torch
    assert torch.__version__.startswith({TORCH_VERSION_PREFIX!r}), torch.__version__
    assert torch.cuda.is_available()
    assert torch.version.cuda
    print(torch.__version__, torch.version.cuda, torch.cuda.get_device_name(0))
    """
)

FEATURE_PARITY = _torch_code(
    """
    import os
    from pathlib import Path
    import torch

    def is_under(path, root):
        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True

    def resolve_soname(soname):
        search_roots = [
            Path(entry)
            for entry in os.environ.get("LD_LIBRARY_PATH", "").split(os.pathsep)
            if entry
        ]
        search_roots.extend([Path("/usr/local/cuda/lib64"), Path("/usr/local/cuda/lib")])
        for root in search_roots:
            candidate = root / soname
            if candidate.exists():
                return candidate.resolve()
        raise AssertionError(f"{soname} was not resolvable from LD_LIBRARY_PATH/rootfs")

    def torch_lib_dir():
        torch_prefix = os.environ.get("TORCH_PREFIX")
        if torch_prefix:
            matches = sorted((Path(torch_prefix) / "lib").glob("python*/site-packages/torch/lib"))
            if len(matches) != 1:
                raise AssertionError(f"expected one torch lib dir under TORCH_PREFIX, got {matches}")
            return matches[0]
        torch_file = getattr(torch, "__file__", None)
        if torch_file:
            return Path(torch_file).resolve().parent / "lib"
        raise AssertionError("TORCH_PREFIX or torch.__file__ is required for artifact parity")

    def assert_file_contains(path, needle):
        if not path.is_file():
            raise AssertionError(f"{path} is missing")
        if needle not in path.read_bytes():
            raise AssertionError(f"{path} does not contain {needle!r}")

    cuda_home = Path(os.environ.get("VASO_CUDA_HOME", "/usr/local/cuda"))
    cuda_roots = {cuda_home.resolve(strict=False)}
    if cuda_home != Path("/usr/local/cuda"):
        cuda_roots.add(Path("/usr/local/cuda").resolve(strict=False))

    assert torch.backends.cudnn.version() == 92400, torch.backends.cudnn.version()
    assert tuple(torch.cuda.nccl.version()) == (2, 30, 7), torch.cuda.nccl.version()
    for soname in ("libcudnn.so.9", "libnccl.so.2"):
        resolved = resolve_soname(soname)
        assert any(is_under(resolved, root) for root in cuda_roots), resolved

    config = torch.__config__.show()
    for expected in ("USE_CUSPARSELT", "USE_KINETO"):
        assert expected in config, config
    lib_dir = torch_lib_dir()
    assert_file_contains(lib_dir / "libtorch_cuda.so", b"cudssCreate")
    assert_file_contains(lib_dir / "libtorch_nvshmem.so", b"nvshmem")
    for forbidden in ("USE_MAGMA=1", "USE_MKLDNN=1", "MAGMA : ON", "MKLDNN : ON"):
        assert forbidden not in config, config

    cuda_backend = torch.backends.cuda
    assert cuda_backend.flash_sdp_enabled()
    assert cuda_backend.mem_efficient_sdp_enabled()
    print("feature parity ok")
    """
)

B200_MATMUL = _torch_code(
    """
    import torch
    x = torch.randn((2048, 2048), device="cuda", dtype=torch.float16)
    y = x @ x.T
    torch.cuda.synchronize()
    assert y.is_cuda and y.shape == (2048, 2048)
    print(float(y[0, 0].cpu()))
    """
)

CUDNN_CONV = _torch_code(
    """
    import torch
    m = torch.nn.Conv2d(16, 32, 3, padding=1).cuda().half()
    x = torch.randn(8, 16, 64, 64, device="cuda", dtype=torch.float16)
    y = m(x)
    torch.cuda.synchronize()
    assert y.shape == (8, 32, 64, 64)
    print(torch.backends.cudnn.version())
    """
)

NCCL_DISTRIBUTED = _torch_code(
    """
    import torch
    import torch.distributed as dist

    dist.init_process_group("nccl")
    rank = dist.get_rank()
    torch.cuda.set_device(rank)
    x = torch.ones(1, device="cuda") * (rank + 1)
    dist.all_reduce(x)
    assert x.item() == 3.0, x.item()
    print("rank", rank, "ok")
    dist.destroy_process_group()
    """
)

GLOO_DISTRIBUTED = _torch_code(
    """
    import torch
    import torch.distributed as dist

    dist.init_process_group("gloo")
    x = torch.ones(1) * (dist.get_rank() + 1)
    dist.all_reduce(x)
    assert x.item() == 3.0, x.item()
    print("gloo rank", dist.get_rank(), "ok")
    dist.destroy_process_group()
    """
)

TORCH_COMPILE = _torch_code(
    """
    import torch

    @torch.compile
    def f(x):
        return torch.sin(x) * 2

    x = torch.randn(1024, device="cuda")
    y = f(x)
    torch.cuda.synchronize()
    assert y.is_cuda
    print(y[:3].cpu())
    """
)

SDPA_FLASH = _torch_code(
    """
    import torch
    from torch.nn.functional import scaled_dot_product_attention as sdpa

    q = torch.randn(2, 8, 128, 64, device="cuda", dtype=torch.float16)
    k = torch.randn_like(q)
    v = torch.randn_like(q)
    with torch.nn.attention.sdpa_kernel(torch.nn.attention.SDPBackend.FLASH_ATTENTION):
        y = sdpa(q, k, v)
    torch.cuda.synchronize()
    assert y.is_cuda
    print(y.shape)
    """
)

TORCHVISION_OPS = _torch_code(
    """
    import torch
    import torchvision
    from torchvision.ops import nms

    boxes = torch.tensor(
        [[0, 0, 10, 10], [1, 1, 11, 11]],
        device="cuda",
        dtype=torch.float32,
    )
    scores = torch.tensor([0.9, 0.8], device="cuda")
    keep = nms(boxes, scores, 0.5)
    assert keep.is_cuda, keep
    print(torchvision.__version__, keep)
    """
)

TORCHAUDIO_OPS = _torch_code(
    """
    import torch
    import torchaudio

    x = torch.randn(1, 16000)
    y = torchaudio.functional.resample(x, 16000, 8000)
    assert y.shape[-1] == 8000
    print(torchaudio.__version__, y.shape)
    """
)

JAX_IMPORT = _torch_code(
    f"""
    import jax
    import jaxlib

    assert jax.__version__.startswith({JAX_VERSION_PREFIX!r}), jax.__version__
    assert jaxlib.__version__.startswith({JAX_VERSION_PREFIX!r}), jaxlib.__version__
    devices = jax.devices("gpu")
    assert devices, devices
    print(jax.__version__, jaxlib.__version__, devices[0])
    """
)

JAX_MATMUL = _torch_code(
    """
    import jax
    import jax.numpy as jnp

    devices = jax.devices("gpu")
    assert devices, devices

    @jax.jit
    def matmul(x, y):
        return (x.astype(jnp.bfloat16) @ y.astype(jnp.bfloat16)).astype(jnp.float32)

    with jax.default_device(devices[0]):
        x = jnp.arange(1024 * 1024, dtype=jnp.float32).reshape(1024, 1024) / 1024.0
        y = jnp.flip(x, axis=1).T
        z = matmul(x, y)
        z.block_until_ready()
    assert z.shape == (1024, 1024)
    assert bool(jnp.isfinite(z).all())
    print(float(jax.device_get(z[0, 0])))
    """
)

JAX_PSUM_2GPU = _torch_code(
    """
    import functools
    import jax
    import jax.numpy as jnp

    devices = jax.devices("gpu")
    assert len(devices) >= 2, devices

    @functools.partial(jax.pmap, axis_name="i", devices=devices[:2])
    def psum(x):
        return jax.lax.psum(x, "i")

    y = psum(jnp.ones((2,), dtype=jnp.float32))
    assert bool(jnp.all(y == 2.0)), y
    print(jax.device_get(y))
    """
)


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return True


def _validate_out_dir(path: Path) -> str | None:
    resolved = path.expanduser().resolve(strict=False)
    for tmp_root in HOST_TMP_ROOTS:
        if resolved == tmp_root or _is_under(resolved, tmp_root):
            return f"gate output directory must not be under host {tmp_root}: {path}"
    return None


def _site_packages(torch_prefix: Path) -> Path:
    matches = sorted((torch_prefix / "lib").glob("python*/site-packages"))
    if len(matches) != 1:
        raise ValueError(
            f"{torch_prefix} must provide exactly one lib/pythonX.Y/site-packages tree"
        )
    return matches[0]


def _cpython_extension_path(torch_prefix: Path) -> str:
    abi_dir = _site_packages(torch_prefix).parent.name
    compact = "".join(part for part in abi_dir if part.isdigit())
    suffix = "cpython-" + compact + "-x86_64-linux-gnu.so"
    return str(Path("lib") / abi_dir / "site-packages" / "torch" / ("_C." + suffix))


def _torch_lib(torch_prefix: Path) -> Path:
    return _site_packages(torch_prefix) / "torch" / "lib"


def _parse_prefixed_paths(items: list[str]) -> tuple[list[tuple[str, Path]], str | None]:
    parsed: list[tuple[str, Path]] = []
    for item in items:
        if "=" not in item:
            return [], f"--runtime-prefix must be NAME=PATH, got {item!r}"
        name, raw_path = item.split("=", 1)
        if not name or not raw_path:
            return [], f"--runtime-prefix must be NAME=PATH, got {item!r}"
        parsed.append((name, Path(raw_path)))
    return parsed, None


def _dedupe(items: Iterable[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result


def _gate_env(
    torch_prefix: Path | None,
    ref_prefix: Path | None,
    python_prefix: Path,
    runtime_prefixes: list[tuple[str, Path]],
    out_dir: Path,
    base_environ: dict[str, str],
    jax_prefix: Path | None = None,
    jaxlib_prefix: Path | None = None,
) -> dict[str, str]:
    site_packages = _site_packages(torch_prefix) if torch_prefix is not None else None
    torch_lib = _torch_lib(torch_prefix) if torch_prefix is not None else None
    jax_site_packages = _site_packages(jax_prefix) if jax_prefix is not None else None
    jaxlib_site_packages = _site_packages(jaxlib_prefix) if jaxlib_prefix is not None else None
    runtime_site_packages = [_site_packages(prefix) for _, prefix in runtime_prefixes]
    runtime_lib_dirs = [
        str(prefix / libdir)
        for _, prefix in runtime_prefixes
        for libdir in ("lib", "lib64")
        if (prefix / libdir).is_dir()
    ]
    ld_entries = [
        "/run/nvidia-driver/lib",
        "/usr/local/cuda/lib64",
    ]
    if torch_prefix is not None:
        ld_entries[1:1] = [str(torch_prefix / "lib"), str(torch_lib)]
    ld_entries.extend(runtime_lib_dirs)
    if jax_prefix is not None:
        ld_entries.append(str(jax_prefix / "lib"))
    if jaxlib_prefix is not None:
        ld_entries.extend([str(jaxlib_prefix / "lib"), str(jaxlib_site_packages / "jaxlib")])
    existing_ld = base_environ.get("LD_LIBRARY_PATH")
    if existing_ld:
        ld_entries.extend(entry for entry in existing_ld.split(os.pathsep) if entry)
    deduped_ld: list[str] = []
    for entry in ld_entries:
        if entry and entry not in deduped_ld:
            deduped_ld.append(entry)

    path_entries = [str(python_prefix / "bin")]
    if torch_prefix is not None:
        path_entries.append(str(torch_prefix / "bin"))
    if jax_prefix is not None:
        path_entries.append(str(jax_prefix / "bin"))
    if jaxlib_prefix is not None:
        path_entries.append(str(jaxlib_prefix / "bin"))
    existing_path = base_environ.get("PATH")
    if existing_path:
        path_entries.append(existing_path)
    tmp_dir = out_dir / "tmp"
    torchelastic_dir = out_dir / "torchelastic"
    cuda_home = base_environ.get("VASO_CUDA_HOME") or "/usr/local/cuda"
    cuda_include = str(Path(cuda_home) / "include")
    cuda_bin = Path(cuda_home) / "bin"
    pythonpath_entries = [str(site_packages)] if site_packages is not None else []
    if jax_site_packages is not None:
        pythonpath_entries.append(str(jax_site_packages))
    if jaxlib_site_packages is not None:
        pythonpath_entries.append(str(jaxlib_site_packages))
    pythonpath_entries.extend(str(path) for path in runtime_site_packages)
    xla_flags = [
        f"--xla_gpu_cuda_data_dir={cuda_home}",
        *(flag for flag in base_environ.get("XLA_FLAGS", "").split() if flag),
    ]
    env = {
        **base_environ,
        "PYTHON": str(python_prefix / "bin" / "python3"),
        "PYTHONPATH": os.pathsep.join(pythonpath_entries),
        "LD_LIBRARY_PATH": os.pathsep.join(deduped_ld),
        "PATH": os.pathsep.join(path_entries),
        "TMPDIR": str(tmp_dir),
        "TORCHINDUCTOR_CACHE_DIR": str(out_dir / "torchinductor"),
        "TRITON_CACHE_DIR": str(out_dir / "triton"),
        "JAX_COMPILATION_CACHE_DIR": str(out_dir / "jax_cache"),
        "JAX_PERSISTENT_CACHE_MIN_COMPILE_TIME_SECS": base_environ.get(
            "JAX_PERSISTENT_CACHE_MIN_COMPILE_TIME_SECS",
            "0",
        ),
        "TRITON_CUDACRT_PATH": cuda_include,
        "TRITON_CUDART_PATH": cuda_include,
        "TRITON_CUOBJDUMP_PATH": str(cuda_bin / "cuobjdump"),
        "TRITON_LIBCUDA_PATH": "/run/nvidia-driver/lib",
        "TRITON_LIBDEVICE_PATH": str(Path(cuda_home) / "nvvm" / "libdevice" / "libdevice.10.bc"),
        "TRITON_NVDISASM_PATH": str(cuda_bin / "nvdisasm"),
        "TRITON_PTXAS_BLACKWELL_PATH": str(cuda_bin / "ptxas"),
        "TRITON_PTXAS_PATH": str(cuda_bin / "ptxas"),
        "MOSAIC_GPU_NVSHMEM_BC_PATH": str(Path(cuda_home) / "lib64" / "libnvshmem_device.bc"),
        "MOSAIC_GPU_NVSHMEM_SO_PATH": str(Path(cuda_home) / "lib64" / "libnvshmem_host.so.3"),
        "TORCHELASTIC_LOG_DIR": str(torchelastic_dir),
        "XLA_FLAGS": " ".join(_dedupe(xla_flags)),
        "XLA_PYTHON_CLIENT_PREALLOCATE": base_environ.get("XLA_PYTHON_CLIENT_PREALLOCATE", "false"),
        "JAX_PLATFORMS": base_environ.get("JAX_PLATFORMS", "cuda"),
    }
    if torch_prefix is not None:
        env["TORCH_PREFIX"] = str(torch_prefix)
    if ref_prefix is not None:
        env["TORCH_REF_PREFIX"] = str(ref_prefix)
    if jax_prefix is not None:
        env["JAX_PREFIX"] = str(jax_prefix)
    if jaxlib_prefix is not None:
        env["JAXLIB_PREFIX"] = str(jaxlib_prefix)
    return env


def _python_command(python_prefix: Path, code: str) -> list[str]:
    return [str(python_prefix / "bin" / "python3"), "-c", code]


def _torchrun_command(python_prefix: Path, out_dir: Path, code: str) -> list[str]:
    return [
        str(python_prefix / "bin" / "python3"),
        "-m",
        "torch.distributed.run",
        "--standalone",
        "--nnodes=1",
        "--nproc-per-node=2",
        "--log-dir",
        str(out_dir / "torchelastic"),
        "--",
        "-c",
        code,
    ]


def _make_gate(
    name: str,
    command: list[str],
    out_dir: Path,
    *,
    group: str = "core",
    blocking: bool | None = None,
    stdout_name: str | None = None,
) -> Gate:
    return Gate(
        name=name,
        group=group,
        command=command,
        blocking=(group == "core" if blocking is None else blocking),
        stdout=out_dir / (stdout_name or f"{name}.stdout.txt"),
        stderr=out_dir / f"{name}.stderr.txt",
    )


def plan_gates(
    torch_prefix: Path | None,
    ref_prefix: Path | None,
    python_prefix: Path,
    out_dir: Path,
    *,
    jax_prefix: Path | None = None,
    jaxlib_prefix: Path | None = None,
) -> list[Gate]:
    python_module = str(python_prefix / "bin" / "python3")
    gates: list[Gate] = []
    if torch_prefix is not None and ref_prefix is not None:
        site_packages_rel = Path("lib") / _site_packages(torch_prefix).parent.name / "site-packages"
        abi_out = out_dir / "torch-abi.json"
        abi_script = "tools/abi_parity.py"
        abi_command = [
            "/usr/bin/python3",
            abi_script,
            "--reference",
            str(ref_prefix),
            "--candidate",
            str(torch_prefix),
            "--data-path",
            str(site_packages_rel / "torch" / "version.py"),
            "--elf-path",
            _cpython_extension_path(torch_prefix),
            "--out",
            str(abi_out),
        ]
        gates.extend(
            [
                _make_gate("abi_prefix_parity", abi_command, out_dir),
                _make_gate("import_cuda", _python_command(python_prefix, IMPORT_CUDA), out_dir),
                _make_gate("feature_parity", _python_command(python_prefix, FEATURE_PARITY), out_dir),
                _make_gate("b200_matmul", _python_command(python_prefix, B200_MATMUL), out_dir),
                _make_gate("cudnn_conv", _python_command(python_prefix, CUDNN_CONV), out_dir),
                _make_gate("nccl_distributed", _torchrun_command(python_prefix, out_dir, NCCL_DISTRIBUTED), out_dir),
                _make_gate("gloo_distributed", _torchrun_command(python_prefix, out_dir, GLOO_DISTRIBUTED), out_dir),
                _make_gate("torch_compile", _python_command(python_prefix, TORCH_COMPILE), out_dir, group="compile"),
                _make_gate("sdpa_flash", _python_command(python_prefix, SDPA_FLASH), out_dir),
                _make_gate("torchvision_ops", _python_command(python_prefix, TORCHVISION_OPS), out_dir, group="vision_audio"),
                _make_gate("torchaudio_ops", _python_command(python_prefix, TORCHAUDIO_OPS), out_dir, group="vision_audio"),
                _make_gate(
                    "collect_env",
                    [python_module, "-m", "torch.utils.collect_env"],
                    out_dir,
                    stdout_name="collect_env.txt",
                ),
                _make_gate(
                    "benchmark_smoke",
                    [python_module, "-m", "torch.utils.benchmark", "--help"],
                    out_dir,
                    group="diagnostic",
                    blocking=False,
                    stdout_name="benchmark-help.txt",
                ),
            ]
        )
    if jax_prefix is not None and jaxlib_prefix is not None:
        gates.extend(
            [
                _make_gate("jax_import", _python_command(python_prefix, JAX_IMPORT), out_dir, group="jax"),
                _make_gate("jax_matmul", _python_command(python_prefix, JAX_MATMUL), out_dir, group="jax"),
                _make_gate("jax_psum_2gpu", _python_command(python_prefix, JAX_PSUM_2GPU), out_dir, group="jax"),
            ]
        )
    return gates


def _write_json(path: Path, doc: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _plan_doc(
    args: argparse.Namespace,
    gates: Iterable[Gate],
    env: dict[str, str],
    selected_gate_names: set[str] | None,
) -> dict[str, object]:
    selected = [
        gate.to_json()
        for gate in gates
        if selected_gate_names is None or gate.name in selected_gate_names
    ]
    env_doc = {
        "PYTHON": env["PYTHON"],
        "PYTHONPATH": env["PYTHONPATH"],
        "LD_LIBRARY_PATH": env["LD_LIBRARY_PATH"],
        "TMPDIR": env["TMPDIR"],
        "TORCHINDUCTOR_CACHE_DIR": env["TORCHINDUCTOR_CACHE_DIR"],
        "TRITON_CACHE_DIR": env["TRITON_CACHE_DIR"],
        "JAX_COMPILATION_CACHE_DIR": env["JAX_COMPILATION_CACHE_DIR"],
        "TRITON_CUDACRT_PATH": env["TRITON_CUDACRT_PATH"],
        "TRITON_CUDART_PATH": env["TRITON_CUDART_PATH"],
        "TRITON_CUOBJDUMP_PATH": env["TRITON_CUOBJDUMP_PATH"],
        "TRITON_LIBCUDA_PATH": env["TRITON_LIBCUDA_PATH"],
        "TRITON_LIBDEVICE_PATH": env["TRITON_LIBDEVICE_PATH"],
        "TRITON_NVDISASM_PATH": env["TRITON_NVDISASM_PATH"],
        "TRITON_PTXAS_BLACKWELL_PATH": env["TRITON_PTXAS_BLACKWELL_PATH"],
        "TRITON_PTXAS_PATH": env["TRITON_PTXAS_PATH"],
        "MOSAIC_GPU_NVSHMEM_BC_PATH": env["MOSAIC_GPU_NVSHMEM_BC_PATH"],
        "MOSAIC_GPU_NVSHMEM_SO_PATH": env["MOSAIC_GPU_NVSHMEM_SO_PATH"],
        "TORCHELASTIC_LOG_DIR": env["TORCHELASTIC_LOG_DIR"],
        "XLA_FLAGS": env["XLA_FLAGS"],
        "XLA_PYTHON_CLIENT_PREALLOCATE": env["XLA_PYTHON_CLIENT_PREALLOCATE"],
        "JAX_PLATFORMS": env["JAX_PLATFORMS"],
    }
    if "TORCH_PREFIX" in env:
        env_doc["TORCH_PREFIX"] = env["TORCH_PREFIX"]
    if "TORCH_REF_PREFIX" in env:
        env_doc["TORCH_REF_PREFIX"] = env["TORCH_REF_PREFIX"]
    if "JAX_PREFIX" in env:
        env_doc["JAX_PREFIX"] = env["JAX_PREFIX"]
    if "JAXLIB_PREFIX" in env:
        env_doc["JAXLIB_PREFIX"] = env["JAXLIB_PREFIX"]
    return {
        "execute_requested": bool(args.execute),
        "python": env["PYTHON"],
        "env": env_doc,
        "out_dir": str(Path(args.out_dir)),
        "gates": selected,
    }


def _select_gate_names(raw_names: list[str], gates: list[Gate]) -> tuple[set[str] | None, str | None]:
    if not raw_names:
        return None, None
    known = {gate.name for gate in gates}
    requested: set[str] = set()
    for raw in raw_names:
        requested.update(name for name in raw.split(",") if name)
    unknown = sorted(requested - known)
    if unknown:
        return None, "unknown gate(s): " + ", ".join(unknown)
    return requested, None


def _run_gate(gate: Gate, env: dict[str, str]) -> dict[str, object]:
    gate.stdout.parent.mkdir(parents=True, exist_ok=True)
    gate.stderr.parent.mkdir(parents=True, exist_ok=True)
    exec_error: str | None = None
    returncode = 0
    with gate.stdout.open("w", encoding="utf-8") as stdout, gate.stderr.open("w", encoding="utf-8") as stderr:
        try:
            result = subprocess.run(
                gate.command,
                cwd=str(EXPERIMENT_ROOT),
                env=env,
                stdout=stdout,
                stderr=stderr,
                check=False,
                text=True,
            )
            returncode = result.returncode
        except OSError as exc:
            exec_error = f"{type(exc).__name__}: {exc}"
            stderr.write("exec_error: " + exec_error + "\n")
            returncode = 127

    result_doc: dict[str, object] = {
        "name": gate.name,
        "group": gate.group,
        "blocking": gate.blocking,
        "returncode": returncode,
        "stdout": str(gate.stdout),
        "stderr": str(gate.stderr),
    }
    if exec_error is not None:
        result_doc["exec_error"] = exec_error
    return result_doc


def _run_gates(gates: Iterable[Gate], env: dict[str, str]) -> dict[str, object]:
    results = [_run_gate(gate, env) for gate in gates]
    failed_blocking = [
        str(result["name"])
        for result in results
        if result["blocking"] and result["returncode"] != 0
    ]
    return {
        "verdict": "failed" if failed_blocking else "passed",
        "failed_blocking_gates": failed_blocking,
        "results": results,
    }


def _guard_results(path: Path, required_groups: set[str]) -> int:
    doc = json.loads(path.read_text(encoding="utf-8"))
    failed = [
        str(result.get("name"))
        for result in doc.get("results", [])
        if str(result.get("group")) in required_groups and int(result.get("returncode", 1)) != 0
    ]
    if failed:
        print(
            "blocking PyTorch gate failure(s) for "
            + ",".join(sorted(required_groups))
            + ": "
            + ", ".join(failed),
            file=sys.stderr,
        )
        return 1
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--torch-prefix")
    parser.add_argument("--torch-ref-prefix")
    parser.add_argument("--python-prefix")
    parser.add_argument("--jax-prefix")
    parser.add_argument("--jaxlib-prefix")
    parser.add_argument("--runtime-prefix", action="append", default=[])
    parser.add_argument("--out-dir")
    parser.add_argument("--plan-out")
    parser.add_argument("--results-out")
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--block-group", action="append", default=[])
    parser.add_argument("--guard-results")
    parser.add_argument("--require-group", action="append", default=[])
    parser.add_argument("--execute", action="store_true")
    return parser


def _parse_args(argv: list[str]) -> tuple[argparse.Namespace | None, int]:
    parser = _parser()
    try:
        return parser.parse_args(argv), 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
        return None, code


def main(argv: list[str] | None = None, environ: dict[str, str] | None = None) -> int:
    args, parse_rc = _parse_args(list(argv or []))
    if args is None:
        return parse_rc

    if args.guard_results:
        return _guard_results(Path(args.guard_results), set(args.require_group or ["core"]))

    missing_args = [
        name
        for name in ("python_prefix", "out_dir")
        if not getattr(args, name)
    ]
    if missing_args:
        print("missing required gate argument(s): " + ", ".join("--" + name.replace("_", "-") for name in missing_args), file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir)
    error = _validate_out_dir(out_dir)
    if error:
        print(error, file=sys.stderr)
        return 2

    torch_prefix = Path(args.torch_prefix) if args.torch_prefix else None
    ref_prefix = Path(args.torch_ref_prefix) if args.torch_ref_prefix else None
    python_prefix = Path(args.python_prefix)
    if bool(args.jax_prefix) != bool(args.jaxlib_prefix):
        print("--jax-prefix and --jaxlib-prefix must be provided together", file=sys.stderr)
        return 2
    if bool(torch_prefix) != bool(ref_prefix):
        print("--torch-prefix and --torch-ref-prefix must be provided together", file=sys.stderr)
        return 2
    if torch_prefix is None and not args.jax_prefix:
        print("one gate profile is required: provide --torch-prefix/--torch-ref-prefix or --jax-prefix/--jaxlib-prefix", file=sys.stderr)
        return 2
    if torch_prefix is not None and args.jax_prefix:
        print("torch and JAX prefixes select separate profiles; run one gate profile at a time", file=sys.stderr)
        return 2
    runtime_prefixes, error = _parse_prefixed_paths(args.runtime_prefix)
    if error:
        print(error, file=sys.stderr)
        return 2
    base_environ = dict(os.environ)
    if environ:
        base_environ.update(environ)
    env = _gate_env(
        torch_prefix,
        ref_prefix,
        python_prefix,
        runtime_prefixes,
        out_dir,
        base_environ,
        jax_prefix=Path(args.jax_prefix) if args.jax_prefix else None,
        jaxlib_prefix=Path(args.jaxlib_prefix) if args.jaxlib_prefix else None,
    )
    gates = plan_gates(
        torch_prefix,
        ref_prefix,
        python_prefix,
        out_dir,
        jax_prefix=Path(args.jax_prefix) if args.jax_prefix else None,
        jaxlib_prefix=Path(args.jaxlib_prefix) if args.jaxlib_prefix else None,
    )
    block_groups = set(args.block_group or ["core"])
    gates = [
        Gate(
            name=gate.name,
            group=gate.group,
            command=gate.command,
            blocking=(gate.group in block_groups and gate.group != "diagnostic"),
            stdout=gate.stdout,
            stderr=gate.stderr,
        )
        for gate in gates
    ]
    selected_names, error = _select_gate_names(args.only, gates)
    if error:
        print(error, file=sys.stderr)
        return 2
    selected_gates = [
        gate for gate in gates if selected_names is None or gate.name in selected_names
    ]

    plan_out = Path(args.plan_out) if args.plan_out else out_dir / "gate-plan.json"
    _write_json(plan_out, _plan_doc(args, gates, env, selected_names))

    if not args.execute:
        return 0

    results = _run_gates(selected_gates, env)
    results_out = Path(args.results_out) if args.results_out else out_dir / "gate-results.json"
    _write_json(results_out, results)
    return 1 if results["failed_blocking_gates"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
