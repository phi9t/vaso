#!/usr/bin/env python3
from __future__ import annotations

import importlib.metadata
import sys

from google.protobuf.internal import api_implementation

import roundtrip_pb2


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: read_message.py <message.pb>", file=sys.stderr)
        return 2

    msg = roundtrip_pb2.RoundTrip()
    with open(argv[1], "rb") as f:
        msg.ParseFromString(f.read())

    if (
        msg.label != "native-protobuf"
        or list(msg.values) != [3, 21, 12]
        or msg.nested.name != "cpp-writer"
        or msg.nested.score != 42
    ):
        print(f"unexpected roundtrip payload: {msg!r}", file=sys.stderr)
        return 1
    if api_implementation.Type() != "cpp":
        print(f"unexpected protobuf implementation: {api_implementation.Type()}", file=sys.stderr)
        return 1

    print(f"protobuf-roundtrip:3.21.12:{importlib.metadata.version('protobuf')}:ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
