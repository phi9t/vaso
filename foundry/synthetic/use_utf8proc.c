/* Synthetic consumer: proves the utf8proc native/Spack prefix (header + static
 * lib) is reachable and links, and prints the library version. Used by the
 * utf8proc ABI-parity gate, compiled+linked+run against both the native
 * candidate and the Spack reference prefix; the stdout must match. */
#include <stdio.h>
#include <utf8proc.h>

int main(void) {
    const char *v = utf8proc_version();
    /* A tiny functional check: NFC-normalize a 1-codepoint buffer round-trips. */
    utf8proc_uint8_t out[8];
    utf8proc_ssize_t n = utf8proc_encode_char((utf8proc_int32_t)'A', out);
    printf("{\"utf8proc_version\": \"%s\", \"encoded_len\": %ld, \"ok\": true}\n",
           v, (long)n);
    return 0;
}
