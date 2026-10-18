#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistr.h>

int main(void) {
  const uint8_t input[] = "hello libunistring";
  size_t n = u8_strlen(input);
  const uint8_t *bad = u8_check(input, strlen((const char *)input));

  printf("libunistring:%zu:%s\n", n, bad == NULL ? "valid" : "invalid");
  return n == 18 && bad == NULL ? 0 : 1;
}
