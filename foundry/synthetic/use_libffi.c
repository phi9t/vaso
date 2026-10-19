#include <ffi.h>
#include <stdio.h>

static int add_ints(int a, int b) {
  return a + b;
}

int main(void) {
  ffi_cif cif;
  ffi_type *args[2] = {&ffi_type_sint, &ffi_type_sint};
  int a = 19;
  int b = 23;
  int result = 0;
  void *values[2] = {&a, &b};

  if (ffi_prep_cif(&cif, FFI_DEFAULT_ABI, 2, &ffi_type_sint, args) != FFI_OK) {
    fprintf(stderr, "ffi_prep_cif failed\n");
    return 1;
  }

  ffi_call(&cif, FFI_FN(add_ints), &result, values);
  printf("libffi add: %d\n", result);
  return result == 42 ? 0 : 2;
}
