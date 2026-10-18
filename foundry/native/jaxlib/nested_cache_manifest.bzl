"""content-manifest repository rule for jaxlib nested Bazel cache digests."""

_BUILD = """\
package(default_visibility = ["//visibility:public"])

exports_files(["manifest.json"])
"""

def _watch_if_present(repository_ctx, path_text):
    if not path_text:
        return
    path = repository_ctx.path(path_text)
    if path.exists:
        repository_ctx.watch(path)

def _watch_pinned_archives(repository_ctx, pins, distdir, repository_cache):
    # Watch only the pinned archive files. The manifest's content is the pinned sha list;
    # the cache is read only to verify presence and integrity. watch_tree on the shared
    # repository cache made Bazel digest unrelated extracted trees and hit a Skyframe
    # cycle (post-N1-2 jax/cu130 gate, 2026-10-07).
    for archive in pins.get("archives", []):
        filename = archive.get("filename", "")
        expected_sha = archive.get("sha256", "")
        if distdir and filename:
            _watch_if_present(repository_ctx, distdir + "/" + filename)
        if repository_cache and expected_sha:
            _watch_if_present(repository_ctx, repository_cache + "/content_addressable/sha256/" + expected_sha + "/file")

def _jaxlib_nested_cache_manifest_impl(repository_ctx):
    pins = json.decode(repository_ctx.read(repository_ctx.attr.pins))
    distdir = repository_ctx.attr.distdir or pins.get("distdir", "")
    repository_cache = repository_ctx.attr.repository_cache or pins.get("repository_cache", "")
    _watch_pinned_archives(repository_ctx, pins, distdir, repository_cache)
    args = [
        "python3",
        str(repository_ctx.path(repository_ctx.attr._generator)),
        "--pins",
        str(repository_ctx.path(repository_ctx.attr.pins)),
        "--out",
        str(repository_ctx.path("manifest.json")),
    ]
    if distdir:
        args.extend(["--distdir", distdir])
    if repository_cache:
        args.extend(["--repository-cache", repository_cache])
    result = repository_ctx.execute(
        args,
        timeout = 600,
        quiet = True,
    )
    if result.return_code != 0:
        fail("jaxlib nested cache manifest generation failed (rc={}):\n{}\n{}".format(
            result.return_code,
            result.stdout,
            result.stderr,
        ))
    repository_ctx.file("BUILD.bazel", _BUILD)

jaxlib_nested_cache_manifest = repository_rule(
    implementation = _jaxlib_nested_cache_manifest_impl,
    attrs = {
        "distdir": attr.string(doc = "Nested Bazel distdir. Defaults to nested_http_archives.json."),
        "pins": attr.label(allow_single_file = True, mandatory = True),
        "repository_cache": attr.string(doc = "Nested Bazel repository cache. Defaults to nested_http_archives.json."),
        "_generator": attr.label(
            default = Label("//native/jaxlib:nested_cache_manifest.py"),
            allow_single_file = True,
        ),
    },
    doc = "Write a content manifest for jaxlib's nested Bazel distdir and repository cache.",
)
