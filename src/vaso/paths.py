from __future__ import annotations

from vaso.config import VasoConfig


HOST_LAYOUT_DIRS = (
    "rootfs",
    "home/kvothe",
    "state",
    "cache",
    "runs",
    "traces",
    "tmp",
)


def ensure_host_layout(config: VasoConfig) -> None:
    for rel in HOST_LAYOUT_DIRS:
        (config.state_root / rel).mkdir(parents=True, exist_ok=True)
