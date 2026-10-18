#include <pmix.h>

#include <stdio.h>
#include <string.h>

int main(void) {
    const char *version = PMIx_Get_version();
    if (version == NULL || strstr(version, "6.1.0") == NULL) {
        fprintf(stderr, "unexpected PMIx version: %s\n", version ? version : "(null)");
        return 1;
    }
    printf("pmix:%s\n", version);
    return 0;
}
