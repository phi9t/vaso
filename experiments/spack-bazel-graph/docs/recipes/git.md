# Git frontier recipe

## Position in the hillclimb

`git@2.53.0` is the next lean `py-torch` frontier node after native
`openssh`:

```text
83  numactl  2.0.19  autotools  native; ABI parity green
84  openssh  10.3p1  autotools  native; ABI parity green
85  git      2.53.0  autotools
86  pmix     6.1.0   autotools
```

The focused reference capture used Bazel's vendored Spack inside the insula:

```bash
SPACK_ROOT_PKG='git@2.53.0' \
VASO_NATIVE=0 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_LOCK_OUT=/workspace/experiment/git_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/git_build_graph.json \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

It installed the reference prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/git-2.53.0-okp7t25swul5n7hnm7tpumtwwwybonlw
```

The side-lock capture stopped at the non-canonical-lock test guard after the
reference install, as expected for package evidence captures.

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned to upstream Spack `v1.2.2`. Do not use an ambient
host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/opt/spack/linux-icelake/git-2.53.0-okp7t25swul5n7hnm7tpumtwwwybonlw/.spack/repos/spack_repo/builtin/packages/git/package.py
```

Source provenance:

- package class: `Git(AutotoolsPackage)`
- version: `2.53.0`
- upstream source URL:
  `https://mirrors.edge.kernel.org/pub/software/scm/git/git-2.53.0.tar.gz`
- SHA256:
  `429dc0f5fe5f14109930cdbbb588c5d6ef5b8528910f0d738040744bebdc6275`
- manpage resource URL:
  `https://www.kernel.org/pub/software/scm/git/git-manpages-2.53.0.tar.gz`
- manpage SHA256:
  `4954390466c125e82dce4a978dd1dadda13a916564d908cfdbd319f2e174a8ae`
- build system: `AutotoolsPackage`
- concrete variants: `+man +nls +perl +subtree ~tcltk`
- package patch step:
  - replace `EXTLIBS =` with `#EXTLIBS =` in `Makefile`
  - write `config.mak` with `CSPRNG_METHOD=arc4random` for glibc 2.39
- configure args:
  - `--with-curl=<curl-prefix>`
  - `--with-expat=<expat-prefix>`
  - `--with-openssl=<openssl-prefix>`
  - `--with-libpcre2=<pcre2-prefix>`
  - `--with-zlib=<zlib-ng-prefix>`
  - `--with-iconv=<libiconv-prefix>`
  - `--with-perl=<perl-prefix>/bin/perl`
  - `--without-tcltk`
- build step: `make`
- install step: `make install`
- post-install hooks:
  - install Bash completion to `share/bash-completion/completions/git`
  - install Zsh completion to `share/zsh/site-functions/_git`
  - install manpage resource into `share/man/{man1,man5,man7}`
  - build/install `contrib/subtree` and install `bin/git-subtree`

The focused dependency edges are:

```text
build: autoconf, automake, compiler-wrapper, diffutils, gcc, gmake, libtool, m4
build+link: curl, expat, gettext, libiconv, libidn2, openssl, pcre2, perl, zlib-ng
link: gcc-runtime, glibc
run: openssh
```

## Native recipe

`native/git/git.bzl` mirrors the Spack flow and makes dependency prefixes
explicit:

```text
download git-2.53.0
download git-manpages-2.53.0 into git-manpages/
patch Makefile
write config.mak
PATH=<autoconf>:<automake>:<curl>:<diffutils>:<gettext>:<libtool>:<m4>:<openssh>:<perl>:$PATH
CPPFLAGS/LDFLAGS/LD_RUN_PATH reference only Bazel-built dependency prefixes
./configure --prefix=<prefix> \
  --with-curl=<curl-prefix> \
  --with-expat=<expat-prefix> \
  --with-openssl=<openssl-prefix> \
  --with-libpcre2=<pcre2-prefix> \
  --with-zlib=<zlib-ng-prefix> \
  --with-iconv=<libiconv-prefix> \
  --with-perl=<perl-prefix>/bin/perl \
  --without-tcltk
make V=1
make install
install completions, manpages, and git-subtree
delete .la files
```

The repository rule refuses to run unless the insula has set
`VASO_IN_INSULA=1`. The required native prefix files are:

```text
AUTOCONF_PREFIX
AUTOMAKE_PREFIX
CURL_PREFIX
DIFFUTILS_PREFIX
EXPAT_PREFIX
GETTEXT_PREFIX
LIBICONV_PREFIX
LIBIDN2_PREFIX
LIBTOOL_PREFIX
M4_PREFIX
OPENSSH_PREFIX
OPENSSL_PREFIX
PCRE2_PREFIX
PERL_PREFIX
ZLIB_PREFIX
```

The mechanism verifier must report:

```text
native/git/git.bzl: autotools: AUTOCONF_PREFIX, AUTOMAKE_PREFIX, CURL_PREFIX, DIFFUTILS_PREFIX, EXPAT_PREFIX, GETTEXT_PREFIX, LIBICONV_PREFIX, LIBIDN2_PREFIX, LIBTOOL_PREFIX, M4_PREFIX, OPENSSH_PREFIX, OPENSSL_PREFIX, PCRE2_PREFIX, PERL_PREFIX, ZLIB_PREFIX
```

## Prefix and behavior gate

`//synthetic:git_prefix_parity` compares the native prefix against the
hermetic Spack reference prefix supplied by `run.sh` through `SPACK_GIT_PREFIX`.
The gate covers:

- installed launcher and core executables in `bin/` and `libexec/git-core/`;
- dynamic `NEEDED` dependency parity for representative C executables;
- installed completion scripts and selected manpages, with prefix-normalization;
- `git --version`, repository init/commit, and `git subtree --version`
  behavior inside the insula.

The smoke target exercises `git --version`, creates a small repository, commits
one file, checks completion artifacts, and prints:

```text
git:2.53.0:commit-ok
```
