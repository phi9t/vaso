/* Synthetic consumer for the Lua native/Spack prefix.
 *
 * The ABI gate compiles this program against both prefixes and requires the
 * same stdout. It exercises the public C API and the dynamically linked
 * liblua.so provider while Lua/LuaRocks executables are covered by exec tests.
 */
#include <stdio.h>

#include <lauxlib.h>
#include <lua.h>
#include <lualib.h>

int main(void) {
    lua_State *L = luaL_newstate();
    if (L == NULL) {
        fprintf(stderr, "luaL_newstate failed\n");
        return 1;
    }
    luaL_openlibs(L);
    if (luaL_dostring(L, "return _VERSION .. ':' .. tostring(math.floor(2.8))") != LUA_OK) {
        fprintf(stderr, "luaL_dostring failed: %s\n", lua_tostring(L, -1));
        lua_close(L);
        return 1;
    }
    const char *result = lua_tostring(L, -1);
    if (result == NULL) {
        fprintf(stderr, "lua result was not a string\n");
        lua_close(L);
        return 1;
    }
    printf("lua:%s:LUA_VERSION_NUM=%d\n", result, LUA_VERSION_NUM);
    lua_close(L);
    return 0;
}
