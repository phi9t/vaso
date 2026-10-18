"""Shared declared-input helpers for native build actions."""

load("//native/common:build_settings.bzl", "ReleaseCudaLineInfo")

_LINES = ("cu129", "cu130")

NativeActionPrefixInfo = provider(
    doc = "Install prefix produced by a normal Bazel build action.",
    fields = {
        "prefix": "Declared directory output containing the install prefix.",
        "manifest": "Declared file containing the deterministic prefix content manifest.",
        "package": "Spack package name.",
        "version": "Pinned upstream version.",
    },
)

def native_action_prefix_manifest_attrs():
    return {
        "prefix_manifest_files": attr.label_keyed_string_dict(
            allow_files = True,
            doc = (
                "Dependency content manifests keyed by manifest label, with " +
                "values matching the native prefix dependency key."
            ),
        ),
    }

def native_action_prefix_manifest_label(prefix_file_label):
    if not prefix_file_label:
        return None
    text = str(prefix_file_label)
    suffix = "//:prefix_path.txt"
    if not text.startswith("@") or not text.endswith(suffix):
        return None
    return text[:-len("prefix_path.txt")] + "prefix_manifest.json"

def native_action_prefix_manifest_map(prefix_key_attrs, kwargs):
    manifests = {}
    for key, attr_name in prefix_key_attrs:
        manifest = native_action_prefix_manifest_label(kwargs.get(attr_name))
        if manifest:
            manifests[manifest] = key
    return manifests

def _native_action_prefixes_by_key(ctx, prefix_key_attrs):
    expected = {key: attr_name for key, attr_name in prefix_key_attrs}
    by_key = {}
    for key, attr_name in prefix_key_attrs:
        target = getattr(ctx.attr, attr_name)
        if NativeActionPrefixInfo not in target:
            continue
        info = target[NativeActionPrefixInfo]
        by_key[key] = struct(
            label = target.label,
            manifest = info.manifest,
            prefix = info.prefix,
        )
    for target, dep_key in sorted(ctx.attr.prefix_manifest_files.items(), key = lambda item: item[1]):
        if dep_key not in expected:
            fail("{} declares unexpected dependency content manifest key {}".format(ctx.label, dep_key))
        if dep_key in by_key:
            fail("{} declares dependency content manifest key {} both as an action prefix provider and a manifest file".format(ctx.label, dep_key))
        files = target.files.to_list()
        if len(files) != 1:
            fail("{} dependency content manifest {} must provide exactly one file".format(ctx.label, target.label))
        by_key[dep_key] = struct(
            label = target.label,
            manifest = files[0],
            prefix = None,
        )
    return by_key

def native_action_prefix_manifest_inputs(ctx, prefix_key_attrs, *, require_all):
    by_key = _native_action_prefixes_by_key(ctx, prefix_key_attrs)
    inputs = []
    for key, _attr_name in prefix_key_attrs:
        entry = by_key.get(key)
        if not entry:
            continue
        if entry.prefix:
            inputs.append(entry.prefix)
        inputs.append(entry.manifest)
    if require_all:
        missing = [key for key, _attr_name in prefix_key_attrs if key not in by_key]
        if missing:
            fail("{} is missing dependency content manifest(s): {}".format(ctx.label, ", ".join(missing)))
    return inputs

def _native_action_prefix_manifest_files_by_key(ctx, prefix_key_attrs):
    by_key = _native_action_prefixes_by_key(ctx, prefix_key_attrs)
    files_by_key = {}
    for key, entry in by_key.items():
        files_by_key[key] = entry.manifest
    return files_by_key

def _legacy_native_action_prefix_manifest_files_by_key(ctx, prefix_key_attrs):
    expected = {key: attr_name for key, attr_name in prefix_key_attrs}
    by_key = {}
    for target, dep_key in sorted(ctx.attr.prefix_manifest_files.items(), key = lambda item: item[1]):
        if dep_key not in expected:
            fail("{} declares unexpected dependency content manifest key {}".format(ctx.label, dep_key))
        files = target.files.to_list()
        if len(files) != 1:
            fail("{} dependency content manifest {} must provide exactly one file".format(ctx.label, target.label))
        if dep_key in by_key:
            fail("{} declares dependency content manifest key {} more than once".format(ctx.label, dep_key))
        by_key[dep_key] = files[0]
    return by_key

def native_action_prefix_path_from_manifest_file(manifest_file, fallback_prefix_file = None):
    if manifest_file:
        return manifest_file.dirname + "/prefix"
    if fallback_prefix_file:
        return fallback_prefix_file.path
    fail("native action prefix path requires a dependency content manifest")

def native_action_prefix_path(ctx, prefix_key_attrs, key):
    by_key = _native_action_prefixes_by_key(ctx, prefix_key_attrs)
    entry = by_key.get(key)
    if entry and entry.prefix:
        return entry.prefix.path
    if entry:
        return native_action_prefix_path_from_manifest_file(entry.manifest)
    expected = {expected_key: attr_name for expected_key, attr_name in prefix_key_attrs}
    if key not in expected:
        fail("{} does not declare dependency key {}".format(ctx.label, key))
    return native_action_prefix_path_from_manifest_file(None, getattr(ctx.file, expected[key]))

def native_action_prefix_argv(ctx, prefix_key_attrs):
    by_key = _native_action_prefixes_by_key(ctx, prefix_key_attrs)
    args = []
    for key, attr_name in prefix_key_attrs:
        entry = by_key.get(key)
        if entry and entry.prefix:
            prefix_path = entry.prefix.path
        else:
            manifest = entry.manifest if entry else None
            prefix_path = native_action_prefix_path_from_manifest_file(
                manifest,
                getattr(ctx.file, attr_name),
            )
        args.extend(["--prefix-file", "{}={}".format(key, prefix_path)])
    return args

def native_action_dep_prefix_args(ctx, prefix_key_attrs):
    by_key = _native_action_prefixes_by_key(ctx, prefix_key_attrs)
    args = []
    for key, attr_name in prefix_key_attrs:
        entry = by_key.get(key)
        if entry and entry.prefix:
            prefix_path = entry.prefix.path
        else:
            manifest = entry.manifest if entry else None
            prefix_path = native_action_prefix_path_from_manifest_file(
                manifest,
                getattr(ctx.file, attr_name),
            )
        args.extend(["--dep-prefix", "{}={}".format(key, prefix_path)])
    return args

def native_action_default_rootfs_identity(profile):
    return select({
        "//plans:release_line_{}".format(line): "//plans:{}/{}/rootfs.identity.json".format(profile, line)
        for line in _LINES
    })

def native_action_rootfs_identity_attrs():
    return {
        "rootfs_identity": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Committed rootfs identity slice for the selected CUDA line.",
        ),
        "_line": attr.label(default = Label("//native/common:release_line")),
    }

def native_action_rootfs_identity_file(ctx, *, profile, consumer):
    line = ctx.attr._line[ReleaseCudaLineInfo].value
    rootfs_identity = ctx.file.rootfs_identity
    required_fragment = "/{}/{}/".format(profile, line)
    if required_fragment not in rootfs_identity.path:
        fail(
            "{} rootfs identity {} does not match profile={} release_line={}".format(
                consumer,
                rootfs_identity.path,
                profile,
                line,
            )
        )
    return rootfs_identity
