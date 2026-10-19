#include <sigsegv.h>

#include <stdio.h>

static int handler(void *fault_address, int serious) {
  (void)fault_address;
  (void)serious;
  return 0;
}

int main(void) {
  int installed = sigsegv_install_handler(handler);
  if (installed != 0) {
    fprintf(stderr, "sigsegv_install_handler failed: %d\n", installed);
    return 1;
  }
  sigsegv_deinstall_handler();

  printf("libsigsegv:%04x:%04x\n", LIBSIGSEGV_VERSION, libsigsegv_version);
  return libsigsegv_version == LIBSIGSEGV_VERSION ? 0 : 2;
}
