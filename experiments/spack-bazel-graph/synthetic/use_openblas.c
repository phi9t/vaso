#include <cblas.h>

#include <math.h>
#include <stdio.h>
#include <string.h>

int main(void) {
  const double a[4] = {1.0, 2.0, 3.0, 4.0};
  const double b[4] = {5.0, 6.0, 7.0, 8.0};
  double c[4] = {0.0, 0.0, 0.0, 0.0};
  const double want[4] = {19.0, 22.0, 43.0, 50.0};

  cblas_dgemm(CblasRowMajor, CblasNoTrans, CblasNoTrans,
              2, 2, 2, 1.0, a, 2, b, 2, 0.0, c, 2);
  for (int i = 0; i < 4; ++i) {
    if (fabs(c[i] - want[i]) > 1e-12) {
      fprintf(stderr, "dgemm mismatch at %d: got %.17g want %.17g\n",
              i, c[i], want[i]);
      return 1;
    }
  }

  const char *config = openblas_get_config();
  if (config == NULL || strstr(config, "OpenBLAS 0.3.33") == NULL) {
    fprintf(stderr, "unexpected OpenBLAS config: %s\n",
            config == NULL ? "(null)" : config);
    return 2;
  }

  printf("openblas:0.3.33:%.1f:%.1f:ok\n", c[0], c[3]);
  return 0;
}
