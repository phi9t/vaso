# Recipe deep-dive: zlib-ng (Spack `autotools` build)

> First package in the topological migration hillclimb. This captures **how
> Spack actually drives the build underneath** so a native Bazel build action
> can reproduce it byte/ABI-identically. Evidence is quoted from the installed
> prefix's `.spack/` metadata (`spack-build-out.txt.gz`, `spack-build-env.txt`,
> `spec.json`), not inferred.

- **Package:** `zlib-ng@2.3.3`  (provides `zlib` compat via `--zlib-compat`)
- **Build system (concretized):** `autotools`
- **Spack hash:** `3vqeggdv3nqcxeuaohkonfn24dog6la7`
- **Prefix:** `.../opt/spack/linux-icelake/zlib-ng-2.3.3-3vqeggdv3nqcxeuaohkonfn24dog6la7`
- **Migration status:** `native` — `@zlib_ng_native//:lib`, ABI gate
  `//synthetic:zlib_ng_abi_parity` green.

## 1. How Spack's `autotools` build system works

Spack's `AutotoolsPackage` / `AutotoolsBuilder` (from
`spack_repo/builtin`, vendored in the prefix's `.spack/repos/.../zlib_ng/package.py`)
runs a fixed **phase pipeline**. For this install the recorded phases were:

```
==> zlib-ng: Executing phase: 'autoreconf'
==> zlib-ng: Executing phase: 'configure'
==> zlib-ng: Executing phase: 'build'
==> zlib-ng: Executing phase: 'install'
```

- **autoreconf** — regenerate `configure` from `configure.ac`/`Makefile.am` when
  needed. For zlib-ng the shipped `configure` is used (autoreconf is a no-op
  guard here); Spack also *filters* the `configure` script for portability
  (libtool `-L`/`-R` fixups, Darwin single-module) before running it.
- **configure** — `AutotoolsBuilder.configure()` runs
  `<srcdir>/configure --prefix=<prefix> <configure_args()>`. `configure_args()`
  is where the package's variants become flags (below).
- **build** — `make` (Spack's `gmake`), parallel by `make_jobs`.
- **install** — `make install` into the staged `--prefix`.

The `build_system=autotools` variant is what selects this builder; a package
can also expose a `cmake` builder (zlib-ng does — see its `CMakeBuilder`), and
Spack picks one per concretization.

## 2. Variants → `configure_args()`

From the vendored `zlib_ng/package.py` `AutotoolsBuilder.configure_args()`:

```python
def configure_args(self):
    args = []
    if self.spec.satisfies("+compat"):            args.append("--zlib-compat")
    if self.spec.satisfies("~opt"):               args.append("--without-optimizations")
    if self.spec.satisfies("~shared"):            args.append("--static")
    if self.spec.satisfies("~new_strategies"):    args.append("--without-new-strategies")
    return args
```

The concretized variant set for this node (`spec.json`):

```
build_system=autotools  compat=True  new_strategies=True  opt=True  pic=True  shared=True
cflags=[] cppflags=[] cxxflags=[] fflags=[] ldflags=[] ldlibs=[]
```

All of `+compat +opt +shared +new_strategies` are the *defaults*, so the only
non-default flag emitted is `--zlib-compat` (from `+compat`). The others
(`~opt`, `~shared`, `~new_strategies`) would add flags but are not set.

## 3. The exact configure command Spack ran

Quoted verbatim from `spack-build-out.txt.gz`:

```
<srcdir>/spack-src/configure \
  --prefix=<data-volume>/.../zlib-ng-2.3.3-3vqeggdv3nqcxeuaohkonfn24dog6la7 \
  --zlib-compat
```

Then `make V=1` and `make install`.

## 4. Compiler + flags (from `spack-build-env.txt` + `make V=1`)

Spack does **not** pass `CFLAGS` on the configure line; zlib-ng's own
`configure` picks the optimization/arch defines. The compiler is the host GCC
behind Spack's `compiler-wrapper` shim:

```
SPACK_CC=/usr/bin/gcc
SPACK_CXX=/usr/bin/g++
```

The first compile line (`make V=1`) shows what `configure` chose — `-O2`, C11,
`-DNDEBUG`, `-DZLIB_COMPAT`, `-DWITH_GZFILEOP`, `-DWITH_OPTIM`, and the full
x86-64 feature define set (`-DX86_SSE2 ... -DX86_AVX512VNNI -DX86_VPCLMULQDQ_CRC`):

```
gcc -O2 -std=c11 -Wall -DNDEBUG -DHAVE_SYMVER -D_LARGEFILE64_SOURCE=1 \
    -DZLIB_COMPAT -DWITH_GZFILEOP -DWITH_OPTIM -DX86_FEATURES \
    -DX86_SSE2 -DX86_SSSE3 -DX86_SSE41 -DX86_SSE42 -DX86_PCLMULQDQ_CRC \
    -DX86_AVX2 -DX86_AVX512 -DX86_AVX512VNNI -DX86_VPCLMULQDQ_CRC ...
```

**Migration consequence:** because `configure` derives these defines itself from
`--zlib-compat` + host CPU detection, a native build that runs the **same
`configure --zlib-compat`** on the same host reproduces the same defines and the
same ABI. That is exactly why `native/zlib_ng/zlib_ng.bzl` runs
`./configure --prefix=<prefix> --zlib-compat && make && make install` and
nothing more — matching the recipe rather than hand-porting flags.

## 5. Emitted install tree (the prefix contract)

```
include/  zconf.h  zlib.h  zlib_name_mangling.h
lib/      libz.a
          libz.so -> libz.so.1.3.1.zlib-ng
          libz.so.1 -> libz.so.1.3.1.zlib-ng
          libz.so.1.3.1.zlib-ng          # SONAME libz.so.1
          pkgconfig/zlib.pc
share/    man/man3/...
```

`--zlib-compat` is what makes the SONAME `libz.so.1` and the exported symbols
the classic `ZLIB_1.2.x` versioned zlib API (verified by `nm -D`), so a
`-lz` consumer links unchanged. The ABI gate (`tools/abi_parity.py`) checks
exactly this: layout set, per-lib SONAME + exported-symbol set, and a
downstream link-and-run producing identical stdout vs the Spack prefix.

## 6. Native reproduction (landed)

`native/zlib_ng/zlib_ng.bzl` fetches zlib-ng `2.3.3` by the **same tag + sha256
the Spack package pins** (`f9c65aa9...907d1`), then:

```sh
./configure --prefix="$PREFIX" --zlib-compat
make -j"$(nproc)"
make install
```

Result: a `prefix/`-identical tree, exposed as `@zlib_ng_native//:lib`. Flipping
the lock (`VASO_NATIVE=1`) makes `@spack_zlib_ng//:lib` an alias to it; the
synthetic consumer and the ABI gate both pass. See
`docs/native-migration.md` §"Native leaf proof".

## 7. Recipe template for the next node

The reusable shape this deep-dive establishes, to apply to the next package in
`build_graph.json`'s `topological_order`:

1. Read `spec.json` → `build_system` + variant set.
2. Read the vendored `package.py` builder for that build system →
   `configure_args()` / `cmake_args()` / phase overrides.
3. Quote the real command from `spack-build-out.txt.gz` (`Executing phase`,
   the `configure`/`cmake` line, first `V=1` compile).
4. Record compiler + whether flags are Spack-injected or build-derived.
5. Capture the emitted prefix contract (headers, versioned libs + SONAME,
   pkgconfig).
6. Reproduce with the matching `rules_foreign_cc`-style build (or captured
   configure/make in the insula) and gate with `tools/abi_parity.py`.
