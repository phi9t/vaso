#include <stdio.h>
#include <string.h>
#include <uuid.h>

int main(void) {
    const char *input = "00112233-4455-6677-8899-aabbccddeeff";
    uuid_t value;
    uuid_t copy;
    uuid_t zero;
    char out[37];

    if (uuid_parse(input, value) != 0) {
        fprintf(stderr, "uuid_parse failed\n");
        return 1;
    }
    uuid_unparse_lower(value, out);
    if (strcmp(out, input) != 0) {
        fprintf(stderr, "unexpected uuid_unparse_lower output: %s\n", out);
        return 2;
    }

    uuid_copy(copy, value);
    if (uuid_compare(copy, value) != 0) {
        fprintf(stderr, "uuid_copy/uuid_compare mismatch\n");
        return 3;
    }

    uuid_clear(zero);
    if (!uuid_is_null(zero)) {
        fprintf(stderr, "uuid_clear did not produce null UUID\n");
        return 4;
    }
    if (uuid_is_null(value)) {
        fprintf(stderr, "parsed UUID unexpectedly null\n");
        return 5;
    }

    printf("uuid=%s compare=%d null=%d\n", out, uuid_compare(copy, value), uuid_is_null(zero));
    return 0;
}
