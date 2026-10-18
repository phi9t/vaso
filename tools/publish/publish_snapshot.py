#!/usr/bin/env python3
"""Create a one-commit sanitized snapshot branch for public review."""
from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import getpass
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from collections import Counter
from pathlib import Path, PurePosixPath

DEFAULT_EXCLUDE_PATTERNS = (
    ".scratch",
    ".scratch/**",
    ".claude",
    ".claude/**",
    ".trae",
    ".trae/**",
    ".worktrees",
    ".worktrees/**",
)
PROTECTED_BRANCHES = frozenset(
    {
        "main",
        "master",
        "ultron/mainline",
        "origin/main",
        "origin/master",
        "origin/ultron/mainline",
    }
)
DEFAULT_AUTHOR_NAME = "Snapshot Publisher"
DEFAULT_AUTHOR_EMAIL = "snapshot-publisher@example.invalid"
PUBLICATION_EXCLUDES_PATH = "tools/publish/publication-excludes.txt"
PUBLICATION_REGENERATE_PATH = "tools/publish/publication-regenerate.txt"
BAZEL_GRAPH_ROOT = "foundry"


class UsageError(Exception):
    pass


class PublishError(Exception):
    pass


@dataclasses.dataclass(frozen=True)
class Rule:
    index: int
    kind: str
    pattern: str
    replacement: str = ""
    compiled: re.Pattern[str] | None = None
    include_globs: tuple[str, ...] = ()
    exclude_globs: tuple[str, ...] = ()

    @property
    def is_replacement(self) -> bool:
        return self.kind in {"literal", "regex"}

    @property
    def is_exclude(self) -> bool:
        return self.kind == "exclude"

    def replace(self, text: str) -> tuple[str, int]:
        if self.kind == "literal":
            hits = text.count(self.pattern)
            return text.replace(self.pattern, self.replacement), hits
        if self.kind == "regex" and self.compiled is not None:
            return self.compiled.subn(self.replacement, text)
        return text, 0

    def matches(self, text: str) -> bool:
        if self.kind == "literal":
            return self.pattern in text
        if self.kind == "regex" and self.compiled is not None:
            return self.compiled.search(text) is not None
        return False

    def applies_to_path(self, relpath: str) -> bool:
        relpath = normalize_relpath(relpath)
        if self.include_globs and not any(path_matches_pattern(relpath, pattern) for pattern in self.include_globs):
            return False
        if self.exclude_globs and any(path_matches_pattern(relpath, pattern) for pattern in self.exclude_globs):
            return False
        return True


def git(repo: Path, *args: str, check: bool = True, input_bytes: bytes | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_bytes,
        check=check,
        capture_output=True,
        text=input_bytes is None,
    )


def load_rules(path: Path) -> list[Rule]:
    try:
        raw_rules = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise UsageError(f"rules file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise UsageError(f"rules file is not valid JSON: {path}") from exc
    if not isinstance(raw_rules, list):
        raise UsageError("rules file must contain a JSON list")
    return [parse_rule(index, raw) for index, raw in enumerate(raw_rules)]


def parse_rule(index: int, raw: object) -> Rule:
    if isinstance(raw, list) and len(raw) == 2 and all(isinstance(item, str) for item in raw):
        return regex_rule(index, raw[0], raw[1])
    if not isinstance(raw, dict):
        raise UsageError(f"rule {index} must be a two-string list or an object")

    if "exclude" in raw:
        pattern = raw["exclude"]
        if not isinstance(pattern, str) or not pattern:
            raise UsageError(f"rule {index} exclude must be a non-empty string")
        return Rule(index=index, kind="exclude", pattern=normalize_relpath(pattern))
    if "path_exclude" in raw:
        pattern = raw["path_exclude"]
        if not isinstance(pattern, str) or not pattern:
            raise UsageError(f"rule {index} path_exclude must be a non-empty string")
        return Rule(index=index, kind="exclude", pattern=normalize_relpath(pattern))

    replacement = raw.get("replace", raw.get("replacement", ""))
    if not isinstance(replacement, str):
        raise UsageError(f"rule {index} replacement must be a string")
    include_globs = parse_rule_globs(
        index,
        raw,
        ("include_globs", "include_paths", "paths", "path_globs", "only_paths"),
        "include_globs",
    )
    exclude_globs = parse_rule_globs(
        index,
        raw,
        ("exclude_globs", "exclude_paths", "skip_globs", "skip_paths"),
        "exclude_globs",
    )

    if "literal" in raw:
        pattern = raw["literal"]
        if not isinstance(pattern, str) or not pattern:
            raise UsageError(f"rule {index} literal must be a non-empty string")
        return Rule(
            index=index,
            kind="literal",
            pattern=pattern,
            replacement=replacement,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
        )
    if "regex" in raw:
        pattern = raw["regex"]
        if not isinstance(pattern, str) or not pattern:
            raise UsageError(f"rule {index} regex must be a non-empty string")
        return regex_rule(index, pattern, replacement, include_globs=include_globs, exclude_globs=exclude_globs)
    if "pattern" in raw:
        mode = raw.get("mode", raw.get("type", "regex"))
        pattern = raw["pattern"]
        if not isinstance(mode, str) or not isinstance(pattern, str) or not pattern:
            raise UsageError(f"rule {index} pattern rule is malformed")
        if mode in {"literal", "string"}:
            return Rule(
                index=index,
                kind="literal",
                pattern=pattern,
                replacement=replacement,
                include_globs=include_globs,
                exclude_globs=exclude_globs,
            )
        if mode in {"regex", "regexp"}:
            return regex_rule(index, pattern, replacement, include_globs=include_globs, exclude_globs=exclude_globs)
        if mode in {"exclude", "path_exclude", "path-exclude"}:
            return Rule(index=index, kind="exclude", pattern=normalize_relpath(pattern))
    raise UsageError(f"rule {index} has no supported rule key")


def parse_rule_globs(index: int, raw: dict[str, object], keys: tuple[str, ...], field: str) -> tuple[str, ...]:
    value: object | None = None
    for key in keys:
        if key in raw:
            value = raw[key]
            break
    if value is None:
        return ()
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        items = value
    else:
        raise UsageError(f"rule {index} {field} must be a string or list of strings")
    return tuple(normalize_relpath(item) for item in items if item)


def regex_rule(
    index: int,
    pattern: str,
    replacement: str,
    *,
    include_globs: tuple[str, ...] = (),
    exclude_globs: tuple[str, ...] = (),
) -> Rule:
    if not pattern:
        raise UsageError(f"rule {index} regex must be a non-empty string")
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise UsageError(f"rule {index} regex is invalid") from exc
    return Rule(
        index=index,
        kind="regex",
        pattern=pattern,
        replacement=replacement,
        compiled=compiled,
        include_globs=include_globs,
        exclude_globs=exclude_globs,
    )


def normalize_relpath(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def default_rules_path() -> Path:
    estate = os.environ.get("VASO_ESTATE_ROOT")
    if not estate:
        raise UsageError("--rules is required when VASO_ESTATE_ROOT is unset")
    return Path(estate) / "publish" / "sanitize-rules.json"


def default_worktree_path(branch: str, source_short: str) -> Path:
    estate = os.environ.get("VASO_ESTATE_ROOT")
    if not estate:
        raise UsageError("--worktree is required when VASO_ESTATE_ROOT is unset")
    agent = os.environ.get("VASO_AGENT", "publish")
    safe_branch = re.sub(r"[^A-Za-z0-9_.-]+", "_", branch).strip("_") or "snapshot"
    return Path(estate) / "agents" / agent / "publish-worktrees" / f"{safe_branch}-{source_short}-{int(time.time())}"


def ensure_rules_outside_repo(rules_path: Path, repo_root: Path) -> None:
    resolved = rules_path.resolve()
    if is_relative_to(resolved, repo_root.resolve()):
        raise UsageError("rules file must live outside the repository")


def validate_branch_for_snapshot(branch: str) -> None:
    if branch in PROTECTED_BRANCHES:
        raise UsageError(f"protected branch refused: {branch}")
    if not branch.startswith("publish/"):
        raise UsageError("snapshot branch must be publish/<name>")


def validate_branch_for_push(branch: str) -> None:
    if branch in PROTECTED_BRANCHES:
        raise UsageError(f"protected branch refused: {branch}")
    if not branch.startswith("publish/"):
        raise UsageError("push requires a publish/ branch")


def should_exclude_path(relpath: str, rules: list[Rule], publication_excludes: list[str]) -> tuple[bool, int | None]:
    for pattern in DEFAULT_EXCLUDE_PATTERNS:
        if path_matches_pattern(relpath, pattern):
            return True, None
    for pattern in publication_excludes:
        if path_matches_pattern(relpath, pattern):
            return True, None
    for rule in rules:
        if rule.is_exclude and path_matches_pattern(relpath, rule.pattern):
            return True, rule.index
    return False, None


def path_matches_pattern(relpath: str, pattern: str) -> bool:
    relpath = normalize_relpath(relpath)
    pattern = normalize_relpath(pattern)
    if relpath == pattern:
        return True
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return relpath == prefix or relpath.startswith(f"{prefix}/")
    return fnmatch.fnmatchcase(relpath, pattern)


def safe_archive_name(name: str) -> str | None:
    rel = PurePosixPath(name)
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        return None
    return rel.as_posix()


def clear_worktree(path: Path) -> None:
    for child in path.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def create_worktree(repo_root: Path, worktree: Path, base: str, branch: str, dry_run: bool) -> None:
    if worktree.exists():
        raise UsageError(f"worktree path exists: {worktree}")
    worktree.parent.mkdir(parents=True, exist_ok=True)
    if dry_run:
        git(repo_root, "worktree", "add", "-q", "--detach", str(worktree), base)
    else:
        if git(repo_root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0:
            raise UsageError(f"branch already exists: {branch}")
        git(repo_root, "worktree", "add", "-q", "-b", branch, str(worktree), base)


def read_text_from_source(repo_root: Path, source: str, relpath: str) -> str | None:
    res = subprocess.run(
        ["git", "-C", str(repo_root), "show", f"{source}:{relpath}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return None
    return res.stdout


def manifest_patterns(text: str | None) -> list[str]:
    if text is None:
        return []
    patterns: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        patterns.append(normalize_relpath(stripped))
    return patterns


def load_publication_excludes(repo_root: Path, source: str) -> list[str]:
    return manifest_patterns(read_text_from_source(repo_root, source, PUBLICATION_EXCLUDES_PATH))


def export_source_tree(
    repo_root: Path,
    source: str,
    worktree: Path,
    rules: list[Rule],
    publication_excludes: list[str],
) -> int:
    clear_worktree(worktree)
    hit_counts: Counter[int] = Counter()
    count = 0
    proc = subprocess.Popen(
        ["git", "-C", str(repo_root), "archive", "--format=tar", source],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdout is not None
    with tarfile.open(fileobj=proc.stdout, mode="r|*") as archive:
        for member in archive:
            rel = safe_archive_name(member.name)
            if rel is None:
                raise PublishError("unsafe archive path refused")
            excluded, rule_index = should_exclude_path(rel, rules, publication_excludes)
            if excluded:
                if rule_index is not None:
                    hit_counts[rule_index] += 1
                continue
            member.name = rel
            archive.extract(member, path=worktree)
            if member.isfile():
                count += 1
    stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr is not None else ""
    rc = proc.wait()
    if rc != 0:
        raise PublishError(stderr.strip() or f"git archive failed rc={rc}")
    for index, hits in hit_counts.items():
        export_source_tree.exclude_hits[index] += hits
    return count


export_source_tree.exclude_hits = Counter()  # type: ignore[attr-defined]


def iter_tree_paths(root: Path) -> list[Path]:
    paths = [path for path in root.rglob("*") if ".git" not in path.relative_to(root).parts]
    return sorted(paths, key=lambda path: len(path.relative_to(root).parts), reverse=True)


def scrub_name(name: str, relpath: str, rules: list[Rule], hit_counts: Counter[int]) -> str:
    new = name
    for rule in rules:
        if not rule.is_replacement or not rule.applies_to_path(relpath):
            continue
        new, hits = rule.replace(new)
        if hits:
            hit_counts[rule.index] += hits
    return new


def scrub_filenames(root: Path, rules: list[Rule], hit_counts: Counter[int]) -> int:
    changed = 0
    for path in iter_tree_paths(root):
        if not path.exists():
            continue
        rel = path.relative_to(root).as_posix()
        new_name = scrub_name(path.name, rel, rules, hit_counts)
        if new_name == path.name:
            continue
        target = path.with_name(new_name)
        if target.exists():
            raise PublishError(f"filename scrub collision at {path.relative_to(root)}")
        path.rename(target)
        changed += 1
    return changed


def read_utf8(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return None


def scrub_contents(root: Path, rules: list[Rule], hit_counts: Counter[int]) -> int:
    changed = 0
    for path in sorted(root.rglob("*")):
        if ".git" in path.relative_to(root).parts:
            continue
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            target = os.readlink(path)
            new_target = scrub_name(target, rel, rules, hit_counts)
            if new_target != target:
                path.unlink()
                path.symlink_to(new_target)
                changed += 1
            continue
        if not path.is_file():
            continue
        text = read_utf8(path)
        if text is None:
            continue
        new = text
        for rule in rules:
            if not rule.is_replacement or not rule.applies_to_path(rel):
                continue
            new, hits = rule.replace(new)
            if hits:
                hit_counts[rule.index] += hits
        if new != text:
            path.write_bytes(new.encode("utf-8"))
            changed += 1
    return changed


def prune_tracker_allowlist(root: Path) -> int:
    allowlist = root / "foundry" / "tools" / "tracker_lint_allowlist.txt"
    if not allowlist.is_file():
        return 0
    kept: list[str] = []
    removed = 0
    for line in allowlist.read_text(encoding="utf-8").splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith(".scratch/"):
            removed += 1
            continue
        kept.append(line)
    if removed:
        allowlist.write_text("".join(kept), encoding="utf-8")
    return removed


def glob_literal_prefix(pattern: str) -> str:
    pattern = normalize_relpath(pattern)
    special_indexes = [index for char in "*?[" if (index := pattern.find(char)) != -1]
    if not special_indexes:
        return pattern.rstrip("/")
    prefix = pattern[: min(special_indexes)]
    return prefix.rstrip("/")


def publication_reference_tokens(publication_excludes: list[str]) -> set[str]:
    tokens: set[str] = set()
    for pattern in publication_excludes:
        prefix = glob_literal_prefix(pattern)
        if not prefix:
            continue
        tokens.add(prefix)
        tokens.add(f"//{prefix}")
        if prefix.startswith(f"{BAZEL_GRAPH_ROOT}/"):
            tokens.add(f"//{prefix.removeprefix(f'{BAZEL_GRAPH_ROOT}/')}")
        basename = Path(prefix).name
        if len(basename) >= 4:
            tokens.add(basename)
    return {token for token in tokens if token}


def line_references_publication_exclude(line: str, tokens: set[str]) -> bool:
    return any(token in line for token in tokens)


def paren_balance(line: str) -> int:
    return line.count("(") - line.count(")")


def is_top_level_call(line: str) -> bool:
    return re.match(r"^[A-Za-z_][A-Za-z0-9_]*\(", line) is not None


def prune_bazel_wiring(path: Path, tokens: set[str]) -> int:
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    output: list[str] = []
    removed = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        if not is_top_level_call(line):
            if line_references_publication_exclude(line, tokens):
                removed += 1
            else:
                output.append(line)
            index += 1
            continue

        block = [line]
        balance = paren_balance(line)
        index += 1
        while balance > 0 and index < len(lines):
            block.append(lines[index])
            balance += paren_balance(lines[index])
            index += 1

        if line.startswith("exports_files("):
            kept_block = [entry for entry in block if not line_references_publication_exclude(entry, tokens)]
            removed += len(block) - len(kept_block)
            output.extend(kept_block)
        elif any(line_references_publication_exclude(entry, tokens) for entry in block):
            removed += len(block)
        else:
            output.extend(block)

    rewritten = "".join(output)
    if rewritten != original:
        path.write_text(rewritten, encoding="utf-8")
    return removed


def prune_reference_lines(path: Path, tokens: set[str]) -> int:
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    kept = [line for line in lines if not line_references_publication_exclude(line, tokens)]
    removed = len(lines) - len(kept)
    if removed:
        path.write_text("".join(kept), encoding="utf-8")
    return removed


def apply_publication_overlay(root: Path, publication_excludes: list[str]) -> int:
    tokens = publication_reference_tokens(publication_excludes)
    # Private tracker and coordination inputs are always excluded from exports;
    # their live Bazel targets must leave the public graph as well.
    build_tokens = tokens | publication_reference_tokens(list(DEFAULT_EXCLUDE_PATTERNS))
    changed = 0
    for path in sorted(root.rglob("*")):
        if ".git" in path.relative_to(root).parts or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel == PUBLICATION_EXCLUDES_PATH:
            continue
        if path.name == "BUILD.bazel":
            changed += prune_bazel_wiring(path, build_tokens)
        elif path.suffix in {".md", ".rst", ".txt"}:
            changed += prune_reference_lines(path, tokens)
    return changed


def run_regeneration_manifest(root: Path) -> int:
    manifest = root / PUBLICATION_REGENERATE_PATH
    if not manifest.is_file():
        return 0
    commands = [
        line.strip()
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    for index, command in enumerate(commands, start=1):
        result = subprocess.run(command, cwd=root, shell=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        if result.returncode != 0:
            raise PublishError(f"regeneration command {index} failed rc={result.returncode}")
    return len(commands)


def sync_git_index(worktree: Path) -> None:
    git(worktree, "add", "-A")


def publication_paths(root: Path) -> list[Path]:
    """Scan the staged publication, excluding ignored verification outputs."""
    if (root / ".git").exists():
        names = git(root, "ls-files", "-z").stdout.split("\0")
        return sorted(root / name for name in names if name)
    return sorted(path for path in root.rglob("*") if ".git" not in path.relative_to(root).parts)


def scan_remaining(root: Path, rules: list[Rule]) -> list[str]:
    findings: list[str] = []
    for path in publication_paths(root):
        rel = path.relative_to(root).as_posix()
        for rule in rules:
            if rule.is_exclude and path_matches_pattern(rel, rule.pattern):
                findings.append(f"rule {rule.index} path {rel}")
            elif rule.is_replacement and rule.applies_to_path(rel) and rule.matches(rel):
                findings.append(f"rule {rule.index} filename {rel}")
        if path.is_symlink():
            target = os.readlink(path)
            for rule in rules:
                if rule.is_replacement and rule.applies_to_path(rel) and rule.matches(target):
                    findings.append(f"rule {rule.index} symlink {rel}")
            continue
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="surrogateescape")
        for rule in rules:
            if rule.is_replacement and rule.applies_to_path(rel) and rule.matches(text):
                findings.append(f"rule {rule.index} content {rel}")
    return findings


# Machine-identifying residue the rules may miss. Paths inside the insula
# rootfs (/vaso, /usr, /opt, ...) are fine; host volumes and identities are not.
HOST_IDENTITY_PATTERNS = (
    ("host-volume-path", re.compile(r"/data\d{2}/")),
    ("host-user-path", re.compile(r"/Users/[A-Za-z0-9._-]+")),
    ("gpu-uuid", re.compile(r"\bGPU-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")),
    ("mac-address", re.compile(r"(?<![0-9A-Fa-f:-])(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}(?![0-9A-Fa-f:-])")),
)
PRIVATE_IPV4_PATTERN = re.compile(r"(?<![0-9A-Za-z_.])(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?![0-9A-Za-z_.]|/\d)")


def host_ip_addresses() -> set[str]:
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return set()
    addresses = set()
    for value in out.split():
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if not address.is_loopback and not address.is_unspecified:
            addresses.add(value)
    return addresses


def host_tokens(extra: list[str]) -> set[str]:
    tokens = {token for token in extra if token}
    # Namespace identities are rootfs data, not the machine's identities.
    # Host callers can pass their discovered identifiers through --host-token.
    if os.environ.get("VASO_IN_INSULA") == "1":
        return tokens
    user = getpass.getuser()
    if len(user) >= 3:
        tokens.add(user)
    host = socket.gethostname()
    if host and host != "localhost":
        tokens.add(host)
        tokens.add(host.split(".")[0])
    tokens |= host_ip_addresses()
    return tokens


def host_identity_hits(text: str, tokens: set[str]) -> list[str]:
    kinds = [kind for kind, pattern in HOST_IDENTITY_PATTERNS if pattern.search(text)]
    # Four-part package versions can look like private IPv4 addresses. Treat
    # unknown addresses as identifiers in network contexts; known host IPs are
    # always covered by the literal host tokens, including IPv6.
    for match in PRIVATE_IPV4_PATTERN.finditer(text):
        prefix = text[max(text.rfind("\n", 0, match.start()) + 1, match.start() - 80):match.start()]
        if not prefix.strip() or re.search(r"ip|addr|host|inet|nameserver|server|endpoint|connect|ping|ssh|node|network", prefix, re.IGNORECASE):
            kinds.append("private-ipv4")
            break
    if any(re.search(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", text) for token in tokens):
        kinds.append("host-token")
    return kinds


def scan_host_identity(root: Path, tokens: set[str]) -> list[str]:
    findings: list[str] = []
    for path in publication_paths(root):
        rel = path.relative_to(root).as_posix()
        findings += [f"{kind} filename {rel}" for kind in host_identity_hits(rel, tokens)]
        if path.is_symlink():
            findings += [f"{kind} symlink {rel}" for kind in host_identity_hits(os.readlink(path), tokens)]
            continue
        if not path.is_file():
            continue
        # Latin-1 preserves ASCII identifiers embedded in ELF files and other
        # binary payloads without silently excluding undecodable files.
        text = path.read_bytes().decode("latin-1")
        findings += [f"{kind} content {rel}" for kind in host_identity_hits(text, tokens)]
    return findings


def run_verify(worktree: Path, command: str) -> int:
    res = subprocess.run(command, cwd=worktree, shell=True)
    return res.returncode


def commit_snapshot(worktree: Path, message: str, author_name: str, author_email: str) -> str:
    git(worktree, "add", "-A")
    # The public commit must carry only the publishing identity and message:
    # drop the caller's GIT_* overrides and injected config, and skip hooks
    # that could append trailers.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        {
            "GIT_AUTHOR_NAME": author_name,
            "GIT_AUTHOR_EMAIL": author_email,
            "GIT_COMMITTER_NAME": author_name,
            "GIT_COMMITTER_EMAIL": author_email,
        }
    )
    subprocess.run(
        [
            "git", "-C", str(worktree), "-c", f"core.hooksPath={os.devnull}", "-c", "commit.template=",
            "commit", "-q", "--no-verify", "--allow-empty", "--cleanup=strip", "-m", message,
        ],
        check=True,
        env=env,
    )
    body = git(worktree, "log", "-1", "--format=%B").stdout.strip()
    if body != message.strip():
        raise SystemExit("publish: snapshot commit message was altered after commit; refusing")
    return git(worktree, "rev-parse", "--short", "HEAD").stdout.strip()


def clean_commit_message(message: str) -> str:
    stripped = message.strip()
    if not stripped:
        raise UsageError("commit message must not be empty")
    if "\n" in stripped or "\r" in stripped:
        raise UsageError("commit message must be a single clean subject line")
    if re.search(r"(?im)^co-authored-by:", stripped):
        raise UsageError("commit message must not contain co-author trailers")
    return stripped


def push_branch(repo_root: Path, branch: str) -> None:
    git(repo_root, "push", "origin", f"refs/heads/{branch}:refs/heads/{branch}")


def tracked_files(repo_root: Path, rev: str) -> list[str]:
    res = subprocess.run(
        ["git", "-C", str(repo_root), "ls-tree", "-r", "-z", "--name-only", rev],
        check=True,
        capture_output=True,
    )
    return [item.decode("utf-8") for item in res.stdout.split(b"\0") if item]


def git_show_bytes(repo_root: Path, rev: str, relpath: str) -> bytes:
    res = subprocess.run(["git", "-C", str(repo_root), "show", f"{rev}:{relpath}"], check=True, capture_output=True)
    return res.stdout


def rules_guard_findings(repo_root: Path, rules: list[Rule], rev: str = "HEAD") -> list[str]:
    findings: list[str] = []
    for relpath in tracked_files(repo_root, rev):
        basename = Path(relpath).name.lower()
        if basename in {"sanitize-rules.json", "sanitize_rules.json"}:
            findings.append(f"tracked sanitize rules file: {relpath}")
        for rule in rules:
            if rule.is_replacement and rule.matches(relpath):
                findings.append(f"tracked scrub term matched by rule {rule.index} in tracked path")
        data = git_show_bytes(repo_root, rev, relpath)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for rule in rules:
            if rule.is_replacement and rule.matches(text):
                findings.append(f"tracked scrub term matched by rule {rule.index} in tracked file")
    return findings


def print_summary(
    *,
    worktree: Path,
    source: str,
    base: str,
    branch: str,
    dry_run: bool,
    file_count: int,
    hit_counts: Counter[int],
    rules: list[Rule],
    publication_excludes: int,
    overlay_edits: int,
    regeneration_count: int,
    verify_rc: int | None,
    commit_sha: str | None,
    pushed: bool,
) -> None:
    print(f"worktree: {worktree}")
    print(f"source: {source}")
    print(f"base: {base}")
    print(f"branch: {branch}")
    print(f"dry-run: {'yes' if dry_run else 'no'}")
    print(f"files: {file_count}")
    print(f"publication excludes: {publication_excludes}")
    print(f"publication overlay edits: {overlay_edits}")
    print(f"regeneration commands: {regeneration_count}")
    for rule in rules:
        print(f"rule {rule.index} hits: {hit_counts.get(rule.index, 0)}")
    print("rescan: clean")
    if verify_rc is None:
        print("verify: skipped")
    elif verify_rc == 0:
        print("verify: passed")
    else:
        print(f"verify: failed rc={verify_rc}")
    if commit_sha:
        print(f"commit: {commit_sha}")
    elif dry_run:
        print("commit: skipped dry-run")
    else:
        print("commit: skipped")
    print(f"push: {'pushed' if pushed else 'skipped'}")


def publish(args: argparse.Namespace) -> int:
    repo_root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").stdout.strip())
    rules_path = args.rules or default_rules_path()
    rules_path = rules_path.expanduser()
    rules = load_rules(rules_path)
    ensure_rules_outside_repo(rules_path, repo_root)

    if args.check_rules_not_tracked:
        findings = rules_guard_findings(repo_root, rules)
        if findings:
            for finding in findings:
                print(f"REFUSED: {finding}", file=sys.stderr)
            return 1
        print("rules guard: clean")
        return 0

    if not args.branch:
        raise UsageError("--branch is required")
    if args.push:
        validate_branch_for_push(args.branch)
    validate_branch_for_snapshot(args.branch)

    source = args.source
    base = args.base
    source_short = git(repo_root, "rev-parse", "--short", source).stdout.strip()
    publication_excludes = load_publication_excludes(repo_root, source)
    worktree = (args.worktree or default_worktree_path(args.branch, source_short)).resolve()
    create_worktree(repo_root, worktree, base, args.branch, args.dry_run)

    export_source_tree.exclude_hits = Counter()  # type: ignore[attr-defined]
    file_count = export_source_tree(repo_root, source, worktree, rules, publication_excludes)
    hit_counts: Counter[int] = Counter(export_source_tree.exclude_hits)  # type: ignore[attr-defined]
    overlay_edits = apply_publication_overlay(worktree, publication_excludes)
    scrub_filenames(worktree, rules, hit_counts)
    scrub_contents(worktree, rules, hit_counts)
    pruned_allowlist_entries = prune_tracker_allowlist(worktree)
    if pruned_allowlist_entries:
        print(f"tracker allowlist pruned: {pruned_allowlist_entries}")
    regeneration_count = run_regeneration_manifest(worktree)
    sync_git_index(worktree)

    findings = scan_remaining(worktree, rules)
    findings += [f"host identity {f}" for f in scan_host_identity(worktree, host_tokens(args.host_token))]
    if findings:
        for finding in findings[:20]:
            print(f"REFUSED: rescan failed: {finding}", file=sys.stderr)
        if len(findings) > 20:
            print(f"REFUSED: rescan failed: {len(findings) - 20} additional findings", file=sys.stderr)
        return 1

    verify_command = args.verify_command or "tools/agents/standing-verify.sh --agent publish-snapshot"
    verify_rc = run_verify(worktree, verify_command)
    if verify_rc != 0:
        print_summary(
            worktree=worktree,
            source=source_short,
            base=base,
            branch=args.branch,
            dry_run=args.dry_run,
            file_count=file_count,
            hit_counts=hit_counts,
            rules=rules,
            publication_excludes=len(publication_excludes),
            overlay_edits=overlay_edits,
            regeneration_count=regeneration_count,
            verify_rc=verify_rc,
            commit_sha=None,
            pushed=False,
        )
        return 1

    commit_sha = None
    pushed = False
    # Verification can generate files that git add would otherwise publish
    # without a scan. Stage and check that final set before committing.
    sync_git_index(worktree)
    findings = scan_remaining(worktree, rules)
    findings += [f"host identity {f}" for f in scan_host_identity(worktree, host_tokens(args.host_token))]
    findings += [f"excluded path {path.relative_to(worktree)}" for path in publication_paths(worktree)
                 if should_exclude_path(path.relative_to(worktree).as_posix(), rules, publication_excludes)[0]]
    if findings:
        for finding in findings[:20]:
            print(f"REFUSED: post-verify rescan failed: {finding}", file=sys.stderr)
        return 1
    if not args.dry_run:
        message = clean_commit_message(args.message or f"Publish sanitized snapshot {source_short}")
        commit_sha = commit_snapshot(worktree, message, args.author_name, args.author_email)
        if args.push:
            push_branch(repo_root, args.branch)
            pushed = True

    print_summary(
        worktree=worktree,
        source=source_short,
        base=base,
        branch=args.branch,
        dry_run=args.dry_run,
        file_count=file_count,
        hit_counts=hit_counts,
        rules=rules,
        publication_excludes=len(publication_excludes),
        overlay_edits=overlay_edits,
        regeneration_count=regeneration_count,
        verify_rc=verify_rc,
        commit_sha=commit_sha,
        pushed=pushed,
    )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="HEAD")
    parser.add_argument("--base", default="origin/ultron/mainline")
    parser.add_argument("--branch")
    parser.add_argument("--rules", type=Path)
    parser.add_argument("--worktree", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--verify-command")
    parser.add_argument("--message", "-m")
    parser.add_argument("--host-token", action="append", default=[], help="extra host identity literal the rescan must not find")
    parser.add_argument("--author-name", default=DEFAULT_AUTHOR_NAME)
    parser.add_argument("--author-email", default=DEFAULT_AUTHOR_EMAIL)
    parser.add_argument("--check-rules-not-tracked", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        return publish(parse_args(argv))
    except UsageError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    except (PublishError, subprocess.CalledProcessError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
