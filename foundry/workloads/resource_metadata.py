"""Inspect workload resource declarations without importing executable workloads.

WORKLOADS in run_workloads.py remains the sole declaration. This passive reader
accepts its literal dataclass declaration shape and fails closed on other forms.
"""

from __future__ import annotations

import ast
from pathlib import Path


def minimum_gpus(path: Path, source: bytes | None = None) -> dict[str, int]:
    try:
        tree = ast.parse(source if source is not None else path.read_bytes())
        declarations = [
            node.value for node in tree.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name) and node.target.id == "WORKLOADS"
        ]
        if len(declarations) != 1 or not isinstance(declarations[0], ast.Dict):
            raise ValueError("expected one literal WORKLOADS dictionary")
        result = {}
        for key, value in zip(declarations[0].keys, declarations[0].values):
            name = ast.literal_eval(key)
            if (not isinstance(name, str) or name in result
                or not isinstance(value, ast.Call)
                or not isinstance(value.func, ast.Name) or value.func.id != "Workload"
                or len(value.args) != 4 or value.keywords
                or ast.literal_eval(value.args[0]) != name):
                raise ValueError("invalid Workload declaration")
            minimum = ast.literal_eval(value.args[2])
            if type(minimum) is not int or minimum < 1:
                raise ValueError("invalid minimum GPU count")
            result[name] = minimum
        if not result:
            raise ValueError("empty WORKLOADS dictionary")
        return result
    except (OSError, SyntaxError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid Foundry workload resource declarations in {path}: {exc}") from exc
