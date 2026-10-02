#include <magic.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    const char payload[] = "hello file\n";
    magic_t cookie = magic_open(MAGIC_NONE);
    if (cookie == NULL) {
        return 1;
    }
    if (magic_load(cookie, NULL) != 0) {
        fprintf(stderr, "magic_load failed: %s\n", magic_error(cookie));
        magic_close(cookie);
        return 1;
    }
    const char *desc = magic_buffer(cookie, payload, strlen(payload));
    if (desc == NULL || strstr(desc, "text") == NULL) {
        fprintf(stderr, "unexpected magic result: %s\n", desc ? desc : "(null)");
        magic_close(cookie);
        return 1;
    }
    printf("magic:%d:text\n", magic_version());
    magic_close(cookie);
    return 0;
}
