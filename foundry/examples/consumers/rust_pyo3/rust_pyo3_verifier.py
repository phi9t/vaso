#!/usr/bin/env python3
"""Verify the shipped Rust/PyO3 wheel from the profile Python."""

from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import zipfile
from pathlib import Path


def _write_safetensors(path: Path) -> dict[str, object]:
    tensors = {
        "points": {
            "dtype": "F32",
            "shape": [2, 3],
            "data": struct.pack("<6f", 1.5, -2.0, 3.25, 4.5, 5.75, -6.0),
        },
        "ids": {
            "dtype": "I64",
            "shape": [4],
            "data": struct.pack("<4q", 7, 11, 13, 17),
        },
    }
    offset = 0
    header: dict[str, object] = {}
    body = bytearray()
    for name, doc in tensors.items():
        data = doc["data"]
        assert isinstance(data, bytes)
        begin = offset
        body.extend(data)
        offset += len(data)
        header[name] = {
            "dtype": doc["dtype"],
            "shape": doc["shape"],
            "data_offsets": [begin, offset],
        }
    header_data = json.dumps(header, separators=(",", ":")).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(header_data)) + header_data + bytes(body))
    return tensors


def _extract_wheel(wheel: Path, target: Path) -> dict[str, object]:
    with zipfile.ZipFile(wheel) as zf:
        zf.extractall(target)
        provenance_names = [name for name in zf.namelist() if name.endswith(".dist-info/vaso_provenance.json")]
        if len(provenance_names) != 1:
            raise AssertionError("wheel must contain exactly one vaso_provenance.json")
        provenance = json.loads(zf.read(provenance_names[0]).decode("utf-8"))
        for name in zf.namelist():
            data = zf.read(name)
            for marker in (b"<data-volume>/", b"/workspace/", b"/vaso-bazel-ob/", b"output-base", b".vaso-estate"):
                if marker in data:
                    raise AssertionError(f"{name} contains non-relocatable marker {marker!r}")
        return provenance


def _profile_output_base(torch_prefix: Path) -> Path | None:
    parts = torch_prefix.parts
    for idx, part in enumerate(parts):
        if part == "execroot":
            return Path(*parts[:idx])
    return None


def _add_profile_python_deps(torch_prefix: Path) -> None:
    output_base = _profile_output_base(torch_prefix)
    if output_base is None:
        return
    external = output_base / "external"
    if not external.is_dir():
        return
    for site in sorted(external.glob("+py_*_native+*_native/prefix/lib/python3.13/site-packages")):
        if site.is_dir():
            sys.path.insert(0, str(site))


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} WHEEL")
    wheel = Path(sys.argv[1])
    torch_prefix = os.environ.get("VASO_TORCH_PREFIX")
    if not torch_prefix:
        raise SystemExit("VASO_TORCH_PREFIX must point at the shipped torch prefix")
    torch_site = Path(torch_prefix) / "lib/python3.13/site-packages"
    _add_profile_python_deps(Path(torch_prefix))
    sys.path.insert(0, str(torch_site))

    scratch_parent = Path(os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT") or Path.cwd())
    scratch_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch_parent) as tmp:
        root = Path(tmp)
        provenance = _extract_wheel(wheel, root / "wheel")
        if provenance["python"] != "3.13.13":
            raise AssertionError(f"unexpected python provenance: {provenance}")
        if provenance["abi"] != "abi3-py313":
            raise AssertionError(f"unexpected abi provenance: {provenance}")
        if not str(provenance["rust_toolchain"]).startswith("/vaso/sources/rust/toolchains/"):
            raise AssertionError(f"rust toolchain was not pinned under /vaso/sources: {provenance}")

        sys.path.insert(0, str(root / "wheel"))
        import torch
        import vaso_rust_pyo3

        tokenizer = vaso_rust_pyo3.FixtureTokenizer()
        corpus = [
            "the rust pyo3 tokenizer round-trip",
            "regex split: punctuation, spaces, and UTF-8 cafe",
            "mmap tensors stay inside the mapped file",
        ]
        for text in corpus:
            encoded = tokenizer.encode(text)
            decoded = tokenizer.decode(encoded)
            if decoded != text:
                raise AssertionError(f"tokenizer round-trip failed: {text!r} -> {encoded!r} -> {decoded!r}")

        fixture = root / "fixture.safetensors"
        originals = _write_safetensors(fixture)
        reader = vaso_rust_pyo3.open_tensor_file(str(fixture))
        mmap_start, mmap_end = reader.mmap_bounds()
        points = reader.tensor("points")
        ids = reader.tensor("ids")
        expected_points = torch.tensor(
            [[1.5, -2.0, 3.25], [4.5, 5.75, -6.0]],
            dtype=torch.float32,
        )
        expected_ids = torch.tensor([7, 11, 13, 17], dtype=torch.int64)
        if not torch.equal(points, expected_points):
            raise AssertionError(f"points mismatch: {points}")
        if not torch.equal(ids, expected_ids):
            raise AssertionError(f"ids mismatch: {ids}")
        for name, tensor in (("points", points), ("ids", ids)):
            ptr = tensor.data_ptr()
            if not (mmap_start <= ptr < mmap_end):
                raise AssertionError(f"{name} mmap pointers are inside check failed: {ptr} not in [{mmap_start}, {mmap_end})")

    print("rust_pyo3 verifier: round-trip ok; mmap pointers are inside mapped safetensors file")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
