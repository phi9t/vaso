# OpenSSH frontier recipe

## Position in the hillclimb

`openssh@10.3p1` is the next lean `py-torch` frontier node after native
`numactl`:

```text
82  fribidi  1.0.12  autotools  native; ABI parity green
83  numactl  2.0.19  autotools  native; ABI parity green
84  openssh  10.3p1  autotools
85  git      2.53.0  autotools
```

The focused reference capture used Bazel's vendored Spack inside the insula:

```bash
SPACK_ROOT_PKG='openssh@10.3p1' \
VASO_NATIVE=0 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_LOCK_OUT=/workspace/experiment/openssh_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/openssh_build_graph.json \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

It installed the reference prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/openssh-10.3p1-s4m26ugek53hj42s3jwywoeuiaye4ocs
```

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned to upstream Spack `v1.2.2`. Do not use an ambient
host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/openssh/package.py
```

Source provenance:

- package class: `Openssh(AutotoolsPackage)`
- version: `10.3p1`
- upstream source URL:
  `https://mirrors.sonic.net/pub/OpenBSD/OpenSSH/portable/openssh-8.7p1.tar.gz`
  with the `10.3p1` version substituting the archive basename
- SHA256:
  `56682a36bb92dcf4b4f016fd8ec8e74059b79a8de25c15d670d731e7d18e45f4`
- build system: `AutotoolsPackage`
- concrete variant: `+gssapi`
- patch step:
  - replace `$(INSTALL) -m 4711` with `$(INSTALL) -m711` in `Makefile.in`
  - replace `if (n != 3 && n != 4)` with `if (n < 2)` in `configure`
- configure args:
  - `--with-privsep-path=<prefix>/var/empty`
  - `--with-kerberos5=<krb5-prefix>`
- install step: serial `make install`

The focused dependency edges are:

```text
build: compiler-wrapper, gcc, gmake
build+link: krb5, libedit, ncurses, openssl, zlib-ng
link: gcc-runtime, glibc, libxcrypt
```

## Native recipe

`native/openssh/openssh.bzl` mirrors the Spack flow and makes all non-toolchain
dependency prefixes explicit:

```text
download openssh-10.3p1
patch Makefile.in and configure
CPPFLAGS/LDFLAGS include only Bazel-built dependency prefixes
./configure --prefix=<prefix> \
  --with-privsep-path=<prefix>/var/empty \
  --with-kerberos5=<krb5-prefix> \
  --with-ssl-dir=<openssl-prefix> \
  --with-zlib=<zlib-ng-prefix>
make V=1
make install
delete .la files
```

The repository rule refuses to run unless the insula has set
`VASO_IN_INSULA=1`. The required native prefix files are:

```text
KRB5_PREFIX
LIBEDIT_PREFIX
LIBXCRYPT_PREFIX
NCURSES_PREFIX
OPENSSL_PREFIX
ZLIB_PREFIX
```

`libedit` and `ncurses` stay explicit dependency prefixes because Spack's DAG
contains them, but the native configure invocation does not force libedit on:
the Spack reference `sftp` binary does not carry a `libedit.so.0` NEEDED edge.

The mechanism verifier must report:

```text
native/openssh/openssh.bzl: autotools: KRB5_PREFIX, LIBEDIT_PREFIX, LIBXCRYPT_PREFIX, NCURSES_PREFIX, OPENSSL_PREFIX, ZLIB_PREFIX
```

## Prefix and behavior gate

`//synthetic:openssh_prefix_parity` compares the native prefix against the
hermetic Spack reference prefix supplied by `run.sh` through
`SPACK_OPENSSH_PREFIX`. The gate covers:

- installed client/server executables in `bin/`, `sbin/`, and `libexec/`;
- dynamic `NEEDED` dependency parity for those executables;
- generated `etc/ssh_config`, `etc/sshd_config`, and `var/empty`;
- `ssh -V` behavior inside the insula.

The smoke target exercises `ssh -V`, generates an Ed25519 key with
`ssh-keygen`, checks the public key format, and prints:

```text
openssh:10.3p1:keygen-ok
```
