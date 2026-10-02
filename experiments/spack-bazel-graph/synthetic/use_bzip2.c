#include <bzlib.h>
#include <stdio.h>
#include <string.h>

int main(void) {
  const char *payload = "hello bzip2";
  char compressed[256];
  unsigned int compressed_len = sizeof(compressed);
  int rc = BZ2_bzBuffToBuffCompress(
      compressed,
      &compressed_len,
      (char *)payload,
      (unsigned int)strlen(payload) + 1,
      9,
      0,
      30);
  if (rc != BZ_OK) {
    fprintf(stderr, "compress failed: %d\n", rc);
    return 1;
  }

  char decoded[256];
  unsigned int decoded_len = sizeof(decoded);
  rc = BZ2_bzBuffToBuffDecompress(
      decoded,
      &decoded_len,
      compressed,
      compressed_len,
      0,
      0);
  if (rc != BZ_OK) {
    fprintf(stderr, "decompress failed: %d\n", rc);
    return 1;
  }

  printf("%s\n", decoded);
  return 0;
}
