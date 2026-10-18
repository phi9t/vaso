"""Helpers shared by release-venv Python consumer verifiers."""

from __future__ import annotations

import os
from pathlib import Path
from types import ModuleType


def assert_shipped_wheel(module: ModuleType, distribution: str) -> Path:
    venv = Path(os.environ["VASO_RELEASE_VENV"]).resolve()
    module_file = Path(getattr(module, "__file__", "")).resolve()
    try:
        module_file.relative_to(venv)
    except ValueError as exc:
        raise AssertionError(
            f"{distribution} was not imported from the release venv: {module_file} outside {venv}"
        ) from exc

    release_dir = Path(os.environ["VASO_RELEASE_DIR"]).resolve()
    wheels = sorted(release_dir.glob(f"**/{distribution.replace('_', '-')}-*.whl"))
    if not wheels and distribution == "jax":
        wheels = sorted(release_dir.glob("**/jax-*.whl"))
    if not wheels:
        raise AssertionError(f"release dir has no wheel for {distribution}: {release_dir}")
    return module_file
