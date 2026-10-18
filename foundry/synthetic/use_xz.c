#include <lzma.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

int main(void) {
  const char *payload = "hello xz";
  size_t in_size = strlen(payload) + 1;

  size_t out_size = lzma_stream_buffer_bound(in_size);
  uint8_t *out = (uint8_t *)malloc(out_size);
  if (!out) {
    fprintf(stderr, "oom\n");
    return 1;
  }

  size_t out_pos = 0;
  lzma_ret r = lzma_easy_buffer_encode(
      /*preset=*/6, /*check=*/LZMA_CHECK_CRC64,
      /*allocator=*/NULL,
      (const uint8_t *)payload, in_size,
      out, &out_pos, out_size);
  if (r != LZMA_OK) {
    fprintf(stderr, "encode failed: %d\n", (int)r);
    free(out);
    return 1;
  }

  uint8_t decoded[64] = {0};
  size_t decoded_pos = 0;
  size_t in_pos = 0;
  uint64_t memlimit = UINT64_MAX;
  r = lzma_stream_buffer_decode(
      &memlimit,
      /*flags=*/0,
      /*allocator=*/NULL,
      out, &in_pos, out_pos,
      decoded, &decoded_pos, sizeof(decoded));
  free(out);
  if (r != LZMA_OK) {
    fprintf(stderr, "decode failed: %d\n", (int)r);
    return 1;
  }

  printf("%s\n", (char *)decoded);
  return 0;
}
