from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
PUBLIC_FILES = ("README.md", "LICENSE")


def test_public_root_files_are_tracked():
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--error-unmatch", "--", *PUBLIC_FILES],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert set(result.stdout.splitlines()) == set(PUBLIC_FILES)


def test_public_root_files_are_non_empty():
    for rel in PUBLIC_FILES:
        path = ROOT / rel
        assert path.is_file()
        assert path.read_bytes().strip()
