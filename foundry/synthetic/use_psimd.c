#include <stdio.h>

#include <psimd.h>

int main(void) {
  const float input[4] = {1.0f, -2.0f, 3.5f, 8.0f};
  float output[4] = {0.0f, 0.0f, 0.0f, 0.0f};
  const psimd_f32 values = psimd_load_f32(input);
  psimd_store_f32(output, values);
  for (int i = 0; i < 4; ++i) {
    if (output[i] != input[i]) {
      fprintf(stderr, "psimd lane %d mismatch: %.8g != %.8g\n", i, output[i], input[i]);
      return 1;
    }
  }
  printf("psimd:%.1f:%.1f:%.1f:%.1f\n", output[0], output[1], output[2], output[3]);
  return 0;
}
