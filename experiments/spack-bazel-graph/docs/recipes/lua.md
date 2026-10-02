# lua@5.3.6

## Position in the hillclimb

`lua@5.3.6` is the py-torch frontier node immediately after native
`bdftopcf@1.1.2`. In the captured `SPACK_ROOT_PKG=py-torch` graph it appears
as:

```text
70  lua  5.3.6  makefile
```

The focused reference graph used for this migration is:

```bash
SPACK_ROOT_PKG='lua@5.3.6'
```

The focused all-Spack run wrote `lua_spack_graph.lock.json` and
`lua_build_graph.json`. The native run wrote
`lua_native_spack_graph.lock.json` and `lua_native_build_graph.json`, then
flips `spack_lua` to `@lua_native//:lib` without changing the Spack DAG edges.

## Hermetic Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path in the isolated estate:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/lua/package.py
```

Source provenance from the Spack recipe:

- package class: `Lua(MakefilePackage)`
- concrete Linux build mechanism: upstream Makefile `make linux`, `make install`
- concrete variants: `+shared fetcher=curl`
- upstream source URL: `https://www.lua.org/ftp/lua-5.3.6.tar.gz`
- SHA256: `fc5fd69bb8736323f026672b1b7235da613d7177e72558893a0bdcd320466d60`
- bundled resource: LuaRocks `3.11.1`
- LuaRocks URL:
  `https://luarocks.github.io/luarocks/releases/luarocks-3.11.1.tar.gz`
- LuaRocks SHA256:
  `c3fb3d960dffb2b2fe9de7e3cb004dc4d0b34bb3d342578af84f84325c669102`

The concrete focused node has:

```text
build: compiler-wrapper, gcc, gmake, ncurses, readline, unzip
link: gcc-runtime, glibc, ncurses, readline
run: curl, unzip
```

The reference lock keeps the public link aliases:

```text
lua, lua-5.3, lua-53, lua5.3, lua53
```

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/lua-5.3.6-fmqb7zciz5onmynzamriwmesk3jw3jbl
```

The ABI/behavior-relevant installed surface for this migration is:

```text
bin/lua
bin/luac
bin/luarocks
bin/luarocks-admin
include/lua.h
include/lauxlib.h
include/lualib.h
lib/liblua.a
lib/liblua.so.5.3.6
lib/liblua.so.5.3
lib/liblua.so
lib/liblua5.3.so
lib/liblua53.so
lib/liblua-5.3.so
lib/liblua-53.so
lib/pkgconfig/lua5.3.pc
lib/pkgconfig/lua.pc
etc/luarocks/config-5.3.lua
share/lua/5.3/luarocks/core/cfg.lua
```

The reference `lib/liblua.so.5.3.6` has SONAME `liblua.so.5.3` and 147
exported dynamic symbols.

## Native build recipe

`native/lua/lua.bzl` mirrors the concrete Spack Makefile flow:

```text
download lua-5.3.6.tar.gz
download luarocks-3.11.1.tar.gz
validate CURL_PREFIX, NCURSES_PREFIX, READLINE_PREFIX, and UNZIP_PREFIX
export PATH=<curl-prefix>/bin:<unzip-prefix>/bin:$PATH
make -C <lua-src> linux \
  MYCFLAGS=-I<readline>/include -I<ncurses>/include -I<ncurses>/include/ncursesw \
  MYLDFLAGS=-L<readline>/lib -L<ncurses>/lib -Wl,-rpath,<prefix>/lib:<ncurses>/lib:<readline>/lib -Wl,--disable-new-dtags \
  MYLIBS="-lncurses -ltinfo -lncursesw -ltinfow" \
  CC="gcc -std=gnu99 -fPIC"
make -C <lua-src> INSTALL_TOP=<prefix> install
gcc -shared -Wl,-soname,liblua.so.5.3 ... -o <prefix>/lib/liblua.so.5.3.6
create Spack-compatible liblua symlink aliases
write prefix-normalized lua5.3.pc and lua.pc
./configure --prefix=<prefix> --with-lua=<prefix>
make build
make install
remove libtool archives
emit prefix_path.txt
```

The native provider exposes:

- `@lua_native//:prefix` for the installed prefix filegroup;
- `@lua_native//:prefix_path.txt` for downstream native repository rules;
- `@lua_native//:lib` as the C ABI provider for Lua headers and `liblua`.

The corresponding mechanism verifier is the Makefile dependency-prefix case:

```text
native/lua/lua.bzl: makefile: CURL_PREFIX, NCURSES_PREFIX, READLINE_PREFIX, UNZIP_PREFIX
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, consumes each
dependency through a mandatory Bazel `*_prefix_file`, validates all four
prefixes before build, pins native `curl` and `unzip` on `PATH`, and threads
`readline`/`ncurses` through Makefile `MYCFLAGS`, `MYLDFLAGS`, and `MYLIBS`.
The `# spack-build-system: makefile` marker is intentional: the Lua rule also
builds the LuaRocks resource via `./configure`, but the migrated Spack package
mechanism remains Makefile.

## Gates

The smoke targets are:

```text
//synthetic:use_lua_native
//synthetic:use_lua_prefix_native
```

`//synthetic:use_lua_native` compiles a C consumer against `@lua_native//:lib`
and prints:

```text
lua:Lua 5.3:2:LUA_VERSION_NUM=503
```

`//synthetic:use_lua_prefix_native` runs the installed native Lua and LuaRocks
executables, checks the prefix surface, and prints:

```text
lua:5.3.6:luarocks:3.11.1:ok
```

`//synthetic:lua_abi_parity` compares the native prefix against the hermetic
Spack reference. It covers:

- selected 20-path installed layout;
- SONAME `liblua.so.5.3` and 147 exported dynamic symbols;
- static archive parity for `lib/liblua.a`;
- prefix-normalized `lib/pkgconfig/lua5.3.pc`, `lib/pkgconfig/lua.pc`, and
  `etc/luarocks/config-5.3.lua`;
- executable NEEDED parity for `bin/lua` and `bin/luac`;
- matching `bin/lua`, `bin/luac`, and `bin/luarocks` version behavior;
- matching downstream C link-and-run output.

Current status: native Lua is gated inside the hermetic CUDA insula. The
focused native verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='lua@5.3.6' \
VASO_LOCK_OUT=/workspace/experiment/lua_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/lua_native_build_graph.json \
VASO_NATIVE=1 \
VASO_FORCE_FETCH_REPOS='@lua_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_lua_native //synthetic:use_lua_prefix_native //synthetic:lua_abi_parity //tools:hermetic_native_deps_guard_test //tools:native_build_mechanism_guard_unit_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside the isolated CUDA 12.9.1
insula, reported hermetic Spack version `1.2.2`, passed
`//tools:hermetic_spack_guard_test`, passed
`//tools:hermetic_native_deps_guard_test`, passed
`//tools:native_build_mechanism_guard_unit_test`, passed
`//tools:abi_parity_unit_test`, passed `//synthetic:use_lua_native`, passed
`//synthetic:use_lua_prefix_native`, and passed `//synthetic:lua_abi_parity`.
