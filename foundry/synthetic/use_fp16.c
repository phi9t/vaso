#include <stdint.h>
#include <stdio.h>

#include <fp16.h>

int main(void) {
  const float input = 1.5f;
  const uint16_t half = fp16_ieee_from_fp32_value(input);
  const float output = fp16_ieee_to_fp32_value(half);
  if (half != 0x3e00u || output != input) {
    fprintf(stderr, "fp16 conversion mismatch: 0x%04x -> %.8g\n", half, output);
    return 1;
  }
  printf("fp16:%04x:%.1f\n", half, output);
  return 0;
}
