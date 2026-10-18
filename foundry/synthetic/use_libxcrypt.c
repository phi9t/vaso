#include <crypt.h>

#include <stdio.h>
#include <string.h>

int main(void) {
  const char *salt = "$6$rounds=5000$vasobazelgraph$";
  char *hash = crypt("hermetic", salt);
  if (hash == NULL) {
    fputs("crypt returned null\n", stderr);
    return 1;
  }
  if (strncmp(hash, salt, strlen(salt)) != 0) {
    fprintf(stderr, "unexpected crypt prefix: %s\n", hash);
    return 2;
  }
  if (strlen(hash) < 64) {
    fprintf(stderr, "unexpected crypt length: %zu\n", strlen(hash));
    return 3;
  }

  printf("libxcrypt:%s:%zu\n", XCRYPT_VERSION_STR, strlen(hash));
  return 0;
}
