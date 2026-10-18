#include <raqm.h>
#include <stdio.h>
#include <string.h>

int main(void) {
  if (strcmp(raqm_version_string(), "0.10.5") != 0) {
    fprintf(stderr, "unexpected libraqm version: %s\n", raqm_version_string());
    return 1;
  }
  if (!raqm_version_atleast(0, 10, 5)) {
    fprintf(stderr, "libraqm version predicate failed\n");
    return 2;
  }

  raqm_t *rq = raqm_create();
  if (!rq) {
    fprintf(stderr, "raqm_create failed\n");
    return 3;
  }

  const char text[] = "vaso";
  if (!raqm_set_text_utf8(rq, text, (int)strlen(text))) {
    fprintf(stderr, "raqm_set_text_utf8 failed\n");
    raqm_destroy(rq);
    return 4;
  }
  if (!raqm_set_par_direction(rq, RAQM_DIRECTION_LTR)) {
    fprintf(stderr, "raqm_set_par_direction failed\n");
    raqm_destroy(rq);
    return 5;
  }

  int major = 0;
  int minor = 0;
  int micro = 0;
  raqm_version(&major, &minor, &micro);
  raqm_destroy(rq);

  printf("libraqm:%s:%d.%d.%d:ltr-ok\n",
         raqm_version_string(),
         major,
         minor,
         micro);
  return 0;
}
