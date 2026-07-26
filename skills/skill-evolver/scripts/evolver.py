#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from typing import BinaryIO, Optional, Sequence

VERSION = "skill-evolver feasibility 0.0.1"
MAX_STDIN_BYTES = 65_536
REQUIRED_HOOK_FIELDS = {"hook_event_name": str, "session_id": str, "turn_id": str, "cwd": str}


def read_bounded_stdin(stream: BinaryIO, limit: int = MAX_STDIN_BYTES) -> bytes:
    raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("hook_input_too_large")
    return raw


def summarize_hook_shape(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("hook_payload_not_object")
    field_types = {key: type(value).__name__ for key, value in sorted(payload.items())}
    required = {
        key: {
            "present": key in payload,
            "type": field_types.get(key),
            "valid": isinstance(payload.get(key), expected)
            and (not isinstance(payload.get(key), str) or bool(payload[key]))
            and (key != "hook_event_name" or payload.get(key) == "Stop"),
        }
        for key, expected in REQUIRED_HOOK_FIELDS.items()
    }
    transcript = payload.get("transcript_path")
    required["transcript_path"] = {"present": "transcript_path" in payload, "type": type(transcript).__name__, "valid": isinstance(transcript, str) and bool(transcript)}
    return {"payload_keys": sorted(payload), "field_types": field_types, "required_fields": required}


def cmd_probe_stop(_args: argparse.Namespace) -> int:
    try:
        raw = read_bounded_stdin(sys.stdin.buffer)
        summarize_hook_shape(json.loads(raw))
    except Exception:
        pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evolver.py")
    parser.add_argument("--version", action="version", version=VERSION)
    subparsers = parser.add_subparsers(dest="command", required=True)
    probe_stop = subparsers.add_parser("probe-stop")
    probe_stop.add_argument("--installation", required=True)
    probe_stop.set_defaults(handler=cmd_probe_stop)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
