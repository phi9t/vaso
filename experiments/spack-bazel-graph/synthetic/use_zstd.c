/* Synthetic consumer: proves the zstd native/Spack prefix (header + shared lib)
 * is reachable and links, round-trips a compress/decompress, and prints the
 * library version. Used by the zstd ABI-parity gate: compiled+linked+run
 * against both the native candidate and the Spack reference prefix; stdout must
 * match, and the shared-lib SONAME + exported symbols must agree. */
#include <stdio.h>
#include <string.h>
#include <zstd.h>

int main(void) {
    const char *msg = "vaso-spack-bazel-graph-zstd";
    char comp[128];
    char back[128];
    size_t c = ZSTD_compress(comp, sizeof(comp), msg, strlen(msg), 3);
    if (ZSTD_isError(c)) {
        fprintf(stderr, "compress failed: %s\n", ZSTD_getErrorName(c));
        return 1;
    }
    size_t d = ZSTD_decompress(back, sizeof(back), comp, c);
    if (ZSTD_isError(d) || d != strlen(msg) || memcmp(back, msg, d) != 0) {
        fprintf(stderr, "round-trip mismatch\n");
        return 1;
    }
    printf("{\"zstd_version\": \"%s\", \"roundtrip\": \"%.*s\", \"ok\": true}\n",
           ZSTD_versionString(), (int)d, back);
    return 0;
}
