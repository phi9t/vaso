#!/usr/bin/env python3
"""Sweep native outputs for action-local path leaks."""

from __future__ import annotations

import argparse
import json
import mmap
import os
import sys
import zipfile
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator


SCRIPT_DIR = Path(__file__).resolve().parent
GRAPH_ROOT = SCRIPT_DIR.parent
NATIVE_COMMON = GRAPH_ROOT / "native" / "common"
if str(NATIVE_COMMON) not in sys.path:
    sys.path.insert(0, str(NATIVE_COMMON))

import plan_executor


CONTEXT_BYTES = 60
CHUNK_BYTES = 1024 * 1024


class LeakHit:
    def __init__(
        self,
        *,
        path: Path,
        token: str,
        context: str,
        kind: str,
        offset: int | None = None,
        member: str | None = None,
    ) -> None:
        self.path = str(path)
        self.member = member
        self.token = token
        self.context = context
        self.kind = kind
        self.offset = offset

    @property
    def display_path(self) -> str:
        if self.member is None:
            return self.path
        return f"{self.path}::{self.member}"

    def as_json(self) -> dict[str, object]:
        return {
            "path": self.path,
            "member": self.member,
            "display_path": self.display_path,
            "kind": self.kind,
            "offset": self.offset,
            "token": self.token,
            "context": self.context,
        }

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LeakHit):
            return False
        return self.as_json() == other.as_json()

    def __repr__(self) -> str:
        return f"LeakHit({self.as_json()!r})"


def build_forbidden_tokens(
    *,
    work: Path | None = None,
    prefix: Path | None = None,
    wheel: Path | None = None,
) -> set[str]:
    action_paths = [path for path in (work, prefix, wheel) if path is not None]
    tokens = set(plan_executor.action_path_tokens(action_paths))
    tokens.update(str(path) for path in (work, prefix, wheel) if path is not None)
    return {token for token in tokens if token}


def _token_bytes(tokens: Iterable[str]) -> list[tuple[str, bytes]]:
    return [(token, token.encode("utf-8")) for token in sorted(tokens) if token]


def printable_context(data: bytes) -> str:
    pieces: list[str] = []
    for byte in data:
        if byte == 9:
            pieces.append(r"\t")
        elif byte == 10:
            pieces.append(r"\n")
        elif byte == 13:
            pieces.append(r"\r")
        elif 32 <= byte <= 126:
            pieces.append(chr(byte))
        else:
            pieces.append(f"\\x{byte:02x}")
    return "".join(pieces)


def _hit_from_buffer(
    *,
    path: Path,
    token: str,
    offset: int,
    buffer: bytes | mmap.mmap,
    relative_offset: int,
    kind: str,
    member: str | None = None,
    context_bytes: int,
) -> LeakHit:
    start = max(0, relative_offset - context_bytes)
    end = min(len(buffer), relative_offset + len(token.encode("utf-8")) + context_bytes)
    return LeakHit(
        path=path,
        member=member,
        token=token,
        offset=offset,
        kind=kind,
        context=printable_context(bytes(buffer[start:end])),
    )


def _scan_mmap(
    path: Path,
    tokens: list[tuple[str, bytes]],
    *,
    kind: str,
    member: str | None = None,
    context_bytes: int,
) -> Iterator[LeakHit]:
    size = path.stat().st_size
    if size == 0:
        return
    with path.open("rb") as handle:
        with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
            for token, needle in tokens:
                offset = 0
                while True:
                    match = data.find(needle, offset)
                    if match < 0:
                        break
                    yield _hit_from_buffer(
                        path=path,
                        member=member,
                        token=token,
                        offset=match,
                        buffer=data,
                        relative_offset=match,
                        kind=kind,
                        context_bytes=context_bytes,
                    )
                    offset = match + 1


def _scan_stream(
    handle: BinaryIO,
    path: Path,
    tokens: list[tuple[str, bytes]],
    *,
    kind: str,
    member: str | None,
    context_bytes: int,
) -> Iterator[LeakHit]:
    max_token = max((len(needle) for _, needle in tokens), default=0)
    keep = max_token + context_bytes
    pending = b""
    base_offset = 0
    while True:
        chunk = handle.read(CHUNK_BYTES)
        if not chunk:
            break
        data = pending + chunk
        scan_upto = max(0, len(data) - keep)
        yield from _scan_buffer_region(
            data,
            path,
            tokens,
            kind=kind,
            member=member,
            context_bytes=context_bytes,
            base_offset=base_offset,
            scan_upto=scan_upto,
        )
        pending = data[-keep:] if keep else b""
        base_offset += len(data) - len(pending)
    yield from _scan_buffer_region(
        pending,
        path,
        tokens,
        kind=kind,
        member=member,
        context_bytes=context_bytes,
        base_offset=base_offset,
        scan_upto=len(pending),
    )


def _scan_buffer_region(
    data: bytes,
    path: Path,
    tokens: list[tuple[str, bytes]],
    *,
    kind: str,
    member: str | None,
    context_bytes: int,
    base_offset: int,
    scan_upto: int,
) -> Iterator[LeakHit]:
    for token, needle in tokens:
        offset = 0
        while True:
            match = data.find(needle, offset)
            if match < 0 or match >= scan_upto:
                break
            yield _hit_from_buffer(
                path=path,
                member=member,
                token=token,
                offset=base_offset + match,
                buffer=data,
                relative_offset=match,
                kind=kind,
                context_bytes=context_bytes,
            )
            offset = match + 1


def _scan_symlink(path: Path, tokens: list[tuple[str, bytes]], *, context_bytes: int) -> Iterator[LeakHit]:
    target = os.readlink(path)
    target_bytes = target.encode("utf-8", errors="surrogateescape")
    for token, needle in tokens:
        offset = 0
        while True:
            match = target_bytes.find(needle, offset)
            if match < 0:
                break
            yield _hit_from_buffer(
                path=path,
                token=token,
                offset=match,
                buffer=target_bytes,
                relative_offset=match,
                kind="symlink",
                context_bytes=context_bytes,
            )
            offset = match + 1


def _zip_entry_name_hits(path: Path, info: zipfile.ZipInfo, tokens: list[tuple[str, bytes]], *, context_bytes: int) -> Iterator[LeakHit]:
    name_bytes = info.filename.encode("utf-8")
    for token, needle in tokens:
        offset = 0
        while True:
            match = name_bytes.find(needle, offset)
            if match < 0:
                break
            yield _hit_from_buffer(
                path=path,
                member=info.filename,
                token=token,
                offset=match,
                buffer=name_bytes,
                relative_offset=match,
                kind="archive-member-name",
                context_bytes=context_bytes,
            )
            offset = match + 1


def _scan_zip(path: Path, tokens: list[tuple[str, bytes]], *, context_bytes: int) -> Iterator[LeakHit]:
    with zipfile.ZipFile(path) as archive:
        for info in sorted(archive.infolist(), key=lambda item: item.filename):
            yield from _zip_entry_name_hits(path, info, tokens, context_bytes=context_bytes)
            if info.is_dir():
                continue
            with archive.open(info, "r") as member:
                yield from _scan_stream(
                    member,
                    path,
                    tokens,
                    kind="archive-member",
                    member=info.filename,
                    context_bytes=context_bytes,
                )


def _is_zip_path(path: Path) -> bool:
    if path.suffix not in {".whl", ".zip"}:
        return False
    return zipfile.is_zipfile(path)


def scan_file(path: Path, tokens: Iterable[str], *, context_bytes: int = CONTEXT_BYTES) -> Iterator[LeakHit]:
    token_list = _token_bytes(tokens)
    if not token_list:
        return
    if path.is_symlink():
        yield from _scan_symlink(path, token_list, context_bytes=context_bytes)
    elif _is_zip_path(path):
        yield from _scan_zip(path, token_list, context_bytes=context_bytes)
    elif path.is_file():
        yield from _scan_mmap(path, token_list, kind="file", context_bytes=context_bytes)


def _iter_tree(root: Path) -> Iterator[Path]:
    if root.is_symlink() or root.is_file():
        yield root
        return
    for child in sorted(root.rglob("*")):
        if child.is_symlink() or child.is_file():
            yield child


def scan_paths(paths: Iterable[Path], tokens: Iterable[str], *, context_bytes: int = CONTEXT_BYTES) -> Iterator[LeakHit]:
    token_set = set(tokens)
    for path in paths:
        if not path.exists() and not path.is_symlink():
            raise FileNotFoundError(path)
        for candidate in _iter_tree(path):
            yield from scan_file(candidate, token_set, context_bytes=context_bytes)


def _write_text_report(hits: list[LeakHit]) -> None:
    if not hits:
        print("No path leaks found.")
        return
    print(f"Path leaks found: {len(hits)}")
    for hit in hits:
        offset = "target" if hit.offset is None else str(hit.offset)
        print(f"{hit.display_path}: kind={hit.kind} offset={offset} token={hit.token!r}")
        print(f"  context: {hit.context}")


def _write_json_report(hits: list[LeakHit]) -> None:
    report = {
        "hit_count": len(hits),
        "hits": [hit.as_json() for hit in hits],
    }
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    print()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="write machine-readable JSON")
    parser.add_argument("--work", type=Path, help="action work path to forbid")
    parser.add_argument("--prefix", type=Path, help="action prefix output path to forbid")
    parser.add_argument("--wheel", type=Path, help="action wheel output path to forbid")
    parser.add_argument("paths", nargs="+", type=Path, help="prefix, wheel, zip, file, or tree to scan")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    tokens = build_forbidden_tokens(work=args.work, prefix=args.prefix, wheel=args.wheel)
    try:
        hits = list(scan_paths(args.paths, tokens))
    except FileNotFoundError as exc:
        print(f"leak_sweep: path not found: {exc.filename or exc}", file=sys.stderr)
        return 2
    if args.json:
        _write_json_report(hits)
    else:
        _write_text_report(hits)
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
