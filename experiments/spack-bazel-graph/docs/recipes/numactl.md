# numactl frontier recipe

## Position in the hillclimb

`numactl@2.0.19` is the next lean `py-torch` frontier node after native
`fribidi`. In the captured lean PyTorch graph it appears as:

```text
82  fribidi  1.0.12  autotools  native; ABI parity green
83  numactl  2.0.19  autotools
84  openssh  10.3p1  autotools
```

The focused reference capture used the hermetic driver:

```bash
SPACK_ROOT_PKG='numactl@2.0.19' \
VASO_NATIVE=0 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_LOCK_OUT=/workspace/experiment/numactl_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/numactl_build_graph.json \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

It installed:

```text
/vaso/cache/spack/opt/spack/linux-icelake/numactl-2.0.19-vvxwvsgqsyjqbzwnmpym6z7vdeab2ite
```

and stopped at the non-canonical-lock guard, as expected for a side reference
capture.

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/numactl/package.py
```

Source provenance from the Spack recipe:

- package class: `Numactl(AutotoolsPackage)`
- version: `2.0.19`
- upstream source URL:
  `https://github.com/numactl/numactl/archive/v2.0.19.tar.gz`
- SHA256:
  `8b84ffdebfa0d730fb2fc71bb7ec96bb2d38bf76fb67246fde416a68e04125e4`
- build system: `AutotoolsPackage`
- `force_autoreconf = True`
- package-specific configure args: none
- `autoreconf()` override: `./autogen.sh`

The concrete focused dependency edges are:

```text
build: autoconf, automake, compiler-wrapper, gcc, gmake, libtool, m4
link: gcc-runtime, glibc
```

The hermetic Spack build log shows:

```text
./autogen.sh
<spack-src>/configure --prefix=<numactl-prefix>
make V=1
make install
```

Configure detects `-latomic` for the concrete toolchain. The shared-library
link line uses:

```text
-version-info 1:0:0 -Wl,--version-script,./versions.ldscript \
  -Wl,-init,numa_init -Wl,-fini,numa_fini -latomic
```

and emits SONAME `libnuma.so.1`.

## Build recipe

`native/numactl/numactl.bzl` mirrors the Spack Autotools flow:

```text
download numactl v2.0.19
cd <src>
PATH=<autoconf>:<automake>:<libtool>:<m4>:$PATH
M4=<m4-prefix>/bin/m4
./autogen.sh
./configure --prefix=<prefix>
make V=1
make install
delete .la files
```

The repository rule refuses to run unless the hermetic insula has set
`VASO_IN_INSULA=1`. Build tool dependencies are not discovered from the host.
These mandatory Bazel prefix files are read by the repository rule and
validated by the shell build before configure:

```text
AUTOCONF_PREFIX
AUTOMAKE_PREFIX
LIBTOOL_PREFIX
M4_PREFIX
```

The mechanism verifier must report:

```text
native/numactl/numactl.bzl: autotools: AUTOCONF_PREFIX, AUTOMAKE_PREFIX, LIBTOOL_PREFIX, M4_PREFIX
```

## ABI and behavior gate

`//synthetic:numactl_abi_parity` compares the native prefix against the
hermetic Spack reference prefix supplied by `run.sh` through
`SPACK_NUMACTL_PREFIX`. The gate covers:

- `include/`;
- `lib/libnuma.so -> libnuma.so.1 -> libnuma.so.1.0.0`;
- SONAME and exported dynamic symbol parity for `libnuma`;
- `lib/pkgconfig/numa.pc`;
- `bin/numactl` and `bin/numastat` presence;
- `bin/numactl --show` behavior inside the insula;
- a downstream C link-and-run consumer that checks `LIBNUMA_API_VERSION` and
  stable bitmask APIs.

The smoke target prints:

```text
numactl:2.0.19:bitmask-ok
```
