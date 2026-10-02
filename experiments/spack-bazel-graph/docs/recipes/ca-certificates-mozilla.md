# ca-certificates-mozilla native recipe

## Position in the hillclimb

`ca-certificates-mozilla` is the first non-toolchain node in the installed
`python` build DAG:

```text
0 ca-certificates-mozilla  generic
1 compiler-wrapper         generic [toolchain]
2 gcc                      autotools [toolchain]
3 glibc                    autotools [toolchain]
4 gcc-runtime              generic [toolchain]
5 gmake                    generic [toolchain]
6 berkeley-db              autotools
7 libffi                   autotools
...
30 python                  generic
```

This is the first data-prefix migration. It has no headers, libraries, SONAME,
or downstream C link contract; the contract is byte identity of the installed
certificate bundle.

## Spack evidence

Reference prefix:

`/vaso/cache/spack/opt/spack/linux-icelake/ca-certificates-mozilla-2026-03-19-rqrucx6l7glresbjdrjgxiingvbp2rck`

Installed files:

```text
share/cacert.pem
```

Source provenance from the installed SBOM and archived package recipe:

- URL: `https://curl.se/ca/cacert-2026-03-19.pem`
- SHA256: `b6e66569cc3d438dd5abe514d0df50005d570bfc96c14dca8f768d020cb96171`
- Spack package class: `Package`
- Build system variant: `generic`

The Spack build log has a single phase:

```text
ca-certificates-mozilla: Executing phase: 'install'
Installing cacert-2026-03-19.pem to <prefix>/share/cacert.pem
```

The archived package recipe implements:

```python
def url_for_version(self, version):
    return f"https://curl.se/ca/cacert-{version}.pem"

def patch(self):
    self.spec.pem_path = join_path(self.prefix.share, "cacert.pem")

def install(self, spec, prefix):
    share = mkdirp(prefix.share)
    install(f"cacert-{spec.version}.pem", join_path(share, "cacert.pem"))
```

## Native build

`native/ca_certificates_mozilla/ca_certificates_mozilla.bzl` downloads the
pinned PEM directly into:

```text
prefix/share/cacert.pem
```

The repository exposes:

- `@ca_certificates_mozilla_native//:prefix` for prefix parity testing
- `@ca_certificates_mozilla_native//:lib` as an empty `cc_library` data carrier,
  preserving the stable provider-alias shape used by migrated nodes

## Prefix parity gate

`//synthetic:ca_certificates_mozilla_prefix_parity` runs
`tools/abi_parity.py` with:

```text
--data-path share/cacert.pem
```

For this non-ELF package the gate verifies:

- the ABI-relevant layout includes `share/cacert.pem`
- `share/cacert.pem` exists in both prefixes
- the candidate and Spack reference PEM files have identical SHA256 hashes

The ELF SONAME/symbol axis is vacuously true because the prefix has no shared
libraries, and no downstream C link/run axis is requested.
