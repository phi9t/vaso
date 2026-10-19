#include <pixman.h>
#include <stdint.h>
#include <stdio.h>

int main(void) {
  uint32_t pixel = 0;
  pixman_image_t *image = pixman_image_create_bits(PIXMAN_a8r8g8b8, 1, 1,
                                                   &pixel, 4);
  if (image == NULL) {
    fprintf(stderr, "pixman_image_create_bits failed\n");
    return 1;
  }

  pixman_color_t color = {
      .red = 0x1111,
      .green = 0x2222,
      .blue = 0x3333,
      .alpha = 0xffff,
  };
  pixman_image_fill_rectangles(PIXMAN_OP_SRC, image, &color, 1,
                               &(pixman_rectangle16_t){0, 0, 1, 1});
  pixman_image_unref(image);

  if (pixel == 0) {
    fprintf(stderr, "pixman fill produced zero pixel\n");
    return 1;
  }

  printf("pixman:%s:pixel=%08x\n", pixman_version_string(), pixel);
  return 0;
}
