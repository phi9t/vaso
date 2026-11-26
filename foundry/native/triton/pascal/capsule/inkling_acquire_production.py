#!/usr/bin/env python3
"""Acquire pinned public production Inkling assets inside a networked Insula capsule.

Only the model shard, its index/configuration, and tokenizer/processor assets
are fetched. Streaming .part files resume with HTTP Range; completed downloads
are admitted by exact byte count and pinned SHA256 for every asset.
The acquisition manifest records bounded retry outcomes and durable progress.
No credentials are read or supplied, and redirect/signed URLs are never logged.
Run under the capsule payload owner with a timeout longer than --max-seconds.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


REPO = "thinkingmachines/Inkling-2-Mini"
REVISION = "c9d028302cd0f035fdf8e7e91f089a4ce94d2195"
FILES = {
    "config.json": (2722, "a7a349601f0984c4abf999c480ef775d8567d21f5f84b4e8a3e035b44f288dc2"),
    "generation_config.json": (98, "a08737c07a65a8fdf69f7cf086576210e5aad4b241437090b26d7b0c4f7905f7"),
    "hf_quant_config.json": (67924, "7c656207d3ce37879c7be209a036b0dde43bf01f78159bff06880b868ed57143"),
    "model.safetensors.index.json": (109300, "c1631065b7b00787993522dddc66a68b3abee869192977fb3dcb796e63bc5838"),
    "processor_config.json": (461, "d7350adce18c8be4b607f261c8f0f7eabbf720bb7aaf648a43d4f8372bbacc2d"),
    "special_tokens_map.json": (529, "c16f44b3594563d82743ddb0417a2be1bfe3e2000dfad2da4b8f32554593e1a0"),
    "tokenizer_config.json": (13235, "bf09342d285139fd37ce10c70878af1d52cb390e9901e10248654293a80ee636"),
    "chat_template.jinja": (6868, "53a9035f3255945704b5857657cfb00c1165090c0641c4f782a0cbf268cc16dc"),
    "tokenizer.json": (26178678, "7f332727f9a3f093dd452a39cc978e6757627e5dad1b0673aa4e75174f236dd6"),
    "model-00001-of-00001.safetensors": (19526019168, "65e60f51aa1284d4fb022584908c315270334d991828bb774af60584523539d1"),
}
# Small-file digests are pinned from two independent acquisitions of REVISION.
# The two large LFS assets retain their published object SHA256.
LFS_ASSETS = {"tokenizer.json", "model-00001-of-00001.safetensors"}
TOTAL_BYTES = sum(size for size, _ in FILES.values())


class AcquisitionError(Exception):
    """A safe-to-log acquisition failure code, with no network URL attached."""


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def safe_error(exc):
    result = {"type": type(exc).__name__}
    if isinstance(exc, AcquisitionError):
        result["code"] = str(exc)
    if isinstance(exc, urllib.error.HTTPError):
        result["http_status"] = exc.code
    if isinstance(exc, OSError) and exc.errno is not None:
        result["errno"] = exc.errno
    if isinstance(exc, urllib.error.URLError):
        result["reason_type"] = type(exc.reason).__name__
    return result


def write_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def acquire(args):
    destination = args.output
    destination.mkdir(parents=True, exist_ok=True)
    lock = (destination / ".acquisition.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    manifest_path = destination / "acquisition.json"
    prior = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    if prior and (prior.get("repo"), prior.get("revision")) != (REPO, REVISION):
        raise AcquisitionError("destination_belongs_to_different_source")
    # A manifest is a historical receipt, not proof of present bytes.
    done = {}
    state = {"schema_version": 1, "repo": REPO, "revision": REVISION,
             "purpose": "published production checkpoint", "trained": True,
             "expected_total_bytes": TOTAL_BYTES, "complete": False,
             "started_utc": utc_now(), "files": list(done.values()),
             "attempt_events": list(prior.get("attempt_events", [])),
             "previous_complete": prior.get("complete", False)}
    started = time.monotonic()
    deadline = started + args.max_seconds
    last_progress = 0.0

    def budget():
        if time.monotonic() >= deadline:
            raise AcquisitionError("time_budget_exhausted_resume_same_destination")

    def save(event, name=None, received=None, *, emit=True):
        nonlocal last_progress
        now = time.monotonic()
        state.update(state=event, current_file=name, current_file_bytes=received,
                     completed_bytes=sum(item["bytes"] for item in done.values()),
                     files=list(done.values()), updated_utc=utc_now(),
                     duration_seconds=now - started)
        write_json(manifest_path, state)
        last_progress = now
        if emit:
            print(json.dumps({"event": event, "file": name, "file_bytes": received,
                              "completed_bytes": state["completed_bytes"],
                              "expected_total_bytes": TOTAL_BYTES}), flush=True)

    def hash_partial(path, hasher, name):
        size = 0
        save("rehashing_partial", name, 0)
        with path.open("rb") as handle:
            while chunk := handle.read(8 * 1024 * 1024):
                budget()
                hasher.update(chunk)
                size += len(chunk)
                if time.monotonic() - last_progress >= args.progress_seconds:
                    save("rehashing_partial", name, size)
        return size

    def finish(name, part, final, expected_size, expected_sha, hasher):
        if part.stat().st_size != expected_size:
            raise AcquisitionError("partial_size_mismatch")
        digest = hasher.hexdigest()
        if expected_sha is not None and digest != expected_sha:
            quarantine(name, part, "partial_sha256_mismatch")
            raise AcquisitionError("sha256_mismatch_partial_quarantined_retry")
        with part.open("rb") as handle:
            os.fsync(handle.fileno())
        part.replace(final)
        done[name] = {"name": name, "bytes": expected_size, "sha256": digest,
                      "sha256_matches_published_lfs": name in LFS_ASSETS,
                      "sha256_matches_pinned": True}
        save("downloaded", name, expected_size)

    def quarantine(name, path, reason):
        # Preserve suspect bytes without leaving them eligible for resumption.
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix=name + ".corrupt-", dir=destination))
        path.replace(folder / path.name)
        state["attempt_events"].append({"file": name, "event": reason,
                                        "quarantine": folder.name, "utc": utc_now()})
        save("quarantined", name, 0)

    try:
        save("starting")
        remaining = 0
        for name, (expected_size, expected_sha) in FILES.items():
            budget()
            if expected_sha is None:
                raise AcquisitionError("missing_pinned_sha256")
            final, part = destination / name, destination / (name + ".part")
            if final.is_symlink() or part.is_symlink():
                raise AcquisitionError("symlink_asset_rejected")
            if final.exists():
                if not final.is_file():
                    raise AcquisitionError("nonregular_final_asset")
                if final.stat().st_size == expected_size:
                    hasher = hashlib.sha256()
                    count = hash_partial(final, hasher, name)
                    if count == expected_size and hasher.hexdigest() == expected_sha:
                        done[name] = {"name": name, "bytes": expected_size, "sha256": expected_sha,
                                      "sha256_matches_published_lfs": name in LFS_ASSETS,
                                      "sha256_matches_pinned": True}
                        save("verified_existing", name, count)
                        continue
                quarantine(name, final, "existing_asset_integrity_mismatch")
            if part.exists():
                if not part.is_file():
                    raise AcquisitionError("nonregular_partial_asset")
                size = part.stat().st_size
                if size > expected_size:
                    quarantine(name, part, "oversize_partial")
                elif size == expected_size:
                    hasher = hashlib.sha256()
                    count = hash_partial(part, hasher, name)
                    if count == expected_size and hasher.hexdigest() == expected_sha:
                        finish(name, part, final, expected_size, expected_sha, hasher)
                        continue
                    quarantine(name, part, "partial_sha256_mismatch")
            existing = part.stat().st_size if part.is_file() else 0
            remaining += expected_size - existing
        if remaining and shutil.disk_usage(destination).free < remaining + 64 * 1024 * 1024:
            raise AcquisitionError("insufficient_disk_space")
        for name, (expected_size, expected_sha) in FILES.items():
            budget()
            final, part = destination / name, destination / (name + ".part")
            if name in done:
                save("reused_verified", name, expected_size)
                continue
            if final.exists():
                raise AcquisitionError("unexpected_final_asset_after_preflight")
            offset = part.stat().st_size if part.exists() else 0
            if offset > expected_size:
                raise AcquisitionError("partial_exceeds_expected_size")
            hasher = hashlib.sha256()
            if offset:
                offset = hash_partial(part, hasher, name)
            failures = 0
            while offset < expected_size:
                budget()
                headers = {"User-Agent": "vaso-insula-inkling-acquisition", "Accept-Encoding": "identity"}
                if offset:
                    headers["Range"] = f"bytes={offset}-"
                url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{name}?download=true"
                request = urllib.request.Request(url, headers=headers)
                try:
                    save("downloading", name, offset)
                    with urllib.request.urlopen(request, timeout=args.socket_timeout) as response:
                        status = response.status
                        content_range = response.headers.get("Content-Range", "")
                        if status == 206:
                            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
                            if (not match or int(match[1]) != offset or int(match[3]) != expected_size
                                    or int(match[2]) >= expected_size or int(match[2]) < offset):
                                raise AcquisitionError("invalid_content_range")
                            response_end = int(match[2]) + 1
                        elif status == 200:
                            response_end = expected_size
                            if offset:
                                # A server may ignore Range. Restart this owned partial
                                # explicitly instead of appending duplicate bytes.
                                state["attempt_events"].append({"file": name, "event": "range_ignored_restart",
                                                                 "previous_bytes": offset, "utc": utc_now()})
                                offset, hasher = 0, hashlib.sha256()
                        else:
                            raise AcquisitionError("unexpected_success_http_status")
                        with part.open("ab" if offset else "wb") as handle:
                            while chunk := response.read(1024 * 1024):
                                budget()
                                if offset + len(chunk) > response_end:
                                    raise AcquisitionError("response_exceeds_declared_size")
                                handle.write(chunk)
                                hasher.update(chunk)
                                offset += len(chunk)
                                if time.monotonic() - last_progress >= args.progress_seconds:
                                    handle.flush()
                                    save("downloading", name, offset)
                            handle.flush()
                        if offset != response_end:
                            raise AcquisitionError("short_response_resume_available")
                    # A valid partial range can be shorter than the remaining file.
                    # Continue with its next byte without spending a failure retry.
                except AcquisitionError as exc:
                    if str(exc) != "short_response_resume_available":
                        raise
                    failures += 1
                    state["attempt_events"].append({"file": name, "attempt_failure": failures,
                                                     "bytes": offset, "error": safe_error(exc), "utc": utc_now()})
                    save("retrying", name, offset)
                    if failures >= args.retries:
                        raise AcquisitionError("retry_budget_exhausted_resume_same_destination") from None
                    time.sleep(min(2 ** failures, 16))
                except (OSError, urllib.error.URLError, http.client.IncompleteRead) as exc:
                    failures += 1
                    state["attempt_events"].append({"file": name, "attempt_failure": failures,
                                                     "bytes": offset, "error": safe_error(exc), "utc": utc_now()})
                    save("retrying", name, offset)
                    if isinstance(exc, OSError) and exc.errno in (5, 13, 28, 30, 122):
                        raise AcquisitionError("filesystem_write_failed_partial_preserved") from None
                    actual_size = part.stat().st_size if part.exists() else 0
                    if actual_size != offset:
                        hasher = hashlib.sha256()
                        offset = hash_partial(part, hasher, name) if part.exists() else 0
                    # Stop at a repository access wall. Refreshing an expired CDN
                    # redirect on a subsequent retry still uses the original public URL.
                    denied_at_hub = (isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403)
                                     and urllib.parse.urlparse(exc.url).hostname == "huggingface.co")
                    if denied_at_hub:
                        raise AcquisitionError("repository_access_denied_no_credentials_supplied") from None
                    if failures >= args.retries:
                        raise AcquisitionError("retry_budget_exhausted_resume_same_destination") from None
                    time.sleep(min(2 ** failures, 16))
            finish(name, part, final, expected_size, expected_sha, hasher)
        state["complete"] = True
        save("complete")
        return 0
    except Exception as exc:
        state["error"] = safe_error(exc)
        save("failed", state.get("current_file"), state.get("current_file_bytes"))
        print(json.dumps({"event": "acquisition_failed", "error": state["error"]}), flush=True)
        return 1
    finally:
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-seconds", type=int, default=7000)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--socket-timeout", type=int, default=60)
    parser.add_argument("--progress-seconds", type=int, default=10)
    args = parser.parse_args()
    if min(args.max_seconds, args.retries, args.socket_timeout, args.progress_seconds) <= 0:
        parser.error("Acquisition budgets must be positive")
    try:
        return acquire(args)
    except Exception as exc:
        # Do not print exception chains containing CDN redirect URLs.
        print(json.dumps({"event": "acquisition_admission_failed", "error": safe_error(exc)}), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
