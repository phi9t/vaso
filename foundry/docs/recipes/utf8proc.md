# Recipe deep-dive: utf8proc (Spack `cmake` build)

> Second package in the topological migration hillclimb and the **first
> `cmake`-build-system recipe**. Captures how Spack's `CMakeBuilder` drives the
> build underneath so a native Bazel build reproduces it byte/ABI-identically.
> Evidence quoted from the installed prefix's `.spack/` metadata.

- **Package:** `utf8proc@2.10.0`
- **Build system (concretized):** `cmake`
- **Spack hash:** `xtbyinrlmgebzssj63b7ialc3rcqelw3`
- **Prefix:** `.../opt/spack/linux-icelake/utf8proc-2.10.0-xtbyinrlmgebzssj63b7ialc3rcqelw3`
- **Migration status:** `native` — `@utf8proc_native//:lib`, ABI gate
  `//synthetic:utf8proc_abi_parity` green.

## 1. How Spack's `cmake` build system works

Spack's `CMakePackage` / `CMakeBuilder` runs a three-phase pipeline. Recorded
phases for this install:

```
==> utf8proc: Executing phase: 'cmake'
==> utf8proc: Executing phase: 'build'
==> utf8proc: Executing phase: 'install'
```

- **cmake** — configure. `CMakeBuilder.cmake()` runs
  `cmake -G <generator> <std_cmake_args> <cmake_args()>` in a separate build
  dir, where `std_cmake_args` is a large fixed preamble Spack injects for every
  cmake package (install prefix, rpath policy, prefix path, build type,
  verbose makefile, policy defaults, ...). `cmake_args()` is the package's
  variant-derived overlay.
- **build** — `make` (or `ninja`, per the `generator` variant).
- **install** — `make install` / `ninja install` into `--prefix`.

The `generator` variant selects Make vs Ninja; here it is `make`, so Spack ran
`-G "Unix Makefiles"`.

## 2. Spack's injected `std_cmake_args` (quoted from the build log)

Unlike autotools (where zlib-ng's `configure` derives most flags itself), the
cmake builder **injects a rich standard preamble**. From
`spack-build-out.txt.gz`:

```
cmake -G 'Unix Makefiles'
  -DCMAKE_INSTALL_PREFIX:STRING=<prefix>
  -DCMAKE_INSTALL_RPATH_USE_LINK_PATH:BOOL=ON
  -DCMAKE_INSTALL_RPATH:STRING=<prefix>/lib;<prefix>/lib64
  -DCMAKE_PREFIX_PATH:STRING=<cmake>;<compiler-wrapper>;<gcc-runtime>;<gmake>
  -DCMAKE_BUILD_TYPE:STRING=Release
  -DCMAKE_VERBOSE_MAKEFILE:BOOL=ON
  -DCMAKE_INTERPROCEDURAL_OPTIMIZATION:BOOL=OFF
  -DCMAKE_POLICY_DEFAULT_CMP0090:STRING=NEW
  -DCMAKE_FIND_USE_PACKAGE_REGISTRY:BOOL=OFF
  -DCMAKE_EXPORT_COMPILE_COMMANDS:BOOL=ON
  -DBUILD_SHARED_LIBS:BOOL=OFF          # <- from cmake_args()
  <srcdir>
```

- `CMAKE_BUILD_TYPE=Release` ← `build_type=Release` variant.
- `CMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF` ← `ipo=False` variant.
- The rpath / prefix-path / policy flags are Spack's standard hermetic wiring;
  they place the install rpath at `<prefix>/lib[64]` and point package discovery
  at the concretized deps only.

## 3. Variants → `cmake_args()`

The concretized variant set (`spec.json`):

```
build_system=cmake  build_type=Release  generator=make  ipo=False  shared=False
cflags=[] cppflags=[] cxxflags=[] ...
```

From the vendored `utf8proc/package.py` `CMakeBuilder.cmake_args()`:

```python
def cmake_args(self):
    args = []
    args.append(self.define_from_variant("BUILD_SHARED_LIBS", "shared"))
    return args
```

`shared=False` → `-DBUILD_SHARED_LIBS:BOOL=OFF`, which is why the prefix ships a
**static** `libutf8proc.a` and no `.so`. `build_type`/`ipo`/`generator` are
handled by the standard cmake builder machinery, not `cmake_args()`.

## 4. Emitted install tree (the prefix contract)

```
include/  utf8proc.h
lib/      libutf8proc.a          # static; shared=False
          pkgconfig/libutf8proc.pc
```

No shared object, so the ABI gate's soname/symbol axis is trivially empty; the
**layout** axis (header + `.a` + `.pc`) and the **link-and-run** axis (a
consumer statically linked `-lutf8proc` against each prefix printing identical
`utf8proc_version()`) carry the parity proof.

## 5. Native reproduction (landed)

`native/utf8proc/utf8proc.bzl` fetches utf8proc `2.10.0` by the **same tag +
sha256 the Spack package pins** (`6f4f1b63...f136`), then runs Spack's cmake
recipe:

```sh
cmake -G "Unix Makefiles" \
  -DCMAKE_INSTALL_PREFIX="$PREFIX" \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=OFF \
  "$SRC"
make -j"$(nproc)"
make install
```

Result: a `prefix/`-identical tree, exposed as `@utf8proc_native//:lib`. The
ABI gate `//synthetic:utf8proc_abi_parity` passes (layout + link-and-run green)
vs the Spack utf8proc prefix.

`cmake_bin` in `MODULE.bazel` points at the Spack cmake 3.31.11 so the build is
hermetic; inside the sealed insula the bundle's cmake is used. Note the recipe
does **not** replicate Spack's full `std_cmake_args` rpath/prefix-path preamble:
for a self-contained leaf that produces a byte-identical tree, only the
prefix + build_type + shared flag matter to the emitted artifact. If a future
cmake package's output depends on the injected rpath/prefix-path (e.g. it
records absolute rpaths in a shared `.so`), the recipe must carry those flags —
the ABI gate is what surfaces such a divergence.

## 6. autotools vs cmake — recipe-class contrast

| | autotools (zlib-ng) | cmake (utf8proc) |
|---|---|---|
| Spack phases | autoreconf / configure / build / install | cmake / build / install |
| flag source | mostly `configure`-derived from a few `configure_args` | rich injected `std_cmake_args` + variant `cmake_args` |
| generator | make | make or ninja (`generator` variant) |
| variant→flag | `+compat` → `--zlib-compat` | `~shared` → `-DBUILD_SHARED_LIBS=OFF` |
| native repro | `./configure ... && make && make install` | `cmake -G ... -D... && make && make install` |

The next build-system class to capture is `makefile` (zstd) — see
`docs/recipes/` and `migration_ledger.json`.
