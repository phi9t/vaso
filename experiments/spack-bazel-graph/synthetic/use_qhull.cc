#include <libqhull_r/libqhull_r.h>

#include <cstdio>

int main() {
  qhT qh;
  qh_zero(&qh, stderr);

  coordT points[] = {
      0.0, 0.0,
      1.0, 0.0,
      0.0, 1.0,
      1.0, 1.0,
  };
  char flags[] = "qhull Qt";
  int rc = qh_new_qhull(&qh, 2, 4, points, false, flags, nullptr, stderr);
  int facets = qh.num_facets;
  qh_freeqhull(&qh, qh_ALL);

  int curlong = 0;
  int totlong = 0;
  qh_memfreeshort(&qh, &curlong, &totlong);
  std::printf("qhull:%s:rc=%d:facets=%d:mem=%d/%d\n", qh_version, rc, facets,
              curlong, totlong);
  return rc;
}
