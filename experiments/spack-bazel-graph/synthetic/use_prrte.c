#include <prte_version.h>

#include <stdio.h>

int main(void) {
  printf("prrte:%ld.%ld.%ld:0x%08lx\n",
         PRTE_VERSION_MAJOR,
         PRTE_VERSION_MINOR,
         PRTE_VERSION_RELEASE,
         (unsigned long)PRTE_NUMERIC_VERSION);
  return (PRTE_VERSION_MAJOR == 4L &&
          PRTE_VERSION_MINOR == 1L &&
          PRTE_VERSION_RELEASE == 0L) ? 0 : 1;
}
