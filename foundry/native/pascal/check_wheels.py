"""Qualify two newly built Pascal wheels on a CPU-only Insula instance.

Creates a private uv environment, requires imports from the supplied wheels,
checks PyTorch CPU autograd/AdamW, and compiles Triton SM61 code to native cubins.
This does not qualify GPU execution.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback

PREFIX = Path("/opt/vaso/pascal")
UV = PREFIX / "bin/uv"


def stamp():
    return datetime.now(timezone.utc).isoformat()


def wheel_identity(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for data in iter(lambda: handle.read(4 << 20), b""):
            digest.update(data)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def environment(output: Path, venv: Path) -> dict[str, str]:
    return {
        "PATH": ":".join(map(str, [venv / "bin", PREFIX / "cuda-tools/12.6.77/bin",
                                   PREFIX / "bin", Path("/opt/vaso/cuda/bin"),
                                   Path("/usr/local/bin"), Path("/usr/bin"), Path("/bin")])),
        # No /opt/vaso/pascal/python: that prefix contains the OLD Torch/Triton.
        "PYTHONPATH": str(PREFIX / "reference-python") + ":/opt/vaso/python-deps",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
        "LD_LIBRARY_PATH": "/opt/vaso/cuda/lib64:/usr/local/lib",
        "UV_CACHE_DIR": str(output / "uv-cache"), "UV_OFFLINE": "1",
        "TMPDIR": str(output / "tmp"), "TMP": str(output / "tmp"), "TEMP": str(output / "tmp"),
        "CUDA_VISIBLE_DEVICES": "", "CUDA_HOME": "/opt/vaso/cuda",
        "TRITON_PTXAS_PATH": "/opt/vaso/cuda/bin/ptxas",
        "TRITON_PTXAS_BLACKWELL_PATH": str(PREFIX / "vendor/bin/ptxas-blackwell"),
        "TRITON_CUOBJDUMP_PATH": str(PREFIX / "cuda-tools/12.6.77/bin/cuobjdump"),
        "TRITON_NVDISASM_PATH": str(PREFIX / "cuda-tools/12.6.77/bin/nvdisasm"),
        "TRITON_LIBDEVICE_PATH": "/opt/vaso/cuda/nvvm/libdevice/libdevice.10.bc",
        "TRITON_CACHE_DIR": str(output / "triton-cache"),
        "TRITON_HOME": str(output / "triton-home"),
        "C_INCLUDE_PATH": "/opt/vaso/cuda/include", "CPLUS_INCLUDE_PATH": "/opt/vaso/cuda/include",
        "CC": "gcc", "CXX": "g++", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
        "LC_ALL": "C", "PIP_NO_INDEX": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
    }


def require_private_import(module, venv: Path) -> str:
    location = Path(module.__file__).resolve()
    if not location.is_relative_to(venv.resolve()):
        raise RuntimeError(module.__name__ + " was loaded outside the new wheel environment: " + str(location))
    return str(location)


def worker(output: Path, venv: Path) -> dict:
    import torch
    import triton
    from triton._C import libtriton
    from triton.backends.compiler import GPUTarget
    from triton.compiler.compiler import ASTSource

    if torch.__version__ != "2.15.0+vaso.cu126.sm61" or triton.__version__ != "3.8.0":
        raise RuntimeError("unexpected wheel versions")
    if tuple(sys.version_info[:2]) != (3, 12) or torch.version.cuda != "12.6":
        raise RuntimeError("wrong CPython ABI or CUDA build")
    imports = {m.__name__: require_private_import(m, venv) for m in (torch, torch._C, triton, libtriton)}
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    x = torch.tensor([[1., 2., 3.], [4., 5., 6.]], dtype=torch.float64, requires_grad=True)
    w = torch.nn.Parameter(torch.tensor([[.1, .2], [.3, .4], [.5, .6]], dtype=torch.float64))
    y = x @ w
    target = torch.full_like(y, .25)
    loss = (y - target).square().mean()
    expected_gradient = 2. * (y.detach() - target) / y.numel()
    expected_x_grad = expected_gradient @ w.detach().t()
    expected_w_grad = x.detach().t() @ expected_gradient
    loss.backward()
    torch.testing.assert_close(x.grad, expected_x_grad, atol=1e-12, rtol=1e-12)
    torch.testing.assert_close(w.grad, expected_w_grad, atol=1e-12, rtol=1e-12)
    initial = w.detach().clone()
    expected_step = initial * (1. - .01 * .1) - .01 * expected_w_grad / (expected_w_grad.abs() + 1e-8)
    optimizer = torch.optim.AdamW([w], lr=.01, betas=(.9, .999), eps=1e-8,
                                 weight_decay=.1, foreach=False, fused=False)
    optimizer.step()
    torch.testing.assert_close(w, expected_step, atol=1e-12, rtol=1e-12)
    if not all(torch.isfinite(value).all().item() for value in (y, loss, x.grad, w.grad, w)):
        raise RuntimeError("nonfinite CPU result")
    updated_loss = ((x.detach() @ w.detach()) - target).square().mean().item()
    if not updated_loss < loss.item():
        raise RuntimeError("AdamW did not improve the fixture loss")
    cpu_result = {
        "forward_backward_adamw": "passed", "loss_before": loss.item(), "loss_after": updated_loss,
        "x_gradient_max_error": (x.grad - expected_x_grad).abs().max().item(),
        "weight_gradient_max_error": (w.grad - expected_w_grad).abs().max().item(),
        "adamw_max_error": (w.detach() - expected_step).abs().max().item(),
        "device": str(w.device),
    }
    # Reuse the actual committed Pascal kernels; loading this module does not
    # execute its GPU main(). The explicit target bypasses driver discovery.
    probe_path = PREFIX / "qualification/compiler_probe.py"
    spec = importlib.util.spec_from_file_location("pascal_new_wheel_compiler_probe", probe_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("capsule compiler qualification kernels are unavailable")
    probe = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = probe
    spec.loader.exec_module(probe)
    target_gpu = GPUTarget("cuda", 61, 32)
    fixtures = [
        ("masked_add", probe.add, {"a": "*fp32", "b": "*fp32", "c": "*fp32"}, {"N": 1003, "B": 256}),
        ("fp32_dot", probe.dot, {"a": "*fp32", "b": "*fp32", "c": "*fp32", "out": "*fp32"},
         {"M": 16, "N": 16, "K": 16, "INT": False}),
        ("signed_int8_dot", probe.dot, {"a": "*i8", "b": "*i8", "c": "*i32", "out": "*i32"},
         {"M": 32, "N": 32, "K": 32, "INT": True}),
        ("inline_dp4a", probe.dp4a, {"a": "*i32", "b": "*i32", "out": "*i32"}, {"N": 128}),
    ]
    compiled_results = []
    for name, fn, signature, constexprs in fixtures:
        compiled = triton.compile(ASTSource(fn, signature, constexprs), target=target_gpu, options={"num_warps": 4})
        ptx, cubin = compiled.asm["ptx"], compiled.asm["cubin"]
        if ".target sm_61" not in ptx or not cubin.startswith(b"\x7fELF"):
            raise RuntimeError("Triton failed to produce SM61 native cubin: " + name)
        (output / (name + ".ptx")).write_text(ptx)
        cubin_path = output / (name + ".cubin")
        cubin_path.write_bytes(cubin)
        record = {"case": name, "target": "sm_61", "ptx_bytes": len(ptx.encode()),
                  "cubin_bytes": len(cubin), "compiled": True, "gpu_executed": False}
        if name == "inline_dp4a":
            if "dp4a.s32.s32" not in ptx:
                raise RuntimeError("DP4A instruction missing from PTX")
            for tool, args in (
                ("cuobjdump", ["--dump-sass", str(cubin_path)]),
                ("nvdisasm", [str(cubin_path)]),
            ):
                result = subprocess.run([str(PREFIX / "cuda-tools/12.6.77/bin" / tool), *args],
                                        check=True, text=True, capture_output=True)
                (output / (tool + ".sass")).write_text(result.stdout)
                if "IDP.4A" not in result.stdout:
                    raise RuntimeError(tool + " did not show the native SM61 DP4A instruction")
            record["native_dp4a_disassembly"] = "passed"
        compiled_results.append(record)
    return {"passed": True, "imports": imports, "torch": torch.__version__, "triton": triton.__version__,
            "python": sys.version, "cuda_build": torch.version.cuda, "cpu": cpu_result,
            "triton_compilation": compiled_results, "gpu_runtime_qualified": False}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--torch-wheel", type=Path)
    parser.add_argument("--triton-wheel", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--venv", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    output = args.output.resolve()
    if not output.is_relative_to(Path("/workspace")):
        raise ValueError("--output must be inside the explicitly projected /workspace Estate")
    if args.worker:
        result = worker(output, args.venv)
        (output / "worker-result.json").write_text(json.dumps(result, indent=2) + "\n")
        return 0
    if args.torch_wheel is None or args.triton_wheel is None:
        raise ValueError("both explicit newly built wheel paths are required")
    wheels = [args.torch_wheel.resolve(strict=True), args.triton_wheel.resolve(strict=True)]
    output.mkdir(parents=True, exist_ok=False)
    (output / "tmp").mkdir()
    venv = output / "venv"
    env = environment(output, venv)
    receipt = {"schema": "vaso.pascal-wheel-cpu-qualification.v1", "started_at": stamp(),
               "passed": False, "artifacts": [wheel_identity(p) for p in wheels],
               "environment": str(venv), "gpu_runtime_qualified": False}
    try:
        steps = [
            ("venv", [str(UV), "venv", "--offline", "--no-python-downloads", "--no-config",
                      "--python", "/usr/local/bin/python3", str(venv)]),
            ("install", [str(UV), "pip", "install", "--offline", "--no-index", "--no-config",
                         "--no-cache", "--no-deps", "--python", str(venv / "bin/python"), *map(str, wheels)]),
            ("probe", [str(venv / "bin/python"), "-B", str(Path(__file__).resolve()),
                       "--worker", "--venv", str(venv), "--output", str(output)]),
        ]
        for phase, command in steps:
            receipt["phase"] = phase
            with (output / (phase + ".log")).open("w") as log:
                subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        receipt["qualification"] = json.loads((output / "worker-result.json").read_text())
        receipt["passed"] = receipt["qualification"]["passed"]
    except BaseException as exc:
        receipt["error"] = str(exc)
        (output / "exception.txt").write_text(traceback.format_exc())
    finally:
        receipt["finished_at"] = stamp()
        (output / "result.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps(receipt, indent=2))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
