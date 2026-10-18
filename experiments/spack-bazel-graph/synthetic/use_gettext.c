#include <gettext-po.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void) {
  const char *header =
      "Project-Id-Version: vaso-gettext-test 1.0\n"
      "Language: en\n"
      "Content-Type: text/plain; charset=UTF-8\n";
  char *language = po_header_field(header, "Language");
  if (language == NULL) {
    fprintf(stderr, "po_header_field failed\n");
    return 1;
  }

  int ok = strcmp(language, "en") == 0 && libgettextpo_version == 0x010000;
  printf("gettextpo:%s:%x\n", language, libgettextpo_version);
  free(language);
  return ok ? 0 : 1;
}
