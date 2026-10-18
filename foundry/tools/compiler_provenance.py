#!/usr/bin/env python3
"""Check compiler producers recorded in ELF .comment sections."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


CommentReader = Callable[[Path], list[str]]
DEFAULT_PROFILE = "torch"


@dataclass(frozen=True)
class ProvenanceResult:
    consumer: str
    prefix: Path
    checked: int
    skipped: int
    errors: list[str]


def load_policy(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def profile_name(policy: Mapping[str, object], profile: str | None = None) -> str | None:
    profiles = policy.get("profiles", {})
    if not isinstance(profiles, dict) or not profiles:
        return None
    selected = profile or str(policy.get("default_profile") or DEFAULT_PROFILE)
    if selected not in profiles:
        raise SystemExit(f"unknown compiler provenance profile {selected!r}")
    return selected


def policy_consumers(policy: Mapping[str, object], profile: str | None = None) -> dict[str, object]:
    profiles = policy.get("profiles", {})
    if isinstance(profiles, dict) and profiles:
        selected = profile_name(policy, profile)
        assert selected is not None
        profile_config = profiles.get(selected, {})
        if not isinstance(profile_config, dict):
            raise SystemExit(f"compiler provenance profile {selected!r} must be an object")
        consumers = profile_config.get("consumers", {})
    else:
        consumers = policy.get("consumers", {})
    if not isinstance(consumers, dict):
        raise SystemExit("compiler pathway policy must contain a consumers object")
    return consumers


def canonical_consumer(policy: Mapping[str, object], name: str, profile: str | None = None) -> str:
    consumers = policy_consumers(policy, profile)
    if name in consumers:
        return name
    for consumer, config in consumers.items():
        if not isinstance(config, dict):
            continue
        aliases = config.get("aliases", [])
        if isinstance(aliases, list) and name in aliases:
            return str(consumer)
    raise SystemExit(f"unknown compiler provenance consumer {name!r}")


def is_shared_object(path: Path) -> bool:
    name = path.name
    return name.endswith(".so") or ".so." in name


def walk_shared_objects(prefix: Path) -> list[Path]:
    if not prefix.is_dir():
        return []
    return sorted(path for path in prefix.rglob("*") if path.is_file() and is_shared_object(path))


def parse_readelf_comment(text: str) -> list[str]:
    comments: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("String dump of section"):
            continue
        match = re.match(r"^\[\s*[0-9a-fA-F]+\]\s*(?P<comment>.*)$", line)
        if match:
            comment = match.group("comment").strip()
            if comment:
                comments.append(comment)
    return comments


def parse_objdump_comment(text: str) -> list[str]:
    data = bytearray()
    for raw_line in text.splitlines():
        parts = raw_line.strip().split()
        if len(parts) < 2 or not re.fullmatch(r"[0-9a-fA-F]+", parts[0]):
            continue
        for token in parts[1:]:
            if not re.fullmatch(r"[0-9a-fA-F]{2,}", token) or len(token) % 2 != 0:
                break
            data.extend(bytes.fromhex(token))
    comments: list[str] = []
    for raw in data.split(b"\0"):
        comment = raw.decode("utf-8", errors="replace").strip()
        if comment:
            comments.append(comment)
    return comments


def read_comment_with_binutils(path: Path, readelf: str = "readelf", objdump: str = "objdump") -> list[str]:
    readelf_result = subprocess.run(
        [readelf, "-p", ".comment", str(path)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if readelf_result.returncode == 0:
        comments = parse_readelf_comment(readelf_result.stdout)
        if comments:
            return comments

    objdump_result = subprocess.run(
        [objdump, "-s", "-j", ".comment", str(path)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if objdump_result.returncode == 0:
        comments = parse_objdump_comment(objdump_result.stdout)
        if comments:
            return comments
    return []


def _is_allowlisted_prebuilt(path: Path, policy: Mapping[str, object]) -> bool:
    allowlist = _as_dict(policy.get("vendored_prebuilt_allowlist"))
    prefixes = [str(item) for item in _as_list(allowlist.get("basename_prefixes"))]
    return any(path.name.startswith(prefix) for prefix in prefixes)


def _ignored_comment_patterns(path: Path, artifact_policy: Mapping[str, object]) -> list[re.Pattern[str]]:
    patterns = [re.compile(str(pattern)) for pattern in _as_list(artifact_policy.get("ignored_regexes"))]
    for item in _as_list(artifact_policy.get("ignored_comments")):
        if not isinstance(item, dict):
            continue
        path_regex = item.get("path_regex")
        comment_regex = item.get("comment_regex")
        if path_regex is None or comment_regex is None:
            continue
        if re.search(str(path_regex), str(path)):
            patterns.append(re.compile(str(comment_regex)))
    return patterns


def _check_comments(path: Path, comments: list[str], artifact_policy: Mapping[str, object]) -> list[str]:
    errors: list[str] = []
    if not comments:
        return [f"{path}: missing .comment producer"]

    ignored_patterns = _ignored_comment_patterns(path, artifact_policy)
    checked_comments = [
        comment
        for comment in comments
        if not any(pattern.search(comment) for pattern in ignored_patterns)
    ]
    joined = "\n".join(checked_comments)
    for needle in _as_list(artifact_policy.get("required_substrings")):
        if str(needle) not in joined:
            errors.append(f"{path}: missing required .comment producer substring {str(needle)!r}")

    allowed_patterns = [re.compile(str(pattern)) for pattern in _as_list(artifact_policy.get("allowed_regexes"))]
    forbidden_patterns = [re.compile(str(pattern)) for pattern in _as_list(artifact_policy.get("forbidden_regexes"))]
    for pattern in forbidden_patterns:
        if any(pattern.search(comment) for comment in checked_comments):
            errors.append(f"{path}: forbidden .comment producer matched {pattern.pattern!r}")
    if allowed_patterns:
        for comment in checked_comments:
            if not any(pattern.search(comment) for pattern in allowed_patterns):
                errors.append(f"{path}: unapproved .comment producer {comment!r}")
    return errors


def check_prefix(
    prefix: Path,
    consumer: str,
    policy: Mapping[str, object],
    *,
    profile: str | None = None,
    comment_reader: CommentReader | None = None,
) -> ProvenanceResult:
    canonical = canonical_consumer(policy, consumer, profile)
    consumer_policy = _as_dict(policy_consumers(policy, profile).get(canonical))
    artifact_policy = _as_dict(consumer_policy.get("artifact_comment"))
    reader = comment_reader or read_comment_with_binutils
    errors: list[str] = []
    checked = 0
    skipped = 0

    for so in walk_shared_objects(prefix):
        if _is_allowlisted_prebuilt(so, policy):
            skipped += 1
            continue
        checked += 1
        errors.extend(_check_comments(so, reader(so), artifact_policy))

    if not prefix.is_dir():
        errors.append(f"{prefix}: prefix is missing")
    elif checked == 0 and skipped == 0:
        errors.append(f"{prefix}: no non-allowlisted .so files found for {canonical}")

    return ProvenanceResult(canonical, prefix, checked, skipped, errors)


def _reader_from_json(path: Path) -> CommentReader:
    data = json.loads(path.read_text(encoding="utf-8"))

    def read(path_arg: Path) -> list[str]:
        value = data.get(str(path_arg), data.get(path_arg.name, []))
        return [str(item) for item in value]

    return read


def _write_json_out(path: Path, result: ProvenanceResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "consumer": result.consumer,
                "prefix": str(result.prefix),
                "checked": result.checked,
                "skipped": result.skipped,
                "errors": result.errors,
                "verdict": "passed" if not result.errors else "failed",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix", type=Path)
    parser.add_argument("--consumer", required=True)
    parser.add_argument("--profile", default=None, help="Compiler profile containing the consumer.")
    parser.add_argument("--policy", type=Path, default=Path(__file__).with_name("compiler_pathways.json"))
    parser.add_argument("--readelf", default="/usr/bin/readelf")
    parser.add_argument("--objdump", default="/usr/bin/objdump")
    parser.add_argument("--comments-json", type=Path)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)

    policy = load_policy(args.policy)
    profile = profile_name(policy, args.profile)
    if args.comments_json is not None:
        reader = _reader_from_json(args.comments_json)
    else:
        reader = lambda path: read_comment_with_binutils(path, args.readelf, args.objdump)
    result = check_prefix(args.prefix, args.consumer, policy, profile=profile, comment_reader=reader)
    if args.json_out is not None:
        _write_json_out(args.json_out, result)

    if result.errors:
        print(
            f"compiler provenance failed for {result.consumer} "
            f"(checked={result.checked}, skipped={result.skipped}):\n  "
            + "\n  ".join(result.errors),
            file=sys.stderr,
        )
        return 1
    print(
        f"compiler provenance passed for {result.consumer}: "
        f"checked={result.checked}, skipped={result.skipped}, prefix={result.prefix}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
