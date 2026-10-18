#include <stdio.h>
#include <string.h>

#include <fribidi/fribidi.h>

int main(void) {
    FriBidiChar ascii_a = 0x41;
    FriBidiChar hebrew_alef = 0x05d0;
    FriBidiCharType ascii_type = fribidi_get_bidi_type(ascii_a);
    FriBidiCharType hebrew_type = fribidi_get_bidi_type(hebrew_alef);

    if (strstr(fribidi_version_info, "1.0.12") == NULL) {
        fprintf(stderr, "unexpected fribidi version: %s\n", fribidi_version_info);
        return 1;
    }
    if (!FRIBIDI_IS_STRONG(ascii_type) || FRIBIDI_IS_RTL(ascii_type)) {
        fprintf(stderr, "unexpected ASCII bidi type: 0x%x\n", ascii_type);
        return 2;
    }
    if (!FRIBIDI_IS_STRONG(hebrew_type) || !FRIBIDI_IS_RTL(hebrew_type)) {
        fprintf(stderr, "unexpected Hebrew bidi type: 0x%x\n", hebrew_type);
        return 3;
    }

    printf("fribidi:1.0.12:bidi-ok\n");
    return 0;
}
