#include <hb.h>
#include <stdio.h>

int main(void) {
  hb_buffer_t *buffer = hb_buffer_create();
  if (!buffer) {
    return 1;
  }

  hb_buffer_add_utf8(buffer, "vaso", -1, 0, -1);
  hb_buffer_guess_segment_properties(buffer);

  unsigned int length = 0;
  hb_glyph_info_t *infos = hb_buffer_get_glyph_infos(buffer, &length);
  hb_direction_t direction = hb_buffer_get_direction(buffer);
  const char *version = hb_version_string();

  printf("harfbuzz:%s:len=%u:dir=%u:first=%u\n",
         version,
         length,
         (unsigned int)direction,
         length > 0 ? infos[0].codepoint : 0);

  hb_buffer_destroy(buffer);
  return length == 4 ? 0 : 2;
}
