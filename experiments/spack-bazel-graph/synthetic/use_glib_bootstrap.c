#include <glib-object.h>
#include <glib-unix.h>
#include <glib.h>
#include <gmodule.h>

#include <stdio.h>
#include <string.h>

int main(void) {
  GString *s = g_string_new("glib");
  g_string_append(s, "-bootstrap");

  gboolean ok = glib_major_version == 2 &&
                glib_minor_version == 88 &&
                g_str_has_prefix(s->str, "glib") &&
                g_module_supported();
  const char *type_name = g_type_name(G_TYPE_OBJECT);
  ok = ok && type_name != NULL && strcmp(type_name, "GObject") == 0;

  printf("glib-bootstrap:%u.%u:%s:%s\n",
         glib_major_version,
         glib_minor_version,
         s->str,
         type_name ? type_name : "missing");
  g_string_free(s, TRUE);
  return ok ? 0 : 1;
}
