#include <fontconfig/fontconfig.h>
#include <stdio.h>

int main(void) {
  if (!FcInit()) {
    fputs("FcInit failed\n", stderr);
    return 1;
  }

  int version = FcGetVersion();
  FcFini();

  printf("fontconfig:%d.%d.%d\n", version / 10000, (version / 100) % 100,
         version % 100);
  return 0;
}
