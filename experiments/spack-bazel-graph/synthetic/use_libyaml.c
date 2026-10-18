#include <stdio.h>
#include <string.h>
#include <yaml.h>

int main(void) {
  const unsigned char input[] = "answer: 42\n";
  yaml_parser_t parser;
  yaml_document_t document;

  if (!yaml_parser_initialize(&parser)) {
    fprintf(stderr, "yaml_parser_initialize failed\n");
    return 1;
  }
  yaml_parser_set_input_string(&parser, input, strlen((const char *)input));

  if (!yaml_parser_load(&parser, &document)) {
    fprintf(stderr, "yaml_parser_load failed: %s\n", parser.problem ? parser.problem : "unknown");
    yaml_parser_delete(&parser);
    return 2;
  }

  yaml_node_t *root = yaml_document_get_root_node(&document);
  int ok = root != NULL && root->type == YAML_MAPPING_NODE;
  printf("libyaml:%s:%s\n", yaml_get_version_string(), ok ? "mapping" : "unexpected");

  yaml_document_delete(&document);
  yaml_parser_delete(&parser);
  return ok ? 0 : 3;
}
