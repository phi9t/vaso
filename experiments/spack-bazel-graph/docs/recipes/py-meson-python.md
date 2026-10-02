# py-meson-python native recipe

## Position in the hillclimb

`py-meson-python@0.19.0` is the next current full PyTorch closure
`PythonPackage` source build after native `py-pyproject-metadata` and before
`py-numpy@2.4.6` in FC-10's selective replay.

The current CE-9 full graphs use `py-torch@2.14.0` with `~magma ~mkldnn` and
`^py-networkx~default`. The native flip changes only
`spack_py_meson_python.build` to `native` and re-exports
`@py_meson_python_native//:lib`; generated dependency edges remain
Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-meson-python-0.19.0-5m72j3hbiqdtuaanfsoma4cjhnbyjdyo
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyMesonPython(PythonPackage)`
- selected build system: `python_pip`
- version: `0.19.0`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/m/meson_python/meson_python-0.19.0.tar.gz`
- source SHA256:
  `9959d198aa69b57fcfd354a34518c6f795b781a73ed0656f4d01660160cc2553`
- active dependencies: `meson@0.64:` build/run, `ninja` build/run,
  `py-packaging@23.2:` build/run, `py-pyproject-metadata@0.9:` build/run,
  `python` build/run, `python-venv` build/run, plus PythonPackage machinery
  `py-pip` and `py-wheel`
- inactive for this concrete Python line and package version:
  `py-tomli`, `py-colorama`, `py-setuptools`, and `py-typing-extensions`

Spack's install path is the standard PythonPackage pip invocation:

```text
<python-venv-prefix>/bin/python3 -m pip \
  -vvv \
  --no-input \
  --no-cache-dir \
  --disable-pip-version-check \
  install \
  --no-deps \
  --ignore-installed \
  --no-build-isolation \
  --no-warn-script-location \
  --no-index \
  --prefix=<py-meson-python-prefix> \
  .
```

The `mesonpy` backend runs Meson and Ninja during metadata preparation.
Generated wheel records, bytecode, and temporary build paths are not part of
the stable prefix contract.

## Native build recipe

`native/py_meson_python/py_meson_python.bzl` mirrors that install method
directly:

```text
download and extract the exact meson_python-0.19.0 source archive by SHA256
read @meson_native//:prefix_path.txt
read @ninja_native//:prefix_path.txt
read @python_313_native//:prefix_path.txt
read @python_venv_native//:prefix_path.txt
derive `PYTHON_ABI` from the native python-venv prefix
read @py_packaging_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_pyproject_metadata_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
validate native meson, ninja, python, python-venv, packaging, pip, pyproject-metadata, and wheel prefixes
clear PYTHONHOME
set PATH from native meson, ninja, pip, wheel, python-venv, python, and rootfs tool paths
set PYTHONPATH from native pip, wheel, packaging, pyproject-metadata, meson, and python-venv under `lib/python${PYTHON_ABI}/site-packages`
PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI} -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate mesonpy package modules and dist-info metadata
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches for
host Spack; all Python packaging tools and Meson/Ninja inputs come from
Bazel-native prefixes.

## Prefix and behavior contract

The stable prefix surface is:

- package modules under `lib/python3.13/site-packages/mesonpy`
- metadata under `lib/python3.13/site-packages/meson_python-0.19.0.dist-info`

The package installs no ELF objects, so the ABI axis is expected to be empty.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_meson_python_native
```

It resolves native `py-meson-python`, Meson, Ninja, Python, Python venv,
`py-packaging`, `py-pip`, `py-pyproject-metadata`, and `py-wheel` prefixes. It
imports `mesonpy`, `mesonbuild.mesonmain`, and
`pyproject_metadata.StandardMetadata`, checks installed metadata version, and
parses a minimal project table with a `meson-python` dependency. Expected
output:

```text
py-meson-python:0.19.0:mesonpy:mesonbuild.mesonmain:demo-pkg:meson-python
```

The parity target is:

```text
//synthetic:py_meson_python_prefix_parity
```

It compares `@py_meson_python_native//:prefix` against the hermetic Spack
reference prefix and covers selected package files plus dist-info metadata
under `lib/python3.13/site-packages`.

Fresh proof is recorded in `.scratch/pytorch-frontier-convergence/issues/10-resume-py-frontier.md`.
