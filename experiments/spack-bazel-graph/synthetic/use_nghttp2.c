#include <nghttp2/nghttp2.h>

#include <stdio.h>
#include <string.h>

int main(void) {
    const nghttp2_info *info = nghttp2_version(0);
    if (info == NULL || info->version_str == NULL ||
        strcmp(info->version_str, "1.67.1") != 0) {
        fprintf(stderr, "unexpected nghttp2 version\n");
        return 1;
    }

    if (!nghttp2_check_header_name((const uint8_t *)":path", 5)) {
        fprintf(stderr, "valid pseudo-header rejected\n");
        return 2;
    }
    if (nghttp2_check_header_name((const uint8_t *)"Bad", 3)) {
        fprintf(stderr, "invalid uppercase header accepted\n");
        return 3;
    }

    printf("nghttp2:%s:%s\n", info->version_str, NGHTTP2_PROTO_VERSION_ID);
    return 0;
}
