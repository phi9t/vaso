#define PCRE2_CODE_UNIT_WIDTH 8
#include <pcre2.h>

#include <stdio.h>
#include <string.h>

int main(void) {
  PCRE2_SPTR pattern = (PCRE2_SPTR)"^vaso-(spack|bazel)-([0-9]+)$";
  PCRE2_SPTR subject = (PCRE2_SPTR)"vaso-bazel-42";
  int errnum = 0;
  PCRE2_SIZE erroff = 0;
  pcre2_code *re = pcre2_compile(pattern, PCRE2_ZERO_TERMINATED, 0, &errnum, &erroff, NULL);
  if (re == NULL) {
    fprintf(stderr, "compile failed at %zu: %d\n", (size_t)erroff, errnum);
    return 1;
  }

  pcre2_match_data *match = pcre2_match_data_create_from_pattern(re, NULL);
  int rc = pcre2_match(
      re, subject, PCRE2_ZERO_TERMINATED, 0, 0, match, NULL);
  if (rc != 3) {
    fprintf(stderr, "match failed: %d\n", rc);
    pcre2_match_data_free(match);
    pcre2_code_free(re);
    return 2;
  }

  PCRE2_SIZE *ovector = pcre2_get_ovector_pointer(match);
  size_t group_len = (size_t)(ovector[5] - ovector[4]);
  char version[64] = {0};
  if (pcre2_config(PCRE2_CONFIG_VERSION, version) <= 0 ||
      strncmp(version, "10.44", 5) != 0) {
    fprintf(stderr, "unexpected pcre2 version: %s\n", version);
    pcre2_match_data_free(match);
    pcre2_code_free(re);
    return 3;
  }

  printf("pcre2:10.44:%.*s:%zu:ok\n",
         (int)group_len, (const char *)subject + ovector[4], group_len);
  pcre2_match_data_free(match);
  pcre2_code_free(re);
  return 0;
}
