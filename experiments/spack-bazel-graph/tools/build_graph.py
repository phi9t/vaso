#!/usr/bin/env python3
"""Emit the Spack build DAG in topological order + classify each node's build system.

The migration hillclimb walks the Spack build graph one package at a time in
*topological order* (dependencies before dependents), deep-diving each node's
build to capture its recipe, then flipping its provider to a native Bazel build
(see docs/native-migration.md, tools/spack_repo.bzl). To drive that walk we need:

  1. the full build DAG (link + run + build edges — richer than the consumption
     graph in spack_graph.lock.json, which drops build-only deps);
  2. a topological order over it, so the hillclimb front is always a node whose
     dependencies are already migrated or trivially satisfiable;
  3. each node's **build system** (autotools / cmake / meson / python / cargo /
     makefile / generic / bundle), which decides *how* the recipe is captured
     and how a native Bazel build action reproduces it.

Spack records the concretized build system on each node as the `build_system`
variant in `spack spec --json`. This script must be run through Bazel's
vendored Spack wrapper inside the insula; host Spack checkouts and arbitrary
installed-prefix snapshots are not workflow inputs.

The output `build_graph.json` is the spine of the hillclimb: a topologically
ordered node list with build-system class, prefix, and edges, plus a migration
`status` field (`spack` until a node is flipped) that the ledger tracks.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import spack_to_bazel

# Build systems Spack concretizes, mapped to how a native Bazel build would
# reproduce the recipe. `generic` means the package.py drives install() by hand
# (often ./configure or cmake invoked manually) — it needs a per-package recipe
# deep-dive rather than a build-system-generic rule.
BUILD_SYSTEM_STRATEGY = {
    "autotools": "configure && make && make install (rules_foreign_cc/configure_make)",
    "cmake": "cmake -G Ninja && ninja install (rules_foreign_cc/cmake)",
    "meson": "meson && ninja install (rules_foreign_cc/meson-ish)",
    "makefile": "make && make install (rules_foreign_cc/make)",
    "python_pip": "pip wheel + install (py rules / captured setup.py)",
    "generic": "package.py install() — needs a per-package recipe deep-dive",
    "bundle": "no build — pure dependency aggregator",
    "cargo": "cargo build --release (rules_rust / captured)",
    "go": "go build (rules_go / captured)",
    "perl": "perl Makefile.PL && make install (captured ExtUtils::MakeMaker)",
}

# Toolchain/runtime nodes Bazel supplies itself; not migration targets.
TOOLCHAIN_NODES = {"compiler-wrapper", "gcc", "gmake", "gcc-runtime", "glibc"}
LEAN_FONT_RESOURCES = ["encodings"]
BROAD_FONT_RESOURCE_PACKAGES = {
    "font-adobe-100dpi",
    "font-adobe-75dpi",
    "font-adobe-utopia-100dpi",
    "font-adobe-utopia-75dpi",
    "font-adobe-utopia-type1",
    "font-alias",
    "font-arabic-misc",
    "font-bh-100dpi",
    "font-bh-75dpi",
    "font-bh-lucidatypewriter-100dpi",
    "font-bh-lucidatypewriter-75dpi",
    "font-bh-ttf",
    "font-bh-type1",
    "font-bitstream-100dpi",
    "font-bitstream-75dpi",
    "font-bitstream-speedo",
    "font-bitstream-type1",
    "font-cronyx-cyrillic",
    "font-cursor-misc",
    "font-daewoo-misc",
    "font-dec-misc",
    "font-ibm-type1",
    "font-isas-misc",
    "font-jis-misc",
    "font-micro-misc",
    "font-misc-cyrillic",
    "font-misc-ethiopic",
    "font-misc-meltho",
    "font-misc-misc",
    "font-mutt-misc",
    "font-schumacher-misc",
    "font-screen-cyrillic",
    "font-sony-misc",
    "font-sun-misc",
    "font-winitzki-cyrillic",
    "font-xfree86-type1",
}


def spack_spec_json(spack_bin: str, root: str, timeout: float, *, fresh: bool = False) -> dict:
    cmd = [spack_bin, "spec", "--json"]
    if fresh:
        cmd.append("--fresh")
    cmd.append(root)
    out = subprocess.run(
        cmd,
        capture_output=True, text=True, timeout=timeout,
    )
    if out.returncode != 0:
        raise SystemExit(f"spack spec failed ({out.returncode}):\n{out.stderr}")
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError as exc:
        stdout = out.stdout if out.stdout else "stdout was empty"
        stderr = out.stderr if out.stderr else "stderr was empty"
        raise SystemExit(
            "spack spec produced invalid JSON "
            f"for {root!r} with {spack_bin!r}: {exc}\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        ) from exc


def spack_prefix(spack_bin: str, spack_hash: str) -> str:
    out = subprocess.run(
        [spack_bin, "location", "-i", "/" + spack_hash],
        capture_output=True, text=True,
    )
    return out.stdout.strip() if out.returncode == 0 else ""


def build_system_of(node: dict) -> str:
    """The concretized build system, from the `build_system` variant."""
    return node.get("parameters", {}).get("build_system", "unknown")


def topo_order(nodes: list[dict], by_hash: dict[str, dict]) -> list[str]:
    """Kahn topological order over ALL dependency edges (deps before dependents).

    We order over the full build DAG (every deptype) because the hillclimb must
    build a node's dependencies before it — build-time deps included, since a
    native build action needs cmake/ninja/autoconf present. Returns node hashes
    with dependencies strictly before dependents. Deterministic (name-sorted).
    """
    # edge: dep_hash -> {dependent hashes}. indegree over dependencies.
    indeg: dict[str, int] = {n["hash"]: 0 for n in nodes}
    children: dict[str, list[str]] = {n["hash"]: [] for n in nodes}
    for n in nodes:
        for dep in n.get("dependencies", []):
            dh = dep["hash"]
            if dh not in indeg:
                continue
            indeg[n["hash"]] += 1
            children[dh].append(n["hash"])

    # name-sorted ready set for determinism.
    def name_of(h: str) -> str:
        return by_hash[h]["name"]

    ready = sorted([h for h, d in indeg.items() if d == 0], key=name_of)
    order: list[str] = []
    while ready:
        h = ready.pop(0)
        order.append(h)
        for c in sorted(children[h], key=name_of):
            indeg[c] -= 1
            if indeg[c] == 0:
                # insert keeping name order
                ready.append(c)
        ready.sort(key=name_of)
    if len(order) != len(nodes):
        raise SystemExit("cycle detected in Spack build DAG (unexpected)")
    return order


def build_graph(
    spec: dict,
    spack_bin: str,
    resolve_prefix: bool,
    native_overrides: dict[str, str] | None = None,
    provided_versions: dict[str, str] | None = None,
    known_version_substitutions: list[dict] | None = None,
) -> dict:
    nodes = spec["spec"]["nodes"]
    by_hash = {n["hash"]: n for n in nodes}
    order = topo_order(nodes, by_hash)
    native_overrides = native_overrides or {}
    spack_to_bazel.validate_odr_sensitive_native_overrides(nodes, native_overrides)
    spack_to_bazel.validate_native_provider_versions(
        nodes,
        native_overrides,
        provided_versions,
        known_version_substitutions,
    )

    def resolve(n: dict) -> str:
        if not resolve_prefix:
            return ""
        # Toolchain/runtime nodes (gcc, glibc, ...) are often externals with no
        # opt-tree prefix and are not migration targets; skip the slow
        # `spack location -i` re-concretize for them.
        if n["name"] in TOOLCHAIN_NODES:
            return ""
        return spack_prefix(spack_bin, n["hash"])

    entries = []
    for pos, h in enumerate(order):
        n = by_hash[h]
        name = n["name"]
        native_prefix = spack_to_bazel.native_override_for(name, n["version"], native_overrides)
        bs = build_system_of(n)
        deps = []
        for dep in n.get("dependencies", []):
            dh = dep["hash"]
            deps.append({
                "name": by_hash.get(dh, {}).get("name", dep["name"]),
                "hash": dh,
                "deptypes": sorted(dep.get("parameters", {}).get("deptypes", [])),
            })
        entry = {
            "topo_index": pos,
            "package": name,
            "version": n["version"],
            "spack_hash": h,
            "build_system": bs,
            "parameters": n.get("parameters", {}),
            "native_strategy": BUILD_SYSTEM_STRATEGY.get(bs, "UNKNOWN — investigate"),
            "is_toolchain": name in TOOLCHAIN_NODES,
            "prefix": resolve(n),
            "deps": sorted(deps, key=lambda d: d["name"]),
            # Migration status: "spack" until flipped to a native Bazel build.
            "status": "native" if native_prefix else "spack",
        }
        if native_prefix:
            entry["native_prefix"] = native_prefix
        entries.append(entry)

    # Build-system histogram for a quick read of the migration surface.
    hist: dict[str, int] = {}
    for e in entries:
        hist[e["build_system"]] = hist.get(e["build_system"], 0) + 1

    return {
        "schema_version": 1,
        "root": nodes[0]["name"],
        "n_nodes": len(entries),
        "build_system_histogram": dict(sorted(hist.items())),
        "topological_order": [e["package"] for e in entries],
        "nodes": entries,
    }


def enforce_lean_font_resources(graph: dict) -> None:
    """Reject concretized X.Org font payload outside the lean encodings set."""
    for node in graph.get("nodes", []):
        package = node.get("package")
        if package in BROAD_FONT_RESOURCE_PACKAGES:
            raise SystemExit(
                "font-util lean resource check failed: broad font-resource "
                f"package {package!r} is present in the concretized graph. "
                "Use 'font-util@1.4.1 fonts:=encodings' or "
                "'^font-util fonts:=encodings' unless this is an intentional "
                "broad-font probe with VASO_ALLOW_BROAD_FONT_RESOURCES=1."
            )
        if package != "font-util":
            continue
        fonts = node.get("parameters", {}).get("fonts")
        if fonts != LEAN_FONT_RESOURCES:
            raise SystemExit(
                "font-util lean resource check failed: expected "
                f"fonts={LEAN_FONT_RESOURCES!r}, got {fonts!r}. Use "
                "'font-util@1.4.1 fonts:=encodings' or "
                "'^font-util fonts:=encodings' so Spack replaces the default "
                "font-resource set instead of adding to it."
            )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spack", default="spack")
    ap.add_argument("--root", required=True, help="root spack spec, e.g. zlib-ng")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--fresh", action="store_true",
                    help="pass --fresh to `spack spec` so concretization ignores stale installed specs")
    ap.add_argument("--no-prefix", action="store_true",
                    help="skip per-node prefix resolution (faster; topology only)")
    ap.add_argument("--require-lean-font-resources", action="store_true",
                    help="fail if font-util is concretized with any resource except encodings")
    ap.add_argument(
        "--native-overrides",
        type=Path,
        default=None,
        help="optional native_overrides.json mapping package or package@version to native rule labels",
    )
    ap.add_argument("--from-installed", type=Path, default=None, help=argparse.SUPPRESS)
    ap.add_argument("--opt-root", type=Path, default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.from_installed is not None:
        raise SystemExit(
            "installed-prefix Spack snapshots are disabled: generate build graphs "
            "via Bazel's @spack_dist tool inside the insula"
        )
    if args.opt_root is not None:
        raise SystemExit(
            "installed-prefix Spack snapshots are disabled: prefix resolution "
            "must come from Bazel's @spack_dist tool inside the insula"
        )
    if os.environ.get("VASO_IN_INSULA") != "1":
        raise SystemExit(
            "live Spack build graph generation must run inside the insula via "
            "Bazel's @spack_dist tool (VASO_IN_INSULA=1)"
        )

    spec = spack_spec_json(args.spack, args.root, args.timeout, fresh=args.fresh)
    native_overrides = spack_to_bazel.read_native_overrides(args.native_overrides)
    provided_versions, known_version_substitutions = spack_to_bazel.read_provider_versions(args.native_overrides)
    graph = build_graph(
        spec,
        args.spack,
        resolve_prefix=not args.no_prefix,
        native_overrides=native_overrides,
        provided_versions=provided_versions,
        known_version_substitutions=known_version_substitutions,
    )
    if args.require_lean_font_resources:
        enforce_lean_font_resources(graph)
    args.out.write_text(json.dumps(graph, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.out}: {graph['n_nodes']} nodes, "
          f"build systems {graph['build_system_histogram']}")
    print("topological order:")
    for e in graph["nodes"]:
        tag = " [toolchain]" if e["is_toolchain"] else ""
        print(f"  {e['topo_index']:>3}  {e['package']:<24} {e['build_system']:<12}{tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
