# Recipe deep-dive: zstd (Spack `makefile` build)

> Third package in the topological migration hillclimb and the **first
> `makefile`-build-system recipe**. Captures how Spack's `MakefileBuilder`
> drives a project's own Makefile directly (no configure/cmake), so a native
> Bazel build reproduces it byte/ABI-identically. Evidence quoted from the
> installed prefix's `.spack/` metadata.

- **Package:** `zstd@1.5.7`
- **Build system (concretized):** `makefile`
- **Spack hash:** `iws64ck5gr7w5upq4sho4t7p42fw3s5u`
- **Prefix:** `.../opt/spack/linux-icelake/zstd-1.5.7-iws64ck5gr7w5upq4sho4t7p42fw3s5u`
- **Migration status:** `native` — `@zstd_native//:lib`, ABI gate
  `//synthetic:zstd_abi_parity` green (this one exercises the shared-lib
  soname+symbol axis).

## 1. How Spack's `makefile` build system works

Spack's `MakefilePackage` / `MakefileBuilder` runs an `edit` → `build` →
`install` pipeline. Unlike autotools/cmake there is **no configure step**: the
project ships a hand-written Makefile, and the builder just edits it (if the
package overrides `edit()`) and runs `make` targets. Recorded phases:

```
==> zstd: Executing phase: 'edit'
==> zstd: Executing phase: 'build'
==> zstd: Executing phase: 'install'
```

For zstd the vendored `package.py` overrides the builder so that:

- **edit** — default (no Makefile edits needed here);
- **build** — a **no-op** (`def build(...): pass`); the install targets build
  what they need;
- **install** — runs the project's own install targets with explicit `PREFIX=`
  and feature toggles.

This is the key difference from autotools/cmake: the package author drives raw
`make` targets, so the recipe is "which targets + which `VAR=value` overrides",
not "which `configure`/`cmake` flags".

## 2. Variants → make targets/vars (from `package.py`)

Concretized variant set (`spec.json`):

```
build_system=makefile  compression=[none]  libs=[shared, static]  programs=True
cflags=[] ...
```

The vendored `zstd/package.py` `MakefileBuilder.install()`:

```python
def install(self, pkg, spec, prefix):
    args = ["VERBOSE=1", "PREFIX=" + prefix]
    lib_args = ["-C", "lib"] + args + ["install-pc", "install-includes"]
    if "libs=shared" in spec: lib_args.append("install-shared")
    if "libs=static" in spec: lib_args.append("install-static")
    make(*lib_args)
    if "+programs" in spec:
        programs_args = ["-C", "programs"] + args
        if "compression=zlib" not in spec: programs_args.append("HAVE_ZLIB=0")
        if "compression=lzma" not in spec: programs_args.append("HAVE_LZMA=0")
        if "compression=lz4"  not in spec: programs_args.append("HAVE_LZ4=0")
        programs_args.append("install")
        make(*programs_args)
```

So variants become **make target lists + `HAVE_*=0` toggles**:
- `libs=[shared,static]` → `install-shared install-static` (both);
- `programs=True` → also build+install the `bin/zstd*` CLI;
- `compression=[none]` → `HAVE_ZLIB=0 HAVE_LZMA=0 HAVE_LZ4=0` (no optional
  compression backends linked into the programs).

## 3. The exact make commands Spack ran (quoted)

From `spack-build-out.txt.gz`:

```
make -C lib      VERBOSE=1 PREFIX=<prefix> install-pc install-includes install-shared install-static
make -C programs VERBOSE=1 PREFIX=<prefix> HAVE_ZLIB=0 HAVE_LZMA=0 HAVE_LZ4=0 install
```

No `./configure`, no `cmake` — the Makefile's own rules build the objects,
`libzstd.so.1.5.7` + symlinks, `libzstd.a`, headers, `libzstd.pc`, and the CLIs.

## 4. Emitted install tree (the prefix contract)

```
include/  zstd.h  zdict.h  zstd_errors.h
lib/      libzstd.a
          libzstd.so -> libzstd.so.1
          libzstd.so.1 -> libzstd.so.1.5.7
          libzstd.so.1.5.7               # SONAME libzstd.so.1
          pkgconfig/libzstd.pc
bin/      zstd unzstd zstdcat zstdgrep zstdless zstdmt
share/    man/...
```

Because zstd ships a **shared** `libzstd.so.1.5.7`, this flip is the first to
exercise the ABI gate's soname/symbol axis: `readelf -d` SONAME `libzstd.so.1`
and the full `nm -D` exported-symbol set (187 symbols) must match the Spack
prefix, in addition to the layout set and the link-and-run round-trip.

## 5. Native reproduction (landed)

`native/zstd/zstd.bzl` fetches zstd `1.5.7` by the **same tag + sha256 the Spack
package pins** (`37d72845...2ee3`), then runs Spack's make targets verbatim:

```sh
make -C "$SRC/lib" VERBOSE=1 PREFIX="$PREFIX" \
  install-pc install-includes install-shared install-static
make -C "$SRC/programs" VERBOSE=1 PREFIX="$PREFIX" \
  HAVE_ZLIB=0 HAVE_LZMA=0 HAVE_LZ4=0 install
```

Result: a `prefix/`-identical tree, `@zstd_native//:lib`. ABI gate
`//synthetic:zstd_abi_parity` green: layout match (8 paths), SONAME match, 187
exported symbols match, link-and-run identical.

## 6. Three recipe classes captured

| | autotools (zlib-ng) | cmake (utf8proc) | makefile (zstd) |
|---|---|---|---|
| phases | autoreconf/configure/build/install | cmake/build/install | edit/build/install |
| configure step | `./configure` | `cmake -G ...` | **none** |
| flag source | `configure_args()` | injected `std_cmake_args` + `cmake_args()` | make target list + `VAR=value` |
| variant→build | `+compat`→`--zlib-compat` | `~shared`→`-DBUILD_SHARED_LIBS=OFF` | `libs=`→`install-shared/static`; `compression=none`→`HAVE_*=0` |
| native repro | `configure && make && make install` | `cmake -D... && make && make install` | `make -C lib/programs ... install-*` |

`ninja` (as a *generator*) is covered by the cmake class via the `generator`
variant (`-G Ninja`); a standalone ninja/meson package would add a fourth class.
Bazel-native upstreams (the eventual JAX/XLA path) are a fifth. These three
cover the bulk of the current graph (`autotools`×many, `cmake`×4, `makefile`×3).
