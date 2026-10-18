#define PACKAGE "vaso-binutils-consumer"
#define PACKAGE_VERSION "1"

#include <bfd.h>
#include <inttypes.h>
#include <stdio.h>

int main(void) {
    const char *end = NULL;
    unsigned int init = bfd_init();
    bfd_vma parsed = bfd_scan_vma("2a", &end, 16);
    if (init != BFD_INIT_MAGIC || end == NULL || *end != '\0' || parsed != 42) {
        return 1;
    }
    printf("bfd:%u:%" PRIu64 "\n", init, (uint64_t)parsed);
    return 0;
}
