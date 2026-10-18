"""Materialize a checked-in native lock as a Bazel external-repo graph.

`graph_package` is a `repository_rule`: it takes one checked-in lock node plus
the Bazel repo names of its link dependencies, and generates an external repo
whose `BUILD` exposes a `:lib` target. Every active node must use a native
provider:

  - `build = "native"`: `:lib` is an `alias` to a native Bazel build target
    (`native_prefix`, e.g. `@zlib_ng_native//:lib`) that produces a
    prefix-identical, ABI-identical install tree.

`vaso_graph` is a `module_extension`: it reads a checked-in profile lock such
as `locks/torch.lock.json` and calls `graph_package` once per node. It also
emits one-release `@spack_<pkg>` compatibility repos as aliases to the new
`@vaso_<pkg>` repos. No per-package BUILD is authored by hand.
"""

load("//native/common:torch_artifact.bzl", "torch_artifact_repository", "torch_artifact_stub_repository")

_NATIVE_BUILD_TEMPLATE = """\
# GENERATED from a checked-in graph node backed by a native provider.
# package: {package}@{version}  hash: {spack_hash}
# native target: {native_prefix}
package(default_visibility = ["//visibility:public"])

alias(
    name = "lib",
    actual = "{native_prefix}",
)

alias(
    name = "prefix_path.txt",
    actual = "{native_prefix_path}",
)
"""

_PROFILE_BUILD_TEMPLATE = """\
# GENERATED from a vaso_graph profile lock.
# package: {package}@{version}  hash: {spack_hash}
load("@rules_cc//cc:defs.bzl", "cc_library")

package(default_visibility = ["//visibility:public"])

cc_library(
    name = "lib",
    hdrs = {hdrs},
    includes = {include_dirs},
    linkopts = {linkopts},
    deps = {deps},
)

exports_files(["prefix_path.txt"])
"""

_MISSING_NATIVE_BUILD_TEMPLATE = """\
# GENERATED stub repo for a compatibility node that is not present in the current lock.
# This exists so MODULE.bazel can import stable repo names even when the chosen
# profile lock does not pull them into the dependency graph.
load("@rules_cc//cc:defs.bzl", "cc_library")

package(default_visibility = ["//visibility:public"])

cc_library(
    name = "missing",
    linkopts = ["-Wl,--no-undefined", "-l__vaso_missing_provider__"],
)

alias(
    name = "lib",
    actual = ":missing",
)

exports_files(["prefix_path.txt"])
"""

_COMPAT_ALIAS_BUILD_TEMPLATE = """\
# GENERATED one-release compatibility alias. Do not add new consumers here.
package(default_visibility = ["//visibility:public"])

alias(
    name = "lib",
    actual = "@{canonical_repo}//:lib",
)

alias(
    name = "prefix_path.txt",
    actual = "@{canonical_repo}//:prefix_path.txt",
)
"""

def _prefix_path_label(native_prefix):
    return native_prefix.rsplit(":", 1)[0] + ":prefix_path.txt"

def _profile_dep_label(repo):
    return "@{}//:lib".format(repo)

def _profile_linkopt(lib):
    return "-l{}".format(lib)

def _profile_include_roots(include_dirs):
    roots = []
    for include_dir in include_dirs:
        root = include_dir.split("/", 1)[0]
        if root and root not in roots:
            roots.append(root)
    return roots

def _profile_hdrs_expr(include_roots):
    if not include_roots:
        return "[]"
    return "glob({}, allow_empty = True)".format(repr([root + "/**" for root in include_roots]))

def _profile_linkopts(repository_ctx, prefix, link_libs):
    linkopts = []
    if prefix:
        lib_dir = prefix + "/lib"
        if repository_ctx.path(prefix + "/lib64").exists:
            lib_dir = prefix + "/lib64"
        linkopts.extend(["-L" + lib_dir, "-Wl,-rpath," + lib_dir])
    linkopts.extend([_profile_linkopt(lib) for lib in link_libs])
    return linkopts

def _graph_package_impl(repository_ctx):
    attr = repository_ctx.attr
    native_prefix = attr.native_prefix
    if attr.native_prefix_label != None:
        native_prefix = str(attr.native_prefix_label)

    if attr.build == "native":
        if not attr.reexport_native:
            include_roots = _profile_include_roots(attr.include_dirs)
            for include_root in include_roots:
                source = attr.prefix + "/" + include_root
                if attr.prefix and repository_ctx.path(source).exists:
                    repository_ctx.symlink(source, include_root)
            repository_ctx.file("prefix_path.txt", attr.prefix + "\n")
            repository_ctx.file(
                "BUILD.bazel",
                _PROFILE_BUILD_TEMPLATE.format(
                    package = attr.package,
                    version = attr.version,
                    spack_hash = attr.spack_hash,
                    hdrs = _profile_hdrs_expr(include_roots),
                    include_dirs = repr(attr.include_dirs),
                    linkopts = repr(_profile_linkopts(repository_ctx, attr.prefix, attr.link_libs)),
                    deps = repr([_profile_dep_label(dep) for dep in attr.link_deps]),
                ),
            )
            return
        if not native_prefix:
            fail("build = 'native' requires native_prefix (a Bazel target label)")
        if native_prefix.startswith(":"):
            repository_ctx.file("prefix_path.txt", "")
            repository_ctx.file("BUILD.bazel", _MISSING_NATIVE_BUILD_TEMPLATE)
            return
        repository_ctx.file(
            "BUILD.bazel",
            _NATIVE_BUILD_TEMPLATE.format(
                package = attr.package,
                version = attr.version,
                spack_hash = attr.spack_hash,
                native_prefix = native_prefix,
                native_prefix_path = _prefix_path_label(native_prefix),
            ),
        )
        return

    fail("unknown build provider {} (expected one of: native, rootfs, vendored, removed, not-a-target)".format(repr(attr.build)))

graph_package = repository_rule(
    implementation = _graph_package_impl,
    attrs = {
        "package": attr.string(mandatory = True),
        "version": attr.string(mandatory = True),
        "spack_hash": attr.string(mandatory = True),
        "prefix": attr.string(default = "", doc = "captured install prefix, empty for native providers"),
        "build": attr.string(
            mandatory = True,
            doc = "provider for this node; 'spack' is not accepted in active profile locks",
        ),
        "native_prefix": attr.string(
            doc = "when build='native', the Bazel target label re-exported as :lib",
        ),
        "native_prefix_label": attr.label(
            doc = "label-typed copy of native_prefix so generated repos inherit Bzlmod repo mappings",
        ),
        "link_deps": attr.string_list(doc = "Bazel repo names of graph link deps"),
        "link_libs": attr.string_list(doc = "soname stems to pass as -l"),
        "include_dirs": attr.string_list(
            doc = "include search dirs relative to the repo (e.g. include, include/libxml2)",
        ),
        "reexport_native": attr.bool(default = True),
    },
)

def _compat_alias_impl(repository_ctx):
    repository_ctx.file(
        "BUILD.bazel",
        _COMPAT_ALIAS_BUILD_TEMPLATE.format(
            canonical_repo = repository_ctx.attr.canonical_repo,
        ),
    )

compat_alias = repository_rule(
    implementation = _compat_alias_impl,
    attrs = {
        "canonical_repo": attr.string(mandatory = True),
    },
)

def _repo_suffix(repo):
    if not repo.startswith("vaso_"):
        fail("graph repo names must use the vaso_ prefix; got {}".format(repr(repo)))
    return repo[len("vaso_"):]

def _package_from_repo(repo):
    return _repo_suffix(repo).replace("_", "-")

def _compat_repo(repo):
    return "spack_" + _repo_suffix(repo)

_CUDA_FAMILY_PROVIDERS = {
    "cuda": "@cuda_native//:lib",
    "cudnn": "@cudnn_native//:lib",
    "cusparselt": "@cusparselt_native//:lib",
    "cudss": "@cudss_native//:lib",
    "nvshmem": "@nvshmem_native//:lib",
    "nccl": "@nccl_native//:lib",
}

_TORCH_ARTIFACT_LINES = ("cu129", "cu130")
_TORCH_ARTIFACT_MANIFESTS = {
    "cu129": Label("//plans:torch/cu129/torch-artifact.json"),
    "cu130": Label("//plans:torch/cu130/torch-artifact.json"),
}
_HEX = "0123456789abcdef"
_ZERO_SHA256 = "0" * 64

def _validate_profile_tag(profile, lock):
    errors = []
    if profile.name != "torch":
        errors.append("unknown vaso_graph profile {}; expected torch".format(repr(profile.name)))
    if profile.torch_source != None:
        errors.append("profile {} torch_source is reserved for FC-P1".format(profile.name))
    if profile.patches:
        errors.append("profile {} patches are reserved for FC-P1".format(profile.name))
    if profile.cuda_arch_list != "10.0+PTX":
        errors.append("profile {} cuda_arch_list must be 10.0+PTX until FC-P2".format(profile.name))
    if profile.cuda_line not in ("cu129", "cu130"):
        errors.append("profile {} cuda_line must be one of cu129, cu130".format(profile.name))
    if not (profile.compiler.startswith("gcc-") or profile.compiler.startswith("clang-")):
        errors.append("profile {} compiler must name a gcc-* or clang-* family".format(profile.name))
    if profile.compiler != "gcc-13.3":
        errors.append("profile {} compiler must be gcc-13.3".format(profile.name))

    if lock.get("schema_version") != 1:
        errors.append("profile {} lock schema_version must be 1".format(profile.name))
    packages = lock.get("packages")
    if type(packages) != "dict" or not packages:
        errors.append("profile {} lock packages must be a non-empty object".format(profile.name))
        return errors
    root = lock.get("root")
    if type(root) != "string" or not root.startswith("vaso_"):
        errors.append("profile lock root must be a vaso_ repo")
    elif root not in packages:
        errors.append("profile lock root {} is not present in packages".format(repr(root)))

    cuda_seen = {}
    python_node = None
    for repo, node in sorted(packages.items()):
        if type(repo) != "string" or not repo.startswith("vaso_"):
            errors.append("profile lock package keys must be vaso_ repos; got {}".format(repr(repo)))
            continue
        if type(node) != "dict":
            errors.append("profile lock node {} must be an object".format(repo))
            continue
        package = node.get("package")
        if type(package) != "string" or not package:
            errors.append("profile lock node {} package must be a non-empty string".format(repo))
            continue
        expected_repo = "vaso_" + package.replace("-", "_")
        if repo != expected_repo:
            errors.append("profile lock node {} for package {} must be keyed as {}".format(
                repr(repo),
                repr(package),
                repr(expected_repo),
            ))
        build = node.get("build", "spack")
        if build != "native":
            errors.append("{}: checked-in profile locks require build='native', got {}".format(repo, repr(build)))
        native_prefix = node.get("native_prefix")
        if type(native_prefix) != "string" or not native_prefix.endswith("//:lib") or not native_prefix.startswith("@"):
            errors.append("{}: native provider label must be an external :lib target".format(repo))
        if package in _CUDA_FAMILY_PROVIDERS:
            previous = cuda_seen.get(package)
            if previous != None:
                errors.append("CUDA-family package {} has multiple providers: {}, {}".format(package, previous, repo))
            cuda_seen[package] = repo
            expected_provider = _CUDA_FAMILY_PROVIDERS[package]
            if native_prefix != expected_provider:
                errors.append("CUDA-family package {} must use provider {}".format(package, expected_provider))
        if package == "python":
            python_node = node

    if python_node == None:
        errors.append("profile {} lock must include vaso_python".format(profile.name))
    else:
        if profile.python != "3.13.13":
            errors.append("profile {} python must be 3.13.13".format(profile.name))
        if python_node.get("version") != profile.python:
            errors.append("profile {} python {} does not match lock python {}".format(
                profile.name,
                profile.python,
                python_node.get("version"),
            ))
        if python_node.get("native_prefix") != "@python_313_native//:lib":
            errors.append("profile {} python must use provider @python_313_native//:lib".format(profile.name))
    return errors

def _generate_graph(lock, ensure, reexport_native):
    generated = set()
    for repo, node in lock["packages"].items():
        expected_repo = "vaso_" + node["package"].replace("-", "_")
        if repo != expected_repo:
            fail("lock node {} for package {} must be keyed as {}".format(
                repr(repo),
                repr(node["package"]),
                repr(expected_repo),
            ))
        generated.add(repo)
        native_prefix = node.get("native_prefix", "")
        kwargs = {
            "name": repo,
            "package": node["package"],
            "version": node["version"],
            "spack_hash": node["spack_hash"],
            "prefix": node["prefix"],
            "build": node.get("build", "spack"),
            "native_prefix": native_prefix,
            "link_deps": node.get("link_deps", []),
            "link_libs": node.get("link_libs", []),
            "include_dirs": node.get("include_dirs", ["include"]),
            "reexport_native": reexport_native,
        }
        if reexport_native and native_prefix and not native_prefix.startswith(":"):
            kwargs["native_prefix_label"] = native_prefix
        graph_package(**kwargs)
        compat_alias(
            name = _compat_repo(repo),
            canonical_repo = repo,
        )

    # Ensure stable apparent repo names always exist, even when the chosen root
    # does not pull them into the current lock. Missing repos are defined as
    # stubs that fail at link if accidentally consumed.
    for repo in sorted(set(ensure)):
        _repo_suffix(repo)
        if repo in generated:
            continue
        graph_package(
            name = repo,
            package = _package_from_repo(repo),
            version = "0",
            spack_hash = "missing",
            prefix = "",
            build = "native",
            native_prefix = ":missing",
            link_deps = [],
            link_libs = [],
            include_dirs = ["include"],
            reexport_native = reexport_native,
        )
        compat_alias(
            name = _compat_repo(repo),
            canonical_repo = repo,
        )

def _validate_torch_artifact_tag(tag):
    errors = []
    if tag.name != "torch":
        errors.append("torch_artifact name must be 'torch', got {}".format(repr(tag.name)))
    if tag.line not in _TORCH_ARTIFACT_LINES:
        errors.append("torch_artifact line must be one of cu129, cu130")
    if not tag.archive:
        errors.append("torch_artifact archive must be non-empty")
    bad_sha_char = False
    for index in range(len(tag.sha256)):
        if tag.sha256[index] not in _HEX:
            bad_sha_char = True
    if len(tag.sha256) != 64 or bad_sha_char:
        errors.append("torch_artifact sha256 must be 64 lowercase hex characters")
    return errors

def _validate_torch_artifact_manifest(line, manifest):
    errors = []
    if type(manifest) != "dict":
        return ["committed torch artifact manifest for {} must be a JSON object".format(line)]
    for key in ("archive", "plan_key", "sha256", "source_commit"):
        if type(manifest.get(key)) != "string" or not manifest.get(key):
            errors.append("committed torch artifact manifest for {} must include string {}".format(line, key))
    archive = manifest.get("archive", "")
    if archive and not archive.startswith("step-b-pending://") and not _is_store_relative_torch_artifact(line, archive):
        errors.append("committed torch artifact manifest for {} archive must be estate-relative under vaso/artifacts/torch/{}".format(line, line))
    sha256 = manifest.get("sha256", "")
    bad_sha_char = False
    for index in range(len(sha256)):
        if sha256[index] not in _HEX:
            bad_sha_char = True
    if len(sha256) != 64 or bad_sha_char:
        errors.append("committed torch artifact manifest for {} sha256 must be 64 lowercase hex characters".format(line))
    if len(manifest.get("plan_key", "")) != 64:
        errors.append("committed torch artifact manifest for {} plan_key must be 64 lowercase hex characters".format(line))
    if len(manifest.get("source_commit", "")) != 40:
        errors.append("committed torch artifact manifest for {} source_commit must be 40 lowercase hex characters".format(line))
    return errors

def _torch_artifact_repo_name(line):
    return "vaso_torch_artifact_" + line

def _is_pending_torch_artifact(manifest):
    return manifest["sha256"] == _ZERO_SHA256 or manifest["archive"].startswith("step-b-pending://")

def _is_store_relative_torch_artifact(line, archive):
    if archive.startswith("/") or archive.startswith("../") or "/../" in archive:
        return False
    return archive.startswith("vaso/artifacts/torch/" + line + "/")

def _join_paths(root, rel):
    if root.endswith("/"):
        root = root[:-1]
    return root + "/" + rel

def _resolve_committed_torch_artifact_archive(module_ctx, line, archive):
    if not _is_store_relative_torch_artifact(line, archive):
        fail("committed torch artifact manifest for {} archive must be estate-relative under vaso/artifacts/torch/{}".format(line, line))
    estate_root = module_ctx.getenv("VASO_ESTATE_ROOT", "")
    if not estate_root:
        fail("set VASO_ESTATE_ROOT to use committed torch artifact manifest for {}".format(line))
    return _join_paths(estate_root, archive)

def _generate_torch_artifacts(tags, module_ctx = None, use_committed_defaults = False):
    if not tags and not use_committed_defaults:
        return
    by_line = {}
    errors = []
    for tag in tags:
        errors.extend(_validate_torch_artifact_tag(tag))
        if tag.line in by_line:
            errors.append("torch_artifact line {} is declared more than once".format(tag.line))
        by_line[tag.line] = tag
    if errors:
        fail("vaso_graph torch_artifact validation failed:\n  " + "\n  ".join(errors))
    for line in _TORCH_ARTIFACT_LINES:
        tag = by_line.get(line)
        if tag == None:
            torch_artifact_stub_repository(
                name = _torch_artifact_repo_name(line),
                line = line,
            )
            continue
        torch_artifact_repository(
            name = _torch_artifact_repo_name(line),
            archive = tag.archive,
            sha256 = tag.sha256,
            strip_prefix = tag.strip_prefix,
        )

def _generate_committed_torch_artifacts(module_ctx, tags):
    by_line = {}
    errors = []
    for tag in tags:
        errors.extend(_validate_torch_artifact_tag(tag))
        if tag.line in by_line:
            errors.append("torch_artifact line {} is declared more than once".format(tag.line))
        by_line[tag.line] = tag
    committed = {}
    for line in _TORCH_ARTIFACT_LINES:
        if line in by_line:
            continue
        manifest = json.decode(module_ctx.read(_TORCH_ARTIFACT_MANIFESTS[line]))
        errors.extend(_validate_torch_artifact_manifest(line, manifest))
        committed[line] = manifest
    if errors:
        fail("vaso_graph torch_artifact validation failed:\n  " + "\n  ".join(errors))

    for line in _TORCH_ARTIFACT_LINES:
        tag = by_line.get(line)
        if tag != None:
            torch_artifact_repository(
                name = _torch_artifact_repo_name(line),
                archive = tag.archive,
                sha256 = tag.sha256,
                strip_prefix = tag.strip_prefix,
            )
            continue
        manifest = committed[line]
        if _is_pending_torch_artifact(manifest):
            torch_artifact_stub_repository(
                name = _torch_artifact_repo_name(line),
                line = line,
                reason = "committed torch artifact pin for CUDA line {} is pending".format(line),
            )
            continue
        torch_artifact_repository(
            name = _torch_artifact_repo_name(line),
            archive = _resolve_committed_torch_artifact_archive(module_ctx, line, manifest["archive"]),
            sha256 = manifest["sha256"],
            strip_prefix = "",
        )

def _vaso_graph_impl(module_ctx):
    lock_label = None
    ensure = []
    profiles = []
    torch_artifact_tags = []
    for mod in module_ctx.modules:
        for graph in mod.tags.graph:
            lock_label = graph.lockfile
            ensure.extend(graph.ensure_repos)
        for profile in mod.tags.profile:
            profiles.append(profile)
        for torch_artifact in mod.tags.torch_artifact:
            torch_artifact_tags.append(torch_artifact)
    if profiles:
        if len(profiles) != 1:
            fail("vaso_graph extension currently accepts exactly one profile(...) tag")
        profile = profiles[0]
        lock = json.decode(module_ctx.read(profile.lock))
        errors = _validate_profile_tag(profile, lock)
        if errors:
            fail("vaso_graph profile validation failed:\n  " + "\n  ".join(errors))
        _generate_graph(lock, ensure, reexport_native = False)
        _generate_committed_torch_artifacts(module_ctx, torch_artifact_tags)
        return
    if torch_artifact_tags:
        if lock_label != None:
            lock = json.decode(module_ctx.read(lock_label))
            _generate_graph(lock, ensure, reexport_native = False)
        _generate_torch_artifacts(torch_artifact_tags)
        return
    if lock_label == None:
        fail("vaso_graph extension requires a profile(lock = ...) tag")

    lock = json.decode(module_ctx.read(lock_label))
    _generate_graph(lock, ensure, reexport_native = True)
    _generate_torch_artifacts(torch_artifact_tags)

_graph_tag = tag_class(attrs = {
    "lockfile": attr.label(mandatory = True),
    "ensure_repos": attr.string_list(
        doc = "apparent repo names (e.g. vaso_xz) to always define; " +
              "missing ones become stub repos that fail if built",
    ),
})

_profile_tag = tag_class(attrs = {
    "name": attr.string(mandatory = True),
    "lock": attr.label(mandatory = True),
    "torch_source": attr.label(),
    "patches": attr.label_list(default = []),
    "cuda_arch_list": attr.string(default = "10.0+PTX"),
    "python": attr.string(default = "3.13.13"),
    "cuda_line": attr.string(default = "cu130"),
    "compiler": attr.string(default = "gcc-13.3"),
})

_torch_artifact_tag = tag_class(attrs = {
    "name": attr.string(mandatory = True),
    "line": attr.string(mandatory = True),
    "sha256": attr.string(mandatory = True),
    "archive": attr.string(mandatory = True),
    "strip_prefix": attr.string(default = ""),
})

vaso_graph = module_extension(
    implementation = _vaso_graph_impl,
    tag_classes = {
        "graph": _graph_tag,
        "profile": _profile_tag,
        "torch_artifact": _torch_artifact_tag,
    },
)
