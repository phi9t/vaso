# openssl native recipe

## Position in the hillclimb

`openssl` is topo index 28 in the `python` root graph. It follows the
already-migrated `perl` build tool and native `zlib-ng` link dependency, and it
makes the next unmigrated frontier `pigz`:

```text
27 perl     generic
28 openssl  generic
29 pigz     makefile
```

The generated lock keeps OpenSSL's Spack DAG edges while flipping only the
provider:

```json
{
  "package": "openssl",
  "version": "3.6.1",
  "build": "native",
  "native_prefix": "@openssl_native//:lib",
  "link_deps": ["spack_zlib_ng"],
  "link_libs": ["crypto", "ssl"],
  "include_dirs": ["include", "include/openssl"]
}
```

## Spack evidence

All evidence here comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
The reference prefix, package recipe, concrete spec, and build log are from the
hermetic `/vaso/cache/spack` store inside the insula. Do not use an ambient
host Spack checkout for this node.

Reference prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/openssl-3.6.1-lmn3n6rerf4leoklf3dt5qvjnjwqedub
```

Source provenance from the Spack recipe:

- package class: `Openssl(Package)`
- Spack comment: "Uses Fake Autotools, should subclass Package"
- upstream source URL:
  `https://www.openssl.org/source/openssl-3.6.1.tar.gz`
- SHA256:
  `b1bfedcd5b289ff22aee87c9d600f515767ebf45f77168cb6d64f231f518a82e`
- concrete variants: `certs=mozilla docs=false shared=true`
- build system: Spack `generic`; OpenSSL uses its own `./config` script rather
  than Autotools, CMake, Ninja, or Bazel
- package dependencies relevant to this native build: build-time Perl,
  build-time Mozilla CA certificates, and zlib-api for both build and link

## Build recipe

The Spack package clears a few environment variables that OpenSSL's scripts may
otherwise interpret:

```python
for v in ("APPS", "BUILD", "RELEASE", "MACHINE", "SYSTEM"):
    env.pop(v, None)
```

On this Linux x86_64 concrete spec it sets `KERNEL_BITS=64`, uses zlib, keeps
assembly enabled, and builds shared libraries. The hermetic build log shows the
effective command:

```text
./config \
  --prefix=<openssl-prefix> \
  --openssldir=<openssl-prefix>/etc/openssl \
  -I<zlib-ng-prefix>/include \
  -L<zlib-ng-prefix>/lib \
  zlib shared
```

After configure, Spack applies the package's generic Makefile cleanup:

```text
filter_file(r"-arch x86_64", "", "Makefile")
```

Then it runs:

```text
make
make -j1 install_sw
```

Because `certs=mozilla`, Spack copies the Mozilla certificate bundle after
install:

```text
<ca-certificates-mozilla-prefix>/share/cacert.pem
  -> <openssl-prefix>/etc/openssl/cert.pem
```

The native provider follows the same shape inside the CUDA insula:

- refuse to run unless `VASO_IN_INSULA=1`
- fetch OpenSSL 3.6.1 by the same SHA256
- consume native `perl`, `zlib-ng`, and `ca-certificates-mozilla` prefix path
  files
- run OpenSSL `./config` with the same prefix, openssldir, zlib include/lib
  flags, `zlib`, and `shared`
- run `make` and `make -j1 install_sw`
- install the native Mozilla certificate bundle as `etc/openssl/cert.pem`

## Emitted prefix contract

The ABI-relevant install surface is:

- headers under `include/openssl`
- shared and static libraries under `lib64`
- OpenSSL engines under `lib64/engines-3`
- OpenSSL providers under `lib64/ossl-modules`
- pkg-config files under `lib64/pkgconfig`
- executables `bin/openssl` and `bin/c_rehash`
- Mozilla certificate bundle at `etc/openssl/cert.pem`

Important dynamic links in the reference prefix:

- `bin/openssl`: NEEDED `libssl.so.3`, `libcrypto.so.3`, `libc.so.6`
- `lib64/libcrypto.so.3`: SONAME `libcrypto.so.3`
- `lib64/libssl.so.3`: SONAME `libssl.so.3`

The ABI gate reports exported-symbol parity for:

- `lib64/libcrypto.so.3`: 5896 symbols
- `lib64/libssl.so.3`: 603 symbols
- `lib64/engines-3/afalg.so`, `capi.so`, `loader_attic.so`, `padlock.so`
- `lib64/ossl-modules/legacy.so`

## Prefix and ABI gate

`//synthetic:openssl_abi_parity` compares `@openssl_native//:prefix` against
the hermetic Spack reference prefix:

- layout: headers, libraries, pkg-config files, selected data files, and
  selected executables
- ABI: SONAME/exported-symbol parity for every shared object
- data: exact Mozilla `cert.pem` SHA256 and prefix-normalized pkg-config parity
  for `libcrypto.pc`, `libssl.pc`, and `openssl.pc`
- executable contract: NEEDED sets for `bin/openssl` and `bin/c_rehash`
- behavior: `openssl version` and `openssl dgst -sha256` outputs match after
  normalizing the per-run temporary test file name
- downstream consumer: C code links against `ssl`, `crypto`, and native
  zlib-ng, then prints matching version/digest output

Verified native status:

```text
SPACK_ROOT_PKG=python VASO_NATIVE=1 VASO_SPACK_TIMEOUT=600 VASO_FORCE_FETCH_REPOS=@openssl_native ./run.sh
rootfs mode: cuda-bundle (base root: $HOME/.vaso-estate/rootfs)
//synthetic:openssl_abi_parity PASSED in 0.5s
```

The gate reported layout parity with `candidate_count = reference_count = 159`,
matching `etc/openssl/cert.pem` SHA256, prefix-normalized pkg-config parity,
matching executable NEEDED sets, matching OpenSSL executable behavior, and
matching downstream link-and-run output.

Current status: native provider green inside the CUDA insula. The next frontier
is `pigz`.
