"""trae_ctl: importable without broker state; rollout-tail turn-id parse."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAE_BROKER_DIR", str(tmp_path))
    sys.path.insert(0, str(ROOT / "scripts" / "agents"))
    spec = importlib.util.spec_from_file_location("trae_ctl", ROOT / "scripts/agents/trae_ctl.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_imports_without_broker_state(tmp_path, monkeypatch):
    mod = load(tmp_path, monkeypatch)
    assert mod.THREAD == "" and mod.HERE == tmp_path


def test_last_turn_id_takes_the_latest(tmp_path, monkeypatch):
    mod = load(tmp_path, monkeypatch)
    a = "01a0f3c6-9f7c-7bb1-ad95-299bd4d777d8"
    b = "46e13558-4baf-44f5-84be-637f8f37b1eb"
    tail = '{"turn_id":"%s"}\n{"x":1}\n{"turn_id":"%s"}\n' % (a, b)
    assert mod.last_turn_id(tail) == b
    assert mod.last_turn_id('{"no":"turn"}') is None
