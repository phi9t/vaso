#include <event2/event.h>
#include <event2/thread.h>

#include <stdio.h>
#include <string.h>

int main(void) {
    const char *version = event_get_version();
    if (version == NULL || strcmp(version, "2.1.12-stable") != 0) {
        fprintf(stderr, "unexpected libevent version: %s\n", version ? version : "(null)");
        return 1;
    }

    if (evthread_use_pthreads() != 0) {
        fprintf(stderr, "evthread_use_pthreads failed\n");
        return 2;
    }

    struct event_base *base = event_base_new();
    if (base == NULL) {
        fprintf(stderr, "event_base_new failed\n");
        return 3;
    }
    event_base_free(base);

    printf("libevent:%s:pthreads\n", version);
    return 0;
}
