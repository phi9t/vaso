#!/usr/bin/env python3
"""Find the one llvm-project commit that torch's Triton and JAX's XLA can share.

The triumvirate (PyTorch, Triton, JAX) must build against exactly one LLVM.
Triton pins its LLVM in `cmake/llvm-info.json` (or `cmake/llvm-hash.txt`),
often a commit on the triton-lang/llvm-project fork. JAX pins XLA in
`third_party/xla/revision.bzl` (or `workspace.bzl`), and XLA pins LLVM in
`third_party/llvm/workspace.bzl` and its Triton in
`third_party/triton/workspace.bzl`.

This tool resolves those pins for a torch ref and a list of JAX versions, and
measures each XLA LLVM against Triton's upstream LLVM base with the GitHub
compare API. The smallest distance is the unification candidate. Porting
Triton across that distance (or rolling XLA back) is the remaining work. See
.claude/skills/triumvirate-llvm-alignment/SKILL.md for the full procedure.

  llvm_alignment.py --torch-ref v2.14.0 --jax 0.9.0 0.10.1 0.10.2 0.11.0
  llvm_alignment.py --triton-commit <sha> --jax 0.10.2 --json

Network: api.github.com and raw.githubusercontent.com (read-only). Set
GITHUB_TOKEN to raise the rate limit (60/h unauthenticated).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

RAW = "https://raw.githubusercontent.com"
API = "https://api.github.com"
SHA = re.compile(r"\b[0-9a-f]{40}\b")


def _get(url: str) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": "vaso-llvm-alignment"})
    if url.startswith(API) and os.environ.get("GITHUB_TOKEN"):
        req.add_header("Authorization", f"Bearer {os.environ['GITHUB_TOKEN']}")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def raw(repo: str, ref: str, path: str) -> str | None:
    body = _get(f"{RAW}/{repo}/{ref}/{path}")
    return body.decode() if body is not None else None


def api(path: str):
    body = _get(f"{API}/{path}")
    return json.loads(body) if body is not None else None


def bzl_value(text: str, name: str) -> str | None:
    m = re.search(rf'{name}\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else None


def triton_pin_for_torch(torch_ref: str) -> str:
    text = raw("pytorch/pytorch", torch_ref, ".ci/docker/ci_commit_pins/triton.txt")
    if not text:
        raise SystemExit(f"no Triton pin in pytorch {torch_ref}")
    return text.strip()


def triton_llvm(triton_commit: str) -> str:
    info = raw("triton-lang/triton", triton_commit, "cmake/llvm-info.json")
    if info:
        return json.loads(info)["llvm_hash"]
    text = raw("triton-lang/triton", triton_commit, "cmake/llvm-hash.txt")
    if text and SHA.search(text):
        return SHA.search(text).group(0)
    raise SystemExit(f"no LLVM pin in triton {triton_commit}")


def upstream_base(commit: str, max_steps: int = 50) -> tuple[str, int]:
    """Walk first parents until a commit is on upstream llvm-project main.

    GitHub resolves fork-network commits through the parent repo, so "the
    commit exists" is not enough: a commit is upstream iff comparing it to
    main shows nothing on its side (behind_by == 0).
    """
    cur, steps = commit, 0
    while steps <= max_steps:
        c = api(f"repos/llvm/llvm-project/compare/{cur}...main")
        if c is not None and c["behind_by"] == 0:
            return cur, steps
        meta = api(f"repos/llvm/llvm-project/commits/{cur}") or api(f"repos/triton-lang/llvm-project/commits/{cur}")
        if not meta or not meta.get("parents"):
            break
        cur, steps = meta["parents"][0]["sha"], steps + 1
    raise SystemExit(f"could not find an upstream base for {commit}")


def xla_for_jax(jax_version: str) -> str:
    for path in ("third_party/xla/revision.bzl", "third_party/xla/workspace.bzl"):
        text = raw("jax-ml/jax", f"jax-v{jax_version}", path)
        if text and (v := bzl_value(text, "XLA_COMMIT")):
            return v
    raise SystemExit(f"no XLA pin in jax {jax_version}")


def xla_pins(xla_commit: str) -> dict:
    llvm = raw("openxla/xla", xla_commit, "third_party/llvm/workspace.bzl") or ""
    triton = raw("openxla/xla", xla_commit, "third_party/triton/workspace.bzl") or ""
    return {
        "llvm": bzl_value(llvm, "LLVM_COMMIT"),
        "llvm_sha256": bzl_value(llvm, "LLVM_SHA256"),
        "triton": bzl_value(triton, "TRITON_COMMIT"),
    }


def distance(base: str, other: str) -> dict:
    c = api(f"repos/llvm/llvm-project/compare/{base}...{other}")
    if not c:
        return {"status": "unknown"}
    return {"status": c["status"], "ahead_by": c["ahead_by"], "behind_by": c["behind_by"],
            "date": c.get("commits", [{}])[-1].get("commit", {}).get("committer", {}).get("date")
            if c.get("commits") else None}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--torch-ref", help="pytorch tag or commit, e.g. v2.14.0")
    g.add_argument("--triton-commit", help="Triton commit, if not taken from torch")
    p.add_argument("--jax", nargs="+", required=True, help="JAX versions, e.g. 0.9.0 0.10.2")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    triton = a.triton_commit or triton_pin_for_torch(a.torch_ref)
    t_llvm = triton_llvm(triton)
    base, fork_steps = upstream_base(t_llvm)
    rows = []
    for v in a.jax:
        xla = xla_for_jax(v)
        pins = xla_pins(xla)
        d = distance(base, pins["llvm"]) if pins["llvm"] else {"status": "unknown"}
        signed = (d.get("ahead_by", 0) or 0) - (d.get("behind_by", 0) or 0)
        rows.append({"jax": v, "xla": xla, **pins, "vs_triton_base": d, "signed_distance": signed})
    best = min(rows, key=lambda r: abs(r["signed_distance"]) if r["vs_triton_base"].get("status") != "unknown" else 1 << 60)
    out = {"torch_ref": a.torch_ref, "triton": triton, "triton_llvm": t_llvm,
           "triton_llvm_upstream_base": base, "triton_fork_commits_on_top": fork_steps,
           "jax": rows, "candidate": {"jax": best["jax"], "llvm": best["llvm"],
                                      "signed_distance": best["signed_distance"]}}
    if a.json:
        print(json.dumps(out, indent=1))
        return 0
    print(f"Triton {triton[:10]}  LLVM {t_llvm[:10]}  upstream base {base[:10]} (+{fork_steps} fork commits)")
    print(f"{'jax':<9} {'xla':<10} {'xla llvm':<10} {'vs triton base':>16}  xla triton")
    for r in rows:
        d = r["vs_triton_base"]
        rel = "unknown" if d.get("status") == "unknown" else (
            f"{d['ahead_by']} ahead" if r["signed_distance"] >= 0 else f"{d['behind_by']} behind")
        print(f"{r['jax']:<9} {r['xla'][:10]:<10} {(r['llvm'] or '?')[:10]:<10} {rel:>16}  {r['triton']}")
    print(f"candidate: jax {best['jax']}  llvm {best['llvm']}  ({best['signed_distance']:+d} commits vs Triton's base)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
