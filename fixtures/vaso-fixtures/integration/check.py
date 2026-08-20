from __future__ import annotations

import subprocess
from pathlib import Path


def run(path: str) -> str:
    return subprocess.check_output([path], text=True).strip()


def main() -> None:
    root = Path.cwd()
    cpp = run(str(root / "cpp" / "hello_cpp"))
    py = run(str(root / "python" / "hello_python"))
    if cpp != "vaso-cpp-ok" or py != "vaso-python-ok":
        raise SystemExit(f"unexpected outputs: {cpp!r} {py!r}")
    print("vaso-integration-ok")


if __name__ == "__main__":
    main()
