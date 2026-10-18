#!/usr/bin/env python3
"""Guard build-time pip invocations against online or unhashed installs."""

from __future__ import annotations

import argparse
import ast
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path


RULE_IDS = frozenset(
    (
        "missing-no-index",
        "missing-hash-policy",
        "native-source-scope",
        "wheelhouse-manifest",
    )
)
DEFAULT_RELATIVE_SOURCES = (
    "run.sh",
    "native/jaxlib/action_driver.py",
    "native/jaxlib/plan.py",
    "native/llvm/plan.py",
    "native/py_llvmlite/plan.py",
    "native/pytorch/action_driver.py",
    "native/pytorch/plan.py",
    "native/torchaudio/action_driver.py",
    "native/torchaudio/plan.py",
    "native/torchvision/action_driver.py",
    "native/torchvision/plan.py",
    "native/triton/action_driver.py",
    "native/triton/plan.py",
)
PIP_OPERATIONS = frozenset(("install", "wheel"))
INLINE_MANIFEST = re.compile(r"\bmanifest\s*=\s*\[[^\n\]]*['\"]sha256['\"]")


@dataclass(frozen=True)
class PipCommand:
    line: int
    operation: str
    tokens: tuple[str, ...]


def source_root_from(anchor: Path) -> Path:
    """Source-tree experiment root for a declared runfile under tools/."""
    return anchor.resolve().parent.parent


def default_source_paths(repo_root: Path) -> list[Path]:
    rels = set(DEFAULT_RELATIVE_SOURCES)
    native_root = repo_root / "native"
    if native_root.is_dir():
        for path in native_root.glob("*/action_driver.py"):
            rels.add(path.relative_to(repo_root).as_posix())
        for path in native_root.glob("*/plan.py"):
            rels.add(path.relative_to(repo_root).as_posix())
    return [repo_root / rel for rel in sorted(rels)]


def _display_path(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _string_tokens(node: ast.AST) -> list[str]:
    if isinstance(node, (ast.List, ast.Tuple)):
        tokens: list[str] = []
        for item in node.elts:
            tokens.extend(_string_tokens(item))
        return tokens
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _string_tokens(node.left) + _string_tokens(node.right)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    return []


def _is_pip_token(token: str) -> bool:
    normalized = token.strip("\"'")
    return normalized == "pip" or normalized.endswith("/pip")


def _pip_operation(tokens: list[str]) -> str | None:
    for index, token in enumerate(tokens):
        if not _is_pip_token(token):
            continue
        for candidate in tokens[index + 1 :]:
            if candidate in PIP_OPERATIONS:
                return candidate
            if candidate.startswith("-"):
                continue
            if candidate == "pip":
                continue
            break
    return None


def _has_no_index(tokens: tuple[str, ...]) -> bool:
    return any(token == "--no-index" or token.startswith("--no-index=") for token in tokens)


def _has_require_hashes(tokens: tuple[str, ...]) -> bool:
    return any(token == "--require-hashes" or token.startswith("--require-hashes=") for token in tokens)


def _has_sha256_wheelhouse_manifest(text: str) -> bool:
    if INLINE_MANIFEST.search(text) and "wheelhouse" in text:
        return True
    return "wheel_manifest" in text and "sha256" in text and "wheels" in text


def _python_pip_commands(path: Path) -> list[PipCommand]:
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        return [PipCommand(exc.lineno or 1, "parse-error", ())]

    commands: list[PipCommand] = []

    class Visitor(ast.NodeVisitor):
        def generic_visit(self, node: ast.AST) -> None:
            if isinstance(node, (ast.List, ast.Tuple, ast.BinOp)):
                tokens = _string_tokens(node)
                operation = _pip_operation(tokens)
                if operation is not None:
                    commands.append(PipCommand(getattr(node, "lineno", 1), operation, tuple(tokens)))
                    return
            super().generic_visit(node)

    Visitor().visit(tree)
    return sorted(commands, key=lambda command: command.line)


def _shell_pip_commands(path: Path) -> list[PipCommand]:
    commands: list[PipCommand] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "pip" not in stripped:
            continue
        try:
            tokens = shlex.split(stripped, comments=True)
        except ValueError:
            tokens = stripped.split()
        operation = _pip_operation(tokens)
        if operation is not None:
            commands.append(PipCommand(line_number, operation, tuple(tokens)))
    return commands


def _scan_commands(path: Path) -> list[PipCommand]:
    if path.suffix == ".py":
        return _python_pip_commands(path)
    if path.suffix == ".sh":
        return _shell_pip_commands(path)
    return []


def check_sources(paths: list[Path], repo_root: Path) -> list[str]:
    errors: list[str] = []
    for path in paths:
        if not path.exists():
            continue
        rel = _display_path(path, repo_root)
        text = path.read_text(encoding="utf-8")
        has_manifest = _has_sha256_wheelhouse_manifest(text)
        for command in _scan_commands(path):
            if command.operation == "parse-error":
                errors.append(f"{rel}:{command.line}: could not parse Python source")
                continue
            if not _has_no_index(command.tokens):
                errors.append(f"{rel}:{command.line}: pip {command.operation} is missing --no-index")
            if command.operation == "install" and not _has_require_hashes(command.tokens) and not has_manifest:
                errors.append(
                    f"{rel}:{command.line}: pip install lacks --require-hashes or a sha256-pinned local wheelhouse"
                )
    return errors


def check(source_root: Path) -> list[str]:
    return check_sources(default_source_paths(source_root), repo_root=source_root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-anchor",
        type=Path,
        default=Path(__file__),
        help="declared runfile under tools/ used to locate the source tree",
    )
    parser.add_argument("--source", type=Path, action="append", default=[])
    args = parser.parse_args(argv)

    source_root = source_root_from(args.source_anchor)
    sources = args.source or default_source_paths(source_root)
    errors = check_sources(sources, repo_root=source_root)
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    print(f"pip hygiene guard passed ({len([path for path in sources if path.exists()])} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
