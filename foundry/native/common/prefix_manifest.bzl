"""Repository-rule helper for native prefix content manifests."""

_PREFIX_MANIFEST_SCRIPT = r"""
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

FIXED_INSULA_MOUNT = "/vaso-bazel-ob"
TMP_ROOT = "/" + "tmp"
VAR_TMP_ROOT = "/var" + TMP_ROOT
ROOTFS_SYSTEM_PREFIXES = (
    "/bin",
    "/etc",
    "/lib",
    "/lib64",
    "/opt",
    "/usr",
)
UNSTABLE_ABSOLUTE_PREFIXES = (
    "/home",
    "/root",
    TMP_ROOT,
    VAR_TMP_ROOT,
    "/vaso/lines",
    "/workspace",
    "<data-volume>",
)

prefix = Path(sys.argv[1])
out = Path(sys.argv[2])
if not prefix.is_dir():
    raise SystemExit(f"prefix manifest input is not a directory: {prefix}")

def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def is_under(path, prefix):
    return path == prefix or path.startswith(prefix + "/")

def is_stable_absolute_path(value):
    if any(is_under(value, prefix) for prefix in UNSTABLE_ABSOLUTE_PREFIXES):
        return False
    if "/execroot/" in value or "/sandbox/" in value or "processwrapper-sandbox" in value:
        return False
    if "output-base" in value and not is_under(value, FIXED_INSULA_MOUNT):
        return False
    if is_under(value, FIXED_INSULA_MOUNT):
        return True
    return any(is_under(value, prefix) for prefix in ROOTFS_SYSTEM_PREFIXES)

VARYING_SUBSTRINGS = (
    "/execroot/",
    "/sandbox/",
    "processwrapper-sandbox",
    ".vaso-estate",
    "/vaso/lines/",
)

def concrete_varying_values():
    # Concrete values that differ between builds, users or machines. File text is checked
    # for these exact values, never for generic prefixes: docs and scripts legitimately
    # mention /tmp, /home/user, /6000 and the like.
    values = []
    output_base = str(prefix).split("/external/", 1)[0] if "/external/" in str(prefix) else ""
    if output_base and output_base != FIXED_INSULA_MOUNT:
        values.append(output_base)
    for name in ("HOME", "TMPDIR"):
        value = os.environ.get(name, "")
        if value and value not in ("/", TMP_ROOT, VAR_TMP_ROOT):
            values.append(value.rstrip("/"))
    return tuple(values)

CONCRETE_VARYING_VALUES = concrete_varying_values()
VIOLATIONS = []

def report(rel, value):
    VIOLATIONS.append(f"{rel}: absolute path {value!r} is not under the fixed output-base mount or rootfs system dirs")

def assert_stable_absolute_paths(rel, text, strict=True):
    if strict:
        # Symlink targets must sit under the fixed mount or rootfs system dirs.
        for match in re.finditer(r"/[^\s'\"<>;`$(){}]+", text):
            value = match.group(0).rstrip(".,:]")
            if not is_stable_absolute_path(value):
                report(rel, value)
        return
    for value in CONCRETE_VARYING_VALUES:
        if value + "/" in text or text.endswith(value) or (value + "\n") in text or (value + " ") in text:
            report(rel, value)
    for needle in VARYING_SUBSTRINGS:
        index = text.find(needle)
        if index >= 0:
            start = text.rfind(" ", 0, index) + 1
            end = len(text)
            for stop in (" ", "\n", "'", '"'):
                found = text.find(stop, index)
                if 0 <= found < end:
                    end = found
            report(rel, text[start:end])
    for match in re.finditer(r"/[^\s'\"<>;`$(){}]*output-base[^\s'\"<>;`$(){}]*", text):
        value = match.group(0).rstrip(".,:]")
        if not is_under(value, FIXED_INSULA_MOUNT):
            report(rel, value)

def should_scan_text(rel):
    name = rel.rsplit("/", 1)[-1]
    return (
        rel.startswith("bin/")
        or name == "pyvenv.cfg"
        or name.endswith(".pc")
        or name.endswith(".pth")
        or name.endswith(".cfg")
    )

entries = []
for root, dirnames, filenames in os.walk(prefix, topdown=True, followlinks=False):
    root_path = Path(root)
    rel_root = root_path.relative_to(prefix)
    dirnames[:] = [name for name in sorted(dirnames) if name != "__pycache__"]
    for name in sorted(list(dirnames)):
        path = root_path / name
        rel = (rel_root / name).as_posix()
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            target = os.readlink(path)
            if target.startswith("/"):
                assert_stable_absolute_paths(rel, target)
            entries.append({
                "path": rel,
                "target": target,
                "type": "symlink",
            })
        else:
            entries.append({
                "mode": format(mode, "04o"),
                "path": rel,
                "type": "directory",
            })
    dirnames[:] = [name for name in sorted(dirnames) if not (root_path / name).is_symlink()]
    for name in sorted(filenames):
        path = root_path / name
        rel = (rel_root / name).as_posix()
        if name.endswith(".pyc"):
            continue
        if path.is_symlink():
            target = os.readlink(path)
            if target.startswith("/"):
                assert_stable_absolute_paths(rel, target)
            entries.append({
                "path": rel,
                "target": target,
                "type": "symlink",
            })
            continue
        if should_scan_text(rel):
            data = path.read_bytes()
            if data.startswith(b"#!") or name.endswith((".pc", ".pth", ".cfg")) or name == "pyvenv.cfg":
                assert_stable_absolute_paths(rel, data.decode("utf-8", errors="ignore"), strict=False)
        entries.append({
            "mode": format(stat.S_IMODE(path.stat().st_mode), "04o"),
            "path": rel,
            "sha256": file_sha256(path),
            "size": path.stat().st_size,
            "type": "file",
        })

dependencies = []
for item in sys.argv[3:]:
    if "=" not in item:
        raise SystemExit(f"dependency manifest must be NAME=PATH, got {item!r}")
    name, manifest_path = item.split("=", 1)
    path = Path(manifest_path)
    if not path.is_file():
        raise SystemExit(f"dependency manifest is missing: {name}={path}")
    document = json.loads(path.read_text(encoding="utf-8"))
    dependencies.append({
        "manifest_sha256": file_sha256(path),
        "name": name,
        "tree_sha256": document.get("tree_sha256", ""),
    })

payload = {
    "dependencies": sorted(dependencies, key=lambda item: item["name"]),
    "entries": entries,
    "schema_version": 1,
}
if VIOLATIONS:
    raise SystemExit("\n".join(sorted(set(VIOLATIONS))))
tree_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
payload["tree_sha256"] = hashlib.sha256(tree_bytes).hexdigest()
out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
"""

def _prefix_manifest_label(prefix_file_label):
    if not prefix_file_label:
        return None
    text = str(prefix_file_label)
    suffix = "//:prefix_path.txt"
    if not text.startswith("@") or not text.endswith(suffix):
        return None
    return Label(text[:-len("prefix_path.txt")] + "prefix_manifest.json")

def _dependency_manifest_args(repository_ctx):
    args = []
    for name in sorted(dir(repository_ctx.attr)):
        if not name.endswith("_prefix_file"):
            continue
        manifest = _prefix_manifest_label(getattr(repository_ctx.attr, name))
        if manifest:
            dep_name = name[:-len("_prefix_file")].replace("_", "-")
            args.append("{}={}".format(dep_name, repository_ctx.path(manifest)))
    return args

def write_prefix_manifest(repository_ctx, prefix):
    """Write prefix_manifest.json with a path-independent digest of prefix."""
    result = repository_ctx.execute(
        [
            "python3",
            "-c",
            _PREFIX_MANIFEST_SCRIPT,
            str(prefix),
            str(repository_ctx.path("prefix_manifest.json")),
        ] + _dependency_manifest_args(repository_ctx),
        timeout = 600,
        quiet = True,
    )
    if result.return_code != 0:
        fail("prefix manifest generation failed (rc={}):\n{}\n{}".format(
            result.return_code,
            result.stdout,
            result.stderr,
        ))
