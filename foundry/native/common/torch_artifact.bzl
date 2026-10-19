"""Public repository rule for sha-pinned Vaso PyTorch prefix artifacts.

Mechanism: artifact-extract. This rule verifies an already-local artifact by
sha256, extracts it, and validates its prefix layout; it does not build code.
"""

load("//native/common:prefix_manifest.bzl", "write_prefix_manifest")

_LINES = ("cu129", "cu130")
_ANCHOR = ".vaso_torch_artifact_prefix"
_PYTHON_VERSION = "3." + "13"
_PYTHON_ABI = "cp" + _PYTHON_VERSION.replace(".", "")
_CONSTANT_ENV = {
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
    "PYTHONHASHSEED": "0",
}
_SHA256_SCRIPT = """\
import hashlib
import sys

digest = hashlib.sha256()
with open(sys.argv[1], "rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
print(digest.hexdigest())
"""

_BUILD_TEMPLATE = """\
load("@rules_cc//cc:defs.bzl", "cc_library")
load("@vaso_graph//native/common:torch_artifact_provider.bzl", "torch_artifact_prefix")

package(default_visibility = ["//visibility:public"])

filegroup(
    name = "prefix_files",
    srcs = glob(["prefix/**"], allow_empty = False),
)

torch_artifact_prefix(
    name = "prefix",
    srcs = [":prefix_files"],
    anchor = "prefix/.vaso_torch_artifact_prefix",
    build_plan = "torch_build_plan.json",
    provider_metadata = "torch_provider_metadata.json",
    wheel = "torch_artifact.whl",
    python_abi = "{python_abi}",
)

cc_library(
    name = "headers",
    hdrs = glob(
        [
            "prefix/include/**",
            "prefix/torch/include/**",
            "prefix/lib/python*/site-packages/torch/include/**",
        ],
        allow_empty = True,
    ),
    includes = [
        "prefix/include",
        "prefix/include/torch/csrc/api/include",
        "prefix/torch/include",
        "prefix/torch/include/torch/csrc/api/include",
        "prefix/lib/python{python_version}/site-packages/torch/include",
        "prefix/lib/python{python_version}/site-packages/torch/include/torch/csrc/api/include",
    ],
)

cc_library(
    name = "libs",
    srcs = glob(
        [
            "prefix/lib/**/*.so*",
            "prefix/lib64/**/*.so*",
            "prefix/torch/lib/**/*.so*",
            "prefix/lib/python*/site-packages/torch/lib/**/*.so*",
        ],
        allow_empty = True,
    ),
    linkopts = [
        "-Lprefix/lib",
        "-Lprefix/lib64",
        "-Lprefix/torch/lib",
        "-Lprefix/lib/python{python_version}/site-packages/torch/lib",
        "-Wl,-rpath,prefix/lib",
        "-Wl,-rpath,prefix/lib64",
        "-Wl,-rpath,prefix/torch/lib",
        "-Wl,-rpath,prefix/lib/python{python_version}/site-packages/torch/lib",
    ],
)

exports_files([
    "prefix_manifest.json",
    "prefix_path.txt",
    "torch_artifact.whl",
    "torch_build_plan.json",
    "torch_provider_metadata.json",
])
"""

_MISSING_BUILD_TEMPLATE = """\
load("@rules_cc//cc:defs.bzl", "cc_library")
load("@vaso_graph//native/common:torch_artifact_missing.bzl", "torch_artifact_missing_prefix")

package(default_visibility = ["//visibility:public"])

torch_artifact_missing_prefix(
    name = "prefix",
    line = "{line}",
    reason = "{reason}",
)

cc_library(
    name = "headers",
    deps = [":prefix"],
)

cc_library(
    name = "libs",
    deps = [":prefix"],
)
"""

def _is_label(value):
    return value.startswith("@") or value.startswith("//") or value.startswith(":")

def _archive_path(repository_ctx):
    archive = repository_ctx.attr.archive
    if _is_label(archive):
        return repository_ctx.path(Label(archive))
    return repository_ctx.path(archive)

def _sha256(repository_ctx, path):
    result = repository_ctx.execute(
        ["python3", "-c", _SHA256_SCRIPT, str(path)],
        environment = _CONSTANT_ENV,
        timeout = 300,
        quiet = True,
    )
    if result.return_code != 0:
        fail("failed to sha256 {} (rc={}):\n{}\n{}".format(
            path,
            result.return_code,
            result.stdout,
            result.stderr,
        ))
    return result.stdout.strip()

def _line_from_repo_name(name):
    for line in _LINES:
        if name.endswith("_" + line):
            return line
    return "unknown"

def _validate_extracted_prefix(repository_ctx, prefix):
    torch_package = "lib/python" + _PYTHON_VERSION + "/site-packages/torch"
    missing = []
    if not repository_ctx.path(prefix + "/" + torch_package).exists:
        missing.append(torch_package + "/")

    header_and_lib_layouts = [
        ("include/torch/extension.h", "lib/libtorch.so"),
        ("torch/include/torch/extension.h", "torch/lib/libtorch.so"),
        (torch_package + "/include/torch/extension.h", torch_package + "/lib/libtorch.so"),
    ]
    has_layout = False
    for header, lib in header_and_lib_layouts:
        if repository_ctx.path(prefix + "/" + header).exists and repository_ctx.path(prefix + "/" + lib).exists:
            has_layout = True
            break
    if not has_layout:
        missing.append(
            "one torch header/library layout: include+lib, torch/include+torch/lib, or " +
            torch_package + "/include+" + torch_package + "/lib",
        )
    if missing:
        fail("torch artifact prefix is missing required path(s): " + ", ".join(missing))

def _write_metadata(repository_ctx, prefix):
    line = _line_from_repo_name(repository_ctx.name)
    repository_ctx.file("prefix_path.txt", str(repository_ctx.path(prefix)) + "\n")
    repository_ctx.file("torch_artifact.whl", "")
    repository_ctx.file(
        "torch_build_plan.json",
        json.encode_indent({
            "artifact_sha256": repository_ctx.attr.sha256,
            "line": line,
            "mechanism": "torch-artifact",
            "schema_version": 1,
        }, indent = "  ") + "\n",
    )
    repository_ctx.file(
        "torch_provider_metadata.json",
        json.encode_indent({
            "artifact_sha256": repository_ctx.attr.sha256,
            "line": line,
            "mechanism": "torch-artifact",
            "package": "py-torch",
            "python_abi": _PYTHON_ABI,
            "schema_version": 1,
            "version": "2.14.0",
        }, indent = "  ") + "\n",
    )

def _torch_artifact_repository_impl(repository_ctx):
    archive = _archive_path(repository_ctx)
    actual_sha256 = _sha256(repository_ctx, archive)
    if actual_sha256 != repository_ctx.attr.sha256:
        fail("torch artifact sha256 mismatch for {}: expected {}, got {}".format(
            archive,
            repository_ctx.attr.sha256,
            actual_sha256,
        ))

    repository_ctx.extract(
        archive,
        output = "prefix",
        stripPrefix = repository_ctx.attr.strip_prefix,
    )
    repository_ctx.file("prefix/" + _ANCHOR, "")
    _validate_extracted_prefix(repository_ctx, "prefix")
    _write_metadata(repository_ctx, "prefix")
    write_prefix_manifest(repository_ctx, repository_ctx.path("prefix"))
    repository_ctx.file("BUILD.bazel", _BUILD_TEMPLATE.format(
        python_abi = _PYTHON_ABI,
        python_version = _PYTHON_VERSION,
    ))

torch_artifact_repository = repository_rule(
    implementation = _torch_artifact_repository_impl,
    attrs = {
        "archive": attr.string(
            mandatory = True,
            doc = "Absolute path under the estate artifact store, workspace path, or label string for a torch prefix tarball.",
        ),
        "sha256": attr.string(mandatory = True),
        "strip_prefix": attr.string(default = ""),
    },
    environ = ["CC", "CXX"],
    doc = "Extract a sha-pinned Vaso PyTorch prefix artifact for public downstream consumers.",
)

def _torch_artifact_stub_repository_impl(repository_ctx):
    repository_ctx.file("BUILD.bazel", _MISSING_BUILD_TEMPLATE.format(
        line = repository_ctx.attr.line,
        reason = repository_ctx.attr.reason,
    ))

torch_artifact_stub_repository = repository_rule(
    implementation = _torch_artifact_stub_repository_impl,
    attrs = {
        "line": attr.string(mandatory = True),
        "reason": attr.string(default = ""),
    },
    environ = ["CC", "CXX"],
    doc = "Missing-line stub for public torch artifact repos referenced by selects.",
)
