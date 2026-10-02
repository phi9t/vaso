#!/usr/bin/env python3
"""Unit tests for ABI parity metadata normalization."""

from __future__ import annotations

import tempfile
import unittest
import importlib.util
import os
import sys
from unittest import mock
from pathlib import Path


SCRIPT = Path(__file__).with_name("abi_parity.py")
SPEC = importlib.util.spec_from_file_location("abi_parity", SCRIPT)
assert SPEC is not None
abi_parity = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = abi_parity
SPEC.loader.exec_module(abi_parity)


class AbiParityTest(unittest.TestCase):
    def test_explicit_elf_paths_compare_needed_and_symbols_outside_libdirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "reference"
            candidate = root / "candidate"
            rel = Path("lib/python3.14/site-packages/pkg/mod.cpython-314-x86_64-linux-gnu.so")
            (reference / rel).parent.mkdir(parents=True)
            (candidate / rel).parent.mkdir(parents=True)
            (reference / rel).write_bytes(b"not an elf")
            (candidate / rel).write_bytes(b"not an elf")

            result = abi_parity.diff_explicit_elfs(reference, candidate, [str(rel)])

        self.assertEqual(result["files"][0]["path"], str(rel))
        self.assertFalse(result["ok"])
        self.assertFalse(result["files"][0]["needed"]["ok"])

    def test_pkgconfig_normalizes_absolute_libdir_and_includedir_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "spack" / "openblas"
            candidate = root / "runfiles" / "prefix"
            external = root / "external" / "openblas"
            ref_pc = reference / "lib" / "pkgconfig" / "openblas.pc"
            cand_pc = candidate / "lib" / "pkgconfig" / "openblas.pc"
            ref_pc.parent.mkdir(parents=True)
            cand_pc.parent.mkdir(parents=True)
            ref_pc.write_text(
                "\n".join(
                    [
                        f"libdir={reference}/lib",
                        f"includedir={reference}/include",
                        "Name: openblas",
                        "Libs: -L${libdir} -lopenblas",
                        "Cflags: -I${includedir}",
                        "",
                    ]
                )
            )
            cand_pc.write_text(
                "\n".join(
                    [
                        f"libdir={external}/lib",
                        f"includedir={external}/include",
                        "Name: openblas",
                        "Libs: -L${libdir} -lopenblas",
                        "Cflags: -I${includedir}",
                        "",
                    ]
                )
            )

            aliases = abi_parity._prefix_aliases(candidate)
            ref_hash = abi_parity._normalized_metadata_sha256(ref_pc, reference)
            cand_hash = abi_parity._normalized_metadata_sha256(cand_pc, candidate)

        self.assertIn(str(external), aliases)
        self.assertEqual(ref_hash, cand_hash)

    def test_pkgconfig_normalizes_spaced_prefix_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "spack" / "icu4c"
            candidate = root / "runfiles" / "prefix"
            external = root / "external" / "icu4c" / "prefix"
            ref_pc = reference / "lib" / "pkgconfig" / "icu-uc.pc"
            cand_pc = candidate / "lib" / "pkgconfig" / "icu-uc.pc"
            ref_pc.parent.mkdir(parents=True)
            cand_pc.parent.mkdir(parents=True)
            ref_pc.write_text(
                "\n".join(
                    [
                        f"prefix = {reference}",
                        "exec_prefix = ${prefix}",
                        "libdir = ${exec_prefix}/lib",
                        "Name: icu-uc",
                        "",
                    ]
                )
            )
            cand_pc.write_text(
                "\n".join(
                    [
                        f"prefix = {external}",
                        "exec_prefix = ${prefix}",
                        "libdir = ${exec_prefix}/lib",
                        "Name: icu-uc",
                        "",
                    ]
                )
            )

            aliases = abi_parity._prefix_aliases(candidate)
            ref_hash = abi_parity._normalized_metadata_sha256(ref_pc, reference)
            cand_hash = abi_parity._normalized_metadata_sha256(cand_pc, candidate)

        self.assertIn(str(external), aliases)
        self.assertEqual(ref_hash, cand_hash)

    def test_share_pkgconfig_normalizes_absolute_prefix_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "spack" / "util-macros"
            candidate = root / "runfiles" / "prefix"
            external = root / "external" / "util-macros"
            ref_pc = reference / "share" / "pkgconfig" / "xorg-macros.pc"
            cand_pc = candidate / "share" / "pkgconfig" / "xorg-macros.pc"
            ref_pc.parent.mkdir(parents=True)
            cand_pc.parent.mkdir(parents=True)
            ref_pc.write_text(
                "\n".join(
                    [
                        f"prefix={reference}",
                        "exec_prefix=${prefix}",
                        "datadir=${prefix}/share",
                        "Name: X.Org Macros",
                        "Version: 1.20.2",
                        "",
                    ]
                )
            )
            cand_pc.write_text(
                "\n".join(
                    [
                        f"prefix={external}",
                        "exec_prefix=${prefix}",
                        "datadir=${prefix}/share",
                        "Name: X.Org Macros",
                        "Version: 1.20.2",
                        "",
                    ]
                )
            )

            aliases = abi_parity._prefix_aliases(candidate)
            ref_hash = abi_parity._normalized_metadata_sha256(ref_pc, reference)
            cand_hash = abi_parity._normalized_metadata_sha256(cand_pc, candidate)

        self.assertIn(str(external), aliases)
        self.assertEqual(ref_hash, cand_hash)

    def test_config_script_normalizes_prefix_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "spack" / "pcre2"
            candidate = root / "runfiles" / "prefix"
            external = root / "external" / "pcre2"
            ref_config = reference / "bin" / "pcre2-config"
            cand_config = candidate / "bin" / "pcre2-config"
            ref_config.parent.mkdir(parents=True)
            cand_config.parent.mkdir(parents=True)
            ref_config.write_text(f"#!/bin/sh\nprefix={reference}\necho $prefix\n")
            cand_config.write_text(f"#!/bin/sh\nprefix={external}\necho $prefix\n")

            ref_hash = abi_parity._normalized_metadata_sha256(ref_config, reference)
            cand_hash = abi_parity._normalized_metadata_sha256(cand_config, candidate)

        self.assertEqual(ref_hash, cand_hash)

    def test_script_normalizes_prefix_bin_path_fragment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "spack" / "mkfontscale"
            candidate = root / "runfiles" / "mkfontscale"
            ref_script = reference / "bin" / "mkfontdir"
            cand_script = candidate / "bin" / "mkfontdir"
            ref_script.parent.mkdir(parents=True)
            cand_script.parent.mkdir(parents=True)
            ref_script.write_text(
                "#!/bin/sh\n"
                f'PATH="{reference}/bin:$PATH"\n'
                'exec mkfontscale -b -s -l "$@"\n'
            )
            cand_script.write_text(
                "#!/bin/sh\n"
                f'PATH="{candidate}/bin:$PATH"\n'
                'exec mkfontscale -b -s -l "$@"\n'
            )

            result = abi_parity.diff_data_files(
                reference,
                candidate,
                ["bin/mkfontdir"],
            )

        self.assertTrue(result["ok"], result)

    def test_openssh_config_normalizes_embedded_prefix_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "spack" / "openssh"
            candidate = root / "runfiles" / "openssh"
            ref_config = reference / "etc" / "sshd_config"
            cand_config = candidate / "etc" / "sshd_config"
            ref_config.parent.mkdir(parents=True)
            cand_config.parent.mkdir(parents=True)
            ref_config.write_text(
                "\n".join(
                    [
                        f"# This sshd was compiled with PATH=/usr/bin:{reference}/bin",
                        f"#HostKey {reference}/etc/ssh_host_ed25519_key",
                        f"Subsystem\tsftp\t{reference}/libexec/sftp-server",
                        "",
                    ]
                )
            )
            cand_config.write_text(
                "\n".join(
                    [
                        f"# This sshd was compiled with PATH=/usr/bin:{candidate}/bin",
                        f"#HostKey {candidate}/etc/ssh_host_ed25519_key",
                        f"Subsystem\tsftp\t{candidate}/libexec/sftp-server",
                        "",
                    ]
                )
            )

            result = abi_parity.diff_data_files(
                reference,
                candidate,
                ["etc/sshd_config"],
            )

        self.assertTrue(result["ok"], result)

    def test_fontconfig_fonts_conf_normalizes_prefix_and_font_dir_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "spack" / "fontconfig"
            candidate = root / "runfiles" / "fontconfig"
            ref_font_util = root / "spack" / "font-util"
            cand_font_util = root / "runfiles" / "font-util"
            ref_config = reference / "etc" / "fonts" / "fonts.conf"
            cand_config = candidate / "etc" / "fonts" / "fonts.conf"
            ref_config.parent.mkdir(parents=True)
            cand_config.parent.mkdir(parents=True)
            ref_config.write_text(
                "\n".join(
                    [
                        "<fontconfig>",
                        f"  <dir>{ref_font_util}/share/fonts</dir>",
                        "  <dir>/usr/share/fonts</dir>",
                        f"  <cachedir>{reference}/var/cache/fontconfig</cachedir>",
                        "</fontconfig>",
                        "",
                    ]
                )
            )
            cand_config.write_text(
                "\n".join(
                    [
                        "<fontconfig>",
                        f"  <dir>{cand_font_util}/share/fonts</dir>",
                        "  <dir>/usr/share/fonts</dir>",
                        f"  <cachedir>{candidate}/var/cache/fontconfig</cachedir>",
                        "</fontconfig>",
                        "",
                    ]
                )
            )

            result = abi_parity.diff_data_files(
                reference,
                candidate,
                ["etc/fonts/fonts.conf"],
                reference_link_prefixes=[ref_font_util],
                candidate_link_prefixes=[cand_font_util],
            )

        self.assertTrue(result["ok"], result)

    def test_data_path_accepts_matching_empty_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "spack" / "openssh"
            candidate = root / "runfiles" / "openssh"
            (reference / "var" / "empty").mkdir(parents=True)
            (candidate / "var" / "empty").mkdir(parents=True)

            result = abi_parity.diff_data_files(
                reference,
                candidate,
                ["var/empty"],
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["files"][0]["kind"], "directory")

    def test_link_and_run_appends_explicit_link_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "reference"
            candidate = root / "candidate"
            consumer = root / "use_static.c"
            for prefix in (reference, candidate):
                (prefix / "include").mkdir(parents=True)
                (prefix / "lib").mkdir()
            consumer.write_text("int main(void) { return 0; }\n")
            seen_cmds = []

            def fake_run(cmd):
                seen_cmds.append(cmd)
                class Result:
                    returncode = 0
                    stdout = "ok\n"
                    stderr = ""
                return Result()

            with mock.patch.object(abi_parity, "_run", side_effect=fake_run):
                result = abi_parity.link_and_run(
                    consumer,
                    reference,
                    candidate,
                    ["pthreadpool"],
                    ["include"],
                    ["-pthread"],
                    [],
                    [],
                )

        self.assertTrue(result["ok"], result)
        compile_cmds = [cmd for cmd in seen_cmds if str(consumer) in cmd]
        self.assertEqual(len(compile_cmds), 2)
        for cmd in compile_cmds:
            self.assertIn("-lpthreadpool", cmd)
            self.assertIn("-pthread", cmd)
            self.assertGreater(cmd.index("-pthread"), cmd.index("-lpthreadpool"))

    def test_link_and_run_excludes_link_prefix_headers_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "reference"
            candidate = root / "candidate"
            ref_dep = root / "ref-dep"
            cand_dep = root / "cand-dep"
            consumer = root / "use_dep.c"
            for prefix in (reference, candidate, ref_dep, cand_dep):
                (prefix / "include").mkdir(parents=True)
                (prefix / "lib").mkdir()
            consumer.write_text("int main(void) { return 0; }\n")
            seen_cmds = []

            def fake_run(cmd):
                seen_cmds.append(cmd)

                class Result:
                    returncode = 0
                    stdout = "ok\n"
                    stderr = ""

                return Result()

            with mock.patch.object(abi_parity, "_run", side_effect=fake_run):
                result = abi_parity.link_and_run(
                    consumer,
                    reference,
                    candidate,
                    ["example"],
                    [],
                    [],
                    [ref_dep],
                    [cand_dep],
                )

        self.assertTrue(result["ok"], result)
        compile_cmds = [cmd for cmd in seen_cmds if str(consumer) in cmd]
        self.assertEqual(len(compile_cmds), 2)
        self.assertIn(str(ref_dep / "lib"), compile_cmds[0])
        self.assertIn(str(cand_dep / "lib"), compile_cmds[1])
        self.assertNotIn(str(ref_dep / "include"), compile_cmds[0])
        self.assertNotIn(str(cand_dep / "include"), compile_cmds[1])

    def test_link_and_run_can_include_link_prefix_headers_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "reference"
            candidate = root / "candidate"
            ref_dep = root / "ref-dep"
            cand_dep = root / "cand-dep"
            consumer = root / "use_dep.c"
            for prefix in (reference, candidate, ref_dep, cand_dep):
                (prefix / "include").mkdir(parents=True)
                (prefix / "lib").mkdir()
            consumer.write_text("int main(void) { return 0; }\n")
            seen_cmds = []

            def fake_run(cmd):
                seen_cmds.append(cmd)

                class Result:
                    returncode = 0
                    stdout = "ok\n"
                    stderr = ""

                return Result()

            with mock.patch.object(abi_parity, "_run", side_effect=fake_run):
                result = abi_parity.link_and_run(
                    consumer,
                    reference,
                    candidate,
                    ["example"],
                    [],
                    [],
                    [ref_dep],
                    [cand_dep],
                    include_link_prefix_headers=True,
                )

        self.assertTrue(result["ok"], result)
        compile_cmds = [cmd for cmd in seen_cmds if str(consumer) in cmd]
        self.assertEqual(len(compile_cmds), 2)
        self.assertIn(str(ref_dep / "include"), compile_cmds[0])
        self.assertIn(str(cand_dep / "include"), compile_cmds[1])

    def test_link_and_run_can_limit_link_prefix_headers_to_top_include(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMPDIR")) as tmp:
            root = Path(tmp)
            reference = root / "reference"
            candidate = root / "candidate"
            ref_dep = root / "ref-dep"
            cand_dep = root / "cand-dep"
            consumer = root / "use_dep.cc"
            for prefix in (reference, candidate, ref_dep, cand_dep):
                (prefix / "include" / "cuda").mkdir(parents=True)
                (prefix / "lib").mkdir()
            consumer.write_text("int main() { return 0; }\n")
            seen_cmds = []

            def fake_run(cmd):
                seen_cmds.append(cmd)

                class Result:
                    returncode = 0
                    stdout = "ok\n"
                    stderr = ""

                return Result()

            with mock.patch.object(abi_parity, "_run", side_effect=fake_run):
                result = abi_parity.link_and_run(
                    consumer,
                    reference,
                    candidate,
                    ["example"],
                    [],
                    [],
                    [ref_dep],
                    [cand_dep],
                    include_link_prefix_headers=True,
                    link_prefix_top_include_only=True,
                )

        self.assertTrue(result["ok"], result)
        compile_cmds = [cmd for cmd in seen_cmds if str(consumer) in cmd]
        self.assertEqual(len(compile_cmds), 2)
        self.assertIn(str(ref_dep / "include"), compile_cmds[0])
        self.assertIn(str(cand_dep / "include"), compile_cmds[1])
        self.assertNotIn(str(ref_dep / "include" / "cuda"), compile_cmds[0])
        self.assertNotIn(str(cand_dep / "include" / "cuda"), compile_cmds[1])

    def test_data_path_normalizes_symlink_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "spack" / "python-venv"
            candidate = root / "runfiles" / "python_venv"
            ref_python = root / "spack" / "python"
            cand_python = root / "runfiles" / "python"
            ref_link = reference / "bin" / "python3.14"
            cand_link = candidate / "bin" / "python3.14"
            ref_link.parent.mkdir(parents=True)
            cand_link.parent.mkdir(parents=True)
            ref_link.symlink_to(ref_python / "bin" / "python3.14")
            cand_link.symlink_to(cand_python / "bin" / "python3.14")

            result = abi_parity.diff_data_files(
                reference,
                candidate,
                ["bin/python3.14"],
                reference_link_prefixes=[ref_python],
                candidate_link_prefixes=[cand_python],
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["files"][0]["kind"], "symlink")

    def test_pyvenv_cfg_normalizes_venv_and_python_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "spack" / "python-venv"
            candidate = root / "runfiles" / "python_venv"
            ref_python = root / "spack" / "python"
            cand_python = root / "runfiles" / "python"
            ref_cfg = reference / "pyvenv.cfg"
            cand_cfg = candidate / "pyvenv.cfg"
            ref_cfg.parent.mkdir(parents=True)
            cand_cfg.parent.mkdir(parents=True)
            ref_cfg.write_text(
                "\n".join(
                    [
                        f"home = {ref_python}/bin",
                        "include-system-site-packages = false",
                        "version = 3.14.5",
                        f"executable = {ref_python}/bin/python3.14",
                        f"command = {ref_python}/bin/python3.14 -m venv --without-pip {reference}",
                        "",
                    ]
                )
            )
            cand_cfg.write_text(
                "\n".join(
                    [
                        f"home = {cand_python}/bin",
                        "include-system-site-packages = false",
                        "version = 3.14.5",
                        f"executable = {cand_python}/bin/python3.14",
                        f"command = {cand_python}/bin/python3.14 -m venv --without-pip {candidate}",
                        "",
                    ]
                )
            )

            result = abi_parity.diff_data_files(
                reference,
                candidate,
                ["pyvenv.cfg"],
                reference_link_prefixes=[ref_python],
                candidate_link_prefixes=[cand_python],
            )

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["files"][0]["prefix_normalized"]["ok"])

    def test_python_venv_activation_scripts_normalize_prompt_basename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "spack" / "python-venv-1.0-abc123"
            candidate = root / "external" / "python_venv_native" / "prefix"
            ref_activate = reference / "bin" / "activate"
            cand_activate = candidate / "bin" / "activate"
            ref_activate.parent.mkdir(parents=True)
            cand_activate.parent.mkdir(parents=True)
            (reference / "pyvenv.cfg").write_text("version = 3.14.5\n")
            (candidate / "pyvenv.cfg").write_text("version = 3.14.5\n")
            ref_activate.write_text(
                f"export VIRTUAL_ENV={reference}\n"
                "VIRTUAL_ENV_PROMPT=python-venv-1.0-abc123\n"
                "PS1=\"(\"python-venv-1.0-abc123\") ${PS1:-}\"\n"
                '        printf "%s(%s)%s " (set_color 4B8BBE) python-venv-1.0-abc123 (set_color normal)\n'
            )
            cand_activate.write_text(
                f"export VIRTUAL_ENV={candidate}\n"
                "VIRTUAL_ENV_PROMPT=prefix\n"
                "PS1=\"(\"prefix\") ${PS1:-}\"\n"
                '        printf "%s(%s)%s " (set_color 4B8BBE) prefix (set_color normal)\n'
            )

            result = abi_parity.diff_data_files(
                reference,
                candidate,
                ["bin/activate"],
            )

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["files"][0]["prefix_normalized"]["ok"])

    def test_runfiles_symlink_to_external_symlink_compares_package_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "spack" / "flex"
            external = root / "external" / "flex_native" / "prefix"
            candidate = root / "runfiles" / "flex_native" / "prefix"
            ref_lib = reference / "lib"
            external_lib = external / "lib"
            cand_lib = candidate / "lib"
            ref_lib.mkdir(parents=True)
            external_lib.mkdir(parents=True)
            cand_lib.mkdir(parents=True)
            (ref_lib / "libfl.a").write_bytes(b"archive")
            (external_lib / "libfl.a").write_bytes(b"archive")
            (cand_lib / "libfl.a").write_bytes(b"archive")
            (ref_lib / "libl.a").symlink_to("libfl.a")
            (external_lib / "libl.a").symlink_to("libfl.a")
            (cand_lib / "libl.a").symlink_to(external_lib / "libl.a")

            result = abi_parity.diff_data_files(
                reference,
                candidate,
                ["lib/libl.a"],
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["files"][0]["reference_target"], "libfl.a")
        self.assertEqual(result["files"][0]["candidate_target"], "libfl.a")

    def test_config_script_normalizes_spack_compiler_wrapper_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "spack" / "curl"
            candidate = root / "runfiles" / "curl"
            wrapper = (
                root
                / "stage"
                / "spack-stage-curl"
                / "spack-src"
                / "spack"
                / "compiler-wrapper-12345"
                / "libexec"
                / "spack"
                / "gcc"
                / "gcc"
            )
            ref_config = reference / "bin" / "curl-config"
            cand_config = candidate / "bin" / "curl-config"
            ref_config.parent.mkdir(parents=True)
            cand_config.parent.mkdir(parents=True)
            ref_config.write_text(
                "\n".join(
                    [
                        "#!/bin/sh",
                        f'prefix="{reference}"',
                        f'CC="{wrapper}"',
                        'echo "$CC"',
                        "",
                    ]
                )
            )
            cand_config.write_text(
                "\n".join(
                    [
                        "#!/bin/sh",
                        f'prefix="{candidate}"',
                        'CC="gcc"',
                        'echo "$CC"',
                        "",
                    ]
                )
            )

            ref_hash = abi_parity._normalized_metadata_sha256(ref_config, reference)
            cand_hash = abi_parity._normalized_metadata_sha256(cand_config, candidate)

        self.assertEqual(ref_hash, cand_hash)

    def test_icu_generated_inc_metadata_normalizes_prefix_and_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "spack" / "icu4c"
            candidate = root / "runfiles" / "prefix"
            wrapper = (
                root
                / "spack"
                / "compiler-wrapper-1.1.0-abc"
                / "libexec"
                / "spack"
                / "gcc"
                / "gcc"
            )
            ref_inc = reference / "lib" / "icu" / "pkgdata.inc"
            cand_inc = candidate / "lib" / "icu" / "pkgdata.inc"
            ref_inc.parent.mkdir(parents=True)
            cand_inc.parent.mkdir(parents=True)
            ref_inc.write_text(
                "\n".join(
                    [
                        f"COMPILE={wrapper} -O2 -std=c11 -c",
                        f"LIBFLAGS=-I{reference}/include -DPIC -fPIC",
                        "",
                    ]
                )
            )
            cand_inc.write_text(
                "\n".join(
                    [
                        "COMPILE=gcc -O2 -std=c11 -c",
                        f"LIBFLAGS=-I{candidate}/include -DPIC -fPIC",
                        "",
                    ]
                )
            )

            result = abi_parity.diff_data_files(
                reference,
                candidate,
                ["lib/icu/pkgdata.inc"],
            )

        self.assertTrue(result["ok"], result)

    def test_icu_generated_inc_metadata_normalizes_command_spacing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "spack" / "icu4c"
            candidate = root / "runfiles" / "prefix"
            wrapper = (
                root
                / "spack"
                / "compiler-wrapper-1.1.0-abc"
                / "libexec"
                / "spack"
                / "gcc"
                / "gcc"
            )
            ref_inc = reference / "lib" / "icu" / "pkgdata.inc"
            cand_inc = candidate / "lib" / "icu" / "pkgdata.inc"
            ref_inc.parent.mkdir(parents=True)
            cand_inc.parent.mkdir(parents=True)
            ref_inc.write_text(
                "\n".join(
                    [
                        f"COMPILE={wrapper} -D_REENTRANT  -O2 -std=c11   -c",
                        f"GENLIB={wrapper} -O2 -std=c11    -shared -Wl,-Bsymbolic",
                        "",
                    ]
                )
            )
            cand_inc.write_text(
                "\n".join(
                    [
                        "COMPILE=gcc -D_REENTRANT  -O2 -std=c11 -c",
                        "GENLIB=gcc -O2 -std=c11  -shared -Wl,-Bsymbolic",
                        "",
                    ]
                )
            )

            result = abi_parity.diff_data_files(
                reference,
                candidate,
                ["lib/icu/pkgdata.inc"],
            )

        self.assertTrue(result["ok"], result)

    def test_pkgconfig_normalizes_explicit_dependency_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "spack" / "hwloc"
            candidate = root / "runfiles" / "prefix"
            ref_xml = root / "spack" / "libxml2"
            cand_xml = root / "external" / "libxml2"
            cand_xml_runfiles = root / "runfiles" / "libxml2"
            ref_pci = root / "spack" / "libpciaccess"
            cand_pci = root / "external" / "libpciaccess"
            cand_pci_runfiles = root / "runfiles" / "libpciaccess"
            ref_pc = reference / "lib" / "pkgconfig" / "hwloc.pc"
            cand_pc = candidate / "lib" / "pkgconfig" / "hwloc.pc"
            cand_xml_pc = cand_xml_runfiles / "lib" / "pkgconfig" / "libxml-2.0.pc"
            cand_pci_pc = cand_pci_runfiles / "lib" / "pkgconfig" / "pciaccess.pc"
            ref_pc.parent.mkdir(parents=True)
            cand_pc.parent.mkdir(parents=True)
            cand_xml_pc.parent.mkdir(parents=True)
            cand_pci_pc.parent.mkdir(parents=True)
            ref_pc.write_text(
                "\n".join(
                    [
                        f"prefix={reference}",
                        "libdir=${prefix}/lib",
                        "Name: hwloc",
                        f"Libs.private: -L{ref_xml}/lib -lxml2 -L{ref_pci}/lib -lpciaccess",
                        "",
                    ]
                )
            )
            cand_pc.write_text(
                "\n".join(
                    [
                        f"prefix={candidate}",
                        "libdir=${prefix}/lib",
                        "Name: hwloc",
                        f"Libs.private: -L{cand_xml}/lib -lxml2 -L{cand_pci}/lib -lpciaccess",
                        "",
                    ]
                )
            )
            cand_xml_pc.write_text(f"prefix={cand_xml}\nlibdir=${{prefix}}/lib\n")
            cand_pci_pc.write_text(f"prefix={cand_pci}\nlibdir=${{prefix}}/lib\n")

            ref_hash = abi_parity._normalized_metadata_sha256(
                ref_pc, reference, extra_prefixes=[ref_xml, ref_pci])
            cand_hash = abi_parity._normalized_metadata_sha256(
                cand_pc,
                candidate,
                extra_prefixes=[cand_xml_runfiles, cand_pci_runfiles],
            )

        self.assertEqual(ref_hash, cand_hash)

    def test_explicit_script_data_path_normalizes_prefix_and_dependency_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "spack" / "autoconf"
            candidate_external = root / "external" / "autoconf"
            candidate = root / "runfiles" / "autoconf"
            ref_perl = root / "spack" / "perl"
            cand_perl_external = root / "external" / "perl"
            cand_perl = root / "runfiles" / "perl"
            ref_m4 = root / "spack" / "m4"
            cand_m4_external = root / "external" / "m4"
            cand_m4 = root / "runfiles" / "m4"
            ref_script = reference / "bin" / "autoconf"
            cand_script = candidate_external / "bin" / "autoconf"
            ref_script.parent.mkdir(parents=True)
            cand_script.parent.mkdir(parents=True)
            (cand_perl_external / "bin").mkdir(parents=True)
            (cand_m4_external / "lib" / "pkgconfig").mkdir(parents=True)
            (cand_perl_external / "bin" / "perl").write_text("#!/bin/sh\n")
            (cand_m4_external / "lib" / "pkgconfig" / "m4.pc").write_text(
                f"prefix={cand_m4_external}\nlibdir=${{prefix}}/lib\n"
            )
            candidate.parent.mkdir(parents=True, exist_ok=True)
            cand_perl.parent.mkdir(parents=True, exist_ok=True)
            cand_m4.parent.mkdir(parents=True, exist_ok=True)
            candidate.symlink_to(candidate_external, target_is_directory=True)
            cand_perl.symlink_to(cand_perl_external, target_is_directory=True)
            cand_m4.symlink_to(cand_m4_external, target_is_directory=True)
            ref_script.write_text(
                "\n".join(
                    [
                        f"#! {ref_perl}/bin/perl",
                        f"my $pkgdatadir = '{reference}/share/autoconf';",
                        f"my $m4 = '{ref_m4}/bin/m4';",
                        "",
                    ]
                )
            )
            cand_script.write_text(
                "\n".join(
                    [
                        f"#! {cand_perl_external}/bin/perl",
                        f"my $pkgdatadir = '{candidate}/share/autoconf';",
                        f"my $m4 = '{cand_m4_external}/bin/m4';",
                        "",
                    ]
                )
            )

            result = abi_parity.diff_data_files(
                reference,
                candidate,
                ["bin/autoconf"],
                reference_link_prefixes=[ref_perl, ref_m4],
                candidate_link_prefixes=[cand_perl, cand_m4],
            )

        self.assertTrue(result["ok"], result)

    def test_explicit_script_data_path_normalizes_embedded_prefix_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "spack" / "autoconf"
            candidate_external = root / "external" / "autoconf"
            candidate = root / "runfiles" / "autoconf"
            ref_script = reference / "bin" / "autoconf"
            cand_script = candidate / "bin" / "autoconf"
            ref_script.parent.mkdir(parents=True)
            cand_script.parent.mkdir(parents=True)
            ref_script.write_text(
                "\n".join(
                    [
                        "#! /usr/bin/env perl",
                        f"my $autom4te = '{reference}/bin/autom4te';",
                        f"my $pkgdatadir = '{reference}/share/autoconf';",
                        "",
                    ]
                )
            )
            cand_script.write_text(
                "\n".join(
                    [
                        "#! /usr/bin/env perl",
                        f"my $autom4te = '{candidate_external}/bin/autom4te';",
                        f"my $pkgdatadir = '{candidate_external}/share/autoconf';",
                        "",
                    ]
                )
            )

            result = abi_parity.diff_data_files(
                reference,
                candidate,
                ["bin/autoconf"],
            )

        self.assertTrue(result["ok"], result)

    def test_explicit_perl_module_data_path_normalizes_embedded_prefix_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "spack" / "automake"
            candidate_external = root / "external" / "automake"
            candidate = root / "runfiles" / "automake"
            ref_module = reference / "share" / "automake-1.18" / "Automake" / "Config.pm"
            cand_module = candidate / "share" / "automake-1.18" / "Automake" / "Config.pm"
            ref_module.parent.mkdir(parents=True)
            cand_module.parent.mkdir(parents=True)
            ref_module.write_text(
                f"our $libdir = $ENV{{\"AUTOMAKE_LIBDIR\"}} || '{reference}/share/automake-1.18';\n"
            )
            cand_module.write_text(
                f"our $libdir = $ENV{{\"AUTOMAKE_LIBDIR\"}} || '{candidate_external}/share/automake-1.18';\n"
            )

            result = abi_parity.diff_data_files(
                reference,
                candidate,
                ["share/automake-1.18/Automake/Config.pm"],
            )

        self.assertTrue(result["ok"], result)

    def test_luarocks_config_data_path_normalizes_embedded_prefix_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "spack" / "lua"
            candidate_external = root / "external" / "lua"
            candidate = root / "runfiles" / "lua"
            ref_config = reference / "etc" / "luarocks" / "config-5.3.lua"
            cand_config = candidate / "etc" / "luarocks" / "config-5.3.lua"
            ref_config.parent.mkdir(parents=True)
            cand_config.parent.mkdir(parents=True)
            ref_config.write_text(
                "\n".join(
                    [
                        "rocks_trees = {",
                        f'   {{ name = "system", root = "{reference}" }};',
                        "}",
                        "variables = {",
                        f'   LUA_DIR = "{reference}";',
                        f'   LUA_BINDIR = "{reference}/bin";',
                        f'   LUA = "{reference}/bin/lua";',
                        "}",
                        "",
                    ]
                )
            )
            cand_config.write_text(
                "\n".join(
                    [
                        "rocks_trees = {",
                        f'   {{ name = "system", root = "{candidate_external}" }};',
                        "}",
                        "variables = {",
                        f'   LUA_DIR = "{candidate_external}";',
                        f'   LUA_BINDIR = "{candidate_external}/bin";',
                        f'   LUA = "{candidate_external}/bin/lua";',
                        "}",
                        "",
                    ]
                )
            )

            result = abi_parity.diff_data_files(
                reference,
                candidate,
                ["etc/luarocks/config-5.3.lua"],
            )

        self.assertTrue(result["ok"], result)

    def test_manpage_data_path_normalizes_embedded_prefix_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "spack" / "font-util"
            candidate_external = root / "external" / "font-util"
            candidate = root / "runfiles" / "font-util"
            ref_man = reference / "share" / "man" / "man1" / "ucs2any.1"
            cand_man = candidate / "share" / "man" / "man1" / "ucs2any.1"
            ref_man.parent.mkdir(parents=True)
            cand_man.parent.mkdir(parents=True)
            ref_man.write_text(
                f".I {reference}/share/fonts/X11/util\n"
                "directory.\n"
            )
            cand_man.write_text(
                f".I {candidate_external}/share/fonts/X11/util\n"
                "directory.\n"
            )

            result = abi_parity.diff_data_files(
                reference,
                candidate,
                ["share/man/man1/ucs2any.1"],
            )

        self.assertTrue(result["ok"], result)

    def test_zsh_completion_data_path_normalizes_embedded_prefix_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "spack" / "git"
            candidate_external = root / "external" / "git"
            candidate = root / "runfiles" / "git"
            ref_completion = reference / "share" / "zsh" / "site-functions" / "_git"
            cand_completion = candidate / "share" / "zsh" / "site-functions" / "_git"
            ref_completion.parent.mkdir(parents=True)
            cand_completion.parent.mkdir(parents=True)
            ref_completion.write_text(
                "\n".join(
                    [
                        "locations=(",
                        f'  "{reference}/share/bash-completion/completions/git"',
                        ")",
                        "",
                    ]
                )
            )
            cand_completion.write_text(
                "\n".join(
                    [
                        "locations=(",
                        f'  "{candidate_external}/share/bash-completion/completions/git"',
                        ")",
                        "",
                    ]
                )
            )

            result = abi_parity.diff_data_files(
                reference,
                candidate,
                ["share/zsh/site-functions/_git"],
            )

        self.assertTrue(result["ok"], result)

    def test_exec_test_can_run_from_temp_project_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "reference"
            candidate = root / "candidate"
            ref_dep = root / "ref-dep"
            cand_dep = root / "cand-dep"
            for prefix, text in ((reference, "reference"), (candidate, "candidate")):
                tool = prefix / "bin" / "need-configure"
                tool.parent.mkdir(parents=True)
                tool.write_text(
                    "#!/usr/bin/env bash\n"
                    "set -euo pipefail\n"
                    "test -f configure.ac\n"
                    "helper\n"
                    "echo ok\n"
                )
                tool.chmod(0o755)
            for prefix in (ref_dep, cand_dep):
                helper = prefix / "bin" / "helper"
                helper.parent.mkdir(parents=True)
                helper.write_text("#!/usr/bin/env bash\nexit 0\n")
                helper.chmod(0o755)

            result = abi_parity.exec_and_compare(
                reference,
                candidate,
                [
                    '{"argv":["bin/need-configure"],'
                    '"cwd":"tmp",'
                    '"files":{"configure.ac":"AC_INIT([x], [1])\\n"},'
                    '"returncodes":[0]}'
                ],
                [ref_dep],
                [cand_dep],
            )

        self.assertTrue(result["ok"], result)

    def test_exec_test_setup_runs_before_main_command_in_same_temp_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "reference"
            candidate = root / "candidate"
            for prefix in (reference, candidate):
                setup = prefix / "bin" / "write-marker"
                main = prefix / "bin" / "check-marker"
                setup.parent.mkdir(parents=True)
                setup.write_text("#!/usr/bin/env bash\nprintf marker > generated.txt\n")
                setup.chmod(0o755)
                main.write_text("#!/usr/bin/env bash\ngrep -q marker generated.txt\n")
                main.chmod(0o755)

            result = abi_parity.exec_and_compare(
                reference,
                candidate,
                [
                    '{"argv":["bin/check-marker"],'
                    '"cwd":"tmp",'
                    '"setup":[["bin/write-marker"]],'
                    '"returncodes":[0]}'
                ],
                [],
                [],
            )

        self.assertTrue(result["ok"], result)

    def test_exec_test_compares_declared_output_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "reference"
            candidate = root / "candidate"
            for prefix, payload in ((reference, "same"), (candidate, "different")):
                tool = prefix / "bin" / "write-output"
                tool.parent.mkdir(parents=True)
                tool.write_text(
                    "#!/usr/bin/env bash\n"
                    "set -euo pipefail\n"
                    "printf '" + payload + "' > \"$1\"\n"
                )
                tool.chmod(0o755)

            result = abi_parity.exec_and_compare(
                reference,
                candidate,
                [
                    '{"argv":["bin/write-output","{out}"],'
                    '"outputs":["out"],'
                    '"returncodes":[0]}'
                ],
                [],
                [],
            )

        self.assertFalse(result["ok"], result)
        entry = result["tests"][0]
        self.assertFalse(entry["outputs_match"], entry)
        self.assertEqual(entry["reference"]["outputs"]["out"]["size"], 4)
        self.assertEqual(entry["candidate"]["outputs"]["out"]["size"], 9)

    def test_exec_test_adds_python_package_prefix_to_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "reference"
            candidate = root / "candidate"
            for prefix in (reference, candidate):
                package = prefix / "lib" / "python3.14" / "site-packages" / "pkgmarker"
                package.mkdir(parents=True)
                (package / "__init__.py").write_text('VALUE = "from-prefix"\n')
                tool = prefix / "bin" / "show-pkgmarker"
                tool.parent.mkdir(parents=True)
                tool.write_text(
                    "#!/usr/bin/env python3\n"
                    "import pkgmarker\n"
                    "print(pkgmarker.VALUE)\n"
                )
                tool.chmod(0o755)

            result = abi_parity.exec_and_compare(
                reference,
                candidate,
                ['{"argv":["bin/show-pkgmarker"],"returncodes":[0]}'],
                [],
                [],
            )

        self.assertTrue(result["ok"], result)

    def test_exec_test_creates_declared_dirs_before_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "reference"
            candidate = root / "candidate"
            for prefix in (reference, candidate):
                tool = prefix / "bin" / "write-index"
                tool.parent.mkdir(parents=True)
                tool.write_text(
                    "#!/usr/bin/env bash\n"
                    "set -euo pipefail\n"
                    "test -d \"$1\"\n"
                    "printf '0\\n' > \"$1/index.txt\"\n"
                )
                tool.chmod(0o755)

            result = abi_parity.exec_and_compare(
                reference,
                candidate,
                [
                    '{"argv":["bin/write-index","{fontdir}"],'
                    '"dirs":["fontdir"],'
                    '"outputs":["fontdir/index.txt"],'
                    '"returncodes":[0]}'
                ],
                [],
                [],
            )

        self.assertTrue(result["ok"], result)
        entry = result["tests"][0]
        self.assertTrue(entry["outputs_match"], entry)
        self.assertEqual(entry["reference"]["outputs"]["fontdir/index.txt"]["size"], 2)

    def test_exec_test_normalizes_prefix_and_compiler_wrapper_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "spack" / "swig"
            candidate_external = root / "external" / "swig"
            candidate = root / "runfiles" / "swig"
            wrapper = (
                root
                / "spack"
                / "compiler-wrapper-1.1.0-abc"
                / "libexec"
                / "spack"
                / "gcc"
                / "g++"
            )
            ref_tool = reference / "bin" / "swig"
            cand_tool = candidate_external / "bin" / "swig"
            ref_tool.parent.mkdir(parents=True)
            cand_tool.parent.mkdir(parents=True)
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.symlink_to(candidate_external, target_is_directory=True)
            ref_tool.write_text(
                "#!/usr/bin/env bash\n"
                f"printf 'Compiled with {wrapper} [x86_64-pc-linux-gnu]\\n'\n"
                f"printf '{reference}/share/swig/4.4.1\\n'\n"
            )
            cand_tool.write_text(
                "#!/usr/bin/env bash\n"
                "printf 'Compiled with g++ [x86_64-pc-linux-gnu]\\n'\n"
                f"printf '{candidate_external}/share/swig/4.4.1\\n'\n"
            )
            ref_tool.chmod(0o755)
            cand_tool.chmod(0o755)

            result = abi_parity.exec_and_compare(
                reference,
                candidate,
                ['{"argv":["bin/swig"],"returncodes":[0]}'],
                [],
                [],
            )

        self.assertTrue(result["ok"], result)


if __name__ == "__main__":
    unittest.main()
