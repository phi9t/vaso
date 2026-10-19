#include <iconv.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

int main(void) {
  iconv_t cd = iconv_open("UTF-8", "ISO-8859-1");
  if (cd == (iconv_t)-1) {
    fprintf(stderr, "iconv_open failed\n");
    return 1;
  }

  // ISO-8859-1 bytes: 'c' 'a' 'f' 0xE9 -> "café" in UTF-8.
  char input_buf[] = {'c', 'a', 'f', (char)0xE9, 0};
  char output_buf[32] = {0};

  char *in_ptr = input_buf;
  size_t in_left = strlen(input_buf);
  char *out_ptr = output_buf;
  size_t out_left = sizeof(output_buf);

  size_t r = iconv(cd, &in_ptr, &in_left, &out_ptr, &out_left);
  iconv_close(cd);
  if (r == (size_t)-1) {
    fprintf(stderr, "iconv failed\n");
    return 1;
  }

  printf("%s\n", output_buf);
  return 0;
}
