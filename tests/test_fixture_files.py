from pathlib import Path


FIXTURE = Path("fixtures/vaso-fixtures")


def test_fixture_workspace_files_exist():
    for rel in [
        "MODULE.bazel",
        "cpp/BUILD.bazel",
        "cpp/hello.cc",
        "python/BUILD.bazel",
        "python/hello.py",
        "integration/BUILD.bazel",
        "integration/check.py",
    ]:
        assert (FIXTURE / rel).is_file()


def test_fixture_expected_outputs_are_stable():
    assert "vaso-cpp-ok" in (FIXTURE / "cpp/hello.cc").read_text()
    assert "vaso-python-ok" in (FIXTURE / "python/hello.py").read_text()
