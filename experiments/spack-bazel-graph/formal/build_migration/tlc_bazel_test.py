#!/usr/bin/env python3
"""Hermetic TLC driver for the build-migration formal model (good/bad pair).

Mirrors ferric_continuum/formal/distributed_training/tlc_bazel_test.py: runs the
MigrationGood / MigrationBad models with Bazel's own JDK ($(JAVA)) against the
pinned @tla2tools//jar, with no host `java` or TLA_TOOLS_JAR assumption.

Contract:
  * MigrationGood must complete cleanly (Inv + MigrationCompletes; exit 0).
  * MigrationBad must violate Inv (TopologyImmutable / NativeDownwardClosed);
    TLC exits 12 for that, which is the EXPECTED outcome, so we assert on the
    classified result, not the raw exit code.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from python.runfiles import runfiles  # rules_python runfiles library
except Exception:  # pragma: no cover - standalone (non-Bazel) invocation
    runfiles = None


def _rf():
    return runfiles.Create() if runfiles is not None else None


def _resolve_jar(jar_arg: str) -> Path:
    """Resolve the tla2tools jar. $(location @tla2tools//jar) is emitted as a
    runfiles-relative path like `+http_jar+tla2tools/jar/downloaded.jar`."""
    p = Path(jar_arg)
    if p.is_file():
        return p
    rf = _rf()
    if rf is not None:
        for key in (jar_arg, jar_arg.replace("external/", "", 1),
                    f"_main/{jar_arg}"):
            resolved = rf.Rlocation(key)
            if resolved and Path(resolved).is_file():
                return Path(resolved)
    # Fallback: glob the runfiles tree for the downloaded jar.
    here = Path(__file__).resolve().parent
    for base in (here, Path.cwd()):
        for hit in base.rglob("downloaded.jar"):
            if "tla2tools" in str(hit):
                return hit
        for hit in base.rglob("tla2tools.jar"):
            return hit
    raise SystemExit(f"could not resolve tla2tools jar: {jar_arg}")


def _find_runfile(rel: str) -> Path:
    """Locate a data dep in runfiles by trying common roots."""
    rf = _rf()
    if rf is not None:
        resolved = rf.Rlocation(f"_main/{rel}")
        if resolved and Path(resolved).exists():
            return Path(resolved)
    here = Path(__file__).resolve().parent
    candidates = [here / rel, Path.cwd() / rel]
    rd = os.environ.get("RUNFILES_DIR")
    if rd:
        candidates.append(Path(rd) / "_main" / rel)
    for c in candidates:
        if c.exists():
            return c
    for base in (here, Path.cwd()):
        for hit in base.rglob(Path(rel).name):
            return hit
    raise SystemExit(f"runfile not found: {rel}")


def _resolve_java(java_arg: str) -> Path:
    j = Path(java_arg)
    if j.is_file():
        return j
    rf = _rf()
    if rf is not None:
        for key in (java_arg, java_arg.replace("external/", "", 1)):
            resolved = rf.Rlocation(key)
            if resolved and Path(resolved).is_file():
                return Path(resolved)
    rd = os.environ.get("RUNFILES_DIR")
    for cand in ([Path(rd) / java_arg] if rd else []) + [Path.cwd() / java_arg]:
        if cand.is_file():
            return cand
    stripped = java_arg.replace("external/", "", 1)
    for base in ([Path(rd)] if rd else []) + [Path.cwd()]:
        c = base / stripped
        if c.is_file():
            return c
    raise SystemExit(f"could not resolve $(JAVA): {java_arg}")


def _classify(stdout: str, exit_code: int) -> str:
    if "Invariant" in stdout and "is violated" in stdout:
        return "invariant_violation"
    if "Error:" in stdout and "Parsing or semantic" in stdout:
        return "parse_error"
    if "No error has been found" in stdout:
        return "success"
    return "success" if exit_code == 0 else "error"


def run_model(java: Path, jar: Path, model_dir: Path, stem: str) -> tuple[int, str]:
    # jar/java must be absolute: run_model uses cwd=model_dir (a tempdir), so a
    # runfiles-relative -cp path would not resolve there.
    cmd = [str(java.resolve()), "-XX:+UseParallelGC", "-cp", str(jar.resolve()),
           "tlc2.TLC", "-config", f"{stem}.cfg", f"{stem}.tla"]
    res = subprocess.run(cmd, cwd=model_dir, capture_output=True, text=True)
    return res.returncode, res.stdout + res.stderr


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("java_bin")
    ap.add_argument("jar")
    ap.add_argument("--good", default="MigrationGood")
    ap.add_argument("--bad", default="MigrationBad")
    args = ap.parse_args(argv[1:])

    java = _resolve_java(args.java_bin)
    jar = _resolve_jar(args.jar)

    # The three .tla + two .cfg files must sit in one dir (Good/Bad INSTANCE /
    # reference the shared Migration.tla). Copy them into a temp dir.
    src = _find_runfile("formal/build_migration/Migration.tla").parent
    with tempfile.TemporaryDirectory() as tmp:
        model_dir = Path(tmp)
        for name in ("Migration.tla",
                     f"{args.good}.tla", f"{args.good}.cfg",
                     f"{args.bad}.tla", f"{args.bad}.cfg"):
            (model_dir / name).write_text((src / name).read_text())

        failures: list[str] = []

        gexit, gout = run_model(java, jar, model_dir, args.good)
        gres = _classify(gout, gexit)
        print(f"[good] exit={gexit} result={gres}")
        if not (gexit == 0 and gres == "success"):
            failures.append(f"{args.good} did not complete cleanly")
            print(gout[-3000:], file=sys.stderr)

        bexit, bout = run_model(java, jar, model_dir, args.bad)
        bres = _classify(bout, bexit)
        print(f"[bad]  exit={bexit} result={bres}")
        if bres != "invariant_violation":
            failures.append(f"{args.bad} did not surface the expected invariant violation")
            print(bout[-3000:], file=sys.stderr)

    if failures:
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        return 1
    print(f"PASS: {args.good} clean, {args.bad} invariant violation detected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
