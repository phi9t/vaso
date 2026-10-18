/* Synthetic consumer: proves the Spack hermetic prefix (headers + libs) is
 * reachable purely through the Bazel-generated @spack_zlib_ng//:lib target.
 *
 * We call into zlib (provided by the Spack zlib-ng package) and print its
 * version plus a round-trip compress/uncompress checksum. If the headers or the
 * shared object did not resolve through the generated graph, this would not
 * compile or link. */
#include <stdio.h>
#include <string.h>
#include <zlib.h>

int main(void) {
    const char *msg = "vaso-spack-bazel-graph";
    unsigned char comp[128];
    unsigned char back[128];
    uLongf comp_len = sizeof(comp);
    uLongf back_len = sizeof(back);

    if (compress(comp, &comp_len, (const Bytef *)msg, (uLong)strlen(msg)) != Z_OK) {
        fprintf(stderr, "compress failed\n");
        return 1;
    }
    if (uncompress(back, &back_len, comp, comp_len) != Z_OK) {
        fprintf(stderr, "uncompress failed\n");
        return 1;
    }
    if (back_len != strlen(msg) || memcmp(back, msg, back_len) != 0) {
        fprintf(stderr, "round-trip mismatch\n");
        return 1;
    }

    printf("{\"zlib_version\": \"%s\", \"roundtrip\": \"%.*s\", \"ok\": true}\n",
           zlibVersion(), (int)back_len, back);
    return 0;
}
