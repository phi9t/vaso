#include <md5.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

int main(void) {
  const char *payload = "hello libmd";
  char out[MD5_DIGEST_STRING_LENGTH];

  if (MD5Data((const uint8_t *)payload, strlen(payload), out) == NULL) {
    fprintf(stderr, "MD5Data failed\n");
    return 1;
  }

  printf("%s\n", out);
  return strcmp(out, "16f5b9ba3226f38ce2a3b4b704c0d70c") == 0 ? 0 : 1;
}
