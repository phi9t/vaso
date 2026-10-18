#include <stdio.h>

extern void g_irepository_prepend_search_path(const char *directory);

int main(void) {
  g_irepository_prepend_search_path("/tmp");
  puts("gobject-introspection:girepository-1.0");
  return 0;
}
