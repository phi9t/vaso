#!/usr/bin/env python3
"""Reduce a Spack concrete DAG into a compact Bazel-consumable lockfile.

The concrete DAG is queried from the Bazel-vendored Spack wrapper
(`@spack_dist//:spack`) while running inside the insula. We keep only what Bazel
needs to wire a consumption graph:

  - one entry per node, keyed by a Bazel-safe repo name;
  - the hermetic install prefix (structure follows the prefix);
  - the `link`/`run` dependencies as graph edges (build-only deps are dropped
    because Bazel brings its own C toolchain);
  - the `-l<stem>` linker names derived from the prefix's lib dir.

The output `spack_graph.lock.json` is the single source the Bazel module
extension reads, so the Bazel module graph *is* the Spack link-DAG.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Spack packages that exist only to build other packages, plus the implicit
# toolchain runtime libs. A downstream C consumer does not link against these
# through Spack — Bazel's own C toolchain supplies libc and the gcc runtime —
# so they never become Bazel packages here.
BUILD_ONLY_NODES = {"compiler-wrapper", "gcc", "gmake", "gcc-runtime", "glibc"}
# Deptypes that create a real consumption edge for a linker/runtime consumer.
CONSUME_DEPTYPES = {"link", "run"}
# C++/Python companion packages where two provider versions in one downstream
# process can cause ODR or ABI failures. Native overrides for these packages
# must name the exact concrete Spack version, and every package in the family
# must resolve to one unified concrete version family, so an old native capture
# cannot silently satisfy a newer Spack node.
ODR_SENSITIVE_PROVIDER_PACKAGES = {
    "abseil-cpp",
    "boost",
    "grpc",
    "grpc-cpp",
    "protobuf",
    "py-grpcio",
    "py-protobuf",
}
ODR_SENSITIVE_PROVIDER_FAMILIES = {
    "abseil-cpp": "abseil-cpp",
    "boost": "boost",
    "grpc": "grpc",
    "grpc-cpp": "grpc",
    "py-grpcio": "grpc",
    "protobuf": "protobuf",
    "py-protobuf": "protobuf",
}


def repo_name(pkg: str) -> str:
    """Bazel repo/target names cannot contain '-'."""
    return "spack_" + pkg.replace("-", "_")


def native_override_key_for(name: str, version: str, native_overrides: dict[str, str]) -> str | None:
    """Return the override key that serves one concrete node, preferring name@version."""
    exact_key = f"{name}@{version}"
    if exact_key in native_overrides:
        return exact_key
    if name in native_overrides:
        return name
    return None


def native_override_for(name: str, version: str, native_overrides: dict[str, str]) -> str | None:
    """Return the override label for one concrete node, preferring name@version."""
    key = native_override_key_for(name, version, native_overrides)
    return native_overrides[key] if key is not None else None


def native_provider_version_static_errors(
    native_overrides: dict[str, str],
    provided_versions: dict[str, str],
    known_version_substitutions: list[dict],
) -> list[str]:
    """Check the override file itself, independent of any graph.

    Every native key records the version its recipe builds and vice versa, an
    exact `name@version` key must provide exactly that version, and every
    tolerated substitution names the ticket that removes it.
    """
    errors: list[str] = []
    for key in sorted(set(native_overrides) - set(provided_versions)):
        errors.append(f"native override {key!r} has no provided_versions entry")
    for key in sorted(set(provided_versions) - set(native_overrides)):
        errors.append(f"provided_versions entry {key!r} has no native override")
    for key in sorted(set(native_overrides) & set(provided_versions)):
        _, sep, exact_version = key.partition("@")
        if sep and provided_versions[key] != exact_version:
            errors.append(
                f"exact native override {key!r} must provide {exact_version}, "
                f"but provided_versions says {provided_versions[key]}"
            )
    for entry in known_version_substitutions:
        fields = tuple(entry.get(field) for field in ("key", "graph_version", "provided"))
        if not all(isinstance(value, str) and value for value in fields) or not entry.get("ticket"):
            errors.append(
                "known_version_substitutions entries need non-empty key, "
                f"graph_version, provided, and ticket: {entry!r}"
            )
    return errors


def native_provider_version_errors(
    nodes: list[dict],
    native_overrides: dict[str, str],
    provided_versions: dict[str, str],
    known_version_substitutions: list[dict],
    *,
    report_stale: bool,
) -> list[str]:
    """Reject native flips whose provider builds a different version than the node.

    An unversioned override key falls back to any version of a package, so a
    native recipe captured at one version could silently serve a graph node at
    another. `provided_versions` records the concrete version each native key
    builds; a mismatch is allowed only by an exact `known_version_substitutions`
    entry naming the ticket that removes it. `report_stale` also rejects entries
    that no longer match a real substitution; it is meaningful only against the
    complete authoritative graph, not a partial lock.
    """
    errors = native_provider_version_static_errors(
        native_overrides, provided_versions, known_version_substitutions
    )
    errors.extend(
        native_provider_version_graph_errors(
            nodes,
            native_overrides,
            provided_versions,
            known_version_substitutions,
            report_stale=report_stale,
        )
    )
    return errors


def native_provider_version_graph_errors(
    nodes: list[dict],
    native_overrides: dict[str, str],
    provided_versions: dict[str, str],
    known_version_substitutions: list[dict],
    *,
    report_stale: bool,
) -> list[str]:
    """Check each graph node served by a native override against its provided version."""
    errors: list[str] = []
    allowed: set[tuple[str, str, str]] = set()
    for entry in known_version_substitutions:
        fields = tuple(entry.get(field) for field in ("key", "graph_version", "provided"))
        if all(isinstance(value, str) and value for value in fields) and entry.get("ticket"):
            allowed.add(fields)  # type: ignore[arg-type]

    used: set[tuple[str, str, str]] = set()
    reported: set[tuple[str, str, str]] = set()
    for node in nodes:
        name = node.get("name") or node.get("package") or ""
        version = node.get("version", "")
        key = native_override_key_for(name, version, native_overrides)
        if key is None or key not in provided_versions:
            continue
        provided = provided_versions[key]
        if provided == version:
            continue
        substitution = (key, version, provided)
        if substitution in allowed:
            used.add(substitution)
            continue
        if substitution in reported:
            continue
        reported.add(substitution)
        errors.append(
            f"native override {key!r} builds {name}@{provided} but serves graph "
            f"node {name}@{version}; a native provider must build the node's "
            "concrete version (use an exact name@version key, or record a "
            "ticketed known_version_substitutions entry)"
        )
    if report_stale:
        for key, graph_version, provided in sorted(allowed - used):
            errors.append(
                "stale known_version_substitutions entry: "
                f"{key!r} graph {graph_version} provided {provided} no longer "
                "matches a substitution in the graph"
            )
    return errors


def odr_family_version(name: str, version: str) -> str:
    """Normalize companion package versions that publish the same ABI family.

    Since protobuf 21.x, the Python package version is `<python major>.<protobuf
    version>` (4.21.12 ↔ 21.12, 5.26.1 ↔ 26.1, 6.32.1 ↔ 32.1); 3.x releases
    share the C++ version directly.
    """
    if name == "py-protobuf":
        major, _, rest = version.partition(".")
        if major.isdigit() and int(major) >= 4 and rest:
            return rest
    return version


def validate_odr_sensitive_native_overrides(
    nodes: list[dict],
    native_overrides: dict[str, str],
) -> None:
    """Reject ambiguous native flips for provider families with ODR-sensitive ABIs."""
    active_exact_keys = {
        f"{node.get('name') or node.get('package')}@{node.get('version', '')}"
        for node in nodes
    }
    override_versions_by_family: dict[str, set[str]] = {}
    for override_key in sorted(native_overrides):
        package_name = override_key.split("@", 1)[0]
        if package_name in ODR_SENSITIVE_PROVIDER_PACKAGES and "@" not in override_key:
            raise SystemExit(
                "native override for ODR-sensitive package "
                f"{package_name!r} must be version-qualified as "
                f"'{package_name}@<version>'; protobuf/gRPC/Abseil/Boost "
                "providers must migrate as one version-consistent family."
            )
        if package_name in ODR_SENSITIVE_PROVIDER_PACKAGES and "@" in override_key:
            if override_key not in active_exact_keys:
                continue
            version = override_key.split("@", 1)[1]
            family = ODR_SENSITIVE_PROVIDER_FAMILIES[package_name]
            override_versions_by_family.setdefault(family, set()).add(
                odr_family_version(package_name, version)
            )
    for family, versions in sorted(override_versions_by_family.items()):
        if len(versions) > 1:
            raise SystemExit(
                "native overrides contain multiple versions for ODR-sensitive "
                f"provider family {family!r}: {', '.join(sorted(versions))}. "
                "Keep protobuf/gRPC/Abseil/Boost providers on one concrete "
                "version family before applying native providers."
            )

    versions_by_package: dict[str, set[str]] = {}
    versions_by_family: dict[str, set[str]] = {}
    for node in nodes:
        name = node.get("name") or node.get("package")
        if name not in ODR_SENSITIVE_PROVIDER_PACKAGES:
            continue
        version = node.get("version", "")
        versions_by_package.setdefault(name, set()).add(version)
        family = ODR_SENSITIVE_PROVIDER_FAMILIES[name]
        versions_by_family.setdefault(family, set()).add(odr_family_version(name, version))
        exact_key = f"{name}@{version}"
        if name in native_overrides and exact_key not in native_overrides:
            raise SystemExit(
                "native override for ODR-sensitive package "
                f"{name}@{version} must be version-qualified as {exact_key!r}; "
                "protobuf/gRPC/Abseil/Boost providers must migrate as one "
                "version-consistent family."
            )
    for name, versions in sorted(versions_by_package.items()):
        if len(versions) > 1:
            raise SystemExit(
                "ODR-sensitive package "
                f"{name!r} appears at multiple versions in one lock: "
                f"{', '.join(sorted(versions))}. Keep protobuf/gRPC/Abseil/"
                "Boost families single-version before applying native providers."
            )
    for family, versions in sorted(versions_by_family.items()):
        if len(versions) > 1:
            raise SystemExit(
                "ODR-sensitive provider family "
                f"{family!r} appears at multiple versions in one lock: "
                f"{', '.join(sorted(versions))}. Keep protobuf/gRPC/Abseil/"
                "Boost families single-version before applying native providers."
            )


def spack_spec_json(spack_bin: str, root: str, timeout: float, *, fresh: bool = False) -> dict:
    cmd = [spack_bin, "spec", "--json"]
    if fresh:
        cmd.append("--fresh")
    cmd.append(root)
    out = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if out.returncode != 0:
        raise SystemExit(f"spack spec failed ({out.returncode}):\n{out.stderr}")
    return json.loads(out.stdout)


def spack_prefix(spack_bin: str, spack_hash: str) -> str:
    """Resolve a concrete node's hermetic install prefix by hash.

    `spack spec --json` does not embed the prefix, but `spack location -i` does
    and is the authoritative install-tree query.
    """
    out = subprocess.run(
        [spack_bin, "location", "-i", "/" + spack_hash],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def optional_spack_prefix(spack_bin: str, spack_hash: str) -> str:
    out = subprocess.run(
        [spack_bin, "location", "-i", "/" + spack_hash],
        capture_output=True,
        text=True,
    )
    return out.stdout.strip() if out.returncode == 0 else ""


def link_libs(prefix: str) -> list[str]:
    """Derive `-l<stem>` names from the hermetic prefix's shared/static libs.

    A file `lib/libz.so` (or `libz.a`) yields the linker stem `z`. Versioned
    sonames like `libz.so.1` are ignored; `-lz` resolves through the base
    `libz.so` symlink that Spack installs.
    """
    stems: set[str] = set()
    for libdir in ("lib", "lib64"):
        base = Path(prefix) / libdir
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            name = entry.name
            if not name.startswith("lib"):
                continue
            if name.endswith(".so"):
                stems.add(name[len("lib") : -len(".so")])
            elif name.endswith(".a"):
                stems.add(name[len("lib") : -len(".a")])
    return sorted(s for s in stems if s)


def include_dirs(prefix: str) -> list[str]:
    """Include search paths relative to the generated repo root.

    Many Spack prefixes place public headers under `include/`, but some packages
    (e.g. libxml2) expect consumers to add a nested include directory like
    `include/libxml2` so headers can be included as `<libxml/parser.h>`.

    We include `include/` plus any *first-level* subdirectory under it that
    actually contains at least one header file somewhere below.
    """
    base = Path(prefix) / "include"
    if not base.is_dir():
        return []
    out = ["include"]
    for child in sorted(base.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        if child.name == "bsd" and (child / "sys" / "cdefs.h").is_file():
            continue
        has_hdr = any(
            p.is_file() and p.suffix in (".h", ".hpp", ".hh", ".hxx")
            for p in child.rglob("*")
        )
        if has_hdr:
            out.append("include/" + child.name)
    return out


def validate_native_provider_versions(
    nodes: list[dict],
    native_overrides: dict[str, str],
    provided_versions: dict[str, str] | None,
    known_version_substitutions: list[dict] | None,
) -> None:
    """Refuse to flip any node to a native provider built at another version."""
    if provided_versions is None or not native_overrides:
        return
    errors = native_provider_version_errors(
        nodes,
        native_overrides,
        provided_versions,
        known_version_substitutions or [],
        report_stale=False,
    )
    if errors:
        raise SystemExit("native provider version mismatch:\n" + "\n".join(errors))


def build_lock(
    spec: dict,
    spack_bin: str,
    native_overrides: dict[str, str] | None = None,
    provided_versions: dict[str, str] | None = None,
    known_version_substitutions: list[dict] | None = None,
) -> dict:
    nodes = spec["spec"]["nodes"]
    by_hash = {n["hash"]: n for n in nodes}
    native_overrides = native_overrides or {}
    validate_odr_sensitive_native_overrides(nodes, native_overrides)
    validate_native_provider_versions(nodes, native_overrides, provided_versions, known_version_substitutions)

    packages: dict[str, dict] = {}
    for node in nodes:
        name = node["name"]
        if name in BUILD_ONLY_NODES:
            continue
        native_prefix = native_override_for(name, node["version"], native_overrides)
        prefix = optional_spack_prefix(spack_bin, node["hash"]) if native_prefix else spack_prefix(spack_bin, node["hash"])
        if not prefix and not native_prefix:
            raise SystemExit(f"node {name} has no install prefix; is it installed?")
        link_deps: list[str] = []
        for dep in node.get("dependencies", []):
            deptypes = set(dep["parameters"]["deptypes"])
            if not (deptypes & CONSUME_DEPTYPES):
                continue
            dep_node = by_hash.get(dep["hash"], {})
            dep_name = dep_node.get("name", dep["name"])
            if dep_name in BUILD_ONLY_NODES:
                continue
            link_deps.append(repo_name(dep_name))
        packages[repo_name(name)] = {
            "package": name,
            "version": node["version"],
            "spack_hash": node["hash"],
            "prefix": prefix,
            # Every node defaults to the Spack provider. A migration flips this
            # to "native" (via native_overrides.json applied below), never by
            # editing the generated snapshot by hand. The DAG topology is
            # untouched — only a node's *provider* changes.
            "build": "native" if native_prefix else "spack",
            "link_deps": sorted(set(link_deps)),
            "link_libs": link_libs(prefix) if prefix else [],
            "include_dirs": include_dirs(prefix) if prefix else [],
        }
        if native_prefix:
            packages[repo_name(name)]["native_prefix"] = native_prefix

    return {
        "schema_version": 1,
        "root": repo_name(nodes[0]["name"]),
        "packages": dict(sorted(packages.items())),
    }


def read_native_overrides(overrides_path: Path | None) -> dict[str, str]:
    if overrides_path is None or not overrides_path.is_file():
        return {}
    overrides = json.loads(overrides_path.read_text())
    return overrides.get("native", {})


CANONICAL_NATIVE_OVERRIDES = "native_overrides.json"


def read_provider_versions(overrides_path: Path | None) -> tuple[dict[str, str] | None, list[dict]]:
    """Return (provided_versions, known_version_substitutions) for an overrides file.

    The canonical native_overrides.json declares provided_versions for every key
    (migration_ledger_check enforces that). Ad-hoc capture override files such as
    `.tmp_mkfontscale_native_overrides.json` may omit the map; their keys then
    take the versions recorded for the same keys in the canonical file next to
    them. A key with no recorded version in either file is rejected, so an
    ad-hoc flip can never skip the version check. Returns (None, []) only when no
    overrides file is in use.
    """
    if overrides_path is None or not overrides_path.is_file():
        return None, []
    overrides = json.loads(overrides_path.read_text())
    if "provided_versions" in overrides:
        return overrides["provided_versions"], overrides.get("known_version_substitutions", [])

    native = overrides.get("native", {})
    canonical_path = overrides_path.parent / CANONICAL_NATIVE_OVERRIDES
    canonical: dict = {}
    if canonical_path.is_file() and canonical_path.resolve() != overrides_path.resolve():
        canonical = json.loads(canonical_path.read_text())
    canonical_versions = canonical.get("provided_versions", {})
    missing = sorted(key for key in native if key not in canonical_versions)
    if missing:
        raise SystemExit(
            f"{overrides_path} declares no provided_versions and "
            f"{canonical_path} records no provided version for: {', '.join(missing)}. "
            "Add a provided_versions map naming the concrete version each native "
            "recipe builds."
        )
    provided = {key: canonical_versions[key] for key in native}
    substitutions = [
        entry
        for entry in canonical.get("known_version_substitutions", [])
        if entry.get("key") in native
    ]
    return provided, substitutions


def normalize_providers(lock: dict, overrides_path: Path | None) -> dict:
    """Flip nodes to a native provider without editing the generated lock.

    `native_overrides.json` maps a *Spack package name* (e.g. "zlib-ng") to a
    native Bazel rule label that emits a prefix-identical, ABI-identical install
    tree. Every run starts by resetting providers to `build: "spack"`, then
    applies the current override set. That makes `--reuse-if-valid` safe in both
    directions: enabling native flips a reused all-spack lock, and disabling
    native clears stale `native_prefix` fields. Only the provider changes; the
    node's prefix, link_deps, and link_libs — the DAG shape and consumption
    edges — stay exactly as the concrete Spack DAG produced them.
    """
    for node in lock["packages"].values():
        node["build"] = "spack"
        node.pop("native_prefix", None)
    native = read_native_overrides(overrides_path)
    if not native:
        return lock
    validate_odr_sensitive_native_overrides(list(lock["packages"].values()), native)
    provided_versions, known_version_substitutions = read_provider_versions(overrides_path)
    validate_native_provider_versions(
        list(lock["packages"].values()),
        native,
        provided_versions,
        known_version_substitutions,
    )
    for node in lock["packages"].values():
        pkg = node["package"]
        version = node.get("version", "")
        native_prefix = native_override_for(pkg, version, native)
        if native_prefix:
            node["build"] = "native"
            node["native_prefix"] = native_prefix
    return lock


def lock_is_valid(lock_path: Path, expected_root: str | None = None) -> bool:
    """A lock is reusable if it matches the requested root and prefixes exist."""
    try:
        lock = json.loads(lock_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if expected_root is not None and lock.get("root") != expected_root:
        return False
    for node in lock.get("packages", {}).values():
        build = node.get("build", "spack")
        if build == "native":
            if not node.get("native_prefix"):
                return False
        else:
            prefix = node.get("prefix", "")
            if not prefix or not Path(prefix).is_dir():
                return False
        # The newer lock schema records include search paths; older locks that
        # omit it can lead to incorrect consumption graphs (e.g. libxml2).
        if "include_dirs" not in node:
            return False
    return bool(lock.get("packages"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spack", default="spack", help="spack binary")
    parser.add_argument("--root", required=True, help="root spack spec, e.g. zlib-ng")
    parser.add_argument("--out", type=Path, required=True, help="lockfile path")
    parser.add_argument(
        "--from-installed",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--opt-root",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--native-overrides",
        type=Path,
        default=None,
        help="optional native_overrides.json mapping package -> native rule "
        "label; matched nodes are flipped to build:native without editing the "
        "generated topology",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="seconds to allow `spack spec` before failing",
    )
    parser.add_argument(
        "--reuse-if-valid",
        action="store_true",
        help="if --out already exists and its prefixes are installed, keep it "
        "instead of re-running `spack spec` (useful when concretize is slow)",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="pass --fresh to `spack spec` so concretization ignores stale installed specs",
    )
    args = parser.parse_args(argv)

    if args.from_installed is not None or args.opt_root is not None:
        raise SystemExit(
            "installed-prefix Spack snapshots are disabled: generate locks via "
            "Bazel's @spack_dist tool inside the insula"
        )
    if os.environ.get("VASO_IN_INSULA") != "1":
        raise SystemExit(
            "live Spack lock generation must run inside the insula via "
            "Bazel's @spack_dist tool (VASO_IN_INSULA=1)"
        )

    expected_root = repo_name(args.root)
    if (
        args.reuse_if_valid
        and not args.fresh
        and args.out.is_file()
        and lock_is_valid(args.out, expected_root)
    ):
        lock = json.loads(args.out.read_text())
        lock = normalize_providers(lock, args.native_overrides)
        args.out.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
        print(f"reused valid lock {args.out}")
        return 0

    try:
        spec = spack_spec_json(args.spack, args.root, args.timeout, fresh=args.fresh)
    except subprocess.TimeoutExpired:
        if args.out.is_file() and lock_is_valid(args.out, expected_root):
            print(
                f"WARNING: `spack spec {args.root}` timed out after {args.timeout}s; "
                f"reusing existing valid lock {args.out}"
            )
            return 0
        raise SystemExit(
            f"`spack spec {args.root}` timed out after {args.timeout}s and no valid "
            f"existing lock at {args.out} to fall back on"
        )
    provided_versions, known_version_substitutions = read_provider_versions(args.native_overrides)
    lock = build_lock(
        spec,
        args.spack,
        native_overrides=read_native_overrides(args.native_overrides),
        provided_versions=provided_versions,
        known_version_substitutions=known_version_substitutions,
    )

    lock = normalize_providers(lock, args.native_overrides)
    args.out.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.out} ({len(lock['packages'])} packages, root={lock['root']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
