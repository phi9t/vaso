#!/usr/bin/env python3
"""Unit tests for the hermetic PyTorch extension builder."""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extension_builder


class ExtensionBuilderTest(unittest.TestCase):
    def test_final_link_includes_device_link_archives_after_objects(self) -> None:
        args = argparse.Namespace(
            cxx="c++",
            torch_prefix=Path("torch"),
            cuda_prefix=Path("cuda"),
            nccl_prefix=Path("nccl"),
            nvshmem_prefix=Path("nvshmem"),
            device_link_file=[Path("nvshmem/lib/libnvshmem_device.a")],
            link_file=[Path("vendor/libextra.a")],
            link_flag=["-lcudadevrt"],
        )
        objects = [Path("work/objects/cu_0.o"), Path("work/objects/cuda_device_link.o")]
        commands: list[list[str]] = []

        with mock.patch.object(extension_builder, "_run", commands.append):
            extension_builder._link_extension(args, objects, Path("work/_C.so"))

        self.assertEqual(len(commands), 1)
        command = commands[0]
        device_archive = "nvshmem/lib/libnvshmem_device.a"
        self.assertIn(device_archive, command)
        self.assertLess(command.index("work/objects/cuda_device_link.o"), command.index(device_archive))
        self.assertLess(command.index(device_archive), command.index("vendor/libextra.a"))


if __name__ == "__main__":
    unittest.main()
