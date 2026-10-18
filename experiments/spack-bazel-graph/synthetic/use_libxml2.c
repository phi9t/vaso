#include <libxml/parser.h>
#include <libxml/tree.h>
#include <string.h>
#include <stdio.h>

int main(void) {
  LIBXML_TEST_VERSION;
  const char *xml = "<root><child id=\"1\"/></root>";
  xmlDocPtr doc = xmlReadMemory(xml, (int)strlen(xml), "inmem.xml", NULL, 0);
  if (!doc) {
    fprintf(stderr, "parse failed\n");
    return 1;
  }
  xmlNodePtr root = xmlDocGetRootElement(doc);
  if (!root || !root->name) {
    fprintf(stderr, "no root\n");
    xmlFreeDoc(doc);
    return 1;
  }
  printf("%s\n", (const char*)root->name);
  xmlFreeDoc(doc);
  xmlCleanupParser();
  return 0;
}
