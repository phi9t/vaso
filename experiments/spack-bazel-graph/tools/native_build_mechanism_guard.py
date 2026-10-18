#!/usr/bin/env python3
"""Verify native repository rules carry hermetic dependency-prefix inputs.

Native providers are allowed to reproduce different Spack build mechanisms
(Autotools, CMake, Meson, Makefile, package-specific generic flows), but they
must not discover dependency prefixes from the host. This test checks the Starlark rule
and generated build script together:

* every native repository rule refuses host-root execution;
* every dependency `FOO_PREFIX` consumed by the build script is sourced from an
  explicit `foo_prefix_file` label and passed through `repository_ctx.execute`;
* the mechanism uses the expected dependency channel for that build style.

Guard-change protocol: change a guard rule only together with a
`rule_fixture("<rule-id>")` fixture in `native_build_mechanism_guard_test.py`;
the `//tools:guard_change_protocol_test` meta-test enforces that each rule ID
below has at least one test fixture.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


INSTALL_PREFIX_VARS = {"PREFIX", "DESTDIR", "VASO_PREFIX"}
NON_DEP_PREFIX_VARS = INSTALL_PREFIX_VARS | {
    "CMAKE_INSTALL_PREFIX",
    "DCMAKE_INSTALL_PREFIX",
    "PKG_CONFIG_PATH",
    "CMAKE_PREFIX_PATH",
}


@dataclass(frozen=True)
class RuleCheck:
    path: Path
    mechanism: str
    dep_vars: tuple[str, ...]
    planned_prefixes: tuple[str, ...]


@dataclass(frozen=True)
class ToolchainSetting:
    path: Path
    line: int
    variable: str
    value: str
    classification: str

    @property
    def key(self) -> str:
        return f"{_normalise_path(self.path)}:{self.variable}:{self.value}"


IDENTITY_VARS = frozenset({
    "CC",
    "CXX",
    "CPP",
    "FC",
    "F77",
    "F90",
    "F95",
    "LD",
    "AR",
    "AS",
    "NM",
    "OBJCOPY",
    "OBJDUMP",
    "RANLIB",
    "READELF",
    "STRIP",
})
CODEGEN_FLAG_VARS = frozenset({
    "CFLAGS",
    "CPPFLAGS",
    "CXXFLAGS",
    "FCFLAGS",
    "FFLAGS",
    "LDFLAGS",
    "CMAKE_ARGS",
    "CXX_FLTO_FLAGS",
    "LD_FLTO_FLAGS",
})
BAZEL_ENV_PIN_VARS = IDENTITY_VARS | CODEGEN_FLAG_VARS | {"CMAKE"}
BAZEL_CODEGEN_OPTIONS = frozenset({"copt", "cxxopt", "conlyopt", "linkopt"})
CODEGEN_VALUE_RE = re.compile(
    r"(^|\s)("
    r"-O(?:[0-3sgz]|fast)?\b|"
    r"-g(?:[0-9])?\b|"
    r"-march=[^\s]+|"
    r"-mtune=[^\s]+|"
    r"-mcpu=[^\s]+|"
    r"-m(?:avx|sse|no-|fma|arch)[^\s]*|"
    r"-f[A-Za-z0-9_=-]+|"
    r"-std=[^\s]+|"
    r"-D[A-Za-z_][A-Za-z0-9_]*(?:=[^\s]+)?"
    r")(?=$|\s)"
)
CODEGEN_HELPER_RE = re.compile(r"\b(?:spack_target_flags|spack_cflags)\b")
DEP_WIRING_RE = re.compile(
    r"("
    r"\$\{?[A-Z0-9_]+_PREFIX\}?|"
    r"\b[A-Za-z0-9_]+_PREFIX\b|"
    r"\$\{?PREFIX\}?|"
    r"\$\{?prefix_path\}?|"
    r"\bprefix_path\b|"
    r"/include\b|"
    r"/lib\b|"
    r"pkgconfig|"
    r"-I[^\s]*|"
    r"-L[^\s]*|"
    r"-Wl,-rpath"
    r")"
)
SHELL_ASSIGN_RE = re.compile(
    r'(?:(?<=^)|(?<=[\s(]))'
    r'(?:export\s+)?'
    r'(?P<var>[A-Za-z_][A-Za-z0-9_]*)'
    r'(?P<op>\+?=)'
    r'(?P<value>"(?:[^"\\]|\\.)*"|\'[^\']*\'|[^\s\\]*)'
)
QUOTED_ASSIGN_RE = re.compile(
    r'(?P<quote>["\'])'
    r'(?P<var>[A-Za-z_][A-Za-z0-9_]*)'
    r'(?P<op>\+?=)'
    r'(?P<value>.*?)'
    r'(?P=quote)'
)
UNSET_FLAG_RE = re.compile(
    r"(?:(?<=^)|(?<=[\s;]))unset\s+"
    r"(?P<var>CFLAGS|CPPFLAGS|CXXFLAGS|FCFLAGS|FFLAGS|LDFLAGS)\b"
)
CMAKE_DEFINE_RE = re.compile(
    r'["\']?-D'
    r'(?P<var>CMAKE_[A-Za-z0-9_]*(?:COMPILER|FLAGS)|CMAKE_INTERPROCEDURAL_OPTIMIZATION)'
    r'(?::[A-Za-z0-9_]+)?='
    r'(?P<value>"[^"]*"|\'[^\']*\'|[^"\'\s\\]+)'
)
BAZEL_OPTION_RE = re.compile(
    r"--(?P<option>repo_env|action_env|copt|cxxopt|conlyopt|linkopt)="
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s\\]+)"
)
STARLARK_ENV_ITEM_RE = re.compile(
    r'"(?P<var>[A-Za-z_][A-Za-z0-9_]*)"\s*:\s*(?P<value>[^,]+),?'
)
STARLARK_ENV_ASSIGN_RE = re.compile(
    r'env\["(?P<var>[A-Za-z_][A-Za-z0-9_]*)"\]\s*=\s*(?P<value>.+)'
)
ROOTFS_COMPONENT_NAME_ALIASES = {
    "cuda_toolkit": "cuda",
}
ROOTFS_NATIVE_DOWNLOAD_ATTRS = (
    "urls",
    "sha256",
    "strip_prefix",
    "patch_urls",
    "patch_sha256",
    "patch_sha256s",
    "wheel_filename",
)
ROOTFS_NATIVE_SOURCE_ATTRS = (
    "package_version",
    "header_version",
    "cuda_arch",
)
RULE_IDS = frozenset(
    (
        "autotools-pkg-config",
        "binary-archive",
        "boost-build",
        "cmake-prefix-channel",
        "configured-python-wheel-action",
        "generated-build-script",
        "makefile-dep-flags",
        "mechanism-coverage",
        "mechanism-dependency-channel",
        "meson-pinned-tools",
        "perl-pinned-invocation",
        "prefix-wiring",
        "python-bootstrap-pip",
        "python-bootstrap-tool",
        "python-pip-install",
        "python-venv",
        "python-wheel-planner",
        "repository-insula-guard",
        "rootfs-lock-boundary",
        "rootfs-toolchain-boundary",
        "sdk-boundary",
        "toolchain-setting-allowlist",
    )
)


def _extract_build_sh(text: str) -> str:
    m = re.search(r'_BUILD_SH\s*=\s*"""\\?\n(?P<body>.*?)"""', text, re.S)
    if not m:
        return ""
    return m.group("body")


def _delegated_rule_text(path: Path, text: str) -> str:
    if "rootfs_cuda_component_impl" not in text:
        return ""
    helper = path.parent.parent / "common" / "rootfs_cuda_component.bzl"
    if helper.exists():
        return helper.read_text()
    return ""


def _normalise_path(path: Path) -> str:
    text = path.as_posix()
    if text.startswith("./"):
        return text[2:]
    return text


def _normalise_value(value: str) -> str:
    value = value.strip()
    if value.endswith("\\"):
        value = value[:-1].rstrip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return re.sub(r"\s+", " ", value).strip()


def _is_cmake_compiler_var(var: str) -> bool:
    return var.startswith("CMAKE_") and var.endswith("_COMPILER")


def _is_cmake_codegen_var(var: str) -> bool:
    return (
        var.startswith("CMAKE_")
        and var.endswith("_FLAGS")
        or var == "CMAKE_INTERPROCEDURAL_OPTIMIZATION"
    )


def _classify_toolchain_setting(var: str, value: str, kind: str) -> str | None:
    if kind in BAZEL_CODEGEN_OPTIONS:
        if kind == "linkopt" and DEP_WIRING_RE.search(value) and not CODEGEN_VALUE_RE.search(value):
            return None
        return "codegen"
    if kind in {"repo_env", "action_env"} and var == "CMAKE":
        return "build-tool"
    if var in IDENTITY_VARS or _is_cmake_compiler_var(var):
        return "identity"
    if _is_cmake_codegen_var(var):
        if var == "CMAKE_INTERPROCEDURAL_OPTIMIZATION":
            return "codegen"
        if CODEGEN_VALUE_RE.search(value) or CODEGEN_HELPER_RE.search(value):
            return "codegen"
        return None
    if var in CODEGEN_FLAG_VARS:
        if value in {"", "<unset>"}:
            return "codegen"
        if CODEGEN_VALUE_RE.search(value) or CODEGEN_HELPER_RE.search(value):
            return "codegen"
        return None
    if var.endswith("_FLAGS") and (CODEGEN_VALUE_RE.search(value) or CODEGEN_HELPER_RE.search(value)):
        return "codegen"
    if CODEGEN_VALUE_RE.search(value) or CODEGEN_HELPER_RE.search(value):
        return "codegen"
    return None


def _shell_blocks(path: Path, text: str) -> list[tuple[list[str], int]]:
    if path.suffix != ".bzl":
        return [(text.splitlines(), 1)]

    blocks: list[tuple[list[str], int]] = []
    for match in re.finditer(r'(?m)^_BUILD_SH\s*=\s*"""\\?\n', text):
        end = text.find('"""', match.end())
        if end == -1:
            continue
        start_line = text[:match.end()].count("\n") + 1
        blocks.append((text[match.end():end].splitlines(), start_line))
    return blocks


def _add_toolchain_setting(
    settings: list[ToolchainSetting],
    path: Path,
    line: int,
    var: str,
    value: str,
    kind: str,
) -> None:
    value = _normalise_value(value)
    classification = _classify_toolchain_setting(var, value, kind)
    if classification is None:
        return
    settings.append(
        ToolchainSetting(
            path=path,
            line=line,
            variable=var,
            value=value,
            classification=classification,
        )
    )


def _scan_bazel_options(settings: list[ToolchainSetting], path: Path, line_no: int, line: str) -> None:
    for match in BAZEL_OPTION_RE.finditer(line):
        option = match.group("option")
        value = _normalise_value(match.group("value"))
        if option in {"repo_env", "action_env"}:
            if "=" not in value:
                continue
            var, env_value = value.split("=", 1)
            if var in BAZEL_ENV_PIN_VARS:
                _add_toolchain_setting(settings, path, line_no, var, env_value, option)
        elif option in BAZEL_CODEGEN_OPTIONS:
            _add_toolchain_setting(settings, path, line_no, "--" + option, value, option)


def _scan_cmake_defines(settings: list[ToolchainSetting], path: Path, line_no: int, line: str) -> None:
    for match in CMAKE_DEFINE_RE.finditer(line):
        _add_toolchain_setting(
            settings,
            path,
            line_no,
            match.group("var"),
            match.group("value"),
            "cmake-option",
        )


def _scan_shell_assignments(settings: list[ToolchainSetting], path: Path, line_no: int, line: str) -> None:
    occupied: list[tuple[int, int]] = []
    for match in QUOTED_ASSIGN_RE.finditer(line):
        _add_toolchain_setting(
            settings,
            path,
            line_no,
            match.group("var"),
            match.group("value"),
            "shell-assign",
        )
        occupied.append((match.start(), match.end()))

    for match in SHELL_ASSIGN_RE.finditer(line):
        if any(match.start() >= start and match.end() <= end for start, end in occupied):
            continue
        if match.start() > 0 and line[match.start() - 1] == "{":
            continue
        _add_toolchain_setting(
            settings,
            path,
            line_no,
            match.group("var"),
            match.group("value"),
            "shell-assign",
        )

    for match in UNSET_FLAG_RE.finditer(line):
        _add_toolchain_setting(
            settings,
            path,
            line_no,
            match.group("var"),
            "<unset>",
            "unset",
        )


def _is_bazel_cpp_toolchain_env(text: str, var: str, value: str) -> bool:
    return (
        var in {"CC", "CXX"}
        and value in {"cc", "cxx"}
        and "find_cpp_toolchain" in text
        and "cc_common.get_tool_for_action" in text
    )


def _scan_starlark_env(settings: list[ToolchainSetting], path: Path, text: str) -> None:
    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for pattern in (STARLARK_ENV_ASSIGN_RE, STARLARK_ENV_ITEM_RE):
            match = pattern.search(stripped)
            if not match:
                continue
            var = match.group("var")
            value = _normalise_value(match.group("value"))
            if _is_bazel_cpp_toolchain_env(text, var, value):
                continue
            _add_toolchain_setting(settings, path, line_no, var, value, "starlark-env")
            break


def find_toolchain_settings(path: Path) -> tuple[ToolchainSetting, ...]:
    text = path.read_text()
    settings: list[ToolchainSetting] = []

    for lines, start_line in _shell_blocks(path, text):
        for offset, line in enumerate(lines):
            line_no = start_line + offset
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            _scan_bazel_options(settings, path, line_no, line)
            _scan_cmake_defines(settings, path, line_no, line)
            _scan_shell_assignments(settings, path, line_no, line)

    if path.suffix == ".bzl":
        _scan_starlark_env(settings, path, text)

    return tuple(dict.fromkeys(settings))


def _read_toolchain_allowlist(path: Path) -> set[str]:
    allowlist: set[str] = set()
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key = line.split("#", 1)[0].rstrip()
        if key:
            allowlist.add(key)
    return allowlist


def check_toolchain_allowlist(files: list[Path], allowlist_path: Path) -> list[str]:
    allowlist = _read_toolchain_allowlist(allowlist_path)
    current: dict[str, ToolchainSetting] = {}
    for path in sorted(files):
        for setting in find_toolchain_settings(path):
            current.setdefault(setting.key, setting)

    errors: list[str] = []
    for key in sorted(set(current) - allowlist):
        setting = current[key]
        errors.append(
            "unlisted toolchain setting: "
            f"{key} ({setting.classification}, {setting.path}:{setting.line})"
        )
    for key in sorted(allowlist - set(current)):
        errors.append(f"stale toolchain allowlist entry: {key}")
    return errors


def _rootfs_lock_components(lock_or_components: Path | set[str]) -> set[str]:
    if isinstance(lock_or_components, set):
        return set(lock_or_components)
    data = json.loads(lock_or_components.read_text())
    components: set[str] = set()
    for line in data.get("lines", {}).values():
        for name in line.get("components", {}):
            components.add(ROOTFS_COMPONENT_NAME_ALIASES.get(name, name))
    return components


def _module_repo_call_blocks(module_text: str, repo_name: str) -> list[str]:
    pattern = re.compile(
        rf"^\s*{re.escape(repo_name)}\(\s*\n(?P<body>.*?)^\s*\)\s*$",
        re.S | re.M,
    )
    return [match.group("body") for match in pattern.finditer(module_text)]


def _normalised_repo_path(path: Path, native_dir: Path) -> str:
    try:
        return path.relative_to(native_dir.parent).as_posix()
    except ValueError:
        return path.as_posix()


def check_rootfs_lock_boundaries(
    module_path: Path,
    native_dir: Path,
    lock_or_components: Path | set[str],
) -> list[str]:
    components = _rootfs_lock_components(lock_or_components)
    module_text = module_path.read_text() if module_path.exists() else ""
    errors: list[str] = []

    for component in sorted(components):
        repo_name = f"{component}_native"
        native_component_dir = native_dir / component
        bzl_files = sorted(native_component_dir.glob("*.bzl")) if native_component_dir.exists() else []
        blocks = _module_repo_call_blocks(module_text, repo_name)

        for block in blocks:
            for attr_name in ROOTFS_NATIVE_DOWNLOAD_ATTRS:
                if re.search(rf"(?m)^\s*{re.escape(attr_name)}\s*=", block):
                    errors.append(f"{repo_name} must not declare {attr_name}")
            for attr_name in ROOTFS_NATIVE_SOURCE_ATTRS:
                if re.search(rf"(?m)^\s*{re.escape(attr_name)}\s*=", block):
                    errors.append(f"{repo_name} must not declare source-build attr {attr_name}")

        for path in bzl_files:
            text = path.read_text()
            analysis_text = text + "\n" + _delegated_rule_text(path, text)
            display = _normalised_repo_path(path, native_dir)
            if "repository_ctx.download" in analysis_text:
                errors.append(f"{display} must not call repository_ctx.download")
            for marker in ("source_build.json", "binary_archive.json"):
                if marker in analysis_text:
                    errors.append(f"{display} must not mention {marker}")
            if re.search(r'cp\s+-a\s+"\$SRC"/\.\s+"\$PREFIX"', analysis_text):
                errors.append(f"{display} must not copy source/archive payloads into PREFIX")
            result, check_errors = check(path)
            expected_mechanism = "rootfs-toolchain-boundary" if component == "llvm" else "sdk-boundary"
            if result is not None and result.mechanism != expected_mechanism:
                errors.append(f"{display} must use {expected_mechanism}, got {result.mechanism}")
            for error in check_errors:
                errors.append(f"{display}: {error}")

    return errors


def _infer_mechanism(text: str, build_sh: str) -> str:
    haystack = (text + "\n" + build_sh).lower()
    if "rootfs-toolchain-boundary" in haystack:
        return "rootfs-toolchain-boundary"
    if "sdk-boundary" in haystack:
        return "sdk-boundary"
    if "binary-archive" in haystack:
        return "binary-archive"
    if re.search(r'(^|\s)(?:"?\$PERL_PREFIX/bin/perl"?|perl)\s+Makefile\.PL\b', build_sh, re.M):
        return "perl"
    if "spack-build-system: makefile" in haystack:
        return "makefile"
    if "spack-build-system: python-bootstrap-tool" in haystack:
        return "python-bootstrap-tool"
    if (
        "bdist_wheel" in haystack
        or re.search(r'python(?:[0-9.]*)?\s+-m\s+build\s+--wheel', haystack) is not None
        or re.search(r'python(?:[0-9.]*)?\s+-m\s+pip\s+wheel\b', haystack) is not None
    ):
        return "python-wheel"
    if (
        re.search(r'\$PYTHON_PREFIX/bin/python(?:[0-9.]*|\$\{PYTHON_ABI\})?"?\s+-m\s+zipfile\b', build_sh)
        and re.search(r'\$PYTHON_VENV_PREFIX/bin/python(?:[0-9.]*|\$\{PYTHON_ABI\})?"?\s+[^"\n]*pip\b', build_sh)
        and "--prefix=" in build_sh
        and "--no-index" in build_sh
    ):
        return "python-bootstrap-pip"
    if (
        re.search(
            r'"?\$PYTHON_VENV_PREFIX/bin/python(?:[0-9.]*|\$\{PYTHON_ABI\})?"?\s+-m\s+pip\b',
            build_sh,
        )
        and "PY_PIP_PREFIX" in build_sh
        and "--prefix=" in build_sh
        and "--no-index" in build_sh
    ):
        return "python-pip-install"
    if re.search(r'\$PYTHON_PREFIX/bin/python(?:[0-9.]*|\$\{PYTHON_ABI\})?"?\s+-m\s+venv\b', build_sh):
        return "python-venv"
    if (
        re.search(r'(^|\s)(\./)?b2\b', build_sh, re.M)
        or ("bootstrap.sh" in haystack and "user-config.jam" in haystack)
    ):
        return "boost-build"
    if "spack-build-system: meson" in haystack or re.search(
        r'(?:"?\$MESON_PREFIX/bin/meson"?|\$\{MESON_PREFIX\}/bin/meson|(^|\s)meson)\s+setup\b',
        build_sh,
        re.M,
    ):
        return "meson"
    if re.search(r'(^|\s|")cmake\b|\$"?\{?cmake', build_sh.lower(), re.M):
        return "cmake"
    if re.search(
        r'(^|\n)\s*(?:[A-Za-z_][A-Za-z0-9_]*=[^\s]+\s+)*(\./|\.\./)?configure\b',
        build_sh,
        re.M,
    ):
        return "autotools"
    if re.search(r'(^|\s)make\b', build_sh, re.M):
        return "makefile"
    return "generic"


def _dep_prefix_vars(build_sh: str) -> tuple[str, ...]:
    vars_seen = set()
    for name in re.findall(r'\b[A-Z][A-Z0-9_]*_PREFIX\b', build_sh):
        if name not in NON_DEP_PREFIX_VARS:
            vars_seen.add(name)
    return tuple(sorted(vars_seen))


def _has_env_guard(text: str, build_sh: str) -> bool:
    has_repo_input = "VASO_IN_INSULA" in text and '"VASO_IN_INSULA"' in text
    starlark_guard = "repository_ctx.os.environ.get(\"VASO_IN_INSULA\")" in text
    shell_guard = "VASO_IN_INSULA" in build_sh and "!= \"1\"" in build_sh
    return has_repo_input and (starlark_guard or shell_guard)


def _has_starlark_insula_guard(text: str) -> bool:
    return "repository_ctx.os.environ.get(\"VASO_IN_INSULA\")" in text


def _attr_prefix_names(var: str) -> tuple[str, ...]:
    lower = var.lower()
    names = [lower + "_file"]
    # Rules often pass package-specific env names such as ZLIB_NG_PREFIX while
    # using the stable dependency edge name zlib_prefix_file from MODULE.bazel.
    if lower.endswith("_ng_prefix"):
        names.append(lower.replace("_ng_prefix", "_prefix") + "_file")
    return tuple(dict.fromkeys(names))


def _has_prefix_file_attr(text: str, attr_name: str) -> bool:
    pattern = (
        rf'"{re.escape(attr_name)}"\s*:\s*attr\.label\('
        rf'(?=[^)]*mandatory\s*=\s*True)'
        rf'(?=[^)]*allow_single_file\s*=\s*True)'
    )
    return re.search(pattern, text, re.S) is not None


def _reads_prefix_file(text: str, attr_name: str) -> bool:
    return (
        f"attr.{attr_name}" in text
        and (
            "repository_ctx.read" in text
            or "_read_prefix" in text
        )
    )


def _passes_prefix_env(text: str, var: str) -> bool:
    return re.search(rf'"{re.escape(var)}"\s*:', text) is not None


def _shell_checks_prefix(build_sh: str, var: str) -> bool:
    # Accept the common explicit guard forms used in these native rules:
    #   -z "${FOO_PREFIX:-}"
    #   ! -d "${FOO_PREFIX:-}/lib"
    #   ! -x "$FOO_PREFIX/bin/tool"
    #   ! -f "$FOO_PREFIX/share/file"
    return (
        re.search(rf'-z\s+"\$\{{{re.escape(var)}:-\}}"', build_sh) is not None
        or re.search(rf'!\s+-[dxfs]\s+"\$\{{?{re.escape(var)}', build_sh) is not None
        or re.search(rf'for var in .*{re.escape(var)}', build_sh) is not None
    )


def _planned_prefix_keys(text: str) -> tuple[str, ...]:
    """Find planner-style prefix inputs, e.g. PyTorch's --prefix key=path args."""
    keys = set()
    for match in re.finditer(r'for\s+key\s+in\s+\((?P<body>[^)]*)\)', text, re.S):
        body = match.group("body")
        if "prefix" not in text[match.end():match.end() + 600]:
            continue
        for key in re.findall(r'"([a-z0-9_+-]+)"', body):
            keys.add(key)
    return tuple(sorted(keys))


def _has_required_string_attr(text: str, attr_name: str) -> bool:
    pattern = (
        rf'"{re.escape(attr_name)}"\s*:\s*attr\.string\('
        rf'(?=[^)]*doc\s*=)'
    )
    return re.search(pattern, text, re.S) is not None


def _planner_validates_prefixes(text: str) -> bool:
    return (
        "preflight(" in text
        and "Path(p).is_dir()" in text
        and "prefix:" in text
    )


def _python_wheel_text(path: Path, text: str) -> str:
    plan = path.with_name("plan.py")
    if plan.exists():
        return text + "\n" + plan.read_text()
    return text


def _configured_action_driver_text(path: Path, text: str) -> str:
    driver = path.with_name("action_driver.py")
    if driver.exists():
        return driver.read_text()
    if "action_driver.py" not in text:
        return ""
    return ""


def _configured_action_prefix_keys(text: str) -> tuple[str, ...]:
    keys = {
        match.group(1)
        for match in re.finditer(r'"([a-z0-9_+-]+)_prefix_file"\s*:\s*attr\.label', text)
    }
    return tuple(sorted(keys))


def _check_configured_python_wheel_action(path: Path, text: str) -> tuple[RuleCheck, list[str]]:
    driver_text = _configured_action_driver_text(path, text)
    planned_prefixes = _configured_action_prefix_keys(text)
    if path.name == "triton_action.bzl" or "triton_action_prefix" in text:
        required_token = "build-native-triton"
    elif path.name == "jaxlib_action.bzl" or "jaxlib_action_prefix" in text:
        required_token = "build-native-llvm"
    elif path.name == "torchvision_action.bzl" or "torchvision_action_prefix" in text:
        required_token = "build-native-torchvision"
    elif path.name == "torchaudio_action.bzl" or "torchaudio_action_prefix" in text:
        required_token = "build-native-torchaudio"
    else:
        required_token = "build-native-pytorch"
    errors: list[str] = []

    if "ctx.actions.run_shell(" not in text:
        errors.append("configured python-wheel action must run through ctx.actions.run_shell")
    if "repository_rule(" in text:
        errors.append("configured python-wheel action must not be a repository_rule")
    if "use_default_shell_env = True" not in text:
        errors.append("configured python-wheel action must receive Bazel action_env from the insula wrapper")
    if '"--prefix-file"' not in text:
        errors.append("configured python-wheel action must pass dependency prefixes as --prefix-file inputs")
    if not planned_prefixes:
        errors.append("configured python-wheel action must enumerate planned prefix_file attrs")
    for key in planned_prefixes:
        if not _has_prefix_file_attr(text, key + "_prefix_file"):
            errors.append(f"configured python-wheel prefix {key!r} is not a mandatory single-file attr.label")
    for needle, label in (
        ("VASO_IN_INSULA", "VASO_IN_INSULA"),
        ("VASO_ROOTFS_BUNDLE_MANIFEST", "VASO_ROOTFS_BUNDLE_MANIFEST"),
    ):
        if needle not in driver_text:
            errors.append(f"configured python-wheel action must validate {label} in its driver")
    for token in ("--source-anchor", "--execute", required_token, "plan.py"):
        if token not in text and token not in driver_text:
            errors.append(f"configured python-wheel action is missing {token}")

    return RuleCheck(
        path=path,
        mechanism="python-wheel-action",
        dep_vars=(),
        planned_prefixes=planned_prefixes,
    ), errors


def _mechanism_dep_channel(mechanism: str, build_sh: str, dep_vars: tuple[str, ...]) -> bool:
    if not dep_vars:
        return True
    if mechanism == "autotools":
        return any(
            token in build_sh
            for token in (
                "CPPFLAGS",
                "CFLAGS",
                "LDFLAGS",
                "LIBS",
                "PKG_CONFIG_PATH",
                "--with-",
                "--enable-",
            )
        ) or any(
            # Autotools packages also consume build-tool dependencies through
            # explicit tool variables and PATH entries, e.g.
            # M4="$M4_PREFIX/bin/m4" and PATH="$DIFFUTILS_PREFIX/bin:$PATH".
            re.search(rf'\b[A-Z][A-Z0-9_]*="\${{{re.escape(var)}}}/bin/', build_sh) is not None
            or re.search(rf'\bPATH=.*\${{{re.escape(var)}}}/bin', build_sh) is not None
            for var in dep_vars
        )
    if mechanism == "cmake":
        return any(
            token in build_sh
            for token in (
                "CMAKE_PREFIX_PATH",
                "CMAKE_LIBRARY_PATH",
                "CMAKE_INCLUDE_PATH",
                "-D",
            )
        )
    if mechanism == "meson":
        return (
            "MESON_PREFIX" in dep_vars
            and "NINJA_PREFIX" in dep_vars
            and (
                "PKG_CONFIG_PATH" in build_sh
                or "CPPFLAGS" in build_sh
                or "CFLAGS" in build_sh
                or "LDFLAGS" in build_sh
                or "-D" in build_sh
            )
        )
    if mechanism == "makefile":
        return any(
            token in build_sh
            for token in (
                "CPPFLAGS",
                "CFLAGS",
                "LDFLAGS",
                "LIBS",
                "PREFIX=",
                "prefix=",
            )
        ) or bool(re.search(r'(^|\n)\s*(\./)?config\b(?=.*\s-I)(?=.*\s-L)', build_sh, re.S))
    if mechanism == "perl":
        return "PERL_PREFIX" in dep_vars and re.search(
            r'(?:"?\$PERL_PREFIX/bin/perl"?|\${PERL_PREFIX}/bin/perl)\s+Makefile\.PL\b',
            build_sh,
        ) is not None
    if mechanism == "python-wheel":
        return "build_plan.json" in build_sh or "plan.py" in build_sh
    if mechanism == "python-bootstrap-pip":
        return (
            "PYTHON_PREFIX" in dep_vars
            and "PYTHON_VENV_PREFIX" in dep_vars
            and "-m zipfile" in build_sh
            and "--prefix=" in build_sh
        )
    if mechanism == "python-pip-install":
        return (
            "PYTHON_VENV_PREFIX" in dep_vars
            and "PY_PIP_PREFIX" in dep_vars
            and "-m pip" in build_sh
            and "PYTHONPATH" in build_sh
            and "--prefix=" in build_sh
        )
    if mechanism == "python-venv":
        return "PYTHON_PREFIX" in dep_vars and re.search(
            r'\$PYTHON_PREFIX/bin/python(?:[0-9.]*|\$\{PYTHON_ABI\})?"?\s+-m\s+venv\b',
            build_sh,
        ) is not None
    if mechanism == "python-bootstrap-tool":
        return (
            "PYTHON_PREFIX" in dep_vars
            and re.search(
                r'(?:"?\$PYTHON_PREFIX/bin/python[0-9.]*"?|\$\{PYTHON_PREFIX\}/bin/python[0-9.]*)\s+configure\.py\s+--bootstrap\b',
                build_sh,
            ) is not None
            and (
                not dep_vars
                or "PATH=" in build_sh
                or all(var == "PYTHON_PREFIX" for var in dep_vars)
            )
        )
    if mechanism == "boost-build":
        return "--user-config" in build_sh and "toolset=" in build_sh
    if mechanism == "sdk-boundary":
        return "for var in" in build_sh or all(var in build_sh for var in dep_vars)
    return True


def _check_mechanism(
    mechanism: str,
    text: str,
    build_sh: str,
    dep_vars: tuple[str, ...],
    planned_prefixes: tuple[str, ...],
) -> list[str]:
    """Mechanism-specific hermetic dependency checks.

    All mechanisms share the `_PREFIX` env-var checks above. This layer verifies
    that the build mechanism also has the expected channel for dependency
    discovery: Autotools through compiler/linker/pkg-config/configure flags,
    CMake through its prefix/path/cache variables, Makefile through explicit
    make/compiler variables, and Python wheels through a structured planner.
    """
    errors: list[str] = []

    if dep_vars and not _mechanism_dep_channel(mechanism, build_sh, dep_vars):
        errors.append(
            f"{mechanism} build with deps lacks a mechanism-specific hermetic "
            "dependency channel"
        )

    if mechanism == "autotools" and dep_vars:
        if "PKG_CONFIG_PATH" in build_sh and "PKG_CONFIG=" not in build_sh:
            errors.append("autotools build sets PKG_CONFIG_PATH without pinning PKG_CONFIG")
    elif mechanism == "cmake" and dep_vars:
        if "CMAKE_PREFIX_PATH" not in build_sh and "CMAKE_LIBRARY_PATH" not in build_sh:
            errors.append("cmake build with deps must use CMAKE_PREFIX_PATH or CMAKE_LIBRARY_PATH")
    elif mechanism == "meson":
        if "MESON_PREFIX" not in dep_vars:
            errors.append("meson build must consume MESON_PREFIX from a prefix file")
        if "NINJA_PREFIX" not in dep_vars:
            errors.append("meson build must consume NINJA_PREFIX from a prefix file")
        if "PKG_CONFIG_PATH" in build_sh and "PKG_CONFIG=" not in build_sh:
            errors.append("meson build sets PKG_CONFIG_PATH without pinning PKG_CONFIG")
        meson_site_packages_paths = (
            "MESON_PREFIX}/lib/python${PYTHON_ABI}/site-packages",
            "MESON_PREFIX/lib/python${PYTHON_ABI}/site-packages",
            "MESON_PREFIX}/lib/python$PYTHON_ABI/site-packages",
            "MESON_PREFIX/lib/python$PYTHON_ABI/site-packages",
        )
        if not any(path in build_sh for path in meson_site_packages_paths):
            errors.append("meson build must expose ABI-derived MESON_PREFIX site-packages on PYTHONPATH")
        if re.search(
            r'(?:"?\$MESON_PREFIX/bin/meson"?|\$\{MESON_PREFIX\}/bin/meson)\s+setup\b',
            build_sh,
        ) is None:
            errors.append("meson build must invoke meson through MESON_PREFIX")
        if re.search(
            r'(?:"?\$NINJA_PREFIX/bin/ninja(?:-build)?"?|\$\{NINJA_PREFIX\}/bin/ninja(?:-build)?)\s+(?:-C\s+)?',
            build_sh,
        ) is None:
            errors.append("meson build must invoke ninja through NINJA_PREFIX")
        if re.search(
            r'(?:"?\$NINJA_PREFIX/bin/ninja(?:-build)?"?|\$\{NINJA_PREFIX\}/bin/ninja(?:-build)?)\s+.*\binstall\b',
            build_sh,
            re.S,
        ) is None:
            errors.append("meson build must run ninja install through NINJA_PREFIX")
        for flag in (
            "-Dprefix=",
            "-Dlibdir=",
            "-Dbuildtype=",
            "-Dstrip=",
            "-Ddefault_library=",
            "-Dwrap_mode=nodownload",
        ):
            if flag not in build_sh:
                errors.append(f"meson build must pass {flag}")
    elif mechanism == "makefile" and dep_vars:
        has_make_flags = re.search(r'\b(make|g?make)\b.*\b[A-Z][A-Z0-9_]*=', build_sh, re.S)
        has_config_flags = re.search(r'(^|\n)\s*(\./)?config\b(?=.*\s-I)(?=.*\s-L)', build_sh, re.S)
        if "make" in build_sh and not (has_make_flags or has_config_flags):
            errors.append("makefile build with deps must pass dependency flags on the make command line or package config line")
    elif mechanism == "perl":
        if "PERL_PREFIX" not in dep_vars:
            errors.append("perl build must consume PERL_PREFIX from a prefix file")
        if re.search(
            r'(?:"?\$PERL_PREFIX/bin/perl"?|\${PERL_PREFIX}/bin/perl)\s+Makefile\.PL\b',
            build_sh,
        ) is None:
            errors.append("perl build must invoke Makefile.PL through PERL_PREFIX")
        if re.search(r'\bINSTALL_BASE=(?:"\$PREFIX"|\$\{PREFIX\})', build_sh) is None:
            errors.append("perl build must pass INSTALL_BASE=\"$PREFIX\" to Makefile.PL")
        if re.search(r'\b(?:make|gmake)\b\s+install\b', build_sh) is None:
            errors.append("perl build must run make install")
    elif mechanism == "python-wheel":
        if not planned_prefixes:
            errors.append("python-wheel build must enumerate planned --prefix inputs")
        if not _planner_validates_prefixes(text):
            errors.append("python-wheel planner must preflight prefix directories")
        for key in planned_prefixes:
            if not _has_required_string_attr(text, key):
                errors.append(f"python-wheel prefix {key!r} is not declared as an attr.string")
    elif mechanism == "python-bootstrap-pip":
        if "PYTHON_PREFIX" not in dep_vars:
            errors.append("python-bootstrap-pip build must consume PYTHON_PREFIX from a prefix file")
        if "PYTHON_VENV_PREFIX" not in dep_vars:
            errors.append("python-bootstrap-pip build must consume PYTHON_VENV_PREFIX from a prefix file")
        if re.search(
            r'\$PYTHON_PREFIX/bin/python(?:[0-9.]*|\$\{PYTHON_ABI\})?"?\s+-m\s+zipfile\b',
            build_sh,
        ) is None:
            errors.append("python-bootstrap-pip build must extract wheels through PYTHON_PREFIX")
        if re.search(
            r'\$PYTHON_VENV_PREFIX/bin/python(?:[0-9.]*|\$\{PYTHON_ABI\})?"?\s+[^"\n]*pip\b',
            build_sh,
        ) is None:
            errors.append("python-bootstrap-pip build must invoke wheel pip through PYTHON_VENV_PREFIX")
        for flag in (
            "--no-input",
            "--no-cache-dir",
            "--disable-pip-version-check",
            "--no-deps",
            "--ignore-installed",
            "--no-build-isolation",
            "--no-warn-script-location",
            "--no-index",
        ):
            if flag not in build_sh:
                errors.append(f"python-bootstrap-pip build must pass {flag}")
        if "--prefix=" not in build_sh:
            errors.append("python-bootstrap-pip build must pass --prefix to pip")
        if "PYTHONHOME=" not in build_sh:
            errors.append("python-bootstrap-pip build must clear PYTHONHOME")
    elif mechanism == "python-pip-install":
        if "PYTHON_VENV_PREFIX" not in dep_vars:
            errors.append("python-pip-install build must consume PYTHON_VENV_PREFIX from a prefix file")
        if "PY_PIP_PREFIX" not in dep_vars:
            errors.append("python-pip-install build must consume PY_PIP_PREFIX from a prefix file")
        if re.search(
            r'"?\$PYTHON_VENV_PREFIX/bin/python(?:[0-9.]*|\$\{PYTHON_ABI\})?"?\s+-m\s+pip\b',
            build_sh,
        ) is None:
            errors.append("python-pip-install build must invoke pip through PYTHON_VENV_PREFIX")
        if "PYTHONPATH" not in build_sh or "PY_PIP_PREFIX" not in build_sh:
            errors.append("python-pip-install build must expose PY_PIP_PREFIX on PYTHONPATH")
        for flag in (
            "--no-input",
            "--no-cache-dir",
            "--disable-pip-version-check",
            "--no-deps",
            "--ignore-installed",
            "--no-build-isolation",
            "--no-warn-script-location",
            "--no-index",
        ):
            if flag not in build_sh:
                errors.append(f"python-pip-install build must pass {flag}")
        if "--prefix=" not in build_sh:
            errors.append("python-pip-install build must pass --prefix to pip")
        if "PYTHONHOME=" not in build_sh:
            errors.append("python-pip-install build must clear PYTHONHOME")
        if "PYTHONPATH=" not in build_sh:
            errors.append("python-pip-install build must set PYTHONPATH explicitly")
    elif mechanism == "python-venv":
        if "PYTHON_PREFIX" not in dep_vars:
            errors.append("python-venv build must consume PYTHON_PREFIX from a prefix file")
        if re.search(
            r'\$PYTHON_PREFIX/bin/python(?:[0-9.]*|\$\{PYTHON_ABI\})?"?\s+-m\s+venv\b',
            build_sh,
        ) is None:
            errors.append("python-venv build must invoke venv through PYTHON_PREFIX")
        if "--without-pip" not in build_sh:
            errors.append("python-venv build must pass --without-pip")
        if "PYTHONHOME=" not in build_sh or "PYTHONPATH=" not in build_sh:
            errors.append("python-venv build must clear PYTHONHOME and PYTHONPATH")
    elif mechanism == "python-bootstrap-tool":
        if "PYTHON_PREFIX" not in dep_vars:
            errors.append("python-bootstrap-tool build must consume PYTHON_PREFIX from a prefix file")
        if re.search(
            r'(?:"?\$PYTHON_PREFIX/bin/python[0-9.]*"?|\$\{PYTHON_PREFIX\}/bin/python[0-9.]*)\s+configure\.py\s+--bootstrap\b',
            build_sh,
        ) is None:
            errors.append("python-bootstrap-tool build must invoke configure.py through PYTHON_PREFIX")
        tool_deps = [var for var in dep_vars if var != "PYTHON_PREFIX"]
        for var in tool_deps:
            if re.search(rf'\bPATH=.*\${{{re.escape(var)}}}/bin', build_sh) is None:
                errors.append(f"python-bootstrap-tool build must expose {var}/bin on PATH")
    elif mechanism == "boost-build":
        if "--user-config" not in build_sh:
            errors.append("boost-build build must pass --user-config to b2")
        if "user-config.jam" not in build_sh or re.search(r'\busing\s+[a-z0-9_+-]+\s*:', build_sh) is None:
            errors.append("boost-build build must generate a user-config.jam with an explicit toolset")
        if "bootstrap.sh" in build_sh and "--with-toolset=" not in build_sh:
            errors.append("boost-build bootstrap must pass --with-toolset=")
        for key in ("toolset", "cxxstd", "link", "threading"):
            if re.search(rf'(^|\s){key}=', build_sh) is None:
                errors.append(f"boost-build build must pin {key}=")
        if "--layout=" not in build_sh:
            errors.append("boost-build build must pin --layout=")
    elif mechanism == "sdk-boundary":
        if "VASO_CUDA_HOME" not in build_sh:
            errors.append("sdk-boundary build must validate VASO_CUDA_HOME")
        elif not re.search(r'VASO_CUDA_HOME.*bin/nvcc|bin/nvcc.*VASO_CUDA_HOME', build_sh, re.S):
            errors.append("sdk-boundary build must validate VASO_CUDA_HOME/bin/nvcc")
        if "VASO_ROOTFS_BUNDLE_MANIFEST" not in build_sh or "rootfs-bundle.json" not in text:
            errors.append("sdk-boundary build must verify rootfs CUDA provenance")
        if dep_vars and "for var in" not in build_sh:
            errors.append("sdk-boundary build must validate installer tool dependencies")
        for expected in ("COREUTILS_PREFIX", "GZIP_PREFIX", "LIBXML2_PREFIX"):
            if expected in dep_vars and expected not in build_sh:
                errors.append(f"sdk-boundary build does not validate {expected}")
    elif mechanism == "rootfs-toolchain-boundary":
        if "ROOTFS_LLVM_HOME" not in build_sh:
            errors.append("rootfs-toolchain-boundary build must validate ROOTFS_LLVM_HOME")
        elif not re.search(r'ROOTFS_LLVM_HOME.*bin/clang|bin/clang.*ROOTFS_LLVM_HOME', build_sh, re.S):
            errors.append("rootfs-toolchain-boundary build must validate ROOTFS_LLVM_HOME/bin/clang")
        if "VASO_ROOTFS_BUNDLE_MANIFEST" not in build_sh or "rootfs-bundle.json" not in text:
            errors.append("rootfs-toolchain-boundary build must verify rootfs LLVM provenance")
        for expected in ("35901313800ea6e6cbeb9226e51c7c4b29bfc40e", "/usr/lib/llvm-23", "23.0.0git"):
            if expected not in build_sh and expected not in text:
                errors.append(f"rootfs-toolchain-boundary build must validate {expected}")
    elif mechanism == "binary-archive":
        if not re.search(r'\[\[\s+-f\s+"\$SRC/[^"]+', build_sh) or not re.search(r'\[\[\s+-e\s+"\$SRC/[^"]+', build_sh):
            errors.append("binary-archive build must validate source archive layout")
        if dep_vars and not any(
            re.search(rf'!\s+-[dxfs]\s+"\$\{{?{re.escape(var)}', build_sh) is not None
            for var in dep_vars
        ):
            errors.append("binary-archive build must validate linked dependency prefixes")
        if "cp -a" not in build_sh and "install_tree" not in build_sh:
            errors.append("binary-archive build must copy the archive payload into PREFIX")
    elif mechanism == "generic" and dep_vars:
        errors.append(
            "generic build with dependency prefixes must add a "
            "mechanism-specific hermetic dependency verifier"
        )

    return errors


def check(path: Path) -> tuple[RuleCheck | None, list[str]]:
    text = path.read_text()
    if "repository_rule(" not in text:
        if (
            "pytorch_action_prefix" in text
            or "triton_action_prefix" in text
            or "jaxlib_action_prefix" in text
            or "torchvision_action_prefix" in text
            or "torchaudio_action_prefix" in text
            or "action_driver.py" in text
        ):
            return _check_configured_python_wheel_action(path, text)
        return None, []

    analysis_text = text + "\n" + _delegated_rule_text(path, text)
    build_sh = _extract_build_sh(analysis_text)
    mechanism = _infer_mechanism(analysis_text, build_sh)
    mechanism_text = _python_wheel_text(path, analysis_text) if mechanism == "python-wheel" else analysis_text
    dep_vars = _dep_prefix_vars(build_sh)
    planned_prefixes = _planned_prefix_keys(analysis_text) if mechanism == "python-wheel" else ()
    errors: list[str] = []

    if not _has_env_guard(analysis_text, build_sh):
        errors.append("missing VASO_IN_INSULA repository env input plus shell guard")
    if mechanism == "boost-build" and not _has_starlark_insula_guard(analysis_text):
        errors.append("boost-build repository rule must check VASO_IN_INSULA before source extraction")

    if "repository_ctx.execute(" in analysis_text and not build_sh and mechanism != "python-wheel":
        errors.append("repository executes a native build without a generated build script")

    for var in dep_vars:
        attr_names = _attr_prefix_names(var)
        declared = [name for name in attr_names if _has_prefix_file_attr(analysis_text, name)]
        read = [name for name in attr_names if _reads_prefix_file(analysis_text, name)]
        if not declared:
            errors.append(
                f"{var} is not declared as mandatory attr.label "
                + " or ".join(attr_names)
            )
        if not read:
            errors.append(
                f"{var} is not read from "
                + " or ".join(f"attr.{name}" for name in attr_names)
            )
        if not _passes_prefix_env(analysis_text, var):
            errors.append(f"{var} is not passed via repository_ctx.execute(environment=...)")
        if not _shell_checks_prefix(build_sh, var):
            errors.append(f"{var} is consumed without a shell-side existence check")

    errors.extend(_check_mechanism(mechanism, mechanism_text, build_sh, dep_vars, planned_prefixes))

    return RuleCheck(
        path=path,
        mechanism=mechanism,
        dep_vars=dep_vars,
        planned_prefixes=planned_prefixes,
    ), errors


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--require-mechanisms",
        default="",
        help=(
            "Comma-separated build mechanisms that must appear at least once in "
            "the checked native rules."
        ),
    )
    ap.add_argument(
        "--toolchain-allowlist",
        type=Path,
        help=(
            "Allowlist for existing identity/codegen toolchain settings. Entries "
            "are keyed as file:variable:normalised-value; stale entries fail."
        ),
    )
    ap.add_argument(
        "--rootfs-lock",
        type=Path,
        help=(
            "CUDA ecosystem lock whose rootfs components must remain sdk-boundary "
            "native rules without repository downloads or source/archive metadata."
        ),
    )
    ap.add_argument("files", nargs="+", type=Path)
    args = ap.parse_args(argv)

    all_errors: list[str] = []
    checks: list[RuleCheck] = []
    for path in sorted(args.files):
        if path.suffix != ".bzl":
            continue
        result, errors = check(path)
        if result is None:
            continue
        checks.append(result)
        if errors:
            all_errors.append(str(path) + ":\n  " + "\n  ".join(errors))

    if args.toolchain_allowlist is not None:
        toolchain_errors = check_toolchain_allowlist(args.files, args.toolchain_allowlist)
        if toolchain_errors:
            all_errors.append(
                "toolchain ownership ratchet failed:\n  "
                + "\n  ".join(toolchain_errors)
            )

    if args.rootfs_lock is not None:
        repo_root = args.rootfs_lock.parent.parent
        boundary_errors = check_rootfs_lock_boundaries(
            repo_root / "MODULE.bazel",
            repo_root / "native",
            args.rootfs_lock,
        )
        if boundary_errors:
            all_errors.append(
                "rootfs CUDA native boundary guard failed:\n  "
                + "\n  ".join(boundary_errors)
            )

    if all_errors:
        print(
            "native build mechanism hermetic-deps guard failed:\n"
            + "\n".join(all_errors),
            file=sys.stderr,
        )
        return 1

    by_mechanism: dict[str, int] = {}
    for item in checks:
        by_mechanism[item.mechanism] = by_mechanism.get(item.mechanism, 0) + 1
        deps = ", ".join(item.dep_vars) if item.dep_vars else "no dep prefixes"
        if item.planned_prefixes:
            deps = "planned prefixes: " + ", ".join(item.planned_prefixes)
        print(f"{item.path}: {item.mechanism}: {deps}")

    required = {
        item.strip()
        for item in args.require_mechanisms.split(",")
        if item.strip()
    }
    missing = sorted(required - set(by_mechanism))
    if missing:
        print(
            "native build mechanism hermetic-deps guard failed:\n"
            "missing required mechanisms: " + ", ".join(missing),
            file=sys.stderr,
        )
        return 1

    print("mechanisms:", ", ".join(f"{k}={by_mechanism[k]}" for k in sorted(by_mechanism)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
