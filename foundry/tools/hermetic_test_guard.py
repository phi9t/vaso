#!/usr/bin/env python3
"""Statically guard standing tests against network and moving-ref probes."""

from __future__ import annotations

import argparse
import ast
import os
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


RULE_IDS = frozenset(
    (
        "fetch-url-literal",
        "live-estate-state",
        "local-test",
        "moving-git-ref",
        "nested-bazel-hermetic",
    )
)

TEST_RULE_KINDS = frozenset(("py_test", "sh_test", "cc_test"))
BAZEL_COMMANDS = frozenset(("build", "cquery", "fetch", "query", "run", "sync", "test"))
FETCH_EXECUTABLES = frozenset(("curl", "wget"))
GIT_MOVING_COMMANDS = frozenset(("diff", "log", "merge-base"))
URL_RE = re.compile(r"https?://[^\s'\"\\)\],]+")
BAZEL_DEP_RE = re.compile(r"bazel_dep\(\s*name\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)
LOCAL_OVERRIDE_RE = re.compile(r"local_path_override\(\s*module_name\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)
LIVE_ESTATE_MARKERS = (
    "vaso/lines",
    "rootfs-lines",
    "releases",
)
LIVE_OUTPUT_BASE_MARKER = "cache/bazel/output-base"
TEMP_ENV_MARKERS = ("$TEST_TMPDIR", "$TMPDIR")
TMP_PATH_SEGMENT = "/" + "tmp"


@dataclass(frozen=True, order=True)
class Finding:
    rule: str
    path: str
    line: int
    detail: str

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"

    @property
    def identity(self) -> str:
        return f"{self.rule}\t{self.location}\t{self.detail}"


@dataclass(frozen=True)
class AllowlistEntry:
    rule: str
    location: str
    detail: str
    reason: str

    def matches(self, finding: Finding) -> bool:
        return (
            self.rule == finding.rule
            and self.detail == finding.detail
            and self.location in {finding.location, f"{finding.path}:*"}
        )


@dataclass(frozen=True)
class BuildRule:
    package: str
    kind: str
    name: str
    srcs: tuple[str, ...]
    main: str | None
    tags: tuple[str, ...]
    local: bool
    build_rel: str
    line: int

    @property
    def label(self) -> str:
        return f"//{self.package}:{self.name}" if self.package else f"//:{self.name}"


@dataclass(frozen=True)
class SourceContext:
    repo_root: Path
    path: Path
    rel: str
    text: str
    bazel_deps: frozenset[str]
    local_overrides: frozenset[str]
    has_local_override: bool


def source_root_from(anchor: Path) -> Path:
    resolved = anchor.resolve()
    for candidate in (resolved, *resolved.parents):
        if (candidate / "foundry").is_dir() and (
            candidate / "tools" / "agents" / "standing-verify.sh"
        ).is_file():
            return candidate
    if resolved.parent.name == "tools" and resolved.parents[1].name == "foundry":
        return resolved.parents[2]
    return Path.cwd()


def display_path(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def experiment_root(repo_root: Path) -> Path:
    return repo_root / "foundry"


def _func_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _func_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return None


def _call_name(node: ast.AST) -> str | None:
    return _func_name(node.func) if isinstance(node, ast.Call) else None


def _keyword(node: ast.Call, name: str) -> ast.AST | None:
    for keyword in node.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _string_list(node: ast.AST | None) -> tuple[str, ...]:
    if node is None:
        return ()
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value,)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values: list[str] = []
        for item in node.elts:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                values.append(item.value)
        return tuple(values)
    return ()


def _string_value(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _bool_value(node: ast.AST | None) -> bool:
    return bool(isinstance(node, ast.Constant) and node.value is True)


def parse_build_rules(build_path: Path, repo_root: Path, experiment: Path) -> list[BuildRule]:
    package = build_path.parent.relative_to(experiment).as_posix()
    if package == ".":
        package = ""
    tree = ast.parse(build_path.read_text(encoding="utf-8"), filename=str(build_path))
    rules: list[BuildRule] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kind = _call_name(node)
        if kind is None:
            continue
        kind = kind.rsplit(".", 1)[-1]
        if kind not in TEST_RULE_KINDS and not kind.endswith("_test"):
            continue
        name = _string_value(_keyword(node, "name"))
        if not name:
            continue
        rules.append(
            BuildRule(
                package=package,
                kind=kind,
                name=name,
                srcs=_string_list(_keyword(node, "srcs")),
                main=_string_value(_keyword(node, "main")),
                tags=_string_list(_keyword(node, "tags")),
                local=_bool_value(_keyword(node, "local")),
                build_rel=display_path(build_path, repo_root),
                line=getattr(_keyword(node, "local"), "lineno", node.lineno),
            )
        )
    return rules


def _parse_label(label: str) -> tuple[str, str]:
    if not label.startswith("//"):
        raise ValueError(f"expected absolute Bazel label, got {label!r}")
    body = label[2:]
    if ":" in body:
        package, name = body.split(":", 1)
    else:
        package = body
        name = Path(package).name
    return package, name


def _build_path_for_label(label: str, experiment: Path) -> Path:
    package, _ = _parse_label(label)
    return experiment / package / "BUILD.bazel"


def standing_verify_labels(standing_verify: Path) -> tuple[list[str], set[str]]:
    text = standing_verify.read_text(encoding="utf-8")
    labels: list[str] = []
    excluded_tags: set[str] = set()
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if '"$BAZEL"' not in line or " test" not in line:
            continue
        command_lines = [line]
        for follow in lines[index + 1 :]:
            command_lines.append(follow)
            if ">" in follow or ")" in follow and not follow.rstrip().endswith("\\"):
                break
            if not follow.rstrip().endswith("\\") and "--test_tag_filters" in follow:
                break
        command_text = "\n".join(command_lines)
        for label in re.findall(r"(?<![A-Za-z0-9_:/=+@-])//[A-Za-z0-9_./+-]+(?::[A-Za-z0-9_./+-]+)?", command_text):
            if label not in labels:
                labels.append(label)
        for raw in re.findall(r"--test_tag_filters=([^\s\\]+)", command_text):
            for tag in raw.split(","):
                tag = tag.strip()
                if tag.startswith("-") and len(tag) > 1:
                    excluded_tags.add(tag[1:])
        break
    if not labels:
        raise ValueError(f"{standing_verify}: could not find standing Bazel test labels")
    return labels, excluded_tags


def selected_test_rules(repo_root: Path, standing_verify: Path) -> list[BuildRule]:
    labels, excluded_tags = standing_verify_labels(standing_verify)
    exp = experiment_root(repo_root)
    rules: list[BuildRule] = []
    seen: set[str] = set()
    for label in labels:
        package, name = _parse_label(label)
        build_rules = parse_build_rules(_build_path_for_label(label, exp), repo_root, exp)
        selected = build_rules if name == "all" else [rule for rule in build_rules if rule.name == name]
        for rule in selected:
            if excluded_tags.intersection(rule.tags):
                continue
            if rule.label not in seen:
                seen.add(rule.label)
                rules.append(rule)
    return rules


def _resolve_src(src: str, rule: BuildRule, experiment: Path) -> Path:
    if src.startswith("//"):
        package, name = _parse_label(src)
        return experiment / package / name
    if src.startswith(":"):
        return experiment / rule.package / src[1:]
    return experiment / rule.package / src


def standing_test_source_paths(repo_root: Path, standing_verify: Path) -> list[Path]:
    exp = experiment_root(repo_root)
    paths: list[Path] = []
    seen: set[Path] = set()
    for rule in selected_test_rules(repo_root, standing_verify):
        for src in rule.srcs:
            path = _resolve_src(src, rule, exp)
            if path.suffix not in {".py", ".sh", ".bash"}:
                continue
            if path not in seen:
                seen.add(path)
                paths.append(path)
    return paths


def standing_local_findings(repo_root: Path, standing_verify: Path) -> list[Finding]:
    return [
        Finding("local-test", rule.build_rel, rule.line, f"{rule.label} sets local = True")
        for rule in selected_test_rules(repo_root, standing_verify)
        if rule.local
    ]


def _basename(token: str) -> str:
    token = token.strip("'\"")
    if token.startswith("<") and token.endswith(">"):
        return token
    return os.path.basename(token)


def _looks_like_bazel(token: str) -> bool:
    base = _basename(token)
    lowered = base.lower()
    return lowered in {"bazel", "bazel-real"} or "bazel" in lowered or token == "<bazel>"


def _looks_like_git(token: str) -> bool:
    return _basename(token) == "git" or token == "<git>"


def _has_lockfile_off(tokens: list[str]) -> bool:
    for index, token in enumerate(tokens):
        if token == "--lockfile_mode=off" or token.startswith("--lockfile_mode=off"):
            return True
        if token == "--lockfile_mode" and index + 1 < len(tokens) and tokens[index + 1] == "off":
            return True
    return False


def _is_url_fetch_call(name: str | None) -> bool:
    if name is None:
        return False
    tail = name.rsplit(".", 1)[-1]
    if tail in {"urlopen", "urlretrieve"}:
        return True
    if name.startswith("requests.") and tail in {"delete", "get", "head", "patch", "post", "put", "request"}:
        return True
    return False


def _string_literals(node: ast.AST) -> list[str]:
    return [
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    ]


def _env_var_from_node(node: ast.AST) -> str | None:
    if isinstance(node, ast.Subscript):
        name = _func_name(node.value)
        if name == "os.environ":
            key = _string_value(node.slice)
            if key:
                return key
    if isinstance(node, ast.Call) and _call_name(node) == "os.environ.get" and node.args:
        key = _string_value(node.args[0])
        if key:
            return key
    return None


def _call_returns_executable_token(node: ast.Call) -> str | None:
    name = _call_name(node)
    if not name:
        return None
    tail = name.rsplit(".", 1)[-1].lower()
    if tail in {"bazel_binary", "bazel_executable", "bazel_path"}:
        return "<bazel>"
    if tail in {"git_binary", "git_executable", "git_path"}:
        return "<git>"
    return None


def _path_join(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    return left.rstrip("/") + "/" + right.lstrip("/")


def _scalar_from_node(node: ast.AST, scalars: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append(_scalar_from_node(value.value, scalars) or "{}")
            else:
                return None
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Div)):
        left = _scalar_from_node(node.left, scalars)
        right = _scalar_from_node(node.right, scalars)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Div):
            return _path_join(left, right)
        return left + right
    if isinstance(node, ast.Name):
        if node.id in scalars:
            return scalars[node.id]
        return "<bazel>" if "bazel" in node.id.lower() else f"<{node.id}>"
    if isinstance(node, ast.Attribute):
        name = _func_name(node) or node.attr
        return "<bazel>" if "bazel" in name.lower() else f"<{name}>"
    if env_var := _env_var_from_node(node):
        return f"${env_var}"
    if isinstance(node, ast.Call):
        call_name = _call_name(node)
        if call_name == "str" and node.args:
            return _scalar_from_node(node.args[0], scalars)
        if call_name in {"Path", "pathlib.Path"} and node.args:
            return _scalar_from_node(node.args[0], scalars)
        executable = _call_returns_executable_token(node)
        if executable:
            return executable
    return None


def _token_from_node(node: ast.AST, scalars: dict[str, str]) -> str | None:
    return _scalar_from_node(node, scalars)


def _tokens_from_node(
    node: ast.AST,
    lists: dict[str, list[str]],
    scalars: dict[str, str],
) -> list[str] | None:
    if isinstance(node, (ast.List, ast.Tuple)):
        tokens: list[str] = []
        for item in node.elts:
            if isinstance(item, ast.Starred):
                name = _func_name(item.value) or "starred"
                tokens.append(f"<starred:{name}>")
                continue
            token = _token_from_node(item, scalars)
            if token is None:
                return None
            tokens.append(token)
        return tokens
    if isinstance(node, ast.Name):
        return lists.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _tokens_from_node(node.left, lists, scalars)
        right = _tokens_from_node(node.right, lists, scalars)
        if left is not None and right is not None:
            return [*left, *right]
    return None


def _is_live_path_expression(node: ast.AST, scalars: dict[str, str]) -> bool:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return True
    if _env_var_from_node(node):
        return True
    if isinstance(node, ast.Call):
        name = _call_name(node)
        if name in {"Path", "pathlib.Path"}:
            return True
        if name == "str" and node.args:
            return _is_live_path_expression(node.args[0], scalars)
    if isinstance(node, ast.JoinedStr):
        for value in node.values:
            if isinstance(value, ast.FormattedValue):
                scalar = _scalar_from_node(value.value, scalars) or ""
                if scalar.startswith("$VASO_ESTATE_ROOT"):
                    return True
    if isinstance(node, ast.Name):
        scalar = scalars.get(node.id, "")
        return (
            scalar.startswith("$VASO_ESTATE_ROOT")
            or any(marker in scalar for marker in LIVE_ESTATE_MARKERS)
            or LIVE_OUTPUT_BASE_MARKER in scalar
        )
    return False


def _moving_ref(token: str) -> bool:
    token = token.strip("'\"")
    if not token or token == "--":
        return False
    if token.startswith("-"):
        return False
    if token.startswith("origin/"):
        return True
    if re.fullmatch(r"[0-9a-fA-F]{7,40}", token):
        return False
    if token in {"HEAD", "FETCH_HEAD", "MERGE_HEAD", "ORIG_HEAD"}:
        return False
    if re.fullmatch(r"HEAD(?:[~^][0-9]+?)*", token):
        return False
    if token in {"main", "master", "develop", "trunk", "ultron/mainline"}:
        return True
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]*(?:/[A-Za-z0-9._-]+)+", token):
        return True
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]*", token))


def _git_moving_ref(tokens: list[str]) -> str | None:
    for index, token in enumerate(tokens):
        if not _looks_like_git(token):
            continue
        if index + 1 >= len(tokens) or tokens[index + 1] not in GIT_MOVING_COMMANDS:
            continue
        for arg in tokens[index + 2 :]:
            if arg == "--":
                break
            if _moving_ref(arg):
                return f"git {tokens[index + 1]} against moving ref {arg}"
    return None


def _fetch_urls_from_command(tokens: list[str]) -> list[str]:
    urls = [token for token in tokens if URL_RE.search(token)]
    if not urls:
        return []
    bases = [_basename(token) for token in tokens]
    if any(base in FETCH_EXECUTABLES for base in bases):
        return urls
    for index, base in enumerate(bases[:-1]):
        if base == "git" and tokens[index + 1] == "clone":
            return urls
    return []


def _allowed_estate_temp_path(text: str) -> bool:
    return (
        text.startswith("$VASO_ESTATE_ROOT/agents/")
        and TMP_PATH_SEGMENT in text.removeprefix("$VASO_ESTATE_ROOT/agents/")
    ) or any(marker in text for marker in TEMP_ENV_MARKERS)


def _has_path_component_sequence(text: str, marker: str) -> bool:
    normalized = text.replace("\\", "/")
    if marker == "releases":
        return _has_path_component_sequence(normalized, "$VASO_ESTATE_ROOT/releases")
    pattern = r"(^|[$\s\"'=:/])" + re.escape(marker).replace("/", r"/+") + r"(/|$)"
    return re.search(pattern, normalized) is not None


def _live_estate_detail(value: str) -> str | None:
    text = value.replace("\\", "/").replace("//", "/")
    if text.startswith("<"):
        return None
    if _allowed_estate_temp_path(text):
        return None
    if any(_has_path_component_sequence(text, marker) for marker in LIVE_ESTATE_MARKERS):
        return f"uses live estate path {text}"
    if LIVE_OUTPUT_BASE_MARKER in text and "$VASO_ESTATE_ROOT" in text:
        return f"uses live estate path {text}"
    if "$VASO_ESTATE_ROOT/" in text:
        suffix = text.split("$VASO_ESTATE_ROOT/", 1)[1]
        if suffix and not suffix.startswith("agents/"):
            return f"uses VASO_ESTATE_ROOT outside test temp dir: {text}"
    return None


def _nested_bazel_detail(tokens: list[str], context: SourceContext) -> str | None:
    if not any(_looks_like_bazel(token) for token in tokens[:3]):
        return None
    has_starred_args = any(token.startswith("<starred:") for token in tokens)
    if not any(token in BAZEL_COMMANDS for token in tokens) and not has_starred_args:
        return None
    details: list[str] = []
    if not _has_lockfile_off(tokens) and not has_starred_args:
        details.append("missing --lockfile_mode=off")
    if not context.has_local_override:
        details.append("no local_path_override declarations")
    missing = sorted(context.bazel_deps - context.local_overrides)
    if missing:
        details.append("missing local_path_override for bazel_dep: " + ",".join(missing))
    return "; ".join(details) if details else None


class PythonScanner(ast.NodeVisitor):
    def __init__(self, context: SourceContext) -> None:
        self.context = context
        self.findings: list[Finding] = []
        self.list_env_stack: list[dict[str, list[str]]] = [{}]
        self.scalar_env_stack: list[dict[str, str]] = [{}]

    @property
    def list_env(self) -> dict[str, list[str]]:
        return self.list_env_stack[-1]

    @property
    def scalar_env(self) -> dict[str, str]:
        return self.scalar_env_stack[-1]

    def _append_live_finding(self, node: ast.AST, value: str | None) -> None:
        if value is None:
            return
        detail = _live_estate_detail(value)
        if detail:
            self.findings.append(Finding("live-estate-state", self.context.rel, node.lineno, detail))

    def _append_live_expr_finding(self, node: ast.AST) -> None:
        if _is_live_path_expression(node, self.scalar_env):
            self._append_live_finding(node, _scalar_from_node(node, self.scalar_env))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.list_env_stack.append(dict(self.list_env))
        self.scalar_env_stack.append(dict(self.scalar_env))
        self.generic_visit(node)
        self.scalar_env_stack.pop()
        self.list_env_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node: ast.Assign) -> None:
        tokens = _tokens_from_node(node.value, self.list_env, self.scalar_env)
        scalar = _scalar_from_node(node.value, self.scalar_env)
        for target in node.targets:
            if isinstance(target, ast.Name):
                if tokens is None:
                    self.list_env.pop(target.id, None)
                else:
                    self.list_env[target.id] = tokens
                if scalar is None:
                    self.scalar_env.pop(target.id, None)
                else:
                    self.scalar_env[target.id] = scalar
        self._append_live_expr_finding(node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        tokens = _tokens_from_node(node.value, self.list_env, self.scalar_env) if node.value else None
        scalar = _scalar_from_node(node.value, self.scalar_env) if node.value else None
        if isinstance(node.target, ast.Name):
            if tokens is None:
                self.list_env.pop(node.target.id, None)
            else:
                self.list_env[node.target.id] = tokens
            if scalar is None:
                self.scalar_env.pop(node.target.id, None)
            else:
                self.scalar_env[node.target.id] = scalar
        if node.value is not None:
            self._append_live_expr_finding(node.value)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if isinstance(node.target, ast.Name):
            if isinstance(node.op, ast.Add):
                tokens = _tokens_from_node(node.value, self.list_env, self.scalar_env)
                if tokens is not None and node.target.id in self.list_env:
                    self.list_env[node.target.id] = [*self.list_env[node.target.id], *tokens]
            if isinstance(node.op, ast.Div):
                scalar = _scalar_from_node(node.value, self.scalar_env)
                if scalar is not None and node.target.id in self.scalar_env:
                    self.scalar_env[node.target.id] = _path_join(self.scalar_env[node.target.id], scalar)
                    self._append_live_finding(node, self.scalar_env[node.target.id])
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        if node.value is not None:
            self._append_live_expr_finding(node.value)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node)
        command = _tokens_from_node(node.args[0], self.list_env, self.scalar_env) if node.args else None
        if name and node.args and name.endswith(".extend"):
            receiver = name.rsplit(".", 1)[0]
            if receiver in self.list_env:
                tokens = _tokens_from_node(node.args[0], self.list_env, self.scalar_env)
                if tokens is not None:
                    self.list_env[receiver] = [*self.list_env[receiver], *tokens]
        if name and node.args and name.endswith(".append"):
            receiver = name.rsplit(".", 1)[0]
            if receiver in self.list_env:
                token = _token_from_node(node.args[0], self.scalar_env)
                if token is not None:
                    self.list_env[receiver] = [*self.list_env[receiver], token]
        if name in {"subprocess.run", "subprocess.check_call", "subprocess.check_output", "subprocess.Popen"} and command:
            self._scan_command(node, command)
        if _is_url_fetch_call(name):
            urls: list[str] = []
            for arg in node.args:
                urls.extend(url for literal in _string_literals(arg) for url in URL_RE.findall(literal))
            for keyword in node.keywords:
                urls.extend(url for literal in _string_literals(keyword.value) for url in URL_RE.findall(literal))
            for url in sorted(set(urls)):
                self.findings.append(Finding("fetch-url-literal", self.context.rel, node.lineno, f"fetches {url}"))
        if name and name.rsplit(".", 1)[-1] in {"glob", "iglob", "rglob"}:
            for arg in node.args:
                self._append_live_finding(node, _scalar_from_node(arg, self.scalar_env))
            if isinstance(node.func, ast.Attribute):
                self._append_live_finding(node, _scalar_from_node(node.func.value, self.scalar_env))
        for arg in node.args:
            self._append_live_expr_finding(arg)
        for keyword in node.keywords:
            self._append_live_expr_finding(keyword.value)
        self.generic_visit(node)

    def _scan_command(self, node: ast.Call, tokens: list[str]) -> None:
        for detail in sorted(set(filter(None, (_live_estate_detail(token) for token in tokens)))):
            self.findings.append(Finding("live-estate-state", self.context.rel, node.lineno, detail))
        detail = _nested_bazel_detail(tokens, self.context)
        if detail:
            self.findings.append(Finding("nested-bazel-hermetic", self.context.rel, node.lineno, detail))
        git_detail = _git_moving_ref(tokens)
        if git_detail:
            self.findings.append(Finding("moving-git-ref", self.context.rel, node.lineno, git_detail))
        for url in _fetch_urls_from_command(tokens):
            self.findings.append(Finding("fetch-url-literal", self.context.rel, node.lineno, f"fetches {url}"))


def source_context(path: Path, repo_root: Path) -> SourceContext:
    text = path.read_text(encoding="utf-8")
    local_overrides = set(LOCAL_OVERRIDE_RE.findall(text))
    has_local_override = "local_path_override(" in text
    if "nested_bazel_fixture" in text and "local_override_block(" in text:
        local_overrides.update(_nested_bazel_fixture_override_names(repo_root))
        has_local_override = True
    return SourceContext(
        repo_root=repo_root,
        path=path,
        rel=display_path(path, repo_root),
        text=text,
        bazel_deps=frozenset(BAZEL_DEP_RE.findall(text)),
        local_overrides=frozenset(local_overrides),
        has_local_override=has_local_override,
    )


def _nested_bazel_fixture_override_names(repo_root: Path) -> set[str]:
    helper = experiment_root(repo_root) / "tools" / "nested_bazel_fixture.py"
    if not helper.is_file():
        return set()
    try:
        tree = ast.parse(helper.read_text(encoding="utf-8"), filename=str(helper))
    except SyntaxError:
        return set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "MODULE_OVERRIDE_VERSIONS" for target in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            return set()
        names: set[str] = set()
        for key in node.value.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                names.add(key.value)
        return names
    return set()


def scan_python(path: Path, repo_root: Path) -> list[Finding]:
    context = source_context(path, repo_root)
    tree = ast.parse(context.text, filename=str(path))
    scanner = PythonScanner(context)
    scanner.visit(tree)
    return scanner.findings


def _shell_tokens(line: str) -> list[str]:
    try:
        return shlex.split(line, comments=False)
    except ValueError:
        return line.split()


def scan_shell(path: Path, repo_root: Path) -> list[Finding]:
    context = source_context(path, repo_root)
    findings: list[Finding] = []
    for line_number, raw_line in enumerate(context.text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = _shell_tokens(line)
        detail = _nested_bazel_detail(tokens, context)
        if detail:
            findings.append(Finding("nested-bazel-hermetic", context.rel, line_number, detail))
        git_detail = _git_moving_ref(tokens)
        if git_detail:
            findings.append(Finding("moving-git-ref", context.rel, line_number, git_detail))
        for detail in sorted(set(filter(None, (_live_estate_detail(token) for token in tokens)))):
            findings.append(Finding("live-estate-state", context.rel, line_number, detail))
        for url in _fetch_urls_from_command(tokens):
            findings.append(Finding("fetch-url-literal", context.rel, line_number, f"fetches {url}"))
    return findings


def _module_candidates(module: str, path: Path, repo_root: Path) -> list[Path]:
    parts = module.split(".")
    suffixes = [Path(*parts).with_suffix(".py"), Path(*parts) / "__init__.py"]
    bases = [
        path.parent,
        repo_root,
        experiment_root(repo_root),
    ]
    candidates: list[Path] = []
    for base in bases:
        for suffix in suffixes:
            candidate = base / suffix
            try:
                candidate.relative_to(repo_root)
            except ValueError:
                continue
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _imported_python_paths(path: Path, repo_root: Path) -> list[Path]:
    if path.suffix != ".py" or not path.is_file():
        return []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    paths: list[Path] = []
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module:
                modules.append(node.module)
        for module in modules:
            for candidate in _module_candidates(module, path, repo_root):
                if candidate.is_file() and candidate != path and candidate not in paths:
                    paths.append(candidate)
                    break
    return paths


def expand_python_imports(paths: Iterable[Path], repo_root: Path) -> list[Path]:
    expanded: list[Path] = []
    seen: set[Path] = set()
    queue = list(paths)
    while queue:
        path = queue.pop(0)
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        expanded.append(path)
        for imported in _imported_python_paths(path, repo_root):
            if imported.resolve() not in seen:
                queue.append(imported)
    return expanded


def _normalize_paths(paths: Iterable[Path], repo_root: Path) -> list[Path]:
    return [path if path.is_absolute() else repo_root / path for path in paths]


def collect_findings(
    repo_root: Path,
    *,
    standing_verify: Path | None = None,
    source_paths: list[Path] | None = None,
) -> list[Finding]:
    repo_root = repo_root.resolve()
    if source_paths is None:
        standing = standing_verify or repo_root / "tools" / "agents" / "standing-verify.sh"
        paths = standing_test_source_paths(repo_root, standing)
        findings = standing_local_findings(repo_root, standing)
    else:
        paths = _normalize_paths(source_paths, repo_root)
        findings = []

    paths = expand_python_imports(paths, repo_root)
    for path in paths:
        if not path.is_file():
            continue
        if path.suffix == ".py":
            findings.extend(scan_python(path, repo_root))
        elif path.suffix in {".sh", ".bash"}:
            findings.extend(scan_shell(path, repo_root))
    return sorted(set(findings))


def read_allowlist(path: Path | None) -> list[AllowlistEntry]:
    if path is None or not path.exists():
        return []
    entries: list[AllowlistEntry] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 4:
            raise ValueError(f"{path}:{line_number}: expected '<rule><TAB><path:line><TAB><detail><TAB><reason>'")
        rule, location, detail, reason = fields
        if rule not in RULE_IDS:
            raise ValueError(f"{path}:{line_number}: unknown rule {rule!r}")
        if not reason.strip():
            raise ValueError(f"{path}:{line_number}: allowlist entries need a reason")
        if rule == "fetch-url-literal" and "offline" not in reason.lower():
            raise ValueError(f"{path}:{line_number}: network allowlist reason must name why it is offline-safe")
        entries.append(AllowlistEntry(rule, location, detail, reason))
    return entries


def unallowlisted_findings(findings: list[Finding], allowlist: list[AllowlistEntry]) -> list[Finding]:
    return [finding for finding in findings if not any(entry.matches(finding) for entry in allowlist)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-anchor", type=Path, default=Path(__file__))
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--standing-verify", type=Path)
    parser.add_argument("--allowlist", type=Path)
    parser.add_argument("--source", type=Path, action="append", default=[])
    parser.add_argument("--enforce", action="store_true", help="ignore allowlist entries")
    args = parser.parse_args(argv)

    repo_root = (args.repo_root or source_root_from(args.source_anchor)).resolve()
    allowlist_path = args.allowlist or experiment_root(repo_root) / "tools" / "hermetic_test_guard_allowlist.txt"
    try:
        findings = collect_findings(
            repo_root,
            standing_verify=args.standing_verify,
            source_paths=args.source or None,
        )
        allowlist = [] if args.enforce else read_allowlist(allowlist_path)
    except (OSError, SyntaxError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2

    failures = unallowlisted_findings(findings, allowlist)
    for finding in failures:
        print(finding.identity, file=sys.stderr)
    if failures:
        print(f"hermetic test guard found {len(failures)} hit(s)", file=sys.stderr)
        return 1
    print(f"hermetic test guard passed ({len(findings)} active hit(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
