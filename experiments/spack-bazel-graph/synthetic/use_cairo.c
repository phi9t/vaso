#include <cairo.h>
#include <stdio.h>

int main(void) {
  cairo_surface_t *surface = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, 2, 2);
  if (cairo_surface_status(surface) != CAIRO_STATUS_SUCCESS) {
    fprintf(stderr, "cairo_image_surface_create failed: %s\n",
            cairo_status_to_string(cairo_surface_status(surface)));
    cairo_surface_destroy(surface);
    return 1;
  }

  cairo_t *cr = cairo_create(surface);
  if (cairo_status(cr) != CAIRO_STATUS_SUCCESS) {
    fprintf(stderr, "cairo_create failed: %s\n", cairo_status_to_string(cairo_status(cr)));
    cairo_destroy(cr);
    cairo_surface_destroy(surface);
    return 1;
  }

  cairo_set_source_rgba(cr, 0.25, 0.5, 0.75, 1.0);
  cairo_paint(cr);
  cairo_destroy(cr);
  cairo_surface_flush(surface);

  unsigned char *data = cairo_image_surface_get_data(surface);
  unsigned int pixel = 0;
  for (int i = 0; i < 4; ++i) {
    pixel |= ((unsigned int)data[i]) << (8 * i);
  }
  cairo_surface_destroy(surface);

  if (pixel == 0) {
    fputs("cairo paint produced a zero pixel\n", stderr);
    return 1;
  }

  printf("cairo:%s:pixel=%08x\n", cairo_version_string(), pixel);
  return 0;
}
