# meson native recipe

## Position in the hillclimb

`meson@1.11.1` is the next feasible PythonPackage node after native `ninja`
in the lean `py-torch` frontier. The focused reference graph for
`SPACK_ROOT_PKG='meson@1.11.1 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib'`
has 39 build-graph nodes and uses 22 `autotools`, 14 `generic`, 2 `makefile`,
and 1 `python_pip` build-system node.

Spack still owns the DAG shape. The native flip changes only `spack_meson`'s
provider to `@meson_native//:lib`; the dependency topology remains the concrete
Spack topology.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.
GitHub release metadata reported upstream Spack `v1.2.2` as the latest release
while this recipe was captured, matching the vendored distribution.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/meson-1.11.1-lowirobzhl2mejqbx73kavvl5jo6jubh
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/meson-1.11.1-lowirobzhl2mejqbx73kavvl5jo6jubh/.spack/repos/spack_repo/builtin/packages/meson/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `Meson(PythonPackage)`
- version: `1.11.1`
- source archive: `https://github.com/mesonbuild/meson/archive/1.11.1.tar.gz`
- source SHA256:
  `1a2219422be4a66ad0e8daed125c2a3d5c963458e289203eae22edf3224f5d3e`
- applied patch: `rpath-0.64.patch`
- patch SHA256:
  `0f0b1bd854856c5f0926723437c9cd0507836bb93b45bdb434f5d3f618cc78dc`
- not applied for this version: `python-3.12-support.patch`, which is limited
  to `@1.1:1.2.2`
- package dependencies: `ninja` as a runtime dependency and `py-setuptools` as
  a build/run dependency
- concrete Python build channel: `python`, `python-venv`, `py-pip`,
  `py-setuptools`, and `py-wheel`

The concrete Spack build log shows the install command:

```text
<python-venv-prefix>/bin/python3 -m pip -vvv --no-input --no-cache-dir \
  --disable-pip-version-check install --no-deps --ignore-installed \
  --no-build-isolation --no-warn-script-location --no-index \
  --prefix=<meson-prefix> .
```

The stable installed prefix surface is:

- executable: `bin/meson`
- Python package: `lib/python3.13/site-packages/mesonbuild/`
- metadata: `lib/python3.13/site-packages/meson-1.11.1.dist-info/`
- data: `share/man/man1/meson.1`
- data: `share/polkit-1/actions/com.mesonbuild.install.policy`
- public library ABI: none

## Native build recipe

`native/meson/meson.bzl` mirrors Spack's PythonPackage install:

```text
download and extract the exact meson-1.11.1 source archive by SHA256
apply native/meson/rpath-0.64.patch with strip=1
read @ninja_native//:prefix_path.txt
read @python_313_native//:prefix_path.txt
read @python_venv_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
validate each prefix before invoking pip
derive PYTHON_ABI from the native Python prefix and validate python3.13 paths
clear PYTHONHOME
set PYTHONPATH from native pip, setuptools, wheel, and python-venv prefixes
set PATH from native Ninja
run "$PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI}" -m pip install ... --no-index --prefix
validate bin/meson, representative mesonbuild files, dist-info metadata,
  manpage, and polkit policy
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so configure/build
side effects and dependency discovery stay inside the sealed CUDA rootfs.

The mechanism verifier reports this build channel:

```text
native/meson/meson.bzl: python-pip-install: NINJA_PREFIX, PYTHON_PREFIX, PYTHON_VENV_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_WHEEL_PREFIX
```

`python-pip-install` is the mechanism-specific guard for PythonPackage source
installs. It requires explicit `*_prefix_file` attrs, shell-side prefix checks,
an ABI-derived `$PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI} -m pip`,
`--no-index`, `--no-deps`,
`--no-build-isolation`, `--prefix`, explicit `PYTHONPATH`, and a cleared
`PYTHONHOME`.

## Prefix and behavior gate

The smoke target is:

```text
//synthetic:use_meson_native
```

It passed inside the CUDA insula with output:

```text
meson:1.11.1:mesonbuild.mesonmain:1.11.1:meson-native-smoke
```

The test reads `@meson_native//:prefix_path.txt`, imports
`mesonbuild.mesonmain`, checks `importlib.metadata.version("meson")`, runs
`bin/meson --version`, and uses native Meson plus native Ninja to configure,
build, and run a tiny C program inside the insula.

The parity target is:

```text
//synthetic:meson_prefix_parity
```

It passed inside the CUDA insula against:

```text
/vaso/cache/spack/opt/spack/linux-icelake/meson-1.11.1-lowirobzhl2mejqbx73kavvl5jo6jubh
```

The gate compares `@meson_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected stable Meson Python package files and dist-info metadata;
- `share/man/man1/meson.1`;
- `share/polkit-1/actions/com.mesonbuild.install.policy`;
- prefix-normalized `bin/meson` executable content;
- matching `bin/meson --version` behavior;
- empty ELF ABI axis.

## ODR-sensitive provider invariant

This slice does not add protobuf, gRPC, Abseil, or Boost providers. Those
families must stay on one unified compatible concrete version family across all
companion packages before any native flip. The generator rejects unqualified
overrides and rejects mixed concrete family versions, including
protobuf/Python protobuf and gRPC/gRPC C++ pairings.
