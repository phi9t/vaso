extern int bazel_lab_dep(void);

int bazel_lab_pkg(void) {
  return bazel_lab_dep() + 1;
}
