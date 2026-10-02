"""Materialize a Spack link-DAG as a Bazel external-repo graph.

`spack_package` is a `repository_rule`: it takes one Spack node's hermetic
install prefix plus the Bazel repo names of its Spack *link* dependencies, and
generates an external repo whose `BUILD` exposes a `cc_library` named `:lib`.
Two providers dispatch off the node's `build` field, but both emit the *same*
`@spack_<pkg>//:lib` target so consumers never change when a node is migrated:

  - `build = "spack"` (default): the hermetic prefix is symlinked in, so Bazel
    sees the real `include/` and `lib*/` layout Spack produced. Structure
    follows the hermetic output path; libs are wired via `linkopts`.
  - `build = "native"`: `:lib` is an `alias` to a native Bazel build target
    (`native_prefix`, e.g. `@zlib_ng_native//:lib`) that produces a
    prefix-identical, ABI-identical install tree. The node's Spack prefix,
    `link_deps`, and `link_libs` are untouched — only the *provider* flips.

`spack_graph` is a `module_extension`: it reads `spack_graph.lock.json` and
calls `spack_package` once per node, so the Bazel module graph *is* the Spack
concrete link-DAG. No per-package BUILD is authored by hand.
"""

_BUILD_TEMPLATE = """\
# GENERATED from a Spack concrete node. Do not edit by hand.
# package: {package}@{version}  hash: {spack_hash}
load("@rules_cc//cc:defs.bzl", "cc_library")

package(default_visibility = ["//visibility:public"])

exports_files(["prefix_path.txt"])

cc_library(
    name = "lib",
    hdrs = glob(["include/**"], allow_empty = True),
    includes = {includes},
    linkopts = {linkopts},
    deps = {deps},
)
"""

# A migrated node re-exports a native build target as :lib. The alias keeps the
# @spack_<pkg>//:lib contract stable so unmigrated consumers depend_on it
# unchanged; only the provider behind the alias flips.
_NATIVE_BUILD_TEMPLATE = """\
# GENERATED from a Spack concrete node flipped to a NATIVE provider.
# package: {package}@{version}  hash: {spack_hash}
# native target: {native_prefix}
# The Spack DAG topology is unchanged; this node's provider is now a native
# Bazel build that emits a prefix-identical, ABI-identical install tree.
package(default_visibility = ["//visibility:public"])

alias(
    name = "lib",
    actual = "{native_prefix}",
)
"""

_MISSING_NATIVE_BUILD_TEMPLATE = """\
# GENERATED stub repo for a Spack node that is not present in the current lock.
# This exists so MODULE.bazel can import stable repo names even when the chosen
# SPACK_ROOT_PKG does not pull them into the concrete DAG snapshot.
load("@rules_cc//cc:defs.bzl", "cc_library")

package(default_visibility = ["//visibility:public"])

cc_library(
    name = "missing",
    linkopts = ["-Wl,--no-undefined", "-l__spack_missing_provider__"],
)

alias(
    name = "lib",
    actual = ":missing",
)
"""

def _spack_package_impl(repository_ctx):
    attr = repository_ctx.attr

    if attr.build == "native":
        if not attr.native_prefix:
            fail("build = 'native' requires native_prefix (a Bazel target label)")
        if attr.native_prefix.startswith(":"):
            repository_ctx.file("BUILD.bazel", _MISSING_NATIVE_BUILD_TEMPLATE)
            return
        repository_ctx.file(
            "BUILD.bazel",
            _NATIVE_BUILD_TEMPLATE.format(
                package = attr.package,
                version = attr.version,
                spack_hash = attr.spack_hash,
                native_prefix = attr.native_prefix,
            ),
        )
        return

    if attr.build != "spack":
        fail("unknown build provider {!r} (expected 'spack' or 'native')".format(attr.build))

    prefix = attr.prefix
    if not prefix:
        fail("build = 'spack' requires prefix (hermetic Spack install prefix)")

    # The hermetic prefix is symlinked in, so Bazel sees the real include/ and
    # lib/ layout that Spack produced. Structure follows the output path.
    repository_ctx.symlink(prefix + "/include", "include")
    lib_dir = prefix + "/lib"
    if repository_ctx.path(prefix + "/lib64").exists:
        lib_dir = prefix + "/lib64"
    repository_ctx.symlink(lib_dir, "lib")
    repository_ctx.file("prefix_path.txt", prefix + "\n")

    # Link the package's own libs by absolute path from the hermetic prefix, and
    # set an rpath so the shared objects resolve at runtime without staging.
    linkopts = [
        "-L" + lib_dir,
        "-Wl,-rpath," + lib_dir,
    ]
    for soname in attr.link_libs:
        linkopts.append("-l" + soname)

    deps = ["@{}//:lib".format(dep) for dep in attr.link_deps]
    includes = attr.include_dirs if attr.include_dirs else ["include"]

    repository_ctx.file(
        "BUILD.bazel",
        _BUILD_TEMPLATE.format(
            package = attr.package,
            version = attr.version,
            spack_hash = attr.spack_hash,
            linkopts = repr(linkopts),
            deps = repr(deps),
            includes = repr(includes),
        ),
    )

spack_package = repository_rule(
    implementation = _spack_package_impl,
    attrs = {
        "package": attr.string(mandatory = True),
        "version": attr.string(mandatory = True),
        "spack_hash": attr.string(mandatory = True),
        "prefix": attr.string(default = "", doc = "hermetic Spack install prefix"),
        "build": attr.string(
            default = "spack",
            doc = "provider for this node: 'spack' (prefix symlink) or 'native' (alias to native_prefix)",
        ),
        "native_prefix": attr.string(
            doc = "when build='native', the Bazel target label re-exported as :lib",
        ),
        "link_deps": attr.string_list(doc = "Bazel repo names of Spack link deps"),
        "link_libs": attr.string_list(doc = "soname stems to pass as -l"),
        "include_dirs": attr.string_list(
            doc = "include search dirs relative to the repo (e.g. include, include/libxml2)",
        ),
    },
)

def _spack_graph_impl(module_ctx):
    lock_label = None
    ensure: list[str] = []
    for mod in module_ctx.modules:
        for graph in mod.tags.graph:
            lock_label = graph.lockfile
            ensure.extend(graph.ensure_repos)
    if lock_label == None:
        fail("spack_graph extension requires a graph(lockfile = ...) tag")

    lock = json.decode(module_ctx.read(lock_label))
    generated = set()
    for repo, node in lock["packages"].items():
        generated.add(repo)
        spack_package(
            name = repo,
            package = node["package"],
            version = node["version"],
            spack_hash = node["spack_hash"],
            prefix = node["prefix"],
            build = node.get("build", "spack"),
            native_prefix = node.get("native_prefix", ""),
            link_deps = node.get("link_deps", []),
            link_libs = node.get("link_libs", []),
            include_dirs = node.get("include_dirs", ["include"]),
        )

    # Ensure stable apparent repo names always exist, even when the chosen root
    # does not pull them into the current lock. Missing repos are defined as
    # stubs that fail at link if accidentally consumed.
    for repo in sorted(set(ensure)):
        if repo in generated:
            continue
        pkg = repo
        if pkg.startswith("spack_"):
            pkg = pkg[len("spack_") :]
        spack_package(
            name = repo,
            package = pkg.replace("_", "-"),
            version = "0",
            spack_hash = "missing",
            prefix = "",
            build = "native",
            native_prefix = ":missing",
            link_deps = [],
            link_libs = [],
            include_dirs = ["include"],
        )

_graph_tag = tag_class(attrs = {
    "lockfile": attr.label(mandatory = True),
    "ensure_repos": attr.string_list(
	        doc = "apparent repo names (e.g. spack_xz) to always define; " +
	              "missing ones become stub repos that fail if built",
    ),
})

spack_graph = module_extension(
    implementation = _spack_graph_impl,
    tag_classes = {"graph": _graph_tag},
)
