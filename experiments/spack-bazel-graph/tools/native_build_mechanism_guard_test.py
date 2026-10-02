#!/usr/bin/env python3
"""Regression tests for native_build_mechanism_guard.py."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).with_name("native_build_mechanism_guard.py")
SPEC = importlib.util.spec_from_file_location("native_build_mechanism_guard", SCRIPT)
assert SPEC is not None
guard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)


def rule_text(build_sh: str, attrs: str = "", env: str = "") -> str:
    return textwrap.dedent(
        f'''
        _BUILD_SH = """
        if [[ "${{VASO_IN_INSULA:-0}}" != "1" ]]; then
          exit 2
        fi
        {textwrap.dedent(build_sh).strip()}
        """

        def _impl(repository_ctx):
            if repository_ctx.os.environ.get("VASO_IN_INSULA") != "1":
                fail("native build must run inside insula")
            repository_ctx.file("build.sh", _BUILD_SH)
            repository_ctx.execute(["bash", "build.sh"], environment={{
                {env}
                "VASO_IN_INSULA": "1",
            }})

        native = repository_rule(
            implementation = _impl,
            environ = ["VASO_IN_INSULA"],
            attrs = {{
                {attrs}
            }},
        )
        '''
    )


def dep_rule_text(build_sh: str, var: str, attr: str) -> str:
    return rule_text(
        build_sh,
        attrs=f'"{attr}": attr.label(mandatory = True, allow_single_file = True),',
        env=f'"{var}": repository_ctx.read(repository_ctx.attr.{attr}).strip(),',
    )


def scratch_parent() -> str | None:
    return os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT") or str(Path.cwd())


def rule_fixture(rule_id: str, test_method: str) -> tuple[str, str]:
    assert rule_id in guard.RULE_IDS
    return rule_id, test_method


RULE_FIXTURES = dict(
    (
        rule_fixture("autotools-pkg-config", "test_autotools_requires_pinned_pkg_config_when_using_pkg_config_path"),
        rule_fixture("binary-archive", "test_binary_archive_requires_archive_layout_and_dependency_validation"),
        rule_fixture("boost-build", "test_boost_build_requires_hermetic_user_config_channel"),
        rule_fixture("cmake-prefix-channel", "test_cmake_requires_cmake_prefix_channel_for_deps"),
        rule_fixture("configured-python-wheel-action", "test_configured_python_wheel_action_requires_driver_hermetic_checks"),
        rule_fixture("generated-build-script", "test_repository_execute_requires_generated_build_script"),
        rule_fixture("makefile-dep-flags", "test_makefile_requires_make_command_line_dep_flags"),
        rule_fixture("mechanism-coverage", "test_cli_can_require_mechanism_coverage"),
        rule_fixture("mechanism-dependency-channel", "test_generic_rejects_dependency_prefixes_without_mechanism_verifier"),
        rule_fixture("meson-pinned-tools", "test_meson_requires_spack_standard_args_and_pinned_tools"),
        rule_fixture("perl-pinned-invocation", "test_perl_requires_prefix_pinned_makefile_pl"),
        rule_fixture("prefix-wiring", "test_prefix_wiring_requires_attr_read_env_and_shell_check"),
        rule_fixture("python-bootstrap-pip", "test_python_bootstrap_pip_requires_offline_flags_and_prefixes"),
        rule_fixture("python-bootstrap-tool", "test_python_bootstrap_tool_requires_pinned_python_invocation"),
        rule_fixture("python-pip-install", "test_python_pip_install_requires_pythonpath"),
        rule_fixture("python-venv", "test_python_venv_requires_clean_env_and_without_pip"),
        rule_fixture("python-wheel-planner", "test_python_wheel_requires_planned_prefix_interface"),
        rule_fixture("repository-insula-guard", "test_repository_rule_requires_insula_env_guard"),
        rule_fixture("rootfs-lock-boundary", "test_rootfs_lock_component_rejects_module_download_parameters"),
        rule_fixture("rootfs-toolchain-boundary", "test_rootfs_toolchain_boundary_requires_llvm_home_and_manifest_provenance"),
        rule_fixture("sdk-boundary", "test_sdk_boundary_requires_rootfs_sdk_and_tool_prefix_checks"),
        rule_fixture("toolchain-setting-allowlist", "test_toolchain_guard_rejects_unlisted_compiler_identity"),
    )
)


class NativeBuildMechanismGuardTest(unittest.TestCase):
    def test_rule_fixture_manifest_matches_guard_rule_ids(self) -> None:
        self.assertEqual(set(RULE_FIXTURES), guard.RULE_IDS)
        missing = sorted(name for name in RULE_FIXTURES.values() if not hasattr(self, name))
        self.assertEqual(missing, [])

    def check_text(self, text: str, filename: str = "pkg.bzl") -> tuple[guard.RuleCheck | None, list[str]]:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            path = Path(tmp) / filename
            path.write_text(text)
            return guard.check(path)

    def assert_clean_mechanism(self, text: str, mechanism: str) -> None:
        result, errors = self.check_text(text)
        self.assertEqual(errors, [])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, mechanism)

    def test_autotools_requires_pinned_pkg_config_when_using_pkg_config_path(self) -> None:
        text = dep_rule_text(
            """
            [[ -z "${FOO_PREFIX:-}" ]] && exit 1
            export PKG_CONFIG_PATH="$FOO_PREFIX/lib/pkgconfig"
            ./configure --prefix="$PREFIX"
            """,
            "FOO_PREFIX",
            "foo_prefix_file",
        )

        result, errors = self.check_text(text)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "autotools")
        self.assertIn("autotools build sets PKG_CONFIG_PATH without pinning PKG_CONFIG", errors)

    def test_repository_rule_requires_insula_env_guard(self) -> None:
        text = textwrap.dedent(
            '''
            _BUILD_SH = """
            ./configure --prefix="$PREFIX"
            """

            def _impl(repository_ctx):
                repository_ctx.file("build.sh", _BUILD_SH)
                repository_ctx.execute(["bash", "build.sh"], environment={})

            native = repository_rule(implementation = _impl)
            '''
        )

        result, errors = self.check_text(text)

        self.assertIsNotNone(result)
        self.assertIn("missing VASO_IN_INSULA repository env input plus shell guard", errors)

    def test_autotools_accepts_configure_flags_from_prefix_file(self) -> None:
        self.assert_clean_mechanism(
            dep_rule_text(
                """
                [[ -z "${FOO_PREFIX:-}" ]] && exit 1
                export PKG_CONFIG="$FOO_PREFIX/bin/pkgconf"
                export PKG_CONFIG_PATH="$FOO_PREFIX/lib/pkgconfig"
                export CPPFLAGS="-I$FOO_PREFIX/include"
                export LDFLAGS="-L$FOO_PREFIX/lib"
                ./configure --prefix="$PREFIX" --with-foo="$FOO_PREFIX"
                """,
                "FOO_PREFIX",
                "foo_prefix_file",
            ),
            "autotools",
        )

    def test_autotools_classification_ignores_docstring_cmake_mentions(self) -> None:
        text = (
            '"""Recipe supports CMake too, but this native rule uses Autotools."""\n'
            + dep_rule_text(
                """
                [[ -z "${FOO_PREFIX:-}" ]] && exit 1
                export PKG_CONFIG="$FOO_PREFIX/bin/pkgconf"
                export PKG_CONFIG_PATH="$FOO_PREFIX/lib/pkgconfig"
                export CPPFLAGS="-I$FOO_PREFIX/include"
                export LDFLAGS="-L$FOO_PREFIX/lib"
                ./configure --prefix="$PREFIX" --with-foo="$FOO_PREFIX"
                """,
                "FOO_PREFIX",
                "foo_prefix_file",
            )
        )

        result, errors = self.check_text(text)

        self.assertEqual(errors, [])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "autotools")

    def test_autotools_accepts_pinned_tool_prefix_channel(self) -> None:
        self.assert_clean_mechanism(
            dep_rule_text(
                """
                [[ -z "${FOO_PREFIX:-}" || ! -x "${FOO_PREFIX:-}/bin/foo-tool" ]] && exit 1
                export PATH="${FOO_PREFIX}/bin:${PATH}"
                export FOO_TOOL="${FOO_PREFIX}/bin/foo-tool"
                ./configure --prefix="$PREFIX"
                """,
                "FOO_PREFIX",
                "foo_prefix_file",
            ),
            "autotools",
        )

    def test_autotools_detects_env_prefixed_configure_invocation(self) -> None:
        self.assert_clean_mechanism(
            dep_rule_text(
                """
                [[ -z "${FOO_PREFIX:-}" ]] && exit 1
                export PKG_CONFIG="$FOO_PREFIX/bin/pkgconf"
                export PKG_CONFIG_PATH="$FOO_PREFIX/lib/pkgconfig"
                enable_symbol_hiding=no ./configure --prefix="$PREFIX" --with-foo="$FOO_PREFIX"
                make
                """,
                "FOO_PREFIX",
                "foo_prefix_file",
            ),
            "autotools",
        )

    def test_cmake_requires_cmake_prefix_channel_for_deps(self) -> None:
        text = dep_rule_text(
            """
            [[ -z "${FOO_PREFIX:-}" ]] && exit 1
            cmake -S . -B build -DFOO_ROOT="$FOO_PREFIX"
            cmake --build build
            """,
            "FOO_PREFIX",
            "foo_prefix_file",
        )

        result, errors = self.check_text(text)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "cmake")
        self.assertIn("cmake build with deps must use CMAKE_PREFIX_PATH or CMAKE_LIBRARY_PATH", errors)

    def test_cmake_accepts_cmake_prefix_path(self) -> None:
        self.assert_clean_mechanism(
            dep_rule_text(
                """
                [[ -z "${FOO_PREFIX:-}" ]] && exit 1
                cmake -S . -B build -DCMAKE_PREFIX_PATH="$FOO_PREFIX"
                cmake --build build
                """,
                "FOO_PREFIX",
                "foo_prefix_file",
            ),
            "cmake",
        )

    def test_meson_accepts_pinned_meson_ninja_pkgconf_channel(self) -> None:
        self.assert_clean_mechanism(
            rule_text(
                """
                # spack-build-system: meson
                [[ -z "${PYTHON_ABI:-}" ]] && exit 1
                [[ -z "${MESON_PREFIX:-}" || ! -x "${MESON_PREFIX:-}/bin/meson" ]] && exit 1
                [[ -z "${NINJA_PREFIX:-}" || ! -x "${NINJA_PREFIX:-}/bin/ninja" ]] && exit 1
                [[ -z "${PKGCONF_PREFIX:-}" || ! -x "${PKGCONF_PREFIX:-}/bin/pkgconf" ]] && exit 1
                [[ -z "${FOO_PREFIX:-}" || ! -d "${FOO_PREFIX:-}/lib/pkgconfig" ]] && exit 1
                export PATH="${MESON_PREFIX}/bin:${NINJA_PREFIX}/bin:${PKGCONF_PREFIX}/bin:${PATH}"
                export PYTHONPATH="${MESON_PREFIX}/lib/python${PYTHON_ABI}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
                export PKG_CONFIG="${PKGCONF_PREFIX}/bin/pkgconf"
                export PKG_CONFIG_PATH="${FOO_PREFIX}/lib/pkgconfig"
                export CPPFLAGS="-I${FOO_PREFIX}/include"
                export LDFLAGS="-L${FOO_PREFIX}/lib -Wl,-rpath,${FOO_PREFIX}/lib"
                "$MESON_PREFIX/bin/meson" setup "$PWD/build" "$SRC" \
                  -Dprefix="$PREFIX" \
                  -Dlibdir="$PREFIX/lib" \
                  -Dbuildtype=release \
                  -Dstrip=false \
                  -Ddefault_library=shared \
                  -Dwrap_mode=nodownload
                "$NINJA_PREFIX/bin/ninja" -C "$PWD/build" -v
                "$NINJA_PREFIX/bin/ninja" -C "$PWD/build" install
                """,
                attrs="""
                "meson_prefix_file": attr.label(mandatory = True, allow_single_file = True),
                "ninja_prefix_file": attr.label(mandatory = True, allow_single_file = True),
                "pkgconf_prefix_file": attr.label(mandatory = True, allow_single_file = True),
                "foo_prefix_file": attr.label(mandatory = True, allow_single_file = True),
                """,
                env="""
                "PYTHON_ABI": "3.13",
                "MESON_PREFIX": repository_ctx.read(repository_ctx.attr.meson_prefix_file).strip(),
                "NINJA_PREFIX": repository_ctx.read(repository_ctx.attr.ninja_prefix_file).strip(),
                "PKGCONF_PREFIX": repository_ctx.read(repository_ctx.attr.pkgconf_prefix_file).strip(),
                "FOO_PREFIX": repository_ctx.read(repository_ctx.attr.foo_prefix_file).strip(),
                """,
            ),
            "meson",
        )

    def test_meson_rejects_hard_coded_python_abi(self) -> None:
        text = rule_text(
            """
            # spack-build-system: meson
            [[ -z "${MESON_PREFIX:-}" || ! -x "${MESON_PREFIX:-}/bin/meson" ]] && exit 1
            [[ -z "${NINJA_PREFIX:-}" || ! -x "${NINJA_PREFIX:-}/bin/ninja" ]] && exit 1
            export PATH="${MESON_PREFIX}/bin:${NINJA_PREFIX}/bin:${PATH}"
            export PYTHONPATH="${MESON_PREFIX}/lib/python3.14/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
            "$MESON_PREFIX/bin/meson" setup "$PWD/build" "$SRC" \
              -Dprefix="$PREFIX" \
              -Dlibdir="$PREFIX/lib" \
              -Dbuildtype=release \
              -Dstrip=false \
              -Ddefault_library=shared \
              -Dwrap_mode=nodownload
            "$NINJA_PREFIX/bin/ninja" -C "$PWD/build" -v
            "$NINJA_PREFIX/bin/ninja" -C "$PWD/build" install
            """,
            attrs="""
            "meson_prefix_file": attr.label(mandatory = True, allow_single_file = True),
            "ninja_prefix_file": attr.label(mandatory = True, allow_single_file = True),
            """,
            env="""
            "MESON_PREFIX": repository_ctx.read(repository_ctx.attr.meson_prefix_file).strip(),
            "NINJA_PREFIX": repository_ctx.read(repository_ctx.attr.ninja_prefix_file).strip(),
            """,
        )

        result, errors = self.check_text(text)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "meson")
        self.assertIn("meson build must expose ABI-derived MESON_PREFIX site-packages on PYTHONPATH", errors)

    def test_meson_accepts_abi_derived_meson_pythonpath(self) -> None:
        self.assert_clean_mechanism(
            rule_text(
                """
                # spack-build-system: meson
                [[ -z "${PYTHON_ABI:-}" ]] && exit 1
                [[ -z "${MESON_PREFIX:-}" || ! -x "${MESON_PREFIX:-}/bin/meson" ]] && exit 1
                [[ -z "${NINJA_PREFIX:-}" || ! -x "${NINJA_PREFIX:-}/bin/ninja" ]] && exit 1
                export PATH="${MESON_PREFIX}/bin:${NINJA_PREFIX}/bin:${PATH}"
                export PYTHONPATH="${MESON_PREFIX}/lib/python${PYTHON_ABI}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
                "$MESON_PREFIX/bin/meson" setup "$PWD/build" "$SRC" \
                  -Dprefix="$PREFIX" \
                  -Dlibdir="$PREFIX/lib" \
                  -Dbuildtype=release \
                  -Dstrip=false \
                  -Ddefault_library=shared \
                  -Dwrap_mode=nodownload
                "$NINJA_PREFIX/bin/ninja" -C "$PWD/build" -v
                "$NINJA_PREFIX/bin/ninja" -C "$PWD/build" install
                """,
                attrs="""
                "meson_prefix_file": attr.label(mandatory = True, allow_single_file = True),
                "ninja_prefix_file": attr.label(mandatory = True, allow_single_file = True),
                """,
                env="""
                "PYTHON_ABI": "3.13",
                "MESON_PREFIX": repository_ctx.read(repository_ctx.attr.meson_prefix_file).strip(),
                "NINJA_PREFIX": repository_ctx.read(repository_ctx.attr.ninja_prefix_file).strip(),
                """,
            ),
            "meson",
        )

    def test_meson_requires_spack_standard_args_and_pinned_tools(self) -> None:
        text = rule_text(
            """
            # spack-build-system: meson
            [[ -z "${MESON_PREFIX:-}" ]] && exit 1
            meson setup "$PWD/build" "$SRC" -Dprefix="$PREFIX"
            ninja -C "$PWD/build" install
            """,
            attrs="""
            "meson_prefix_file": attr.label(mandatory = True, allow_single_file = True),
            """,
            env='"MESON_PREFIX": repository_ctx.read(repository_ctx.attr.meson_prefix_file).strip(),',
        )

        result, errors = self.check_text(text)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "meson")
        joined = "\n".join(errors)
        self.assertIn("meson build must consume NINJA_PREFIX from a prefix file", joined)
        self.assertIn("meson build must invoke meson through MESON_PREFIX", joined)
        self.assertIn("meson build must expose ABI-derived MESON_PREFIX site-packages on PYTHONPATH", joined)
        self.assertIn("meson build must invoke ninja through NINJA_PREFIX", joined)
        self.assertIn("meson build must pass -Dwrap_mode=nodownload", joined)

    def test_makefile_requires_make_command_line_dep_flags(self) -> None:
        text = dep_rule_text(
            """
            [[ -z "${FOO_PREFIX:-}" ]] && exit 1
            make
            """,
            "FOO_PREFIX",
            "foo_prefix_file",
        )

        result, errors = self.check_text(text)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "makefile")
        self.assertIn("makefile build with deps must pass dependency flags", "\n".join(errors))

    def test_makefile_accepts_explicit_dependency_flags(self) -> None:
        self.assert_clean_mechanism(
            dep_rule_text(
                """
                [[ -z "${FOO_PREFIX:-}" ]] && exit 1
                make CPPFLAGS="-I$FOO_PREFIX/include" LDFLAGS="-L$FOO_PREFIX/lib"
                """,
                "FOO_PREFIX",
                "foo_prefix_file",
            ),
            "makefile",
        )

    def test_makefile_marker_wins_over_nested_resource_configure(self) -> None:
        self.assert_clean_mechanism(
            dep_rule_text(
                """
                # spack-build-system: makefile
                [[ -z "${FOO_PREFIX:-}" ]] && exit 1
                make MYLDFLAGS="-L$FOO_PREFIX/lib" linux
                make INSTALL_TOP="$PREFIX" install
                cd resource
                ./configure --prefix="$PREFIX"
                make install
                """,
                "FOO_PREFIX",
                "foo_prefix_file",
            ),
            "makefile",
        )

    def test_python_bootstrap_tool_accepts_pinned_tool_prefixes(self) -> None:
        self.assert_clean_mechanism(
            rule_text(
                """
                # spack-build-system: python-bootstrap-tool
                [[ -z "${PYTHON_PREFIX:-}" || ! -x "${PYTHON_PREFIX:-}/bin/python3" ]] && exit 1
                [[ -z "${RE2C_PREFIX:-}" || ! -x "${RE2C_PREFIX:-}/bin/re2c" ]] && exit 1
                export PATH="${PYTHON_PREFIX}/bin:${RE2C_PREFIX}/bin:${PATH}"
                "$PYTHON_PREFIX/bin/python3" configure.py --bootstrap
                install -D tool "$PREFIX/bin/tool"
                """,
                attrs="""
                "python_prefix_file": attr.label(mandatory = True, allow_single_file = True),
                "re2c_prefix_file": attr.label(mandatory = True, allow_single_file = True),
                """,
                env="""
                "PYTHON_PREFIX": repository_ctx.read(repository_ctx.attr.python_prefix_file).strip(),
                "RE2C_PREFIX": repository_ctx.read(repository_ctx.attr.re2c_prefix_file).strip(),
                """,
            ),
            "python-bootstrap-tool",
        )

    def test_python_bootstrap_tool_requires_pinned_python_invocation(self) -> None:
        text = dep_rule_text(
            """
            # spack-build-system: python-bootstrap-tool
            [[ -z "${PYTHON_PREFIX:-}" ]] && exit 1
            python configure.py --bootstrap
            """,
            "PYTHON_PREFIX",
            "python_prefix_file",
        )

        result, errors = self.check_text(text)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "python-bootstrap-tool")
        self.assertIn("python-bootstrap-tool build must invoke configure.py through PYTHON_PREFIX", "\n".join(errors))

    def test_perl_requires_prefix_pinned_makefile_pl(self) -> None:
        text = dep_rule_text(
            """
            perl Makefile.PL INSTALL_BASE="$PREFIX"
            make
            make install
            """,
            "PERL_PREFIX",
            "perl_prefix_file",
        )

        result, errors = self.check_text(text)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "perl")
        self.assertIn("perl build must invoke Makefile.PL through PERL_PREFIX", "\n".join(errors))

    def test_perl_accepts_extutils_makemaker_channel(self) -> None:
        self.assert_clean_mechanism(
            dep_rule_text(
                """
                [[ -z "${PERL_PREFIX:-}" || ! -x "${PERL_PREFIX:-}/bin/perl" ]] && exit 1
                export PATH="${PERL_PREFIX}/bin:${PATH}"
                "$PERL_PREFIX/bin/perl" Makefile.PL INSTALL_BASE="$PREFIX"
                make
                make install
                """,
                "PERL_PREFIX",
                "perl_prefix_file",
            ),
            "perl",
        )

    def test_generic_accepts_data_only_rule_with_insula_guard(self) -> None:
        self.assert_clean_mechanism(
            rule_text(
                """
                install -Dm0644 cacert.pem "$PREFIX/share/cacert.pem"
                """
            ),
            "generic",
        )

    def test_generic_rejects_dependency_prefixes_without_mechanism_verifier(self) -> None:
        text = dep_rule_text(
            """
            [[ -z "${FOO_PREFIX:-}" ]] && exit 1
            custom-build --with-foo "$FOO_PREFIX" --prefix "$PREFIX"
            """,
            "FOO_PREFIX",
            "foo_prefix_file",
        )

        result, errors = self.check_text(text)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "generic")
        self.assertIn(
            "generic build with dependency prefixes must add a mechanism-specific",
            "\n".join(errors),
        )

    def test_prefix_wiring_requires_attr_read_env_and_shell_check(self) -> None:
        text = rule_text(
            """
            export CPPFLAGS="-I$FOO_PREFIX/include"
            ./configure --prefix="$PREFIX"
            """
        )

        result, errors = self.check_text(text)

        self.assertIsNotNone(result)
        joined = "\n".join(errors)
        self.assertIn("FOO_PREFIX is not declared as mandatory attr.label", joined)
        self.assertIn("FOO_PREFIX is not read from attr.foo_prefix_file", joined)
        self.assertIn("FOO_PREFIX is not passed via repository_ctx.execute(environment=...)", joined)
        self.assertIn("FOO_PREFIX is consumed without a shell-side existence check", joined)

    def test_binary_archive_requires_archive_layout_and_dependency_validation(self) -> None:
        text = dep_rule_text(
            """
            [[ -z "${CUDA_PREFIX:-}" ]] && exit 1
            cp -a "$SRC"/. "$PREFIX"/
            # binary-archive
            """,
            "CUDA_PREFIX",
            "cuda_prefix_file",
        )

        result, errors = self.check_text(text)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "binary-archive")
        joined = "\n".join(errors)
        self.assertIn("binary-archive build must validate source archive layout", joined)
        self.assertIn("binary-archive build must validate linked dependency prefixes", joined)

    def test_binary_archive_accepts_layout_and_cuda_dependency_checks(self) -> None:
        self.assert_clean_mechanism(
            dep_rule_text(
                """
                [[ -z "${CUDA_PREFIX:-}" || ! -x "${CUDA_PREFIX:-}/bin/nvcc" ]] && exit 1
                [[ -f "$SRC/include/cudnn_version.h" ]] || exit 1
                [[ -e "$SRC/lib/libcudnn.so" || -e "$SRC/lib64/libcudnn.so" ]] || exit 1
                install -d "$PREFIX"
                cp -a "$SRC"/. "$PREFIX"/
                test -f "$PREFIX/include/cudnn_version.h"
                test -e "$PREFIX/lib/libcudnn.so" || test -e "$PREFIX/lib64/libcudnn.so"
                # binary-archive
                """,
                "CUDA_PREFIX",
                "cuda_prefix_file",
            ),
            "binary-archive",
        )

    def test_sdk_boundary_requires_rootfs_sdk_and_tool_prefix_checks(self) -> None:
        text = dep_rule_text(
            """
            [[ -z "${FOO_PREFIX:-}" ]] && exit 1
            install -Dm0644 "$FOO_PREFIX/share/stamp" "$PREFIX/share/stamp"
            # sdk-boundary
            """,
            "FOO_PREFIX",
            "foo_prefix_file",
        )

        result, errors = self.check_text(text)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "sdk-boundary")
        joined = "\n".join(errors)
        self.assertIn("sdk-boundary build must validate VASO_CUDA_HOME", joined)
        self.assertIn("sdk-boundary build must verify rootfs CUDA provenance", joined)
        self.assertIn("sdk-boundary build must validate installer tool dependencies", joined)

    def test_sdk_boundary_accepts_declared_rootfs_sdk_and_tool_prefixes(self) -> None:
        text = rule_text(
            """
            [[ -z "${VASO_CUDA_HOME:-}" || ! -x "${VASO_CUDA_HOME}/bin/nvcc" ]] && exit 1
            [[ -z "${VASO_ROOTFS_BUNDLE_MANIFEST:-}" || ! -f "$VASO_ROOTFS_BUNDLE_MANIFEST" ]] && exit 1
            for var in COREUTILS_PREFIX GZIP_PREFIX LIBXML2_PREFIX; do
              val="${!var:-}"
              [[ -z "$val" || ! -d "$val" ]] && exit 1
            done
            "$COREUTILS_PREFIX/bin/true"
            "$GZIP_PREFIX/bin/gzip" --version >/dev/null
            test -e "$LIBXML2_PREFIX/lib/libxml2.so" || test -e "$LIBXML2_PREFIX/lib64/libxml2.so"
            # sdk-boundary
            """,
            attrs="""
            "coreutils_prefix_file": attr.label(mandatory = True, allow_single_file = True),
            "gzip_prefix_file": attr.label(mandatory = True, allow_single_file = True),
            "libxml2_prefix_file": attr.label(mandatory = True, allow_single_file = True),
            """,
            env="""
            "COREUTILS_PREFIX": repository_ctx.read(repository_ctx.attr.coreutils_prefix_file).strip(),
            "GZIP_PREFIX": repository_ctx.read(repository_ctx.attr.gzip_prefix_file).strip(),
            "LIBXML2_PREFIX": repository_ctx.read(repository_ctx.attr.libxml2_prefix_file).strip(),
            "VASO_CUDA_HOME": repository_ctx.os.environ.get("VASO_CUDA_HOME", "/usr/local/cuda"),
            "VASO_ROOTFS_BUNDLE_MANIFEST": repository_ctx.os.environ.get("VASO_ROOTFS_BUNDLE_MANIFEST", "/run/vaso/rootfs-bundle.json"),
            """,
        )

        result, errors = self.check_text(text)

        self.assertEqual(errors, [])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "sdk-boundary")
        self.assertEqual(result.dep_vars, ("COREUTILS_PREFIX", "GZIP_PREFIX", "LIBXML2_PREFIX"))

    def test_rootfs_toolchain_boundary_requires_llvm_home_and_manifest_provenance(self) -> None:
        text = rule_text(
            """
            # rootfs-toolchain-boundary
            """
        )

        result, errors = self.check_text(text)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "rootfs-toolchain-boundary")
        joined = "\n".join(errors)
        self.assertIn("rootfs-toolchain-boundary build must validate ROOTFS_LLVM_HOME", joined)
        self.assertIn("rootfs-toolchain-boundary build must verify rootfs LLVM provenance", joined)

    def test_rootfs_toolchain_boundary_accepts_pinned_llvm_home_and_manifest(self) -> None:
        text = rule_text(
            """
            [[ -z "${ROOTFS_LLVM_HOME:-}" || ! -x "${ROOTFS_LLVM_HOME}/bin/clang" ]] && exit 1
            [[ -x "${ROOTFS_LLVM_HOME}/bin/clang++" ]] && [[ -x "${ROOTFS_LLVM_HOME}/bin/ld.lld" ]] || exit 1
            [[ -z "${VASO_ROOTFS_BUNDLE_MANIFEST:-}" || ! -f "$VASO_ROOTFS_BUNDLE_MANIFEST" ]] && exit 1
            grep -q 35901313800ea6e6cbeb9226e51c7c4b29bfc40e "$VASO_ROOTFS_BUNDLE_MANIFEST"
            grep -q /usr/lib/llvm-23 "$VASO_ROOTFS_BUNDLE_MANIFEST"
            grep -q 23.0.0git "$VASO_ROOTFS_BUNDLE_MANIFEST"
            # rootfs-toolchain-boundary
            """,
            env="""
            "ROOTFS_LLVM_HOME": repository_ctx.os.environ.get("ROOTFS_LLVM_HOME", "/usr/lib/llvm-23"),
            "VASO_ROOTFS_BUNDLE_MANIFEST": repository_ctx.os.environ.get("VASO_ROOTFS_BUNDLE_MANIFEST", "/run/vaso/rootfs-bundle.json"),
            """,
        )

        self.assert_clean_mechanism(text, "rootfs-toolchain-boundary")

    def test_rootfs_lock_component_rejects_module_download_parameters(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            native = root / "native" / "cudnn"
            native.mkdir(parents=True)
            (root / "MODULE.bazel").write_text(
                """
                cudnn_native(
                    name = "cudnn_native",
                    urls = ["https://example.invalid/cudnn.tar.xz"],
                    sha256 = "abc",
                    strip_prefix = "cudnn",
                )
                """
            )
            (native / "cudnn.bzl").write_text(
                rule_text(
                    """
                    [[ -z "${VASO_CUDA_HOME:-}" || ! -x "${VASO_CUDA_HOME}/bin/nvcc" ]] && exit 1
                    [[ -z "${VASO_ROOTFS_BUNDLE_MANIFEST:-}" || ! -f "$VASO_ROOTFS_BUNDLE_MANIFEST" ]] && exit 1
                    # sdk-boundary
                    """
                )
            )

            errors = guard.check_rootfs_lock_boundaries(root / "MODULE.bazel", root / "native", {"cudnn"})

        joined = "\n".join(errors)
        self.assertIn("cudnn_native must not declare urls", joined)
        self.assertIn("cudnn_native must not declare sha256", joined)
        self.assertIn("cudnn_native must not declare strip_prefix", joined)

    def test_rootfs_lock_component_rejects_native_source_or_archive_materialization(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            native = root / "native" / "nccl"
            native.mkdir(parents=True)
            (root / "MODULE.bazel").write_text(
                """
                nccl_native(
                    name = "nccl_native",
                )
                """
            )
            (native / "nccl.bzl").write_text(
                rule_text(
                    """
                    repository_ctx.download_and_extract(url = attr.urls, sha256 = attr.sha256)
                    cp -a "$SRC"/. "$PREFIX"/
                    # source_build.json
                    # binary_archive.json
                    # sdk-boundary
                    """
                )
            )

            errors = guard.check_rootfs_lock_boundaries(root / "MODULE.bazel", root / "native", {"nccl"})

        joined = "\n".join(errors)
        self.assertIn("native/nccl/nccl.bzl must not call repository_ctx.download", joined)
        self.assertIn("native/nccl/nccl.bzl must not mention source_build.json", joined)
        self.assertIn("native/nccl/nccl.bzl must not mention binary_archive.json", joined)
        self.assertIn("native/nccl/nccl.bzl must not copy source/archive payloads into PREFIX", joined)

    def test_rootfs_lock_components_are_boundaries_in_repository(self) -> None:
        root = SCRIPT.parents[1]
        lock = root / "rootfs" / "cuda_ecosystem.lock.json"

        errors = guard.check_rootfs_lock_boundaries(root / "MODULE.bazel", root / "native", lock)

        self.assertEqual(errors, [])

    def test_boost_build_accepts_user_config_and_explicit_variants(self) -> None:
        self.assert_clean_mechanism(
            rule_text(
                """
                cat > user-config.jam <<'EOF'
                using gcc : : /opt/insula/bin/g++ ;
                EOF
                ./bootstrap.sh --prefix="$PREFIX" --with-toolset=gcc --with-libraries=atomic,chrono,exception,system,thread --without-icu
                ./b2 install --user-config="$PWD/user-config.jam" toolset=gcc cxxstd=11 link=static,shared threading=multi --layout=system variant=release visibility=hidden --disable-icu
                """
            ),
            "boost-build",
        )

    def test_boost_build_requires_hermetic_user_config_channel(self) -> None:
        text = rule_text(
            """
            ./bootstrap.sh --prefix="$PREFIX" --with-toolset=gcc
            ./b2 install toolset=gcc cxxstd=11 link=static,shared threading=multi --layout=system
            """
        )

        result, errors = self.check_text(text)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "boost-build")
        self.assertIn("boost-build build must pass --user-config", "\n".join(errors))

    def test_boost_build_requires_explicit_build_axes(self) -> None:
        text = rule_text(
            """
            cat > user-config.jam <<'EOF'
            using gcc : : /opt/insula/bin/g++ ;
            EOF
            ./bootstrap.sh --prefix="$PREFIX"
            ./b2 install --user-config="$PWD/user-config.jam"
            """
        )

        result, errors = self.check_text(text)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "boost-build")
        self.assertIn("boost-build build must pin toolset=", "\n".join(errors))
        self.assertIn("boost-build build must pin cxxstd=", "\n".join(errors))
        self.assertIn("boost-build build must pin link=", "\n".join(errors))
        self.assertIn("boost-build build must pin threading=", "\n".join(errors))
        self.assertIn("boost-build build must pin --layout=", "\n".join(errors))

    def test_boost_build_requires_repository_env_guard_before_shell_build(self) -> None:
        text = textwrap.dedent(
            '''
            _BUILD_SH = """
            if [[ "${VASO_IN_INSULA:-0}" != "1" ]]; then
              exit 2
            fi
            cat > user-config.jam <<'EOF'
            using gcc : : /opt/insula/bin/g++ ;
            EOF
            ./bootstrap.sh --prefix="$PREFIX" --with-toolset=gcc --with-libraries=atomic,chrono,exception,system,thread --without-icu
            ./b2 install --user-config="$PWD/user-config.jam" toolset=gcc cxxstd=11 link=static,shared threading=multi --layout=system variant=release visibility=hidden --disable-icu
            """

            def _impl(repository_ctx):
                repository_ctx.file("build.sh", _BUILD_SH)
                repository_ctx.execute(["bash", "build.sh"], environment={
                    "VASO_IN_INSULA": "1",
                })

            native = repository_rule(
                implementation = _impl,
                environ = ["VASO_IN_INSULA"],
            )
            '''
        )

        result, errors = self.check_text(text, filename="boost.bzl")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "boost-build")
        self.assertIn("boost-build repository rule must check VASO_IN_INSULA before source extraction", errors)

    def test_boost_native_rule_has_repository_env_guard(self) -> None:
        text = (SCRIPT.parents[1] / "native" / "boost" / "boost.bzl").read_text()

        result, errors = self.check_text(text, filename="boost.bzl")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "boost-build")
        self.assertNotIn("boost-build repository rule must check VASO_IN_INSULA before source extraction", errors)

    def test_python_wheel_requires_planned_prefix_interface(self) -> None:
        text = rule_text(
            """
            python -m build --wheel --no-isolation
            """
        )

        result, errors = self.check_text(text)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "python-wheel")
        self.assertIn("python-wheel build must enumerate planned --prefix inputs", errors)

    def test_python_wheel_detects_pip_wheel_frontend(self) -> None:
        text = rule_text(
            """
            python -m pip wheel --no-build-isolation --no-deps -w artifacts/wheels "$SRC"
            """
        )

        result, errors = self.check_text(text)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "python-wheel")
        self.assertIn("python-wheel build must enumerate planned --prefix inputs", errors)

    def test_python_wheel_accepts_planner_preflight(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            bzl = root / "pytorch_native.bzl"
            bzl.write_text(
                rule_text(
                    """
                    python plan.py --out build_plan.json
                    python -m build --wheel --no-isolation
                    """,
                    attrs='"cuda": attr.string(doc = "CUDA prefix"),',
                )
                + '''
                def _emit_plan(args):
                    for key in ("cuda",):
                        args.append("--prefix")
                '''
            )
            (root / "plan.py").write_text(
                """
                from pathlib import Path

                def preflight(prefixes):
                    for key, p in prefixes.items():
                        if not Path(p).is_dir():
                            raise SystemExit(f"prefix:{key}:{p}")
                """
            )

            result, errors = guard.check(bzl)

        self.assertEqual(errors, [])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "python-wheel")
        self.assertEqual(result.planned_prefixes, ("cuda",))

    def test_configured_python_wheel_action_requires_driver_hermetic_checks(self) -> None:
        text = textwrap.dedent(
            '''
            def _impl(ctx):
                ctx.actions.run_shell(
                    inputs = depset([ctx.file.cuda_prefix_file]),
                    outputs = [],
                    command = "driver --prefix-file cuda=$1",
                    arguments = [ctx.file.cuda_prefix_file.path],
                    use_default_shell_env = True,
                )

            pytorch_action_prefix = rule(
                implementation = _impl,
                attrs = {
                    "cuda_prefix_file": attr.label(allow_single_file = True, mandatory = True),
                },
            )
            '''
        )

        result, errors = self.check_text(text, filename="pytorch_action.bzl")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "python-wheel-action")
        joined = "\n".join(errors)
        self.assertIn("configured python-wheel action must validate VASO_IN_INSULA", joined)
        self.assertIn("configured python-wheel action must validate VASO_ROOTFS_BUNDLE_MANIFEST", joined)

    def test_pytorch_action_rule_has_configured_action_guard(self) -> None:
        path = SCRIPT.parents[1] / "native" / "pytorch" / "pytorch_action.bzl"

        result, errors = guard.check(path)

        self.assertEqual(errors, [])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "python-wheel-action")
        self.assertIn("cuda", result.planned_prefixes)
        self.assertIn("nvshmem", result.planned_prefixes)

    def test_configured_python_wheel_action_accepts_triton_token_gate(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            bzl = root / "triton_action.bzl"
            bzl.write_text(
                textwrap.dedent(
                    '''
                    TritonTokenInfo = provider()
                    _REQUIRED_TOKEN = "build-native-triton"

                    def _impl(ctx):
                        token = ctx.attr._token_flag[TritonTokenInfo].value
                        args = ["--source-anchor", ctx.file.source_anchor.path]
                        args.extend(["--prefix-file", "cuda={}".format(ctx.file.cuda_prefix_file.path)])
                        args.extend(["--token", token])
                        args.append("--execute")
                        ctx.actions.run_shell(
                            inputs = depset([ctx.file.cuda_prefix_file]),
                            outputs = [],
                            command = "action_driver.py",
                            arguments = args,
                            use_default_shell_env = True,
                        )

                    triton_token_flag = rule(
                        implementation = _impl,
                        build_setting = config.string(flag = True),
                    )

                    triton_action_prefix = rule(
                        implementation = _impl,
                        attrs = {
                            "source_anchor": attr.label(allow_single_file = True, mandatory = True),
                            "cuda_prefix_file": attr.label(allow_single_file = True, mandatory = True),
                            "_token_flag": attr.label(default = Label("//native/triton:token")),
                            "_plan": attr.label(default = "//native/triton:plan.py", allow_single_file = True),
                        },
                    )
                    '''
                ),
                encoding="utf-8",
            )
            (root / "action_driver.py").write_text(
                textwrap.dedent(
                    '''
                    import os

                    REQUIRED_TOKEN = "build-native-triton"

                    def check_insula():
                        os.environ.get("VASO_IN_INSULA")
                        os.environ.get("VASO_ROOTFS_BUNDLE_MANIFEST")
                    '''
                ),
                encoding="utf-8",
            )

            result, errors = guard.check(bzl)

        self.assertEqual(errors, [])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "python-wheel-action")
        self.assertEqual(result.planned_prefixes, ("cuda",))

    def test_triton_action_rule_has_configured_action_guard(self) -> None:
        path = SCRIPT.parents[1] / "native" / "triton" / "triton_action.bzl"

        result, errors = guard.check(path)

        self.assertEqual(errors, [])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "python-wheel-action")
        self.assertIn("llvm", result.planned_prefixes)
        self.assertIn("nlohmann_json", result.planned_prefixes)
        self.assertIn("py_lit", result.planned_prefixes)

    def test_torchvision_action_rule_has_configured_action_guard(self) -> None:
        path = SCRIPT.parents[1] / "native" / "torchvision" / "torchvision_action.bzl"

        result, errors = guard.check(path)

        self.assertEqual(errors, [])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "python-wheel-action")
        self.assertIn("py_pillow", result.planned_prefixes)
        self.assertIn("libjpeg_turbo", result.planned_prefixes)
        self.assertIn("zlib_ng", result.planned_prefixes)

    def test_torchaudio_action_rule_has_configured_action_guard(self) -> None:
        path = SCRIPT.parents[1] / "native" / "torchaudio" / "torchaudio_action.bzl"

        result, errors = guard.check(path)

        self.assertEqual(errors, [])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "python-wheel-action")
        self.assertIn("cuda", result.planned_prefixes)
        self.assertIn("ninja", result.planned_prefixes)

    def test_python_venv_accepts_prefix_pinned_venv_creation(self) -> None:
        self.assert_clean_mechanism(
            dep_rule_text(
                """
                [[ -z "${PYTHON_PREFIX:-}" || ! -x "${PYTHON_PREFIX:-}/bin/python3" ]] && exit 1
                [[ -z "${PYTHON_ABI:-}" || ! -d "${PYTHON_PREFIX:-}/include/python${PYTHON_ABI}" ]] && exit 1
                export PYTHONHOME=
                export PYTHONPATH=
                "$PYTHON_PREFIX/bin/python${PYTHON_ABI}" -m venv --without-pip "$PREFIX"
                test -f "$PREFIX/pyvenv.cfg"
                # python-venv
                """,
                "PYTHON_PREFIX",
                "python_prefix_file",
            ),
            "python-venv",
        )

    def test_python_bootstrap_pip_accepts_prefix_pinned_wheel_install(self) -> None:
        self.assert_clean_mechanism(
            rule_text(
                """
                [[ -z "${PYTHON_VENV_PREFIX:-}" || ! -x "${PYTHON_VENV_PREFIX:-}/bin/python3" ]] && exit 1
                [[ -z "${PYTHON_ABI:-}" || ! -d "${PYTHON_PREFIX:-}/include/python${PYTHON_ABI}" ]] && exit 1
                [[ -z "${PYTHON_PREFIX:-}" || ! -x "${PYTHON_PREFIX:-}/bin/python${PYTHON_ABI}" ]] && exit 1
                export PYTHONHOME=
                export PYTHONPATH="$PREFIX/lib/python${PYTHON_ABI}/site-packages"
                "$PYTHON_PREFIX/bin/python${PYTHON_ABI}" -m zipfile -e "$WHEEL" unpacked
                "$PYTHON_VENV_PREFIX/bin/python3" unpacked/pip -vvv --no-input --no-cache-dir --disable-pip-version-check install --no-deps --ignore-installed --no-build-isolation --no-warn-script-location --no-index --prefix="$PREFIX" "$WHEEL"
                test -x "$PREFIX/bin/pip${PYTHON_ABI}"
                """,
                attrs=(
                    '"python_prefix_file": attr.label(mandatory = True, allow_single_file = True),\n'
                    '"python_venv_prefix_file": attr.label(mandatory = True, allow_single_file = True),'
                ),
                env=(
                    '"PYTHON_PREFIX": repository_ctx.read(repository_ctx.attr.python_prefix_file).strip(),\n'
                    '"PYTHON_VENV_PREFIX": repository_ctx.read(repository_ctx.attr.python_venv_prefix_file).strip(),'
                ),
            ),
            "python-bootstrap-pip",
        )

    def test_python_pip_install_accepts_prefix_pinned_wheel_install(self) -> None:
        self.assert_clean_mechanism(
            rule_text(
                """
                [[ -z "${PYTHON_VENV_PREFIX:-}" || ! -x "${PYTHON_VENV_PREFIX:-}/bin/python3" ]] && exit 1
                [[ -z "${PY_PIP_PREFIX:-}" || ! -d "${PY_PIP_PREFIX:-}/lib/python3.14/site-packages/pip" ]] && exit 1
                export PYTHONHOME=
                export PYTHONPATH="$PY_PIP_PREFIX/lib/python3.14/site-packages"
                "$PYTHON_VENV_PREFIX/bin/python3" -m pip -vvv --no-input --no-cache-dir --disable-pip-version-check install --no-deps --ignore-installed --no-build-isolation --no-warn-script-location --no-index --prefix="$PREFIX" "$WHEEL"
                test -d "$PREFIX/lib/python3.14/site-packages/setuptools"
                """,
                attrs=(
                    '"python_venv_prefix_file": attr.label(mandatory = True, allow_single_file = True),\n'
                    '"py_pip_prefix_file": attr.label(mandatory = True, allow_single_file = True),'
                ),
                env=(
                    '"PYTHON_VENV_PREFIX": repository_ctx.read(repository_ctx.attr.python_venv_prefix_file).strip(),\n'
                    '"PY_PIP_PREFIX": repository_ctx.read(repository_ctx.attr.py_pip_prefix_file).strip(),'
                ),
            ),
            "python-pip-install",
        )

    def test_python_pip_install_accepts_derived_python_abi_paths(self) -> None:
        self.assert_clean_mechanism(
            rule_text(
                """
                [[ -z "${PYTHON_ABI:-}" ]] && exit 1
                [[ -z "${PYTHON_VENV_PREFIX:-}" || ! -x "${PYTHON_VENV_PREFIX:-}/bin/python${PYTHON_ABI}" ]] && exit 1
                [[ -z "${PY_PIP_PREFIX:-}" || ! -d "${PY_PIP_PREFIX:-}/lib/python${PYTHON_ABI}/site-packages/pip" ]] && exit 1
                export PYTHONHOME=
                export PYTHONPATH="$PY_PIP_PREFIX/lib/python${PYTHON_ABI}/site-packages:$PYTHON_VENV_PREFIX/lib/python${PYTHON_ABI}/site-packages"
                "$PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI}" -m pip -vvv --no-input --no-cache-dir --disable-pip-version-check install --no-deps --ignore-installed --no-build-isolation --no-warn-script-location --no-index --prefix="$PREFIX" "$WHEEL"
                test -d "$PREFIX/lib/python${PYTHON_ABI}/site-packages/setuptools"
                """,
                attrs=(
                    '"python_venv_prefix_file": attr.label(mandatory = True, allow_single_file = True),\n'
                    '"py_pip_prefix_file": attr.label(mandatory = True, allow_single_file = True),'
                ),
                env=(
                    '"PYTHON_VENV_PREFIX": repository_ctx.read(repository_ctx.attr.python_venv_prefix_file).strip(),\n'
                    '"PY_PIP_PREFIX": repository_ctx.read(repository_ctx.attr.py_pip_prefix_file).strip(),'
                ),
            ),
            "python-pip-install",
        )

    def test_python_bootstrap_pip_requires_offline_flags_and_prefixes(self) -> None:
        text = rule_text(
            """
            [[ -z "${PYTHON_PREFIX:-}" ]] && exit 1
            [[ -z "${PYTHON_VENV_PREFIX:-}" ]] && exit 1
            "$PYTHON_PREFIX/bin/python${PYTHON_ABI}" -m zipfile -e "$WHEEL" unpacked
            "$PYTHON_VENV_PREFIX/bin/python3" unpacked/pip install --prefix="$PREFIX" --no-index "$WHEEL"
            """,
            attrs=(
                '"python_prefix_file": attr.label(mandatory = True, allow_single_file = True),\n'
                '"python_venv_prefix_file": attr.label(mandatory = True, allow_single_file = True),'
            ),
            env=(
                '"PYTHON_PREFIX": repository_ctx.read(repository_ctx.attr.python_prefix_file).strip(),\n'
                '"PYTHON_VENV_PREFIX": repository_ctx.read(repository_ctx.attr.python_venv_prefix_file).strip(),'
            ),
        )

        result, errors = self.check_text(text)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "python-bootstrap-pip")
        joined = "\n".join(errors)
        self.assertIn("python-bootstrap-pip build must pass --no-input", joined)
        self.assertIn("python-bootstrap-pip build must clear PYTHONHOME", joined)

    def test_python_pip_install_requires_pythonpath(self) -> None:
        text = rule_text(
            """
            [[ -z "${PYTHON_VENV_PREFIX:-}" ]] && exit 1
            [[ -z "${PY_PIP_PREFIX:-}" ]] && exit 1
            "$PYTHON_VENV_PREFIX/bin/python3" -m pip install --prefix="$PREFIX" --no-index "$WHEEL"
            """,
            attrs=(
                '"python_venv_prefix_file": attr.label(mandatory = True, allow_single_file = True),\n'
                '"py_pip_prefix_file": attr.label(mandatory = True, allow_single_file = True),'
            ),
            env=(
                '"PYTHON_VENV_PREFIX": repository_ctx.read(repository_ctx.attr.python_venv_prefix_file).strip(),\n'
                '"PY_PIP_PREFIX": repository_ctx.read(repository_ctx.attr.py_pip_prefix_file).strip(),'
            ),
        )

        result, errors = self.check_text(text)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "python-pip-install")
        self.assertIn("python-pip-install build must expose PY_PIP_PREFIX on PYTHONPATH", "\n".join(errors))

    def test_python_venv_requires_clean_env_and_without_pip(self) -> None:
        text = dep_rule_text(
            """
            [[ -z "${PYTHON_PREFIX:-}" ]] && exit 1
            "$PYTHON_PREFIX/bin/python${PYTHON_ABI}" -m venv "$PREFIX"
            """,
            "PYTHON_PREFIX",
            "python_prefix_file",
        )

        result, errors = self.check_text(text)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.mechanism, "python-venv")
        joined = "\n".join(errors)
        self.assertIn("python-venv build must pass --without-pip", joined)
        self.assertIn("python-venv build must clear PYTHONHOME and PYTHONPATH", joined)

    def test_cli_can_require_mechanism_coverage(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            path = Path(tmp) / "generic.bzl"
            path.write_text(rule_text('install -Dm0644 data "$PREFIX/share/data"'))

            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--require-mechanisms",
                    "autotools,generic",
                    str(path),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("missing required mechanisms: autotools", proc.stderr)

    def test_repository_execute_requires_generated_build_script(self) -> None:
        text = textwrap.dedent(
            '''
            def _impl(repository_ctx):
                if repository_ctx.os.environ.get("VASO_IN_INSULA") != "1":
                    fail("native build must run inside insula")
                repository_ctx.execute(["bash", "configure"], environment={
                    "VASO_IN_INSULA": "1",
                })

            native = repository_rule(
                implementation = _impl,
                environ = ["VASO_IN_INSULA"],
            )
            '''
        )

        result, errors = self.check_text(text)

        self.assertIsNotNone(result)
        self.assertIn("repository executes a native build without a generated build script", errors)

    def run_toolchain_guard(self, text: str, allowlist: str = "", filename: str = "pkg.sh") -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            path = root / filename
            allowlist_path = root / "toolchain_setting_allowlist.txt"
            path.write_text(text)
            allowlist_path.write_text(allowlist)

            return subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--toolchain-allowlist",
                    str(allowlist_path),
                    path.name,
                ],
                cwd=root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

    def test_toolchain_guard_rejects_unlisted_compiler_identity(self) -> None:
        proc = self.run_toolchain_guard('export CC="/usr/bin/gcc"\n')

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unlisted toolchain setting", proc.stderr)
        self.assertIn("pkg.sh:CC:/usr/bin/gcc", proc.stderr)

    def test_toolchain_guard_accepts_allowlisted_identity(self) -> None:
        proc = self.run_toolchain_guard(
            'export CC="/usr/bin/gcc"\n',
            "pkg.sh:CC:/usr/bin/gcc\n",
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_toolchain_guard_rejects_stale_allowlist_entry(self) -> None:
        proc = self.run_toolchain_guard(
            "true\n",
            "pkg.sh:CC:/usr/bin/gcc\n",
        )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("stale toolchain allowlist entry", proc.stderr)
        self.assertIn("pkg.sh:CC:/usr/bin/gcc", proc.stderr)

    def test_toolchain_guard_ignores_dependency_wiring_flags(self) -> None:
        proc = self.run_toolchain_guard(
            'export CFLAGS="-I${X_PREFIX}/include ${CFLAGS:-}"\n',
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_toolchain_guard_rejects_bazel_copt(self) -> None:
        proc = self.run_toolchain_guard(
            'bazel build --copt=-O2 //:target\n',
        )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("pkg.sh:--copt:-O2", proc.stderr)


if __name__ == "__main__":
    unittest.main()
