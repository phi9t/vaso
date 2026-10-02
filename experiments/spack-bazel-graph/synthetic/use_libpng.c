#include <png.h>
#include <stdio.h>
#include <string.h>

int main(void) {
  png_structp png = png_create_write_struct(PNG_LIBPNG_VER_STRING, NULL, NULL, NULL);
  if (png == NULL) {
    fprintf(stderr, "png_create_write_struct failed\n");
    return 1;
  }
  png_infop info = png_create_info_struct(png);
  if (info == NULL) {
    png_destroy_write_struct(&png, NULL);
    fprintf(stderr, "png_create_info_struct failed\n");
    return 1;
  }

  if (strcmp(png_get_libpng_ver(png), PNG_LIBPNG_VER_STRING) != 0) {
    fprintf(stderr, "runtime/header libpng version mismatch: %s vs %s\n",
            png_get_libpng_ver(png), PNG_LIBPNG_VER_STRING);
    png_destroy_write_struct(&png, &info);
    return 1;
  }

  png_destroy_write_struct(&png, &info);
  printf("libpng:%s:header=%lu\n", png_get_header_ver(NULL),
         (unsigned long)PNG_LIBPNG_VER);
  return 0;
}
