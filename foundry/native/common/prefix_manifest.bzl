"""Repository-rule helper for lightweight prefix presence receipts."""

_PREFIX_MANIFEST_SCRIPT = r"""
import json
import sys
from pathlib import Path

prefix, out = Path(sys.argv[1]), Path(sys.argv[2])
if not prefix.is_dir():
    raise SystemExit(f"prefix manifest input is not a directory: {prefix}")
dependencies = []
for item in sys.argv[3:]:
    if "=" not in item:
        raise SystemExit("dependency manifest must be NAME=PATH")
    name, filename = item.split("=", 1)
    path = Path(filename)
    if not name or not path.is_file():
        raise SystemExit("dependency manifest is missing or unnamed")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise SystemExit("dependency manifest must be an object")
    dependencies.append({"name": name, "receipt_present": True,
                         "schema_version": document.get("schema_version")})
payload = {"schema_version": 2, "kind": "prefix_presence_receipt",
           "prefix_present": True, "integrity": "not_checked",
           "dependencies": sorted(dependencies, key=lambda item: item["name"])}
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
    """Record prefix and dependency receipt presence; no content or relocation audit."""
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
