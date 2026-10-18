#include <stdio.h>

#include <db.h>

int main(void) {
    int major = 0;
    int minor = 0;
    int patch = 0;
    const char *version = db_version(&major, &minor, &patch);
    printf("%s %d.%d.%d\n", version, major, minor, patch);
    return 0;
}
