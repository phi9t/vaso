#include <stdio.h>
#include <string.h>

#include <unicode/uclean.h>
#include <unicode/ucnv.h>
#include <unicode/uversion.h>

int main(void) {
    UVersionInfo version;
    char version_string[U_MAX_VERSION_STRING_LENGTH] = {0};
    UErrorCode status = U_ZERO_ERROR;

    u_getVersion(version);
    u_versionToString(version, version_string);
    if (strcmp(version_string, "76.1") != 0) {
        fprintf(stderr, "unexpected ICU version: %s\n", version_string);
        return 1;
    }

    UConverter *converter = ucnv_open("UTF-8", &status);
    if (U_FAILURE(status) || converter == NULL) {
        fprintf(stderr, "failed to open UTF-8 converter: %s\n", u_errorName(status));
        return 2;
    }
    ucnv_close(converter);
    u_cleanup();

    printf("icu4c:76.1:utf8-ok\n");
    return 0;
}
