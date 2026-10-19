#include <stdint.h>
#include <stdio.h>

#include <fxdiv.h>

int main(void) {
  const struct fxdiv_divisor_uint32_t divisor = fxdiv_init_uint32_t(7);
  const uint32_t input = 100;
  const uint32_t quotient = fxdiv_quotient_uint32_t(input, divisor);
  const uint32_t remainder = fxdiv_remainder_uint32_t(input, divisor);
  if (quotient != 14 || remainder != 2) {
    fprintf(stderr, "fxdiv mismatch: %u / 7 -> %u r %u\n", input, quotient, remainder);
    return 1;
  }
  printf("fxdiv:%u:%u\n", quotient, remainder);
  return 0;
}
