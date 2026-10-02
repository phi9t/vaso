#include <idn2.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void) {
  uint8_t *out = NULL;
  const uint8_t input[] = {'b', 0xc3, 0xbc, 'c', 'h', 'e', 'r', '.', 'e',
                           'x', 'a',  'm',  'p', 'l', 'e', 0};
  int rc = idn2_lookup_u8(input, &out, IDN2_NONTRANSITIONAL);
  if (rc != IDN2_OK) {
    fprintf(stderr, "idn2_lookup_u8 failed: %s\n", idn2_strerror(rc));
    return 1;
  }

  printf("libidn2:%s:%s\n", idn2_check_version(NULL), (const char *)out);
  int ok = strcmp((const char *)out, "xn--bcher-kva.example") == 0;
  free(out);
  return ok ? 0 : 2;
}
