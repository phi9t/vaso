#!/usr/bin/env python3
"""Execute a committed native build plan inside a Bazel action."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from plan_schema import PLACEHOLDER_RE, load_schema, validate_plan as validate_plan_with_schema

def fail(message: str) -> None:
    raise SystemExit(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"{path}: invalid JSON: {exc}")
    if not isinstance(data, dict):
        fail(f"{path}: expected a JSON object")
    return data


def require_mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"plan field {name} must be an object")
    return value


def require_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        fail(f"plan field {name} must be a non-empty string")
    return value


def require_list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        fail(f"plan field {name} must be a list")
    return value


def validate_plan(plan: dict[str, Any], schema: dict[str, Any]) -> None:
    errors = validate_plan_with_schema(plan, schema)
    if errors:
        fail("; ".join(errors))

    package = require_mapping(plan["package"], "package")
    require_string(package.get("name"), "package.name")
    require_string(package.get("version"), "package.version")

    toolchain = require_mapping(plan["toolchain"], "toolchain")
    require_string(toolchain.get("cc"), "toolchain.cc")
    require_string(toolchain.get("cxx"), "toolchain.cxx")
    require_string(toolchain.get("cuda"), "toolchain.cuda")
    require_string(toolchain.get("python"), "toolchain.python")
    toolchain_env = require_mapping(toolchain.get("env"), "toolchain.env")
    for key in ("CC", "CXX", "CFLAGS", "CXXFLAGS", "CUDAFLAGS", "NVCC_FLAGS"):
        value = toolchain_env.get(key)
        if not isinstance(value, str):
            fail(f"plan field toolchain.env.{key} must be a string")
    rootfs_identity = require_mapping(toolchain.get("rootfs_identity"), "toolchain.rootfs_identity")
    require_string(rootfs_identity.get("label"), "toolchain.rootfs_identity.label")
    require_string(rootfs_identity.get("sha256"), "toolchain.rootfs_identity.sha256")

    source_names = [require_string(source.get("name"), "source.name") for source in require_list(plan["sources"], "sources")]
    if len(source_names) != len(set(source_names)):
        fail("plan sources must have unique names")
    output_kinds = {require_string(require_mapping(output, "outputs[]").get("kind"), "outputs[].kind") for output in require_list(plan["outputs"], "outputs")}
    if not output_kinds.intersection({"prefix", "wheel"}):
        fail("plan outputs must include a prefix or wheel output")
    if "nested_bazel" in plan:
        nested_bazel = require_mapping(plan["nested_bazel"], "nested_bazel")
        archives = require_list(nested_bazel.get("archives"), "nested_bazel.archives")
        if not archives:
            fail("plan nested_bazel.archives must not be empty")
        archive_names: set[str] = set()
        for archive in archives:
            archive_obj = require_mapping(archive, "nested_bazel.archives[]")
            name = require_string(archive_obj.get("name"), "nested_bazel.archives[].name")
            require_string(archive_obj.get("sha256"), "nested_bazel.archives[].sha256")
            urls = require_list(archive_obj.get("urls"), "nested_bazel.archives[].urls")
            if not all(isinstance(url, str) and url for url in urls):
                fail("plan nested_bazel.archives[].urls must be a non-empty string list")
            if name in archive_names:
                fail(f"plan nested_bazel.archives has duplicate archive name {name!r}")
            archive_names.add(name)
        for manifest_name in ("distdir_manifest", "repository_cache_manifest"):
            manifest = require_mapping(nested_bazel.get(manifest_name), f"nested_bazel.{manifest_name}")
            require_string(manifest.get("label"), f"nested_bazel.{manifest_name}.label")
            require_string(manifest.get("sha256"), f"nested_bazel.{manifest_name}.sha256")
        build_py_args = require_list(nested_bazel.get("build_py_args"), "nested_bazel.build_py_args")
        if not all(isinstance(item, str) and item for item in build_py_args):
            fail("plan nested_bazel.build_py_args must be a non-empty string list")
        if any("{nested_build_py_args}" in item for item in build_py_args):
            fail("plan nested_bazel.build_py_args must not reference {nested_build_py_args}")


def safe_extract_archive(archive: Path, destination: Path) -> Path:
    destination.mkdir(parents=True)
    root = destination.resolve()
    try:
        with tarfile.open(archive, "r:*") as tar:
            for member in tar.getmembers():
                target = (destination / member.name).resolve()
                try:
                    target.relative_to(root)
                except ValueError:
                    fail(f"source archive member escapes extraction root: {member.name}")
                if member.issym() or member.islnk():
                    link = PurePosixPath(member.linkname)
                    if link.is_absolute():
                        fail(f"source archive link escapes extraction root: {member.name}")
                    # Symlink targets resolve from the member's directory; hard-link names are
                    # archive-relative. Relative links that stay inside the root are fine
                    # (torchvision 0.29.0's gallery images are ../ symlinks).
                    base = PurePosixPath(member.name).parent if member.issym() else PurePosixPath()
                    resolved = posixpath.normpath(str(base / link))
                    if resolved == ".." or resolved.startswith("../"):
                        fail(f"source archive link escapes extraction root: {member.name}")
            tar.extractall(destination)
    except tarfile.TarError as exc:
        fail(f"failed to extract source archive {archive}: {exc}")
    return destination


def prefix_map_flags(work: Path, out: Path) -> str:
    flags: list[str] = []
    for root in (work, out):
        for flag in ("-ffile-prefix-map", "-fdebug-prefix-map", "-fmacro-prefix-map"):
            flags.append(f"{flag}={root}=.")
    return " ".join(flags)


def job_count() -> str:
    reserved = os.environ.get("VASO_ACTION_RESERVED_CPUS", "")
    if reserved:
        if not reserved.isdecimal() or int(reserved) < 1:
            fail(f"VASO_ACTION_RESERVED_CPUS must be a positive integer, got {reserved!r}")
        return reserved
    try:
        return str(len(os.sched_getaffinity(0)))
    except AttributeError:
        return str(os.cpu_count() or 1)


def cuda_home(rootfs: dict[str, Any]) -> str:
    components = rootfs.get("components")
    if isinstance(components, dict):
        cuda_toolkit = components.get("cuda_toolkit")
        if isinstance(cuda_toolkit, dict):
            install_layout = cuda_toolkit.get("install_layout")
            if isinstance(install_layout, dict) and isinstance(install_layout.get("prefix"), str):
                return install_layout["prefix"]
    return "/usr/local/cuda"


def placeholders(
    plan: dict[str, Any],
    *,
    source_archives: dict[str, Path],
    rootfs_identity: Path,
    rootfs: dict[str, Any],
    source_dirs: dict[str, Path],
    work: Path,
    out: Path,
    wheel: Path | None = None,
    nested_distdir: Path | None = None,
    nested_repository_cache: Path | None = None,
    nested_disk_cache: Path | None = None,
) -> dict[str, str]:
    determinism = require_mapping(plan["determinism"], "determinism")
    primary_source = require_string(require_list(plan["sources"], "sources")[0]["name"], "sources[0].name")
    values = {
        "src": str(source_dirs[primary_source]),
        "work": str(work),
        "out": str(out),
        "source_archive": str(source_archives[primary_source]),
        "rootfs_identity": str(rootfs_identity),
        "cuda_home": cuda_home(rootfs),
        "jobs": job_count(),
        "prefix_map_flags": prefix_map_flags(work, out),
        "seed": require_string(determinism["seed"], "determinism.seed").removeprefix("sha256:"),
        "source_date_epoch": str(determinism["source_date_epoch"]),
    }
    if wheel is not None:
        values["wheel"] = str(wheel)
    if nested_distdir is not None:
        values["nested_distdir"] = str(nested_distdir)
    if nested_repository_cache is not None:
        values["nested_repository_cache"] = str(nested_repository_cache)
    if nested_disk_cache is not None:
        values["nested_disk_cache"] = str(nested_disk_cache)
    return values


def resolve_template(
    value: str,
    values: dict[str, str],
    dep_prefixes: dict[str, str],
    source_archives: dict[str, Path],
    source_dirs: dict[str, Path],
) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name.startswith("prefix:"):
            dep = name.removeprefix("prefix:")
            if dep not in dep_prefixes:
                fail(f"plan references missing dependency prefix {dep!r}")
            return dep_prefixes[dep]
        if name.startswith("source_archive:"):
            source = name.removeprefix("source_archive:")
            if source not in source_archives:
                fail(f"plan references missing source archive {source!r}")
            return str(source_archives[source])
        if name.startswith("src:"):
            source = name.removeprefix("src:")
            if source not in source_dirs:
                fail(f"plan references missing source {source!r}")
            return str(source_dirs[source])
        if name not in values:
            fail(f"plan references unsupported placeholder {{{name}}}")
        return values[name]

    return PLACEHOLDER_RE.sub(replace, value)


def append_env_flags(env: dict[str, str], key: str, flags: str) -> None:
    existing = env.get(key, "").strip()
    env[key] = f"{existing} {flags}".strip() if existing else flags


def cuda_prefix_map_flags(work: Path, out: Path) -> str:
    flags: list[str] = []
    for root in (work, out):
        for flag in ("-ffile-prefix-map", "-fdebug-prefix-map", "-fmacro-prefix-map"):
            flags.append(f"-Xcompiler={flag}={root}=.")
    return " ".join(flags)


def command_env(
    plan: dict[str, Any],
    phase: dict[str, Any],
    values: dict[str, str],
    dep_prefixes: dict[str, str],
    source_archives: dict[str, Path],
    source_dirs: dict[str, Path],
    work: Path,
    out: Path,
) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LC_ALL": os.environ.get("LC_ALL", "C"),
        "PYTHONHASHSEED": os.environ.get("PYTHONHASHSEED", "0"),
        "SOURCE_DATE_EPOCH": values["source_date_epoch"],
    }
    # Compilers spill .s/.c temporaries into TMPDIR. The insula's /tmp is a small private
    # tmpfs (4 GiB); 96 parallel torch compile jobs filled it (post-N1-6 gate, 2026-10-07).
    # A temp dir inside the action's own work tree is on disk, fresh per action, and set at
    # run time, so it never reaches the action key.
    action_tmp = work / "tmp"
    action_tmp.mkdir(parents=True, exist_ok=True)
    for name in ("TMPDIR", "TMP", "TEMP"):
        env[name] = str(action_tmp)
    toolchain = require_mapping(plan["toolchain"], "toolchain")
    for key, value in require_mapping(toolchain["env"], "toolchain.env").items():
        env[key] = resolve_template(str(value), values, dep_prefixes, source_archives, source_dirs)
    for key, value in require_mapping(phase["env"], "phase.env").items():
        env[key] = resolve_template(value, values, dep_prefixes, source_archives, source_dirs)

    seed = values["seed"]
    compiler_flags = values["prefix_map_flags"]
    if seed:
        compiler_flags = f"{compiler_flags} -frandom-seed={seed}"
    append_env_flags(env, "CFLAGS", compiler_flags)
    append_env_flags(env, "CXXFLAGS", compiler_flags)
    nvcc_flags = cuda_prefix_map_flags(work, out)
    if seed:
        nvcc_flags = f"{nvcc_flags} --frandom-seed={seed} --objdir-as-tempdir"
    append_env_flags(env, "NVCC_FLAGS", nvcc_flags)
    append_env_flags(env, "CUDAFLAGS", cuda_prefix_map_flags(work, out))
    return env


def resolve_argv(
    phase: dict[str, Any],
    values: dict[str, str],
    dep_prefixes: dict[str, str],
    source_archives: dict[str, Path],
    source_dirs: dict[str, Path],
    nested_build_py_args: list[str] | None,
) -> list[str]:
    argv: list[str] = []
    for item in require_list(phase["argv"], "phase.argv"):
        if item == "{nested_build_py_args}":
            if nested_build_py_args is None:
                fail("plan references {nested_build_py_args} without nested_bazel.build_py_args")
            argv.extend(nested_build_py_args)
            continue
        if "{nested_build_py_args}" in item:
            fail("plan references {nested_build_py_args}; it must occupy a complete argv item")
        argv.append(resolve_template(item, values, dep_prefixes, source_archives, source_dirs))
    return argv


def parse_name_path_args(items: list[str], flag: str, *, default_name: str | None = None) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for item in items:
        if "=" in item:
            name, path_text = item.split("=", 1)
        elif default_name is not None:
            name, path_text = default_name, item
        else:
            fail(f"{flag} must be NAME=PATH, got {item!r}")
        if not name:
            fail(f"{flag} must name a plan entry")
        paths[name] = Path(path_text)
    return paths


def read_dep_prefixes(plan: dict[str, Any], items: list[str]) -> dict[str, str]:
    declared = set(require_mapping(plan["deps"], "deps"))
    paths = parse_name_path_args(items, "--dep-prefix")
    missing = sorted(declared.difference(paths))
    if missing:
        fail("missing declared dependency prefix input(s): " + ", ".join(missing))
    prefixes: dict[str, str] = {}
    for name, path in sorted(paths.items()):
        if name not in declared:
            fail(f"--dep-prefix names undeclared dependency {name!r}")
        check_path = path if path.is_absolute() else Path.cwd() / path
        if not check_path.exists():
            fail(f"dependency prefix input is missing: {path}")
        # Resolve once, at run time (the argv in the key stays execroot-relative). Through the
        # execroot, dep paths became /vaso-bazel-ob/execroot/_main/external/... and leaked into
        # torch's RUNPATH (post-N1-6 gate, 2026-10-07); the real path is the stable fixed-mount
        # /vaso-bazel-ob/external/<repo>/prefix that release repair rewrites.
        prefixes[name] = os.path.realpath(check_path)
    return prefixes


def verify_extra_inputs(plan: dict[str, Any], input_args: list[str]) -> None:
    paths = parse_name_path_args(input_args, "--input")
    for item in require_list(plan["inputs"], "inputs"):
        input_obj = require_mapping(item, "inputs[]")
        logical_path = require_string(input_obj.get("path"), "inputs[].path")
        expected_sha = require_string(input_obj.get("sha256"), "inputs[].sha256")
        path = paths.get(logical_path)
        if path is None:
            fail(f"missing declared input binding: {logical_path}")
        if not path.is_file():
            fail(f"declared input is missing: {logical_path}")
        actual_sha = sha256_file(path)
        if actual_sha != expected_sha:
            fail(f"declared input sha256 mismatch for {logical_path}: expected {expected_sha}, got {actual_sha}")


def verify_nested_cache_manifests(plan: dict[str, Any], manifest_args: list[str]) -> dict[str, Path]:
    if "nested_bazel" not in plan:
        if manifest_args:
            fail("--nested-cache-manifest was passed but the plan has no nested_bazel section")
        return {}

    nested_bazel = require_mapping(plan["nested_bazel"], "nested_bazel")
    paths = parse_name_path_args(manifest_args, "--nested-cache-manifest")
    expected_kinds = {
        "distdir": require_mapping(nested_bazel["distdir_manifest"], "nested_bazel.distdir_manifest"),
        "repository_cache": require_mapping(
            nested_bazel["repository_cache_manifest"],
            "nested_bazel.repository_cache_manifest",
        ),
    }
    missing = sorted(set(expected_kinds).difference(paths))
    if missing:
        fail("missing declared nested cache manifest input(s): " + ", ".join(missing))
    extras = sorted(set(paths).difference(expected_kinds))
    if extras:
        fail("undeclared nested cache manifest input(s): " + ", ".join(extras))

    expected_archive_shas = sorted(
        require_string(require_mapping(archive, "nested_bazel.archives[]").get("sha256"), "nested_bazel.archives[].sha256")
        for archive in require_list(nested_bazel["archives"], "nested_bazel.archives")
    )
    for kind, manifest in sorted(expected_kinds.items()):
        path = paths[kind]
        if not path.is_file():
            fail(f"nested cache manifest input is missing: {kind}={path}")
        expected_sha = require_string(manifest["sha256"], f"nested_bazel.{kind}_manifest.sha256")
        actual_sha = sha256_file(path)
        if actual_sha != expected_sha:
            fail(f"nested cache manifest sha256 mismatch for {kind}: expected {expected_sha}, got {actual_sha}")
        payload = read_json(path)
        if payload.get("schema_version") != 1:
            fail(f"nested cache manifest {kind} must have schema_version=1")
        entries = payload.get("entries")
        if not isinstance(entries, list):
            fail(f"nested cache manifest {kind} must contain an entries list")
        manifest_archive_shas = sorted(
            entry.get("expected_sha256")
            for entry in entries
            if isinstance(entry, dict) and entry.get("kind") == "archive" and isinstance(entry.get("expected_sha256"), str)
        )
        if manifest_archive_shas != expected_archive_shas:
            fail(f"nested cache manifest {kind} does not match plan nested_bazel.archives")
    return paths


def require_nested_runtime_path(plan: dict[str, Any], path: Path | None, flag: str) -> Path | None:
    if "nested_bazel" not in plan:
        if path is not None:
            fail(f"{flag} was passed but the plan has no nested_bazel section")
        return None
    if path is None:
        fail(f"plan declares nested_bazel but {flag} was not passed")
    return path


def apply_source_patches(source: dict[str, Any], src: Path, patch_paths: dict[str, Path]) -> None:
    for patch in require_list(source.get("patches"), "source.patches"):
        patch_obj = require_mapping(patch, "source.patches[]")
        logical_path = require_string(patch_obj.get("path"), "source.patches[].path")
        expected_sha = require_string(patch_obj.get("sha256"), "source.patches[].sha256")
        patch_path = patch_paths.get(logical_path)
        if patch_path is None:
            fail(f"missing declared patch input: {logical_path}")
        if not patch_path.is_file():
            fail(f"patch input is missing: {patch_path}")
        actual_sha = sha256_file(patch_path)
        if actual_sha != expected_sha:
            fail(f"patch sha256 mismatch for {logical_path}: expected {expected_sha}, got {actual_sha}")
        completed = subprocess.run(["patch", "-p1", "--input", str(patch_path.resolve())], cwd=src, check=False)
        if completed.returncode != 0:
            fail(f"failed to apply patch {logical_path}: exit code {completed.returncode}")


def run_phase(
    name: str,
    plan: dict[str, Any],
    values: dict[str, str],
    dep_prefixes: dict[str, str],
    source_archives: dict[str, Path],
    source_dirs: dict[str, Path],
    work: Path,
    out: Path,
    nested_build_py_args: list[str] | None,
) -> None:
    phase = require_mapping(plan[name], name)
    argv = resolve_argv(phase, values, dep_prefixes, source_archives, source_dirs, nested_build_py_args)
    env = command_env(plan, phase, values, dep_prefixes, source_archives, source_dirs, work, out)
    cwd = Path(resolve_template(require_string(phase["cwd"], f"{name}.cwd"), values, dep_prefixes, source_archives, source_dirs))
    if not cwd.is_dir():
        fail(f"{name} phase cwd is not a directory: {cwd}")
    completed = subprocess.run(argv, cwd=cwd, env=env, check=False)
    if completed.returncode != 0:
        fail(f"{name} phase failed with exit code {completed.returncode}: {argv!r}")


def check_live_checks(owner: str, live_checks: object) -> None:
    if not isinstance(live_checks, dict):
        fail(f"{owner} live_checks is required")
    commands = live_checks.get("commands", [])
    if not isinstance(commands, list):
        fail(f"{owner} live_checks.commands must be a list")
    if not commands:
        fail(f"{owner} live_checks.commands must not be empty")
    for command in commands:
        if not isinstance(command, dict):
            fail(f"{owner} live check must be an object")
        name = require_string(command.get("name"), f"{owner}.live_checks.commands[].name")
        argv = require_list(command.get("argv"), f"live_checks.commands[{name}].argv")
        if not all(isinstance(item, str) for item in argv) or not argv:
            fail(f"{owner} live check {name} argv must be a non-empty string list")
        expected = require_string(command.get("expect_stdout"), f"live_checks.commands[{name}].expect_stdout")
        completed = subprocess.run(argv, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        actual = completed.stdout.strip()
        if completed.returncode != 0 or actual != expected:
            fail(
                "live {} mismatch for {}: expected stdout {!r}, got {!r}, exit code {}".format(
                    owner,
                    name,
                    expected,
                    actual,
                    completed.returncode,
                )
            )


def check_live_rootfs_identity(rootfs: dict[str, Any]) -> None:
    check_live_checks("rootfs identity", rootfs.get("live_checks"))


def check_live_plan(plan: dict[str, Any]) -> None:
    check_live_checks("plan", plan.get("live_checks"))


FIXED_OUTPUT_BASE_MOUNT = "/vaso-bazel-ob"


def action_path_tokens(paths: list[Path]) -> set[str]:
    tokens: set[str] = set()
    for path in [Path.cwd(), *paths]:
        parts = path.resolve().parts
        if path.exists() or path.is_absolute():
            resolved = path.resolve()
        else:
            resolved = (Path.cwd() / path).resolve()
        parts = resolved.parts
        tokens.add(str(resolved))
        if "execroot" in parts:
            index = parts.index("execroot")
            tokens.add(str(Path(*parts[: index + 1])))
            if index > 0 and str(Path(*parts[:index])) != FIXED_OUTPUT_BASE_MOUNT:
                # The output-base root is a leak only when it varies. The fixed insula mount
                # is constant in every mode (N1-2b); build-prefix RPATHs into
                # /vaso-bazel-ob/external are rewritten by the release repair step, and the
                # relocatability guard checks shipped wheels (post-N1-3 gate, 2026-10-07).
                tokens.add(str(Path(*parts[:index])))
    tokens.update({"/vaso/lines/", "/.vaso-estate", "bazel-out/"})
    return {token for token in tokens if token and token != "/" and token != FIXED_OUTPUT_BASE_MOUNT}


def token_bytes(forbidden_tokens: set[str]) -> list[tuple[str, bytes]]:
    return [(token, token.encode("utf-8")) for token in sorted(forbidden_tokens) if token]


def scan_bytes_for_path_leaks(label: str, data: bytes, forbidden: list[tuple[str, bytes]]) -> list[str]:
    hits: list[str] = []
    for token, needle in forbidden:
        offset = 0
        while True:
            match = data.find(needle, offset)
            if match < 0:
                break
            hits.append(f"non-relocatable output leak in {label}: offset={match} token={token}")
            offset = match + 1
    return hits


def scan_zip_for_path_leaks(path: Path, forbidden: list[tuple[str, bytes]]) -> list[str]:
    hits: list[str] = []
    with zipfile.ZipFile(path) as archive:
        for info in sorted(archive.infolist(), key=lambda item: item.filename):
            member_label = f"{path}!{info.filename}"
            hits.extend(scan_bytes_for_path_leaks(member_label, info.filename.encode("utf-8"), forbidden))
            if info.is_dir():
                continue
            with archive.open(info, "r") as member:
                hits.extend(scan_bytes_for_path_leaks(member_label, member.read(), forbidden))
    return hits


def scan_file_for_path_leaks(path: Path, forbidden_tokens: set[str]) -> list[str]:
    forbidden = token_bytes(forbidden_tokens)
    if not forbidden:
        return []
    if path.is_symlink():
        target = os.readlink(path).encode("utf-8", errors="surrogateescape")
        return scan_bytes_for_path_leaks(f"symlink {path}", target, forbidden)
    if not path.is_file():
        return []
    if path.suffix in {".whl", ".zip"} and zipfile.is_zipfile(path):
        return scan_zip_for_path_leaks(path, forbidden)
    return scan_bytes_for_path_leaks(str(path), path.read_bytes(), forbidden)


def scan_output_for_path_leaks(prefix: Path, forbidden_tokens: set[str]) -> list[str]:
    forbidden = token_bytes(forbidden_tokens)
    if not forbidden:
        return []
    hits: list[str] = []
    for path in sorted(prefix.rglob("*")):
        if path.is_symlink():
            target = os.readlink(path).encode("utf-8", errors="surrogateescape")
            hits.extend(scan_bytes_for_path_leaks(f"symlink {path}", target, forbidden))
            continue
        if not path.is_file():
            continue
        if path.suffix in {".whl", ".zip"} and zipfile.is_zipfile(path):
            hits.extend(scan_zip_for_path_leaks(path, forbidden))
        else:
            hits.extend(scan_bytes_for_path_leaks(str(path), path.read_bytes(), forbidden))
    return hits


def fail_on_path_leaks(hits: list[str]) -> None:
    if hits:
        fail("non-relocatable output leak(s):\n" + "\n".join(hits))


def rewrite_pkg_config_files(prefix: Path) -> None:
    if not prefix.is_dir():
        return
    for pc_file in sorted(prefix.rglob("*.pc")):
        if not pc_file.is_file() or pc_file.is_symlink():
            continue
        rel = os.path.relpath(prefix, pc_file.parent)
        rel_posix = "." if rel == "." else Path(rel).as_posix()
        relocatable_prefix = "${pcfiledir}" if rel_posix == "." else f"${{pcfiledir}}/{rel_posix}"
        lines = pc_file.read_text(encoding="utf-8").splitlines(keepends=True)
        changed = False
        rewritten: list[str] = []
        for line in lines:
            newline = "\n" if line.endswith("\n") else ""
            body = line[:-1] if newline else line
            if body.startswith("prefix="):
                rewritten.append(f"prefix={relocatable_prefix}{newline}")
                changed = True
            else:
                rewritten.append(line)
        if changed:
            pc_file.write_text("".join(rewritten), encoding="utf-8")


def output_kinds(plan: dict[str, Any]) -> set[str]:
    return {
        require_string(require_mapping(output, "outputs[]").get("kind"), "outputs[].kind")
        for output in require_list(plan["outputs"], "outputs")
    }


def execute_plan(
    plan_path: Path,
    schema_path: Path,
    source_archive_args: list[str],
    rootfs_identity: Path,
    prefix: Path | None,
    wheel: Path | None,
    patch_args: list[str],
    dep_prefix_args: list[str],
    input_args: list[str],
    nested_cache_manifest_args: list[str],
    nested_distdir: Path | None,
    nested_repository_cache: Path | None,
    nested_disk_cache: Path | None,
) -> None:
    plan = read_json(plan_path)
    schema = load_schema(schema_path)
    validate_plan(plan, schema)
    kinds = output_kinds(plan)
    if "prefix" in kinds and prefix is None:
        fail("plan declares a prefix output but --prefix-out was not passed")
    if "wheel" in kinds and wheel is None:
        fail("plan declares a wheel output but --wheel-out was not passed")

    sources = require_list(plan["sources"], "sources")
    primary_source_name = require_string(require_mapping(sources[0], "sources[0]").get("name"), "sources[0].name")
    source_archives = parse_name_path_args(source_archive_args, "--source-archive", default_name=primary_source_name)
    for source in sources:
        source_obj = require_mapping(source, "sources[]")
        source_name = require_string(source_obj.get("name"), "sources[].name")
        archive = source_archives.get(source_name)
        if archive is None:
            fail(f"missing declared source archive input: {source_name}")
        expected_source_sha = require_string(source_obj.get("sha256"), f"sources[{source_name}].sha256")
        actual_source_sha = sha256_file(archive)
        if actual_source_sha != expected_source_sha:
            fail(f"source archive sha256 mismatch for {source_name}: expected {expected_source_sha}, got {actual_source_sha}")

    expected_rootfs_sha = require_string(
        require_mapping(require_mapping(plan["toolchain"], "toolchain")["rootfs_identity"], "toolchain.rootfs_identity")["sha256"],
        "toolchain.rootfs_identity.sha256",
    )
    actual_rootfs_sha = sha256_file(rootfs_identity)
    if actual_rootfs_sha != expected_rootfs_sha:
        fail(f"rootfs identity sha256 mismatch: expected {expected_rootfs_sha}, got {actual_rootfs_sha}")
    rootfs = read_json(rootfs_identity)
    check_live_rootfs_identity(rootfs)
    check_live_plan(plan)
    verify_extra_inputs(plan, input_args)
    nested_manifest_paths = verify_nested_cache_manifests(plan, nested_cache_manifest_args)
    nested_distdir = require_nested_runtime_path(plan, nested_distdir, "--nested-distdir")
    nested_repository_cache = require_nested_runtime_path(plan, nested_repository_cache, "--nested-repository-cache")
    nested_disk_cache = require_nested_runtime_path(plan, nested_disk_cache, "--nested-disk-cache")

    if prefix is not None:
        prefix = prefix.resolve()
    if wheel is not None:
        wheel = wheel.resolve()
        wheel.parent.mkdir(parents=True, exist_ok=True)
    if prefix is None and wheel is None:
        fail("plan declares no executable output binding; pass --prefix-out or --wheel-out")
    output_root = prefix if prefix is not None else wheel.parent
    assert output_root is not None
    work = output_root / ".work"
    if work.exists() and any(work.iterdir()):
        fail(f"refusing non-empty work dir from a previous action: {work}")
    if prefix is not None and prefix.exists():
        shutil.rmtree(prefix)
    if prefix is not None:
        prefix.mkdir(parents=True)
    else:
        output_root.mkdir(parents=True, exist_ok=True)
    work.mkdir()
    created_work = True
    try:
        patch_paths = parse_name_path_args(patch_args, "--patch")
        source_dirs: dict[str, Path] = {}
        for source in sources:
            source_obj = require_mapping(source, "sources[]")
            source_name = require_string(source_obj.get("name"), "sources[].name")
            archive = source_archives[source_name]
            extract_root = work / "extract" / source_name
            safe_extract_archive(archive, extract_root)
            strip_prefix = require_string(source_obj.get("strip_prefix"), f"sources[{source_name}].strip_prefix")
            src = extract_root / strip_prefix
            if not src.is_dir():
                fail(f"source archive {source_name} did not contain strip_prefix directory: {strip_prefix}")
            apply_source_patches(source_obj, src, patch_paths)
            source_dirs[source_name] = src

        values = placeholders(
            plan,
            source_archives={name: path.resolve() for name, path in source_archives.items()},
            rootfs_identity=rootfs_identity.resolve(),
            rootfs=rootfs,
            source_dirs=source_dirs,
            work=work,
            out=prefix or output_root,
            wheel=wheel,
            nested_distdir=nested_distdir,
            nested_repository_cache=nested_repository_cache,
            nested_disk_cache=nested_disk_cache,
        )
        print(f"plan-executor: jobs={values['jobs']}", file=sys.stderr)
        dep_prefixes = read_dep_prefixes(plan, dep_prefix_args)
        nested_build_py_args: list[str] | None = None
        if "nested_bazel" in plan:
            nested_bazel = require_mapping(plan["nested_bazel"], "nested_bazel")
            nested_build_py_args = [
                resolve_template(item, values, dep_prefixes, source_archives, source_dirs)
                for item in require_list(nested_bazel["build_py_args"], "nested_bazel.build_py_args")
            ]
        for name in ("configure", "build", "install"):
            run_phase(
                name,
                plan,
                values,
                dep_prefixes,
                source_archives,
                source_dirs,
                work,
                prefix or output_root,
                nested_build_py_args,
            )
        if work.exists():
            shutil.rmtree(work)
            created_work = False
        forbidden_paths = [plan_path, schema_path, rootfs_identity, *source_archives.values(), *nested_manifest_paths.values()]
        if prefix is not None:
            rewrite_pkg_config_files(prefix)
            forbidden_paths.append(prefix)
        if wheel is not None:
            forbidden_paths.append(wheel)
        forbidden = {str(work)}
        if prefix is not None:
            forbidden.add(str(prefix))
        if wheel is not None:
            forbidden.add(str(wheel))
        forbidden.update(action_path_tokens(forbidden_paths))
        leak_hits: list[str] = []
        if prefix is not None:
            leak_hits.extend(scan_output_for_path_leaks(prefix, forbidden))
        if wheel is not None and wheel.is_file():
            leak_hits.extend(scan_file_for_path_leaks(wheel, forbidden))
        fail_on_path_leaks(leak_hits)
    finally:
        if created_work and work.exists():
            shutil.rmtree(work)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--schema", type=Path, default=Path(__file__).resolve().parents[2] / "plans" / "schema" / "v1.json")
    parser.add_argument("--source-archive", action="append", required=True)
    parser.add_argument("--rootfs-identity", type=Path, required=True)
    parser.add_argument("--prefix-out", type=Path)
    parser.add_argument("--wheel-out", type=Path)
    parser.add_argument("--patch", action="append", default=[])
    parser.add_argument("--dep-prefix", action="append", default=[])
    parser.add_argument("--input", action="append", default=[])
    parser.add_argument("--nested-cache-manifest", action="append", default=[])
    parser.add_argument("--nested-distdir", type=Path)
    parser.add_argument("--nested-repository-cache", type=Path)
    parser.add_argument("--nested-disk-cache", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # argv lets launchers such as native/pytorch/runtime_jobs_executor.py call main() directly.
    args = parse_args(argv)
    try:
        execute_plan(
            args.plan,
            args.schema,
            args.source_archive,
            args.rootfs_identity,
            args.prefix_out,
            args.wheel_out,
            args.patch,
            args.dep_prefix,
            args.input,
            args.nested_cache_manifest,
            args.nested_distdir,
            args.nested_repository_cache,
            args.nested_disk_cache,
        )
    except SystemExit as exc:
        message = str(exc)
        if message:
            print(message, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
