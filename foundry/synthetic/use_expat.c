#include <expat.h>
#include <stdio.h>
#include <string.h>

int main(void) {
  const char *xml = "<root><child/></root>";
  XML_Parser parser = XML_ParserCreate(NULL);
  if (!parser) {
    fprintf(stderr, "parser allocation failed\n");
    return 1;
  }

  enum XML_Status status = XML_Parse(parser, xml, (int)strlen(xml), XML_TRUE);
  if (status != XML_STATUS_OK) {
    fprintf(stderr, "parse failed: %s\n",
            XML_ErrorString(XML_GetErrorCode(parser)));
    XML_ParserFree(parser);
    return 1;
  }

  printf("%s\n", XML_ExpatVersion());
  XML_ParserFree(parser);
  return 0;
}
