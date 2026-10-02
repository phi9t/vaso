#include <gelf.h>
#include <libelf.h>
#include <stdio.h>

int main(void) {
    if (elf_version(EV_CURRENT) == EV_NONE) {
        fprintf(stderr, "elf_version failed\n");
        return 1;
    }

    printf("elfutils:libelf:%d\n", EV_CURRENT);
    return 0;
}
