"""Explicit Pascal source-wheel path. The release Bazel matrix is unchanged."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
PROFILE = json.loads((HERE / "profile.json").read_text())
CUDA = "/opt/vaso/cuda"
TOOL_PATH = "/opt/vaso/cmake/bin:/opt/vaso/python-deps/bin:" + CUDA + "/bin:/usr/local/bin:/usr/bin:/bin"
LIB_PATH = CUDA + "/lib64:/usr/local/lib"


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
        json.dump(data, f, indent=2)
        f.write("\n")
        tmp = Path(f.name)
    os.replace(tmp, path)


class Site:
    def __init__(self, filename):
        self.config = json.loads(Path(filename).read_text())
        self.rootfs = Path(self.config["rootfs"]).resolve(strict=True)
        self.estate = Path(self.config["estate_root"]).resolve(strict=True)
        self.vaso = (self.estate / "vaso").resolve(strict=True)
        self.inputs = self.scoped(self.config["inputs_dir"])
        self.work = self.scoped(self.config["work_dir"])
        self.torch = self.scoped(self.config["torch_install"])
        self.vendor = self.scoped(self.config["vendor_tools"])
        self.gpu_lease_estate = Path(self.config["gpu_lease_estate"]).resolve(strict=True)
        if self.work == self.inputs or self.work.is_relative_to(self.inputs) or self.inputs.is_relative_to(self.work):
            raise ValueError("work_dir and inputs_dir must be disjoint")
        if not self.work.is_relative_to(self.vaso / "build"):
            raise ValueError("work_dir must be in estate /vaso/build")

    def scoped(self, rel):
        p = (self.vaso / rel).resolve()
        if Path(rel).is_absolute() or not p.is_relative_to(self.vaso) or p == self.vaso:
            raise ValueError("expected scoped estate-relative path: " + str(rel))
        return p

    def guest(self, p):
        return "/vaso/" + str(p.relative_to(self.vaso))

    def request(self, cwd, env, argv):
        return {"rootfs": str(self.rootfs), "estate": str(self.estate),
                "workdir": self.guest(cwd), "environment": env, "argv": argv,
                "gpu_lease_estate": str(self.gpu_lease_estate)}

    def common_env(self):
        return {"PATH": TOOL_PATH, "PYTHONPATH": "/opt/vaso/python-deps",
                "LD_LIBRARY_PATH": LIB_PATH, "CUDA_HOME": CUDA,
                "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
                "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "TRITON_PTXAS_PATH": CUDA + "/bin/ptxas",
                "C_INCLUDE_PATH": CUDA + "/include",
                "CPLUS_INCLUDE_PATH": CUDA + "/include",
                "TRITON_LIBDEVICE_PATH": CUDA + "/nvvm/libdevice/libdevice.10.bc"}

    def build_request(self, jobs):
        if not 1 <= jobs <= (os.cpu_count() or 1):
            raise ValueError("jobs exceeds available CPU count")
        w, i, v = map(self.guest, (self.work, self.inputs, self.vendor))
        env = self.common_env()
        env.update({
            "PATH": i + "/python-build-deps/bin:" + TOOL_PATH,
            "PYTHONPATH": i + "/python-build-deps:/opt/vaso/python-deps",
            "HOME": w + "/home", "TMPDIR": w + "/tmp",
            "TRITON_CACHE_DIR": w + "/build-cache", "TRITON_OFFLINE_BUILD": "1",
            "TRITON_BUILD_PROTON": "OFF",
            "LLVM_SYSPATH": i + "/" + PROFILE["llvm_prefix"], "JSON_SYSPATH": i + "/json",
            "TRITON_PTXAS_BLACKWELL_PATH": v + "/ptxas-blackwell",
            "TRITON_CUOBJDUMP_PATH": v + "/cuobjdump",
            "TRITON_NVDISASM_PATH": v + "/nvdisasm",
            "TRITON_CUDACRT_PATH": CUDA + "/include", "TRITON_CUDART_PATH": CUDA + "/include",
            "TRITON_CUPTI_INCLUDE_PATH": CUDA + "/include", "TRITON_CUPTI_LIB_PATH": CUDA + "/lib64",
            "TRITON_APPEND_CMAKE_ARGS": "-DTRITON_BUILD_UT=OFF -DTRITON_BUILD_PROTON=OFF -DZLIB_LIBRARY=/usr/lib/x86_64-linux-gnu/libz.so.1 -DZLIB_INCLUDE_DIR=/usr/include",
            "MAX_JOBS": str(jobs), "CMAKE_BUILD_PARALLEL_LEVEL": str(jobs), "CC": "gcc", "CXX": "g++"})
        return self.request(self.work / "source", env,
            ["/usr/local/bin/python3", "-m", "pip", "wheel", "-v", "--no-deps",
             "--no-build-isolation", "--no-index", "--no-cache-dir", "-w", w + "/wheelhouse", w + "/source"])

    def prepare(self, jobs):
        required = [
            self.rootfs / "opt/vaso/build-manifest.json",
            self.rootfs / "opt/vaso/cuda/bin/ptxas",
            self.rootfs / "opt/vaso/cmake/bin/cmake",
            self.rootfs / "opt/vaso/python-deps/bin/ninja",
            self.inputs / PROFILE["source_archive"],
            self.inputs / PROFILE["llvm_prefix"] / "lib/cmake/mlir/MLIRConfig.cmake",
            self.inputs / "json/include/nlohmann/json.hpp",
            self.inputs / "python-build-deps/pybind11/__init__.py",
            self.vendor / "cuobjdump", self.vendor / "nvdisasm", self.vendor / "ptxas-blackwell"]
        for p in required:
            if not p.is_file():
                raise FileNotFoundError(p)
        self.work.mkdir(parents=True, exist_ok=True)
        req = self.build_request(jobs)
        inputs = {"profile": PROFILE, "patch": (HERE / "signed-int8-dot.patch").read_text(),
                  "rootfs": str(self.rootfs), "inputs": str(self.inputs),
                  "build_environment": {k: v for k, v in req["environment"].items()
                                        if k not in ("MAX_JOBS", "CMAKE_BUILD_PARALLEL_LEVEL")}}
        marker = self.work / "inputs.json"
        source = self.work / "source"
        if marker.exists():
            if json.loads(marker.read_text()) != inputs or not source.is_dir():
                raise RuntimeError("input identity changed or source missing; select a new work_dir")
        else:
            if source.exists():
                raise RuntimeError("unowned source exists")
            archive = self.inputs / PROFILE["source_archive"]
            with archive.open("rb") as f:
                if hashlib.file_digest(f, "sha256").hexdigest() != PROFILE["source_sha256"]:
                    raise ValueError("Triton source archive checksum mismatch")
            with tempfile.TemporaryDirectory(dir=self.work) as tmp:
                with tarfile.open(archive) as f:
                    f.extractall(tmp, filter="data")
                src = Path(tmp) / ("triton-" + PROFILE["triton_commit"])
                subprocess.run(["patch", "-p1", "--batch", "--forward", "-i",
                                str(HERE / "signed-int8-dot.patch")], cwd=src, check=True)
                src.rename(source)
            atomic_json(marker, inputs)
        for n in ("home", "tmp", "build-cache", "wheelhouse", "logs"):
            (self.work / n).mkdir(exist_ok=True)
        atomic_json(self.work / "request-build.json", req)
        return req

    def qualify(self, wheel, control, timeout):
        wheel = self.scoped(wheel) if wheel else None
        if wheel is None:
            wheels = sorted((self.work / "wheelhouse").glob("triton-*.whl"))
            if len(wheels) != 1:
                raise ValueError("expected exactly one source-built wheel; use --wheel explicitly")
            wheel = wheels[0]
        if not wheel.is_file() or not wheel.name.endswith(".whl"):
            raise ValueError("wheel must name an existing wheel in the estate")
        if not control:
            status = json.loads((self.work / "status.json").read_text())
            if not status.get("wheel_built") or Path(status["wheel"]).resolve() != wheel:
                raise ValueError("production qualification requires this build's wheel")
        attempt = self.work / "qualification" / str(time.time_ns())
        attempt.mkdir(parents=True)
        g = self.guest(attempt)
        env = self.common_env()
        env.update({"HOME": g, "TMPDIR": g, "TRITON_CACHE_DIR": g + "/cache",
                    "TRITON_HOME": g, "CC": "gcc", "CXX": "g++",
                    "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"})
        # Install the exact selected wheel in a fresh destination, offline.
        req = self.request(attempt, env, ["/usr/local/bin/python3", "-m", "pip",
            "install", "--no-index", "--no-deps", "--target", g + "/installed", self.guest(wheel)])
        install_req = attempt / "install.json"
        atomic_json(install_req, req)
        rc = run_logged(REPO / "foundry/native/pytorch215/enter_insula.sh",
                        install_req, attempt / "install.log", timeout)
        if rc:
            atomic_json(attempt / "result.json", {"passed": False, "stage": "install", "exit_code": rc})
            return rc
        # Runtime vendor tools and headers are supplied by explicit CUDA12 providers.
        env.update({"LD_LIBRARY_PATH": "/run/nvidia-driver/lib:" + LIB_PATH,
                    "PYTHONPATH": g + "/installed:" + self.guest(self.torch) + ":/opt/vaso/python-deps"})
        import shutil
        shutil.copyfile(HERE / "qualification_probe.py", attempt / "probe.py")
        argv = ["/usr/local/bin/python3", g + "/probe.py", "--install", g + "/installed",
                "--output", g + "/runtime.json"]
        if control:
            argv += ["--control"]
        req = self.request(attempt, env, argv)
        gpu_req = attempt / "gpu.json"
        atomic_json(gpu_req, req)
        rc = run_logged(REPO / "foundry/native/pytorch215/gpu_qualification.sh",
                        gpu_req, attempt / "gpu.log", min(timeout, 300))
        runtime = json.loads((attempt / "runtime.json").read_text()) if (attempt / "runtime.json").exists() else {}
        passed = rc == 0 and runtime.get("passed") is True and runtime.get("control_only") == control
        atomic_json(attempt / "result.json", {"passed": passed, "control_only": control,
            "wheel": str(wheel), "exit_code": rc, "runtime": runtime})
        if not control:
            status.update(runtime_qualified=passed, qualification=str(attempt / "result.json"))
            atomic_json(self.work / "status.json", status)
        print(attempt / "result.json")
        return 0 if passed else (rc or 1)


def run_logged(wrapper, reqfile, logfile, timeout):
    with logfile.open("w") as log:
        p = subprocess.Popen(["bash", str(wrapper), str(reqfile)], stdout=log,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True)
        try:
            return p.wait(timeout=timeout)
        except BaseException as exc:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
                try:
                    p.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait()
            if isinstance(exc, subprocess.TimeoutExpired):
                return 124
            raise


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--site", required=True)
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=14400)
    ap.add_argument("--wheel", help="estate-relative wheel path")
    ap.add_argument("--control", action="store_true", help="baseline smoke test, never release qualification")
    ap.add_argument("action", choices=("prepare", "build", "status", "qualify"))
    a = ap.parse_args()
    if a.timeout <= 0:
        ap.error("--timeout must be positive")
    s = Site(a.site)
    status = s.work / "status.json"
    if a.action == "status":
        print(status.read_text() if status.exists() else '{"state":"not_started"}')
        return 0
    s.work.mkdir(parents=True, exist_ok=True)
    with (s.work / "owner.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if a.action == "qualify":
            return s.qualify(a.wheel, a.control, a.timeout)
        s.prepare(a.jobs)
        if a.action == "prepare":
            print(s.work / "request-build.json")
            return 0
        log = s.work / "logs" / ("build-" + str(time.time_ns()) + ".log")
        record = {"state": "running", "log": str(log), "owner_pid": os.getpid(),
                  "wheel_built": False, "runtime_qualified": False}
        atomic_json(status, record)
        try:
            rc = run_logged(REPO / "foundry/native/pytorch215/enter_insula.sh",
                            s.work / "request-build.json", log, a.timeout)
            wheels = sorted(s.work.joinpath("wheelhouse").glob("triton-*.whl"))
            if rc == 0 and len(wheels) != 1:
                rc = 1
            record.update(state="built" if rc == 0 else "failed", exit_code=rc,
                          wheel_built=rc == 0,
                          wheel=str(wheels[0]) if rc == 0 else None)
        except BaseException as exc:
            record.update(state="failed", error=str(exc))
            raise
        finally:
            atomic_json(status, record)
        return rc


if __name__ == "__main__":
    raise SystemExit(main())
