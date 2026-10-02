#include <lzo/lzo1x.h>

#include <stdio.h>
#include <string.h>

int main(void) {
  static const unsigned char input[] = "lzo native provider smoke";
  unsigned char compressed[sizeof(input) + sizeof(input) / 16 + 64 + 3];
  unsigned char roundtrip[sizeof(input)];
  unsigned char workmem[LZO1X_1_MEM_COMPRESS];
  lzo_uint compressed_len = 0;
  lzo_uint roundtrip_len = sizeof(roundtrip);

  if (lzo_init() != LZO_E_OK) {
    fprintf(stderr, "lzo_init failed\n");
    return 1;
  }

  int rc = lzo1x_1_compress(input, sizeof(input), compressed, &compressed_len, workmem);
  if (rc != LZO_E_OK) {
    fprintf(stderr, "lzo1x_1_compress failed: %d\n", rc);
    return 2;
  }

  rc = lzo1x_decompress_safe(compressed, compressed_len, roundtrip, &roundtrip_len, NULL);
  if (rc != LZO_E_OK) {
    fprintf(stderr, "lzo1x_decompress_safe failed: %d\n", rc);
    return 3;
  }

  if (roundtrip_len != sizeof(input) || memcmp(input, roundtrip, sizeof(input)) != 0) {
    fprintf(stderr, "lzo roundtrip mismatch\n");
    return 4;
  }

  printf("lzo:2.10:%zu:ok\n", sizeof(input));
  return 0;
}
