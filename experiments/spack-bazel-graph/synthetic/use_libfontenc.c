#include <stdio.h>
#include <string.h>

#include <X11/fonts/fontenc.h>

int main(void) {
    const char *xlfd = "-misc-fixed-medium-r-normal--13-120-75-75-c-70-iso10646-1";
    const char *dir = FontEncDirectory();
    char *name = FontEncFromXLFD(xlfd, (int)strlen(xlfd));
    if (dir == NULL || strstr(dir, "encodings.dir") == NULL) {
        fprintf(stderr, "unexpected font encoding directory: %s\n", dir ? dir : "(null)");
        return 1;
    }
    if (name == NULL || strcmp(name, "iso10646-1") != 0) {
        fprintf(stderr, "unexpected XLFD encoding: %s\n", name ? name : "(null)");
        return 1;
    }
    printf("libfontenc:%s:%s\n", name, strstr(dir, "encodings.dir") ? "encodings-dir" : "missing");
    return 0;
}
