#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <gdbm.h>

int main(void) {
    char path[] = "/tmp/vaso-gdbm-XXXXXX";
    int fd = mkstemp(path);
    if (fd < 0) {
        perror("mkstemp");
        return 1;
    }
    close(fd);
    unlink(path);

    GDBM_FILE db = gdbm_open(path, 0, GDBM_NEWDB, 0600, NULL);
    if (db == NULL) {
        fprintf(stderr, "gdbm_open: %s\n", gdbm_strerror(gdbm_errno));
        return 1;
    }

    datum key = {(char *)"alpha", 5};
    datum value = {(char *)"vaso", 4};
    if (gdbm_store(db, key, value, GDBM_REPLACE) != 0) {
        fprintf(stderr, "gdbm_store failed\n");
        gdbm_close(db);
        unlink(path);
        return 1;
    }

    datum fetched = gdbm_fetch(db, key);
    if (fetched.dptr == NULL) {
        fprintf(stderr, "gdbm_fetch failed\n");
        gdbm_close(db);
        unlink(path);
        return 1;
    }

    gdbm_count_t count = 0;
    if (gdbm_count(db, &count) != 0) {
        fprintf(stderr, "gdbm_count failed\n");
        free(fetched.dptr);
        gdbm_close(db);
        unlink(path);
        return 1;
    }

    printf("gdbm=%d.%d.%d count=%llu value=%.*s\n",
           gdbm_version_number[0],
           gdbm_version_number[1],
           gdbm_version_number[2],
           (unsigned long long)count,
           fetched.dsize,
           fetched.dptr);

    free(fetched.dptr);
    gdbm_close(db);
    unlink(path);
    return 0;
}
