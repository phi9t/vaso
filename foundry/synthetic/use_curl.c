#include <curl/curl.h>

#include <stdio.h>
#include <string.h>

int main(void) {
    const curl_version_info_data *info = curl_version_info(CURLVERSION_NOW);
    if (info == NULL || info->version == NULL) {
        fprintf(stderr, "missing curl version info\n");
        return 1;
    }
    if (strcmp(info->version, "8.20.0") != 0) {
        fprintf(stderr, "unexpected curl version: %s\n", info->version);
        return 2;
    }
    if ((info->features & CURL_VERSION_SSL) == 0) {
        fprintf(stderr, "curl lacks SSL feature\n");
        return 3;
    }
    if ((info->features & CURL_VERSION_HTTP2) == 0) {
        fprintf(stderr, "curl lacks HTTP/2 feature\n");
        return 4;
    }

    CURL *curl = curl_easy_init();
    if (curl == NULL) {
        fprintf(stderr, "curl_easy_init failed\n");
        return 5;
    }
    curl_easy_cleanup(curl);

    printf("curl:%s:ssl:http2\n", info->version);
    return 0;
}
