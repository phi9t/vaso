#include <numa.h>
#include <stdio.h>
#include <stdlib.h>

int main(void) {
  if (LIBNUMA_API_VERSION != 2) {
    fprintf(stderr, "unexpected libnuma API version: %d\n", LIBNUMA_API_VERSION);
    return 1;
  }

  struct bitmask *mask = numa_bitmask_alloc(8);
  if (mask == NULL) {
    fputs("numa_bitmask_alloc failed\n", stderr);
    return 1;
  }

  numa_bitmask_clearall(mask);
  numa_bitmask_setbit(mask, 3);
  numa_bitmask_setbit(mask, 5);

  if (!numa_bitmask_isbitset(mask, 3) ||
      !numa_bitmask_isbitset(mask, 5) ||
      numa_bitmask_isbitset(mask, 4) ||
      numa_bitmask_weight(mask) != 2) {
    numa_bitmask_free(mask);
    fputs("unexpected libnuma bitmask behavior\n", stderr);
    return 1;
  }

  numa_bitmask_free(mask);
  printf("numactl:2.0.19:bitmask-ok\n");
  return 0;
}
