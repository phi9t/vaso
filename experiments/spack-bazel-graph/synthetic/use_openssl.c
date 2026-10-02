/* Synthetic OpenSSL consumer. It proves the generated @spack_openssl//:lib
 * provider exposes headers and link flags consistently across Spack/native
 * provider flips. */
#include <openssl/crypto.h>
#include <openssl/evp.h>
#include <openssl/opensslv.h>
#include <openssl/ssl.h>

#include <stdio.h>
#include <string.h>

int main(void) {
    const unsigned char msg[] = "vaso-spack-bazel-graph";
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int digest_len = 0;

    EVP_MD_CTX *ctx = EVP_MD_CTX_new();
    if (ctx == NULL) {
        fprintf(stderr, "EVP_MD_CTX_new failed\n");
        return 1;
    }
    if (EVP_DigestInit_ex(ctx, EVP_sha256(), NULL) != 1 ||
        EVP_DigestUpdate(ctx, msg, strlen((const char *)msg)) != 1 ||
        EVP_DigestFinal_ex(ctx, digest, &digest_len) != 1) {
        EVP_MD_CTX_free(ctx);
        fprintf(stderr, "digest failed\n");
        return 1;
    }
    EVP_MD_CTX_free(ctx);

    SSL_library_init();
    printf("openssl=%s sha256_len=%u first=%02x%02x\n",
           OpenSSL_version(OPENSSL_VERSION),
           digest_len,
           digest[0],
           digest[1]);
    return 0;
}
