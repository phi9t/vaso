#include <stdint.h>
#include <stdio.h>

#include "cpuinfo.h"

int main(void) {
  if (!cpuinfo_initialize()) {
    fprintf(stderr, "cpuinfo_initialize failed\n");
    return 1;
  }
  const uint32_t processors = cpuinfo_get_processors_count();
  const uint32_t packages = cpuinfo_get_packages_count();
  if (processors == 0 || packages == 0) {
    fprintf(stderr, "unexpected cpuinfo counts: processors=%u packages=%u\n",
            processors, packages);
    return 1;
  }
  printf("cpuinfo:%u:%u\n", processors > 0, packages > 0);
  return 0;
}
