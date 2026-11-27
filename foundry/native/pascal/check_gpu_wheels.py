"""Run the existing Pascal GPU acceptance suites against a private new-wheel venv.

Launch only through the finite Insula payload owner with both physical GPU
leases. The venv and its wheel identities come from check_wheels.py's successful
CPU receipt. This workload does not install packages or acquire checkpoints.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import traceback

PREFIX = Path("/opt/vaso/pascal")
QUALIFICATION = PREFIX / "qualification"
CHECKPOINT_REPO = "hf-internal-testing/tiny-inkling2-mini"
CHECKPOINT_REVISION = "ee242a48af34ff0729518f4ac28cfcb847e470ab"


def stamp():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def workspace_path(path: Path, *, existing=False) -> Path:
    path = path.resolve(strict=existing)
    if not path.is_relative_to(Path("/workspace")):
        raise ValueError("Qualification inputs and outputs must be inside /workspace")
    return path


def read_cpu_receipt(path):
    receipt = json.loads(path.read_text())
    if (receipt.get("schema") != "vaso.pascal-wheel-cpu-qualification.v1"
            or receipt.get("passed") is not True
            or receipt.get("qualification", {}).get("passed") is not True):
        raise ValueError("A successful private-wheel CPU qualification receipt is required")
    venv = workspace_path(Path(receipt["environment"]), existing=True)
    if not (venv / "bin/python").is_file():
        raise ValueError("The qualified private Python environment is unavailable")
    expected_imports = {"torch", "torch._C", "triton", "triton._C.libtriton"}
    if set(receipt["qualification"].get("imports", {})) != expected_imports:
        raise ValueError("CPU receipt does not prove all four private wheel imports")
    for location in receipt["qualification"]["imports"].values():
        if not workspace_path(Path(location), existing=True).is_relative_to(venv):
            raise ValueError("CPU receipt contains an import outside its private environment")
    artifacts = receipt.get("artifacts", [])
    if len(artifacts) != 2:
        raise ValueError("CPU receipt must identify both final wheel artifacts")
    for artifact in artifacts:
        wheel = workspace_path(Path(artifact["path"]), existing=True)
        if wheel.stat().st_size != artifact["bytes"] or len(artifact["sha256"]) != 64:
            raise ValueError("CPU-qualified wheel identity is no longer available")
    return receipt, venv


def environment(output, venv):
    site = venv / "lib/python3.12/site-packages"
    env = {
        "PATH": ":".join(map(str, [venv / "bin", PREFIX / "cuda-tools/12.6.77/bin",
                                  PREFIX / "bin", Path("/opt/vaso/cuda/bin"),
                                  Path("/usr/local/bin"), Path("/usr/bin"), Path("/bin")])),
        # The capsule's python prefix supplies Transformers. The new venv MUST
        # precede it because that same capsule prefix also contains old wheels.
        "PYTHONPATH": ":".join(map(str, [site, QUALIFICATION, PREFIX / "python",
                                        PREFIX / "reference-python", Path("/opt/vaso/python-deps")])),
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
        "LD_LIBRARY_PATH": "/run/insula/nvidia:/opt/vaso/cuda/lib64:/usr/local/lib",
        "TMPDIR": str(output / "tmp"), "TMP": str(output / "tmp"), "TEMP": str(output / "tmp"),
        "UV_CACHE_DIR": str(output / "uv-cache"), "UV_OFFLINE": "1",
        "CUDA_HOME": "/opt/vaso/cuda", "CUDA_CACHE_PATH": str(output / "cuda-cache"),
        "TRITON_PTXAS_PATH": "/opt/vaso/cuda/bin/ptxas",
        "TRITON_PTXAS_BLACKWELL_PATH": str(PREFIX / "vendor/bin/ptxas-blackwell"),
        "TRITON_CUOBJDUMP_PATH": str(PREFIX / "cuda-tools/12.6.77/bin/cuobjdump"),
        "TRITON_NVDISASM_PATH": str(PREFIX / "cuda-tools/12.6.77/bin/nvdisasm"),
        "TRITON_LIBDEVICE_PATH": "/opt/vaso/cuda/nvvm/libdevice/libdevice.10.bc",
        "TRITON_LIBCUDA_PATH": "/run/insula/nvidia",
        "TRITON_CACHE_DIR": str(output / "triton-cache"), "TRITON_HOME": str(output / "triton-home"),
        "C_INCLUDE_PATH": "/opt/vaso/cuda/include", "CPLUS_INCLUDE_PATH": "/opt/vaso/cuda/include",
        "CC": "gcc", "CXX": "g++", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
        "HF_HOME": str(output / "huggingface"), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "HF_DEACTIVATE_ASYNC_LOAD": "1", "LC_ALL": "C", "PIP_NO_INDEX": "1",
    }
    if "CUDA_VISIBLE_DEVICES" in os.environ:
        env["CUDA_VISIBLE_DEVICES"] = os.environ["CUDA_VISIBLE_DEVICES"]
    return env


def suite_specs(output, venv, checkpoint):
    site = venv / "lib/python3.12/site-packages"
    def spec(name, script, arguments, result=None):
        return {"name": name, "script": str(QUALIFICATION / script), "args": arguments,
                "result": str(result or output / (name + "-result.json"))}
    specs = [
        spec("compiler", "compiler_probe.py", ["--install", str(site), "--output",
                                              str(output / "compiler-result.json")]),
        spec("operators", "inkling_triton_suite.py", [], Path("/workspace/receipts/operator-result.json")),
        spec("model", "model_triton_test_v2.py", [], Path("/workspace/receipts/model-result.json")),
        spec("transport", "p0_transport_test.py", [str(output / "transport-result.json")]),
        spec("packed_dense", "inkling_packed_inference.py",
             ["--checkpoint", str(checkpoint), "--result", str(output / "packed_dense-result.json"),
              "--process-label", output.name, "--new-tokens", "4", "--repeats", "2", "--gpus", "all"]),
    ]
    for compute in (0, 1):
        name = "archive_compute_" + str(compute)
        specs.append(spec(name, "packed_archive_probe.py",
                          ["--checkpoint", str(checkpoint), "--result", str(output / (name + "-result.json")),
                           "--compute-device", "cuda:" + str(compute), "--archive-device", "cuda:" + str(1 - compute),
                           "--new-tokens", "4", "--repeats", "2"]))
    return specs


def validate_coverage(name, result):
    if result.get("passed") is not True:
        raise ValueError(name + " did not report success")
    if name in ("compiler", "operators", "model"):
        rows = result.get("results", [])
        expected_count = {"compiler": 16, "operators": 26, "model": 2}[name]
        if (len(rows) != expected_count or {r.get("gpu") for r in rows} != {0, 1}
                or not all(r.get("passed") is True for r in rows)):
            raise ValueError(name + " did not cover both admitted GPUs completely")
        if name == "compiler" and result.get("control_only") is not False:
            raise ValueError("The compiler control suite is insufficient")
    elif name == "transport":
        roles = {(r.get("compute"), r.get("archive")) for r in result.get("roles", [])}
        if (roles != {("cuda:0", "cuda:1"), ("cuda:1", "cuda:0")}
                or result.get("device_allocations_released") is not True
                or not all(r.get("byte_checks", 0) > 0 for r in result["roles"])):
            raise ValueError("Transport must pass in both roles and release allocations")
    elif name == "packed_dense":
        actual = {(r["gpu"], r["mode"], r["repeat"], r["case"], r["use_cache"])
                  for r in result.get("runs", [])}
        expected = {(gpu, mode, repeat, case, cached) for gpu in (0, 1)
                    for mode in ("dense_fp32", "packed_fp32") for repeat in (0, 1)
                    for case in ("plain", "chat", "window_boundary", "batch_two") for cached in (False, True)}
        if actual != expected or len(result.get("cpu_references", [])) != 8 or len(result.get("eviction_runs", [])) != 4:
            raise ValueError("Packed inference is missing dense references, repeated cases, or evictions")
    else:
        compute = int(name.rsplit("_", 1)[1])
        actual = {(r["archive_mode"], r["use_cache"], r["repeat"]) for r in result.get("runs", [])}
        expected = {(mode, cached, repeat) for mode in ("full", "mixed")
                    for cached in (False, True) for repeat in (0, 1)}
        if (actual != expected or result.get("compute_device") != "cuda:" + str(compute)
                or result.get("archive_device") != "cuda:" + str(1 - compute)
                or result.get("closed", {}).get("resident_bytes") != 0
                or result.get("closed", {}).get("archive", {}).get("resident_bytes") != 0):
            raise ValueError("Archive role, coverage, or allocation release is incomplete")


def private_imports(modules, venv, expected):
    imports = {}
    for module in modules:
        location = Path(module.__file__).resolve()
        if not location.is_relative_to(venv):
            raise RuntimeError(module.__name__ + " imported an old capsule wheel")
        if str(location) != expected[module.__name__]:
            raise RuntimeError(module.__name__ + " differs from the CPU-qualified import")
        imports[module.__name__] = str(location)
    return imports


def run_suite(spec, output, venv, cpu_receipt):
    import torch
    import triton
    from triton._C import libtriton

    if Path(sys.prefix).resolve() != venv:
        raise RuntimeError("Worker is not running the private environment's interpreter")
    imports = private_imports((torch, torch._C, triton, libtriton), venv,
                              cpu_receipt["qualification"]["imports"])
    if (torch.__version__ != "2.15.0+vaso.cu126.sm61" or triton.__version__ != "3.8.0"
            or tuple(sys.version_info[:2]) != (3, 12) or torch.version.cuda != "12.6"):
        raise RuntimeError("Unexpected Torch, Triton, CPython ABI or CUDA build")
    if torch.cuda.device_count() != 2 or "sm_61" not in torch.cuda.get_arch_list():
        raise RuntimeError("The workload requires both admitted Pascal GPUs")
    devices = []
    for gpu in (0, 1):
        if torch.cuda.get_device_capability(gpu) != (6, 1):
            raise RuntimeError("GPU is not the expected SM61 device")
        devices.append({"logical_id": gpu, "name": torch.cuda.get_device_name(gpu), "capability": [6, 1]})
    write_json(output / (spec["name"] + "-imports.json"),
               {"imports": imports, "devices": devices, "torch": torch.__version__,
                "triton": triton.__version__, "cuda_build": torch.version.cuda})
    sys.argv = [spec["script"], *spec["args"]]
    try:
        runpy.run_path(spec["script"], run_name="__main__")
    except SystemExit as exit_status:
        if exit_status.code not in (None, 0):
            raise
    result = json.loads(Path(spec["result"]).read_text())
    validate_coverage(spec["name"], result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--cpu-result", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    cpu_path = workspace_path(args.cpu_result, existing=True)
    checkpoint = workspace_path(args.checkpoint, existing=True)
    output = workspace_path(args.output)
    cpu_receipt, venv = read_cpu_receipt(cpu_path)
    specs = suite_specs(output, venv, checkpoint)
    if args.worker:
        selected = [s for s in specs if s["name"] == args.worker]
        if len(selected) != 1:
            raise ValueError("Unknown qualification suite")
        run_suite(selected[0], output, venv, cpu_receipt)
        return 0
    output.mkdir(parents=True, exist_ok=False)
    (output / "tmp").mkdir()
    receipt = {"schema": "vaso.pascal-wheel-gpu-qualification.v1", "passed": False,
               "started_at": stamp(), "environment": str(venv), "artifacts": cpu_receipt["artifacts"],
               "cpu_receipt": {"path": str(cpu_path), "sha256": hashlib.sha256(cpu_path.read_bytes()).hexdigest()},
               "trained_model_quality_qualified": False, "suites": []}
    try:
        acquisition = json.loads((checkpoint / "acquisition.json").read_text())
        if (acquisition.get("complete") is not True or acquisition.get("repo") != CHECKPOINT_REPO
                or acquisition.get("revision") != CHECKPOINT_REVISION):
            raise ValueError("An already acquired pinned tiny integration checkpoint is required")
        receipt["checkpoint"] = {"path": str(checkpoint), "repo": CHECKPOINT_REPO, "revision": CHECKPOINT_REVISION,
                                 "trained": False}
        Path("/workspace/receipts").mkdir(exist_ok=True)
        for spec in specs:
            if not Path(spec["script"]).is_file() or Path(spec["result"]).exists():
                raise ValueError("Suite source is unavailable or a result already exists: " + spec["name"])
        write_json(output / "plan.json", specs)
        env = environment(output, venv)
        for spec in specs:
            name = spec["name"]
            receipt["phase"] = name
            write_json(output / "result.json", receipt)
            command = [str(venv / "bin/python"), "-B", str(Path(__file__).resolve()),
                       "--cpu-result", str(cpu_path), "--checkpoint", str(checkpoint),
                       "--output", str(output), "--worker", name]
            with (output / (name + ".log")).open("w") as log:
                subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
            result = json.loads(Path(spec["result"]).read_text())
            validate_coverage(name, result)
            write_json(output / (name + "-result.json"), result)
            receipt["suites"].append({"name": name, "passed": True, "exit_code": 0,
                                     "result": str(output / (name + "-result.json")),
                                     "imports": json.loads((output / (name + "-imports.json")).read_text())})
        receipt.update(passed=True, phase="complete", gpu_runtime_qualified=True)
    except BaseException as error:
        receipt["error"] = str(error)
        (output / "exception.txt").write_text(traceback.format_exc())
    finally:
        receipt["finished_at"] = stamp()
        write_json(output / "result.json", receipt)
        print(json.dumps(receipt, indent=2))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
