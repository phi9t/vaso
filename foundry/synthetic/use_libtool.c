#include <stdio.h>
#include <string.h>

#include <ltdl.h>

int main(void) {
    const char *path;

    if (lt_dlinit() != 0) {
        fprintf(stderr, "lt_dlinit: %s\n", lt_dlerror());
        return 1;
    }
    if (lt_dlsetsearchpath("/tmp/libtool-native-smoke") != 0) {
        fprintf(stderr, "lt_dlsetsearchpath: %s\n", lt_dlerror());
        lt_dlexit();
        return 1;
    }
    path = lt_dlgetsearchpath();
    if (path == NULL || strcmp(path, "/tmp/libtool-native-smoke") != 0) {
        fprintf(stderr, "unexpected ltdl search path: %s\n", path ? path : "(null)");
        lt_dlexit();
        return 1;
    }
    if (lt_dlexit() != 0) {
        fprintf(stderr, "lt_dlexit: %s\n", lt_dlerror());
        return 1;
    }

    printf("libtool:2.5.4:ltdl-ok\n");
    return 0;
}
