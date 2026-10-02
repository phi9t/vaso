#include <stdio.h>

#include <ft2build.h>
#include FT_FREETYPE_H

int main(void) {
  FT_Library library;
  FT_Error err = FT_Init_FreeType(&library);
  if (err) {
    fprintf(stderr, "FT_Init_FreeType failed: %d\n", err);
    return 1;
  }

  int major = 0;
  int minor = 0;
  int patch = 0;
  FT_Library_Version(library, &major, &minor, &patch);
  FT_Done_FreeType(library);

  printf("freetype:%d.%d.%d\n", major, minor, patch);
  return 0;
}
