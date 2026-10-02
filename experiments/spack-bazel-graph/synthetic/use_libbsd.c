#include <bsd/string.h>
#include <stdio.h>
#include <string.h>

int main(void) {
  char out[8];
  size_t n = strlcpy(out, "hello libbsd", sizeof(out));

  printf("%s:%zu\n", out, n);
  return strcmp(out, "hello l") == 0 && n == 12 ? 0 : 1;
}
