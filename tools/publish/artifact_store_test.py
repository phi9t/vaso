from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_STORE = ROOT / "tools" / "publish" / "artifact_store.py"
BUNDLE_SH = ROOT / "insula" / "rootfs" / "bundle.sh"


FAKE_ORAS = r"""#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path


def die(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def split_ref(ref: str) -> tuple[Path, str | None, str | None]:
    if "@sha256:" in ref:
        path, digest = ref.rsplit("@sha256:", 1)
        return Path(path), None, "sha256:" + digest
    if ":" not in ref:
        die(f"missing tag in ref: {ref}", 2)
    path, tag = ref.rsplit(":", 1)
    return Path(path), tag, None


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def resolve_manifest(layout: Path, tag: str | None, digest: str | None) -> tuple[str, dict]:
    if digest is None:
        index = read_json(layout / "index.json")
        for item in index.get("manifests", []):
            if item.get("annotations", {}).get("org.opencontainers.image.ref.name") == tag:
                digest = item["digest"]
                break
    if digest is None:
        die(f"ref not found: {tag}")
    algorithm, hex_digest = digest.split(":", 1)
    if algorithm != "sha256":
        die(f"unsupported digest: {digest}")
    data = (layout / "blobs" / "sha256" / hex_digest).read_bytes()
    actual = "sha256:" + sha256_bytes(data)
    if actual != digest:
        die(f"digest mismatch for manifest: expected {digest} actual {actual}")
    return digest, json.loads(data.decode("utf-8"))


def cmd_push(args: list[str]) -> int:
    if os.environ.get("FAKE_ORAS_ECHO_TOKEN") == "1":
        print("fake token=" + os.environ.get("GHCR_TOKEN", ""), file=sys.stderr)
        return 42
    if "--oci-layout" not in args:
        die("missing --oci-layout", 2)
    args = [arg for arg in args if arg != "--oci-layout"]
    annotations = {}
    artifact_type = "application/vnd.unknown"
    while args and args[0].startswith("--"):
        flag = args.pop(0)
        if flag == "--artifact-type":
            artifact_type = args.pop(0)
        elif flag == "--annotation":
            key, value = args.pop(0).split("=", 1)
            annotations[key] = value
        else:
            die(f"unknown push flag: {flag}", 2)
    if len(args) < 2:
        die("push requires ref and files", 2)
    layout, tag, _digest = split_ref(args.pop(0))
    layers = []
    blob_dir = layout / "blobs" / "sha256"
    blob_dir.mkdir(parents=True, exist_ok=True)
    for spec in args:
        path_s, media_type = spec.rsplit(":", 1)
        path = Path(path_s)
        data = path.read_bytes()
        digest = sha256_bytes(data)
        (blob_dir / digest).write_bytes(data)
        layers.append(
            {
                "mediaType": media_type,
                "digest": "sha256:" + digest,
                "size": len(data),
                "annotations": {"org.opencontainers.image.title": path_s},
            }
        )
    config = b"{}"
    config_digest = sha256_bytes(config)
    (blob_dir / config_digest).write_bytes(config)
    manifest = {
        "schemaVersion": 2,
        "artifactType": artifact_type,
        "config": {
            "mediaType": "application/vnd.oci.empty.config.v1+json",
            "digest": "sha256:" + config_digest,
            "size": len(config),
        },
        "layers": layers,
        "annotations": annotations,
    }
    manifest_data = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest_digest = sha256_bytes(manifest_data)
    (blob_dir / manifest_digest).write_bytes(manifest_data)
    write_json(layout / "oci-layout", {"imageLayoutVersion": "1.0.0"})
    descriptor = {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "digest": "sha256:" + manifest_digest,
        "size": len(manifest_data),
        "annotations": {"org.opencontainers.image.ref.name": tag},
    }
    index_path = layout / "index.json"
    index = read_json(index_path) if index_path.exists() else {"schemaVersion": 2, "manifests": []}
    index["manifests"] = [
        item
        for item in index.get("manifests", [])
        if item.get("annotations", {}).get("org.opencontainers.image.ref.name") != tag
    ]
    index["manifests"].append(descriptor)
    write_json(index_path, index)
    print("Digest: sha256:" + manifest_digest)
    return 0


def cmd_resolve(args: list[str]) -> int:
    if "--oci-layout" not in args:
        die("missing --oci-layout", 2)
    args = [arg for arg in args if arg != "--oci-layout"]
    digest, _manifest = resolve_manifest(*split_ref(args[0]))
    print(digest)
    return 0


def cmd_manifest(args: list[str]) -> int:
    if args[0] != "fetch":
        die("unsupported manifest command", 2)
    args = args[1:]
    if "--oci-layout" not in args:
        die("missing --oci-layout", 2)
    args = [arg for arg in args if arg != "--oci-layout"]
    _digest, manifest = resolve_manifest(*split_ref(args[0]))
    print(json.dumps(manifest, sort_keys=True))
    return 0


def cmd_pull(args: list[str]) -> int:
    if "--oci-layout" not in args:
        die("missing --oci-layout", 2)
    args = [arg for arg in args if arg != "--oci-layout"]
    out_dir = None
    cleaned = []
    it = iter(args)
    for arg in it:
        if arg in ("-o", "--output"):
            out_dir = Path(next(it))
        else:
            cleaned.append(arg)
    if out_dir is None:
        die("missing output dir", 2)
    _digest, manifest = resolve_manifest(*split_ref(cleaned[0]))
    for layer in manifest.get("layers", []):
        digest = layer["digest"]
        algorithm, hex_digest = digest.split(":", 1)
        if algorithm != "sha256":
            die(f"unsupported digest: {digest}")
        data = (cleaned_path := split_ref(cleaned[0])[0]) / "blobs" / "sha256" / hex_digest
        payload = data.read_bytes()
        actual = "sha256:" + sha256_bytes(payload)
        if actual != digest:
            die(f"digest mismatch for layer: expected {digest} actual {actual}")
        title = layer.get("annotations", {}).get("org.opencontainers.image.title")
        if not title:
            die("layer missing title")
        dst = out_dir / title
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(payload)
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        die("missing command", 2)
    command, args = argv[0], argv[1:]
    if command == "push":
        return cmd_push(args)
    if command == "resolve":
        return cmd_resolve(args)
    if command == "manifest":
        return cmd_manifest(args)
    if command == "pull":
        return cmd_pull(args)
    die(f"unsupported command: {command}", 2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
"""


def write_fake_oras(tmp_path: Path) -> Path:
    path = tmp_path / "bin" / "oras"
    path.parent.mkdir()
    path.write_text(FAKE_ORAS, encoding="utf-8")
    path.chmod(0o755)
    return path


def run_store(
    tmp_path: Path,
    estate: Path,
    oras: Path,
    *args: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "ORAS_BIN": str(oras),
        "TMPDIR": str(estate / "agents" / "test" / "tmp"),
        "VASO_ESTATE_ROOT": str(estate),
        **(extra_env or {}),
    }
    (estate / "agents" / "test" / "tmp").mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        ["python3", str(ARTIFACT_STORE), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def load_json_stdout(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def make_release_dir(tmp_path: Path) -> Path:
    release = tmp_path / "release-dir"
    (release / "wheelhouse").mkdir(parents=True)
    (release / "torch-2.14.0+vaso.821a3581-py3-none-any.whl").write_bytes(b"wheel\n")
    (release / "wheelhouse" / "helper-1.0-py3-none-any.whl").write_bytes(b"helper\n")
    (release / "sbom.json").write_text('{"sbom": true}\n', encoding="utf-8")
    (release / "acceptance.json").write_text(
        '{"commit": "821a3581", "line": "cu129", "profile": "torch"}\n',
        encoding="utf-8",
    )
    (release / "provenance.json").write_text('{"sha": "821a3581"}\n', encoding="utf-8")
    (release / "cut.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profile": "torch",
                "line": "cu129",
                "sha": "821a3581",
                "version": "2.14.0+vaso.821a3581",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return release


def make_rootfs_estate(estate: Path, line: str = "cu129") -> bytes:
    rootfs = estate / "rootfs-lines" / line / "rootfs"
    (rootfs / "etc").mkdir(parents=True)
    (rootfs / "opt" / "payload").mkdir(parents=True)
    payload = bytes(range(256)) * 4
    (rootfs / "etc" / "issue").write_text("vaso fixture\n", encoding="utf-8")
    (rootfs / "opt" / "payload" / "blob.bin").write_bytes(payload)
    (estate / "rootfs-lines" / line / "rootfs-bundle.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "line": line,
                "build_time": "2026-01-02T03:04:05Z",
                "packages": [{"name": "base-files", "version": "1"}],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return payload


def first_manifest_layer_digest(layout: Path) -> str:
    index = json.loads((layout / "index.json").read_text(encoding="utf-8"))
    manifest_digest = index["manifests"][0]["digest"].split(":", 1)[1]
    manifest = json.loads((layout / "blobs" / "sha256" / manifest_digest).read_text(encoding="utf-8"))
    return manifest["layers"][0]["digest"].split(":", 1)[1]


def test_release_round_trip_uses_local_oci_layout_and_verifies_file_sha256(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    oras = write_fake_oras(tmp_path)
    release = make_release_dir(tmp_path)
    registry = estate / "agents" / "fc-pub2" / "oci"

    pushed = load_json_stdout(
        run_store(
            tmp_path,
            estate,
            oras,
            "push-release",
            "--release-dir",
            str(release),
            "--line",
            "cu129",
            "--profile",
            "torch",
            "--dry-run",
            "--registry",
            str(registry),
            "--json",
        )
    )

    assert pushed["kind"] == "release"
    assert pushed["package"] == "vaso-release-torch-cu129"
    assert pushed["tag"] == "821a3581"
    assert pushed["digest"].startswith("sha256:")
    assert pushed["files"]["wheelhouse/helper-1.0-py3-none-any.whl"]
    layout = registry / "vaso-release-torch-cu129"
    assert (layout / "oci-layout").is_file()

    pulled = tmp_path / "pulled"
    result = run_store(
        tmp_path,
        estate,
        oras,
        "pull",
        "--artifact",
        "release",
        "--line",
        "cu129",
        "--profile",
        "torch",
        "--digest",
        str(pushed["digest"]),
        "--target-dir",
        str(pulled),
        "--dry-run",
        "--registry",
        str(registry),
        "--json",
    )
    pulled_doc = load_json_stdout(result)
    assert pulled_doc["verified_files"] == len(pushed["files"])
    assert (pulled / "wheelhouse" / "helper-1.0-py3-none-any.whl").read_bytes() == b"helper\n"
    assert (pulled / "acceptance.json").read_text(encoding="utf-8").startswith('{"commit"')


def test_rootfs_push_splits_bundle_layers_and_pull_reassembles_verified_bundle(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    oras = write_fake_oras(tmp_path)
    payload = make_rootfs_estate(estate)
    registry = estate / "agents" / "fc-pub2" / "oci"

    pushed = load_json_stdout(
        run_store(
            tmp_path,
            estate,
            oras,
            "push-rootfs",
            "--line",
            "cu129",
            "--dry-run",
            "--registry",
            str(registry),
            "--max-layer-bytes",
            "160",
            "--json",
        )
    )

    assert pushed["kind"] == "rootfs"
    assert pushed["package"] == "vaso-rootfs-cu129"
    assert pushed["tag"] == pushed["archive_sha256"][:12]
    assert pushed["split_parts"] > 1
    part_sizes = [
        item["size"]
        for item in pushed["layers"]
        if str(item["path"]).endswith(".part0000") or ".part" in str(item["path"])
    ]
    assert part_sizes and max(part_sizes) <= 160

    pulled = tmp_path / "pulled-rootfs"
    pulled_doc = load_json_stdout(
        run_store(
            tmp_path,
            estate,
            oras,
            "pull",
            "--artifact",
            "rootfs",
            "--line",
            "cu129",
            "--tag",
            str(pushed["tag"]),
            "--target-dir",
            str(pulled),
            "--dry-run",
            "--registry",
            str(registry),
            "--json",
        )
    )

    assert pulled_doc["verified_files"] == len(pushed["files"])
    archive = pulled / str(pushed["archive"])
    sidecar = pulled / "rootfs-cu129.json"
    assert archive.is_file()
    assert sidecar.is_file()
    extract = tmp_path / "extract"
    unbundle = subprocess.run(
        [
            "bash",
            str(BUNDLE_SH),
            "unbundle",
            "--bundle",
            str(archive),
            "--dest",
            str(extract),
        ],
        cwd=ROOT,
        env={**os.environ, "TMPDIR": str(estate / "agents" / "test" / "tmp")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert unbundle.returncode == 0, unbundle.stderr
    assert (extract / "rootfs" / "opt" / "payload" / "blob.bin").read_bytes() == payload


def test_pull_refuses_a_tampered_oci_layer_before_accepting_files(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    oras = write_fake_oras(tmp_path)
    release = make_release_dir(tmp_path)
    registry = estate / "agents" / "fc-pub2" / "oci"
    pushed = load_json_stdout(
        run_store(
            tmp_path,
            estate,
            oras,
            "push-release",
            "--release-dir",
            str(release),
            "--line",
            "cu129",
            "--profile",
            "torch",
            "--dry-run",
            "--registry",
            str(registry),
            "--json",
        )
    )
    layout = registry / "vaso-release-torch-cu129"
    layer_digest = first_manifest_layer_digest(layout)
    blob = layout / "blobs" / "sha256" / layer_digest
    blob.write_bytes(blob.read_bytes() + b"tampered")

    result = run_store(
        tmp_path,
        estate,
        oras,
        "pull",
        "--artifact",
        "release",
        "--line",
        "cu129",
        "--profile",
        "torch",
        "--tag",
        str(pushed["tag"]),
        "--target-dir",
        str(tmp_path / "pulled"),
        "--dry-run",
        "--registry",
        str(registry),
        "--json",
    )

    assert result.returncode != 0
    assert "digest mismatch" in (result.stderr + result.stdout)


def test_registry_token_is_not_passed_to_oras_or_logged(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    oras = write_fake_oras(tmp_path)
    release = make_release_dir(tmp_path)
    secret = "ghp_pub2_secret_token"
    result = run_store(
        tmp_path,
        estate,
        oras,
        "push-release",
        "--release-dir",
        str(release),
        "--line",
        "cu129",
        "--profile",
        "torch",
        "--dry-run",
        "--registry",
        str(estate / "agents" / "fc-pub2" / "oci"),
        "--json",
        extra_env={"GHCR_TOKEN": secret, "FAKE_ORAS_ECHO_TOKEN": "1"},
    )

    assert result.returncode != 0
    assert secret not in result.stdout
    assert secret not in result.stderr
    assert "fake token=" in result.stderr
    assert "ghp_" not in result.stdout + result.stderr


def test_prebuilt_rootfs_publishes_without_access_to_original_estate_tree(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    make_rootfs_estate(estate)
    oras = write_fake_oras(tmp_path)
    prebuilt = estate / "prebuilt"
    built = subprocess.run(
        ["bash", str(BUNDLE_SH), "--line", "cu129", "--estate-root", str(estate), "--out-dir", str(prebuilt)],
        env={**os.environ, "TMPDIR": str(tmp_path)}, capture_output=True, text=True,
    )
    assert built.returncode == 0, built.stderr
    archive = Path(built.stdout.strip().splitlines()[-1])
    shutil.copy2(estate / "rootfs-lines" / "cu129" / "rootfs-bundle.json", prebuilt / "rootfs-bundle.json")
    shutil.rmtree(estate / "rootfs-lines")
    pushed = load_json_stdout(run_store(
        tmp_path, estate, oras, "push-rootfs", "--line", "cu129", "--bundle", str(archive),
        "--dry-run", "--registry", str(estate / "oci"), "--json",
    ))
    sidecar = json.loads((prebuilt / "rootfs-cu129.json").read_text())
    assert pushed["archive_sha256"] == sidecar["digest"]
    assert pushed["archive"] == archive.name
    sidecar["line"] = "cu130"
    (prebuilt / "rootfs-cu129.json").write_text(json.dumps(sidecar))
    refused = run_store(
        tmp_path, estate, oras, "push-rootfs", "--line", "cu129", "--bundle", str(archive),
        "--dry-run", "--registry", str(estate / "oci"), "--json",
    )
    assert refused.returncode != 0
    assert "sidecar line does not match" in refused.stderr
