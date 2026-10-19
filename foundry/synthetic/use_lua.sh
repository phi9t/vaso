#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*lua_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix)"

test -x "$PREFIX/bin/lua"
test -x "$PREFIX/bin/luac"
test -x "$PREFIX/bin/luarocks"
test -f "$PREFIX/include/lua.h"
test -f "$PREFIX/lib/liblua.so.5.3.6"
test -f "$PREFIX/lib/liblua.a"
test -L "$PREFIX/lib/liblua.so"
test -L "$PREFIX/lib/pkgconfig/lua.pc"
test -f "$PREFIX/etc/luarocks/config-5.3.lua"
test -f "$PREFIX/share/lua/5.3/luarocks/core/cfg.lua"

lua_version="$("$PREFIX/bin/lua" -e 'io.write(_VERSION)')"
luac_version="$("$PREFIX/bin/luac" -v 2>&1)"
luarocks_version="$("$PREFIX/bin/luarocks" --version | head -1)"

[[ "$lua_version" == "Lua 5.3" ]]
[[ "$luac_version" == *"Lua 5.3.6"* ]]
[[ "$luarocks_version" == "$PREFIX/bin/luarocks 3.11.1"* ]]

echo "lua:5.3.6:luarocks:3.11.1:ok"
