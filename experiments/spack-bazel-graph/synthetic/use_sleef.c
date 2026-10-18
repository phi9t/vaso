#include <math.h>
#include <stdio.h>

#include <sleef.h>

int main(void) {
  const double x = 0.5;
  const double s = Sleef_sind1_u35(x);
  const double c = Sleef_cosd1_u35(x);
  const double identity = s * s + c * c;

  if (fabs(identity - 1.0) > 1e-12) {
    fprintf(stderr, "bad sleef identity: %.17g\n", identity);
    return 1;
  }

  printf("sleef:%d.%d.%d:%.12f\n",
         SLEEF_VERSION_MAJOR,
         SLEEF_VERSION_MINOR,
         SLEEF_VERSION_PATCHLEVEL,
         identity);
  return 0;
}
