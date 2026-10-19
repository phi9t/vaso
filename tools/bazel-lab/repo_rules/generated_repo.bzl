def _lab_generated_repo_impl(repository_ctx):
    env_value = repository_ctx.os.environ.get("LAB_REPO_ENV", "unset")
    stamp = repository_ctx.execute(["/bin/sh", "-c", "date +%s%N"]).stdout.strip()
    repository_ctx.file(
        "BUILD.bazel",
        'package(default_visibility = ["//visibility:public"])\nexports_files(["stamp.txt"])\n',
    )
    repository_ctx.file("stamp.txt", "env=%s\nstamp=%s\n" % (env_value, stamp))

lab_generated_repo = repository_rule(
    implementation = _lab_generated_repo_impl,
    environ = ["LAB_REPO_ENV"],
)

def _lab_content_repo_impl(repository_ctx):
    content = repository_ctx.os.environ.get("LAB_CONTENT", "one")
    repository_ctx.file(
        "BUILD.bazel",
        'package(default_visibility = ["//visibility:public"])\nexports_files(["content.txt"])\n',
    )
    repository_ctx.file("content.txt", "content=%s\n" % content)

lab_content_repo = repository_rule(
    implementation = _lab_content_repo_impl,
    environ = ["LAB_CONTENT"],
)
