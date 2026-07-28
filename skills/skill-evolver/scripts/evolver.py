#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import fnmatch
import hashlib
import json
import os
import secrets
import stat
import sys
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator, Optional, Sequence, TextIO

ORIGINAL_FSTAT = os.fstat

VERSION = "skill-evolver feasibility 0.0.2"
MAX_STDIN_BYTES = 65_536
REQUIRED_HOOK_FIELDS = {"hook_event_name": str, "session_id": str, "turn_id": str, "cwd": str}
REQUIRED_SESSION_HOOK_FIELDS = {
    "hook_event_name": str,
    "session_id": str,
    "cwd": str,
}
SESSION_STOP_EVENT_FIELDS = frozenset(
    {"hook_event_name", "session_id", "turn_id", "transcript_path", "cwd"}
)
INSTALLATION_SCHEMA = 1
PROVENANCE_KEYS = {"role", "source_kind"}
PROVENANCE_VALUES = {
    "assistant",
    "external_content",
    "tool",
    "tool_output",
    "user",
    "user_direct",
}
POINTER_SEGMENT_ALLOWLIST = {
    "internal_chat_message_metadata_passthrough",
    "payload",
    "role",
    "session_id",
    "source_kind",
    "turn_id",
}
MAX_TRANSCRIPT_PROBE_BYTES = 2_097_152
MAX_TRANSCRIPT_LOOKUP_ENTRIES = 4096
MAX_GATE_FIXTURE_BYTES = 65_536
SCRUB_CONFIRMATION = "DELETE-FEASIBILITY-RAW"
V2_STATE_LOCK = ".v2-state.lock"
V2_SCRUB_MARKER = ".v2-scrubbed.json"
REVIEWED_STOP_PAYLOAD_KEYS = frozenset(
    {
        "cwd",
        "hook_event_name",
        "last_assistant_message",
        "model",
        "permission_mode",
        "session_id",
        "stop_hook_active",
        "transcript_path",
        "turn_id",
    }
)
HOOK_FIELD_TYPE_NAMES = {
    "NoneType",
    "bool",
    "dict",
    "float",
    "int",
    "list",
    "str",
}
GATE_FIXTURE_NAMES = (
    "stop-cli.structure.json",
    "stop-desktop.structure.json",
    "transcript-cli.structure.json",
    "transcript-desktop.structure.json",
    "access-cli.structure.json",
    "access-desktop.structure.json",
)
GATE_V2_FIXTURE_NAMES = (
    "session-stop-cli.v2.structure.json",
    "session-stop-desktop.v2.structure.json",
    "session-transcript-cli.v2.structure.json",
    "session-transcript-desktop.v2.structure.json",
    "access-cli.v2.structure.json",
    "access-desktop.v2.structure.json",
)
V2_SESSION_STOP_KEYS = frozenset(
    {"cwd", "hook_event_name", "session_id", "transcript_path"}
)
V2_TRANSCRIPT_BINDING_MODES = frozenset(
    {"same_file_identity", "same_inode_lookup", "embedded_session_id"}
)
V2_PREDECESSOR_PATH = "docs/feasibility-report.json"
V2_PREDECESSOR_SHA256 = "ced4503adb44bd041de063c04e0c6c64d0831370fc12e96a920fe97244d8ae15"
GATE_V2_JSON_NAME = "feasibility-report-v2.json"
GATE_V2_MARKDOWN_NAME = "feasibility-report-v2.md"
GATE_V2_TRANSACTION_NAME = ".feasibility-report-v2.transaction.json"
V1_PREDECESSOR_CHECKS = {
    "cli_shared_data_root": True,
    "cli_skill_data_root": False,
    "cli_stop_contract": True,
    "cli_transcript_supported": False,
    "desktop_shared_data_root": True,
    "desktop_skill_data_root": False,
    "desktop_stop_contract": True,
    "desktop_transcript_supported": False,
}


@dataclass(frozen=True)
class Installation:
    data_root: Path
    transcript_roots: tuple[Path, ...]
    python: Path
    nonce: str


@dataclass(frozen=True)
class StopEnvelope:
    session_id: str
    turn_id: str
    transcript_path: Path
    cwd: Path
    shape: dict[str, object]


@dataclass(frozen=True)
class SessionStopEnvelope:
    session_id: str
    turn_id: Optional[str]
    transcript_path: Path
    cwd: Path
    shape: dict[str, object]


@dataclass(frozen=True)
class ResolvedSessionTranscript:
    path: Path
    binding_mode: str
    epoch_reset: bool


def pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def pointer_segment(key: object, ordinal: int) -> str:
    value = str(key)
    return (
        pointer_escape(value)
        if value in POINTER_SEGMENT_ALLOWLIST
        else f"_redacted_{ordinal}"
    )


def _walk_scalar_entries(
    value: object,
    pointer: str = "",
    identity: tuple[object, ...] = (),
) -> Iterator[tuple[str, tuple[object, ...], object]]:
    if isinstance(value, dict):
        for ordinal, key in enumerate(sorted(value)):
            child = f"{pointer}/{pointer_segment(key, ordinal)}"
            yield from _walk_scalar_entries(
                value[key], child, identity + (str(key),)
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_scalar_entries(
                item, f"{pointer}/{index}", identity + (index,)
            )
    else:
        yield pointer or "/", identity, value


def walk_scalars(value: object, pointer: str = "") -> Iterator[tuple[str, object]]:
    for public_pointer, _identity, scalar in _walk_scalar_entries(value, pointer):
        yield public_pointer, scalar


def read_exact_prefix(descriptor: int, size: int) -> bytes:
    if size < 0:
        raise ValueError("negative_captured_size")
    if size > MAX_TRANSCRIPT_PROBE_BYTES:
        raise ValueError("oversized_transcript")
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = os.read(descriptor, min(65_536, remaining))
        if not chunk:
            raise ValueError("transcript_changed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def ensure_private_regular(path: Path) -> None:
    descriptor = os.open(
        str(path),
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        info = ORIGINAL_FSTAT(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ValueError("v2_lifecycle_lock_unavailable")
    finally:
        os.close(descriptor)


def ensure_v2_lifecycle_files(root: Path) -> None:
    ensure_private_regular(root / V2_STATE_LOCK)


@contextmanager
def v2_state_lock(
    installation: Installation,
    *,
    exclusive: bool,
    allow_scrubbed: bool = False,
) -> Iterator[int]:
    root_descriptor = os.open(
        str(installation.data_root),
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        descriptor = os.open(
            V2_STATE_LOCK,
            (os.O_RDWR if exclusive else os.O_RDONLY) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root_descriptor,
        )
    except BaseException:
        os.close(root_descriptor)
        raise
    locked = False
    try:
        root_info = ORIGINAL_FSTAT(root_descriptor)
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or root_info.st_uid != os.getuid()
            or stat.S_IMODE(root_info.st_mode) != 0o700
        ):
            raise ValueError("data_root_permissions")
        info = ORIGINAL_FSTAT(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ValueError("v2_lifecycle_lock_unavailable")
        fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        locked = True
        try:
            marker = os.stat(
                V2_SCRUB_MARKER, dir_fd=root_descriptor, follow_symlinks=False
            )
        except FileNotFoundError:
            marker = None
        if not allow_scrubbed and marker is not None:
            raise ValueError("v2_probe_scrubbed")
        yield root_descriptor
    finally:
        try:
            if locked:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
            os.close(root_descriptor)


def atomic_write_json(path: Path, payload: dict[str, object], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, mode)
        fsync_directory(path.parent)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def validate_private_directory(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("data_root_symlink")
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("data_root_not_directory")
    if info.st_uid != os.getuid():
        raise ValueError("data_root_owner")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("data_root_permissions")
    return resolved


def validate_private_child_directory(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("data_child_symlink")
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ValueError("data_child_permissions")
    return resolved


def validate_private_nonce(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("nonce_symlink")
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ValueError("nonce_permissions")
    return resolved


def validate_transcript_root(path: Path) -> Path:
    requested = path.expanduser()
    if requested.is_symlink():
        raise ValueError("transcript_root_symlink")
    try:
        resolved = requested.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError):
        raise ValueError("transcript_root_not_directory") from None
    info = resolved.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("transcript_root_not_directory")
    if info.st_uid != os.getuid():
        raise ValueError("transcript_root_owner")
    if stat.S_IMODE(info.st_mode) & stat.S_IWOTH:
        raise ValueError("transcript_root_permissions")
    return resolved


def path_identity(path: Path) -> str:
    normalized = unicodedata.normalize(
        "NFC",
        os.path.normpath(str(path)),
    )
    return unicodedata.normalize("NFC", normalized.casefold())


def validate_transcript_separation(
    data_root: Path, transcript_roots: tuple[Path, ...]
) -> None:
    data_parts = Path(path_identity(data_root)).parts
    for transcript_root in transcript_roots:
        transcript_parts = Path(path_identity(transcript_root)).parts
        if (
            data_parts[: len(transcript_parts)] == transcript_parts
            or transcript_parts[: len(data_parts)] == data_parts
        ):
            raise ValueError("data_transcript_overlap")


def initialize_probe(
    data_root: Path,
    transcript_roots: tuple[Path, ...],
    python: Path,
) -> Path:
    if python != Path("/usr/bin/python3") or not python.is_file():
        raise ValueError("unsupported_python")
    requested = data_root.expanduser()
    if requested.is_symlink():
        raise ValueError("data_root_symlink")
    root = requested.parent.resolve(strict=True) / requested.name
    if not transcript_roots:
        raise ValueError("invalid_transcript_roots")
    canonical_transcripts = tuple(
        validate_transcript_root(item) for item in transcript_roots
    )
    # ponytail: The production installer owns workspace and mutable-skill separation.
    validate_transcript_separation(root, canonical_transcripts)
    if root.exists():
        root = validate_private_directory(root)
    else:
        root.mkdir(mode=0o700)
        root = validate_private_directory(root)
    installation_path = root / "installation.json"
    nonce_path = root / "nonce.json"
    if any(path.exists() or path.is_symlink() for path in (installation_path, nonce_path)):
        raise ValueError("existing_installation")
    for child in ("incoming", "incoming-v2", "reports"):
        directory = root / child
        if directory.is_symlink():
            raise ValueError("data_child_symlink")
        if not directory.exists():
            directory.mkdir(mode=0o700)
        validate_private_child_directory(directory)
    ensure_v2_lifecycle_files(root)
    nonce = secrets.token_hex(32)
    atomic_write_json(
        installation_path,
        {
            "schema_version": INSTALLATION_SCHEMA,
            "data_root": str(root),
            "transcript_roots": [str(item) for item in canonical_transcripts],
            "python": "/usr/bin/python3",
        },
    )
    atomic_write_json(nonce_path, {"schema_version": 1, "nonce": nonce})
    return installation_path


def load_installation(path: Path) -> Installation:
    if path.is_symlink():
        raise ValueError("installation_symlink")
    canonical = path.expanduser().resolve(strict=True)
    info = canonical.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("installation_owner_or_type")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("installation_permissions")
    payload = json.loads(canonical.read_text(encoding="utf-8"))
    if payload.get("schema_version") != INSTALLATION_SCHEMA:
        raise ValueError("unsupported_installation_schema")
    fixed_data_root = payload.get("data_root")
    if (
        not isinstance(fixed_data_root, str)
        or not fixed_data_root
        or not Path(fixed_data_root).is_absolute()
    ):
        raise ValueError("invalid_data_root")
    requested_root = Path(fixed_data_root)
    root = validate_private_directory(requested_root)
    if requested_root != root:
        raise ValueError("invalid_data_root")
    if canonical != root / "installation.json":
        raise ValueError("installation_location")
    fixed_transcript_roots = payload.get("transcript_roots")
    if (
        not isinstance(fixed_transcript_roots, list)
        or not fixed_transcript_roots
        or any(
            not isinstance(item, str)
            or not item
            or not Path(item).is_absolute()
            for item in fixed_transcript_roots
        )
    ):
        raise ValueError("invalid_transcript_roots")
    requested_transcript_roots = tuple(
        Path(item) for item in fixed_transcript_roots
    )
    transcript_roots = tuple(
        validate_transcript_root(item) for item in requested_transcript_roots
    )
    if requested_transcript_roots != transcript_roots:
        raise ValueError("invalid_transcript_roots")
    validate_transcript_separation(root, transcript_roots)
    if payload.get("python") != "/usr/bin/python3":
        raise ValueError("unsupported_python")
    for child in ("incoming", "incoming-v2", "reports"):
        validate_private_child_directory(root / child)
    nonce_path = validate_private_nonce(root / "nonce.json")
    nonce_payload = json.loads(nonce_path.read_text(encoding="utf-8"))
    nonce = nonce_payload.get("nonce")
    if (
        nonce_payload.get("schema_version") != 1
        or not isinstance(nonce, str)
        or len(nonce) != 64
        or any(character not in "0123456789abcdef" for character in nonce)
    ):
        raise ValueError("invalid_nonce")
    return Installation(
        data_root=root,
        transcript_roots=transcript_roots,
        python=Path("/usr/bin/python3"),
        nonce=nonce,
    )


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


def is_within(path: Path, roots: tuple[Path, ...]) -> bool:
    candidate = str(path)
    return any(
        os.path.commonpath((candidate, str(root))) == str(root)
        for root in roots
    )


def bounded_text(payload: dict[str, object], key: str, maximum: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise ValueError(f"invalid_{key}")
    return value


def parse_stop_envelope(raw: bytes, installation: Installation) -> StopEnvelope:
    if len(raw) > MAX_STDIN_BYTES:
        raise ValueError("hook_input_too_large")
    payload = json.loads(raw)
    shape = summarize_hook_shape(payload)
    if payload.get("hook_event_name") != "Stop":
        raise ValueError("not_stop_event")
    session_id = bounded_text(payload, "session_id", 512)
    turn_id = bounded_text(payload, "turn_id", 512)
    cwd = Path(bounded_text(payload, "cwd", 4_096)).expanduser().resolve(strict=True)
    transcript_value = bounded_text(payload, "transcript_path", 4_096)
    transcript_path = Path(transcript_value).expanduser()
    if transcript_path.is_symlink():
        raise ValueError("transcript_symlink")
    transcript_path = transcript_path.resolve(strict=True)
    if not is_within(transcript_path, installation.transcript_roots):
        raise ValueError("transcript_outside_roots")
    return StopEnvelope(session_id, turn_id, transcript_path, cwd, shape)


def summarize_session_hook_shape(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("hook_payload_not_object")
    keys = set(REQUIRED_SESSION_HOOK_FIELDS) | {"transcript_path", "turn_id"}
    field_types = {
        key: type(payload[key]).__name__
        for key in sorted(keys & set(payload))
    }
    required = {
        key: {
            "present": key in payload,
            "type": field_types.get(key),
            "valid": isinstance(payload.get(key), expected)
            and (not isinstance(payload.get(key), str) or bool(payload[key]))
            and (key != "hook_event_name" or payload.get(key) == "Stop"),
        }
        for key, expected in REQUIRED_SESSION_HOOK_FIELDS.items()
    }
    transcript = payload.get("transcript_path")
    required["transcript_path"] = {
        "present": "transcript_path" in payload,
        "type": field_types.get("transcript_path"),
        "valid": isinstance(transcript, str) and bool(transcript),
    }
    turn = payload.get("turn_id")
    required["turn_id"] = {
        "present": "turn_id" in payload,
        "type": field_types.get("turn_id"),
        "valid": "turn_id" not in payload or (isinstance(turn, str) and bool(turn)),
    }
    return {
        "payload_keys": sorted(field_types),
        "field_types": field_types,
        "required_fields": required,
    }


def empty_session_hook_shape() -> dict[str, object]:
    return {
        "payload_keys": [],
        "field_types": {},
        "required_fields": {
            key: {"present": False, "type": None, "valid": False}
            for key in (*REQUIRED_SESSION_HOOK_FIELDS, "transcript_path", "turn_id")
        },
    }


def valid_session_hook_shape(shape: object) -> bool:
    if not isinstance(shape, dict) or set(shape) != {
        "payload_keys",
        "field_types",
        "required_fields",
    }:
        return False
    payload_keys = shape["payload_keys"]
    field_types = shape["field_types"]
    required_fields = shape["required_fields"]
    if (
        not isinstance(payload_keys, list)
        or not all(type(key) is str for key in payload_keys)
        or not isinstance(field_types, dict)
        or not isinstance(required_fields, dict)
    ):
        return False
    expected_types = {
        "cwd": "str",
        "hook_event_name": "str",
        "session_id": "str",
        "transcript_path": "str",
    }
    turn_present = "turn_id" in payload_keys
    if turn_present:
        expected_types["turn_id"] = "str"
    if payload_keys != sorted(expected_types) or field_types != expected_types:
        return False
    expected_required = {
        key: {"present": True, "type": "str", "valid": True}
        for key in expected_types
        if key != "turn_id"
    }
    expected_required["turn_id"] = {
        "present": turn_present,
        "type": "str" if turn_present else None,
        "valid": True,
    }
    if set(required_fields) != set(expected_required):
        return False
    for key, expected in expected_required.items():
        field = required_fields[key]
        if (
            not isinstance(field, dict)
            or set(field) != {"present", "type", "valid"}
            or type(field["present"]) is not bool
            or type(field["valid"]) is not bool
            or field != expected
        ):
            return False
    return True


def parse_session_stop_envelope(
    raw: bytes, installation: Installation
) -> SessionStopEnvelope:
    if len(raw) > MAX_STDIN_BYTES:
        raise ValueError("hook_input_too_large")
    payload = json.loads(raw)
    shape = summarize_session_hook_shape(payload)
    if payload.get("hook_event_name") != "Stop":
        raise ValueError("not_stop_event")
    session_id = bounded_text(payload, "session_id", 512)
    turn_id = bounded_text(payload, "turn_id", 512) if "turn_id" in payload else None
    cwd = Path(bounded_text(payload, "cwd", 4_096)).expanduser().resolve(strict=True)
    transcript_value = bounded_text(payload, "transcript_path", 4_096)
    transcript_path = Path(transcript_value).expanduser()
    if transcript_path.is_symlink():
        raise ValueError("transcript_symlink")
    transcript_path = transcript_path.resolve(strict=True)
    if not is_within(transcript_path, installation.transcript_roots):
        raise ValueError("transcript_outside_roots")
    return SessionStopEnvelope(session_id, turn_id, transcript_path, cwd, shape)


def session_stop_event_session_id(event: object) -> Optional[str]:
    if not isinstance(event, dict) or set(event) != SESSION_STOP_EVENT_FIELDS:
        return None
    if event.get("hook_event_name") != "Stop":
        return None
    try:
        session_id = bounded_text(event, "session_id", 512)
        bounded_text(event, "cwd", 4_096)
        bounded_text(event, "transcript_path", 4_096)
        turn_id = event["turn_id"]
        if turn_id is not None:
            bounded_text(event, "turn_id", 512)
    except ValueError:
        return None
    return session_id


def valid_session_transcript_stat(transcript_stat: object) -> bool:
    if not isinstance(transcript_stat, dict) or set(transcript_stat) != {
        "size",
        "mtime_ns",
        "device",
        "inode",
        "regular",
        "owned_by_current_user",
    }:
        return False
    return (
        type(transcript_stat["size"]) is int
        and type(transcript_stat["mtime_ns"]) is int
        and type(transcript_stat["device"]) is int
        and type(transcript_stat["inode"]) is int
        and type(transcript_stat["regular"]) is bool
        and type(transcript_stat["owned_by_current_user"]) is bool
        and transcript_stat["regular"] is True
        and transcript_stat["owned_by_current_user"] is True
    )


def valid_session_stop_observation(
    observation: object, installation: Installation
) -> bool:
    if not isinstance(observation, dict) or set(observation) != {
        "schema_version",
        "received_at_ns",
        "installation_nonce",
        "shape",
        "event",
        "transcript_stat",
    }:
        return False
    return (
        type(observation["schema_version"]) is int
        and observation["schema_version"] == 2
        and type(observation["received_at_ns"]) is int
        and type(observation["installation_nonce"]) is str
        and observation["installation_nonce"] == installation.nonce
        and valid_session_hook_shape(observation["shape"])
        and session_stop_event_session_id(observation["event"]) is not None
        and valid_session_transcript_stat(observation["transcript_stat"])
    )


def stat_transcript(path: Path) -> dict[str, object]:
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(path), flags)
    try:
        info = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("transcript_not_regular")
    if info.st_uid != os.getuid():
        raise ValueError("transcript_owner")
    return {
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "device": info.st_dev,
        "inode": info.st_ino,
        "regular": True,
        "owned_by_current_user": True,
    }


CAPTURE_ERROR_CODES = {
    "hook_input_too_large",
    "invalid_cwd",
    "invalid_session_id",
    "invalid_transcript_path",
    "invalid_turn_id",
    "not_stop_event",
    "transcript_not_regular",
    "transcript_outside_roots",
    "transcript_owner",
    "transcript_symlink",
}


def safe_capture_error_code(error: BaseException) -> str:
    code = str(error)
    return code if isinstance(error, ValueError) and code in CAPTURE_ERROR_CODES else "transcript_unavailable"


def capture_stop(installation: Installation, raw: bytes) -> Path:
    if len(raw) > MAX_STDIN_BYTES:
        raise ValueError("hook_input_too_large")
    payload = json.loads(raw)
    shape = summarize_hook_shape(payload)
    observation = {
        "schema_version": 1,
        "received_at_ns": time.time_ns(),
        "installation_nonce": installation.nonce,
        "shape": shape,
    }
    try:
        envelope = parse_stop_envelope(raw, installation)
        transcript_info = stat_transcript(envelope.transcript_path)
    except (KeyError, OSError, ValueError) as error:
        observation["capture_error_code"] = safe_capture_error_code(error)
    else:
        observation["event"] = {
            "hook_event_name": "Stop",
            "session_id": envelope.session_id,
            "turn_id": envelope.turn_id,
            "transcript_path": str(envelope.transcript_path),
            "cwd": str(envelope.cwd),
        }
        observation["transcript_stat"] = transcript_info
    destination = installation.data_root / "incoming" / (
        f"{observation['received_at_ns']}-{os.getpid()}-{secrets.token_hex(4)}.json"
    )
    atomic_write_json(destination, observation)
    return destination


SESSION_CAPTURE_ERROR_CODES = {
    "hook_input_too_large",
    "invalid_cwd",
    "invalid_session_id",
    "invalid_transcript_path",
    "invalid_turn_id",
    "not_stop_event",
    "transcript_not_regular",
    "transcript_outside_roots",
    "transcript_owner",
    "transcript_symlink",
    "transcript_unavailable",
}

SESSION_STOP_PROMOTION_ERROR_CODES = {
    "invalid_session_stop_observation",
    "session_surface_boundary_mismatch",
    "session_surface_boundary_missing",
    "session_surface_observation_count",
    "session_stop_observation_unavailable",
}


def safe_session_capture_error_code(error: BaseException) -> str:
    code = str(error)
    return (
        code
        if isinstance(error, ValueError) and code in SESSION_CAPTURE_ERROR_CODES
        else "transcript_unavailable"
    )


def session_incoming_directory(installation: Installation) -> Path:
    path = installation.data_root / "incoming-v2"
    if path.is_symlink():
        raise ValueError("data_child_symlink")
    if not path.exists():
        path.mkdir(mode=0o700)
    return validate_private_child_directory(path)


def _capture_session_stop_unlocked(installation: Installation, raw: bytes) -> Path:
    observation: dict[str, object] = {
        "schema_version": 2,
        "received_at_ns": time.time_ns(),
        "installation_nonce": installation.nonce,
        "shape": empty_session_hook_shape(),
    }
    try:
        if len(raw) > MAX_STDIN_BYTES:
            raise ValueError("hook_input_too_large")
        observation["shape"] = summarize_session_hook_shape(json.loads(raw))
        envelope = parse_session_stop_envelope(raw, installation)
        transcript_info = stat_transcript(envelope.transcript_path)
    except Exception as error:
        observation["capture_error_code"] = safe_session_capture_error_code(error)
    else:
        observation["event"] = {
            "hook_event_name": "Stop",
            "session_id": envelope.session_id,
            "turn_id": envelope.turn_id,
            "transcript_path": str(envelope.transcript_path),
            "cwd": str(envelope.cwd),
        }
        observation["transcript_stat"] = transcript_info
    destination = session_incoming_directory(installation) / (
        f"{observation['received_at_ns']}-{os.getpid()}-{secrets.token_hex(4)}.json"
    )
    atomic_write_json(destination, observation)
    return destination


def capture_session_stop(installation: Installation, raw: bytes) -> Path:
    with v2_state_lock(installation, exclusive=True):
        return _capture_session_stop_unlocked(installation, raw)


def cmd_probe_stop(args: argparse.Namespace) -> int:
    try:
        installation = load_installation(Path(args.installation))
        raw = read_bounded_stdin(sys.stdin.buffer)
        capture_stop(installation, raw)
    except Exception:
        pass
    return 0


def cmd_probe_v2_stop(args: argparse.Namespace) -> int:
    try:
        installation = load_installation(Path(args.installation))
        try:
            raw = read_bounded_stdin(sys.stdin.buffer)
        except ValueError as error:
            if str(error) != "hook_input_too_large":
                raise
            raw = b"\0" * (MAX_STDIN_BYTES + 1)
        capture_session_stop(installation, raw)
    except Exception:
        pass
    return 0


def write_json_stdout(payload: dict[str, object], stream: TextIO = sys.stdout) -> None:
    json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
    stream.write("\n")


def cmd_probe_init(args: argparse.Namespace) -> int:
    installation = initialize_probe(
        Path(args.data_root),
        tuple(Path(item) for item in args.transcript_root),
        Path("/usr/bin/python3"),
    )
    write_json_stdout({"status": "initialized", "installation": str(installation)})
    return 0


def validate_observation(path: Path) -> Path:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise ValueError("observation_symlink")
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("observation_not_regular")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("observation_permissions")
    return path


def read_bounded_private_json(path: Path, limit: int = MAX_STDIN_BYTES) -> object:
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags)
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > limit
            ):
                raise ValueError("session_stop_observation_unavailable")
            chunks: list[bytes] = []
            remaining = limit + 1
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            if remaining == 0:
                raise ValueError("session_stop_observation_unavailable")
        finally:
            os.close(descriptor)
        return json.loads(b"".join(chunks).decode("utf-8"))
    except Exception as error:
        raise ValueError("session_stop_observation_unavailable") from error


def is_session_observation_name(name: str) -> bool:
    if len(name) > 128 or not name.endswith(".json"):
        return False
    parts = name.removesuffix(".json").split("-")
    return (
        len(parts) == 3
        and all(parts[:2])
        and all("0" <= character <= "9" for part in parts[:2] for character in part)
        and len(parts[2]) == 8
        and all(character in "0123456789abcdef" for character in parts[2])
    )


def observation_paths(installation: Installation) -> list[Path]:
    return [
        validate_observation(path)
        for path in sorted((installation.data_root / "incoming").glob("*.json"))
    ]


def session_observation_paths(installation: Installation) -> list[Path]:
    paths = sorted(session_incoming_directory(installation).glob("*.json"))
    if any(not is_session_observation_name(path.name) for path in paths):
        raise ValueError("session_stop_observation_unavailable")
    return [
        validate_observation(path)
        for path in paths
    ]


def validate_surface(surface: str) -> str:
    if surface not in {"cli", "desktop"}:
        raise ValueError("invalid_surface")
    return surface


def parse_access_surface(surface: str) -> str:
    if surface in {"cli", "desktop"}:
        return surface
    raise argparse.ArgumentTypeError("invalid_access_surface")


class SanitizedArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: invalid_arguments\n")


ACCESS_EVIDENCE_ERROR = "access_evidence_unavailable"
ACCESS_CHALLENGE_ERROR = "access_challenge_unavailable"
ACCESS_GLOBAL_WRITE_ERROR = "access_global_write_unavailable"
ACCESS_DEFAULT_WRITE_UNEXPECTED = "access_default_write_unexpected"


def challenge_digest(challenge: str) -> str:
    return hashlib.sha256(challenge.encode("ascii")).hexdigest()


def access_reports_directory(installation: Installation) -> Path:
    return validate_private_child_directory(installation.data_root / "reports")


@contextmanager
def access_generation_lock(
    installation: Installation, surface: str
) -> Iterator[None]:
    path = access_reports_directory(installation) / f"{surface}-v2-access.lock"
    descriptor = os.open(
        str(path),
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    locked = False
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ValueError(ACCESS_EVIDENCE_ERROR)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        try:
            if locked:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def access_challenge_path(installation: Installation, surface: str) -> Path:
    return access_reports_directory(installation) / f"{surface}-v2-access-challenge.json"


def access_default_response_path(installation: Installation, surface: str) -> Path:
    return access_reports_directory(installation) / f"{surface}-v2-default-response.json"


def access_explicit_response_path(installation: Installation, surface: str) -> Path:
    return access_reports_directory(installation) / f"{surface}-v2-explicit-response.json"


def access_result(
    surface: str,
    challenge_read: bool,
    global_write: bool,
    write_denied: bool,
    digest: Optional[str],
    error_codes: list[str],
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "surface": surface,
        "challenge_read": challenge_read,
        "global_write": global_write,
        "write_denied": write_denied,
        "challenge_digest": digest,
        "error_codes": error_codes,
    }


def valid_access_challenge(
    payload: object, installation: Installation, surface: str
) -> bool:
    return (
        isinstance(payload, dict)
        and set(payload) == {
            "schema_version",
            "surface",
            "installation_nonce",
            "challenge",
        }
        and type(payload["schema_version"]) is int
        and payload["schema_version"] == 2
        and type(payload["surface"]) is str
        and payload["surface"] == surface
        and type(payload["installation_nonce"]) is str
        and payload["installation_nonce"] == installation.nonce
        and type(payload["challenge"]) is str
        and len(payload["challenge"]) == 64
        and all(character in "0123456789abcdef" for character in payload["challenge"])
    )


def load_access_challenge_v2(
    installation: Installation, surface: str
) -> dict[str, object]:
    surface = validate_surface(surface)
    try:
        payload = read_bounded_private_json(access_challenge_path(installation, surface))
    except (OSError, ValueError) as error:
        raise ValueError(ACCESS_CHALLENGE_ERROR) from error
    if not valid_access_challenge(payload, installation, surface):
        raise ValueError(ACCESS_CHALLENGE_ERROR)
    return payload


def arm_access_v2(installation: Installation, surface: str) -> dict[str, object]:
    surface = validate_surface(surface)
    try:
        with v2_state_lock(installation, exclusive=True):
            with access_generation_lock(installation, surface):
                for path in (
                    access_default_response_path(installation, surface),
                    access_explicit_response_path(installation, surface),
                ):
                    path.unlink(missing_ok=True)
                challenge = secrets.token_hex(32)
                atomic_write_json(
                    access_challenge_path(installation, surface),
                    {
                        "schema_version": 2,
                        "surface": surface,
                        "installation_nonce": installation.nonce,
                        "challenge": challenge,
                    },
                )
    except (OSError, ValueError) as error:
        raise ValueError(ACCESS_EVIDENCE_ERROR) from error
    return {
        "schema_version": 2,
        "surface": surface,
        "armed": True,
        "challenge_digest": challenge_digest(challenge),
        "error_codes": [],
    }


def valid_access_result(
    payload: object,
    surface: str,
    digest: str,
    global_write: bool,
    write_denied: bool,
) -> bool:
    return (
        isinstance(payload, dict)
        and set(payload)
        == {
            "schema_version",
            "surface",
            "challenge_read",
            "global_write",
            "write_denied",
            "challenge_digest",
            "error_codes",
        }
        and type(payload["schema_version"]) is int
        and payload["schema_version"] == 2
        and type(payload["surface"]) is str
        and payload["surface"] == surface
        and payload["challenge_read"] is True
        and payload["global_write"] is global_write
        and payload["write_denied"] is write_denied
        and type(payload["challenge_digest"]) is str
        and payload["challenge_digest"] == digest
        and payload["error_codes"] == []
    )


def write_access_result(output: Path, result: dict[str, object]) -> None:
    atomic_write_json(resolve_report_output(output), result)


def _run_default_access_v2_locked(
    installation: Installation,
    surface: str,
    output: Path,
) -> dict[str, object]:
    surface = validate_surface(surface)
    try:
        challenge = load_access_challenge_v2(installation, surface)
    except (OSError, ValueError):
        result = access_result(
            surface, False, False, False, None, [ACCESS_CHALLENGE_ERROR]
        )
        write_access_result(output, result)
        return result
    digest = challenge_digest(str(challenge["challenge"]))
    result = access_result(surface, True, False, False, digest, [])
    try:
        atomic_write_json(access_default_response_path(installation, surface), result)
    except PermissionError:
        result["write_denied"] = True
    except OSError:
        result["error_codes"] = [ACCESS_GLOBAL_WRITE_ERROR]
    else:
        result["global_write"] = True
        result["error_codes"] = [ACCESS_DEFAULT_WRITE_UNEXPECTED]
    write_access_result(output, result)
    return result


def run_default_access_v2(
    installation: Installation,
    surface: str,
    output: Path,
) -> dict[str, object]:
    try:
        with v2_state_lock(installation, exclusive=False):
            return _run_default_access_v2_locked(installation, surface, output)
    except (OSError, ValueError):
        result = access_result(
            surface, False, False, False, None, [ACCESS_CHALLENGE_ERROR]
        )
        write_access_result(output, result)
        return result


def _run_explicit_access_v2_locked(
    installation: Installation,
    surface: str,
) -> dict[str, object]:
    surface = validate_surface(surface)
    try:
        challenge = load_access_challenge_v2(installation, surface)
    except (OSError, ValueError):
        return access_result(surface, False, False, False, None, [ACCESS_CHALLENGE_ERROR])
    digest = challenge_digest(str(challenge["challenge"]))
    result = access_result(surface, True, True, False, digest, [])
    try:
        path = access_explicit_response_path(installation, surface)
        atomic_write_json(path, result)
        stored = read_bounded_private_json(path)
        if not valid_access_result(stored, surface, digest, True, False):
            raise ValueError(ACCESS_EVIDENCE_ERROR)
    except PermissionError:
        return access_result(surface, True, False, True, digest, [])
    except (OSError, ValueError):
        return access_result(
            surface, True, False, False, digest, [ACCESS_GLOBAL_WRITE_ERROR]
        )
    return result


def run_explicit_access_v2(
    installation: Installation,
    surface: str,
) -> dict[str, object]:
    try:
        with v2_state_lock(installation, exclusive=True):
            with access_generation_lock(installation, surface):
                return _run_explicit_access_v2_locked(installation, surface)
    except (OSError, ValueError):
        return access_result(
            surface, False, False, False, None, [ACCESS_CHALLENGE_ERROR]
        )


def has_successful_session_stop_evidence(
    installation: Installation, surface: str
) -> bool:
    try:
        paths = session_surface_observations(installation, surface)
        observations = [read_bounded_private_json(path) for path in paths]
        if len(observations) != 2 or not all(
            valid_session_stop_observation(item, installation) for item in observations
        ):
            return False
        session_ids = [session_stop_event_session_id(item.get("event")) for item in observations]
        transcript_stat = session_transcript_stat_report(
            [item.get("transcript_stat") for item in observations]
        )
        return (
            all(item.get("installation_nonce") == installation.nonce for item in observations)
            and all(session_id is not None for session_id in session_ids)
            and len(set(session_ids)) == 2
            and all(transcript_stat.values())
        )
    except (OSError, ValueError):
        return False


def failed_access_report(surface: str) -> dict[str, object]:
    return {
        "schema_version": 2,
        "surface": surface,
        "hook_global_read": False,
        "hook_global_write": False,
        "skill_default_read": False,
        "skill_default_write": False,
        "skill_default_write_denied": False,
        "skill_explicit_read": False,
        "skill_explicit_write": False,
        "error_codes": [ACCESS_EVIDENCE_ERROR],
    }


def failed_access_promotion(surface: str, output: Path) -> dict[str, object]:
    report = failed_access_report(surface)
    write_access_result(output, report)
    return report


def promote_access_v2(
    installation: Installation,
    surface: str,
    default_response: Path,
    output: Path,
) -> dict[str, object]:
    surface = validate_surface(surface)
    try:
        with v2_state_lock(installation, exclusive=False):
            with access_generation_lock(installation, surface):
                challenge = load_access_challenge_v2(installation, surface)
                digest = challenge_digest(str(challenge["challenge"]))
                default = read_bounded_private_json(resolve_report_output(default_response))
                explicit = read_bounded_private_json(
                    access_explicit_response_path(installation, surface)
                )
                evidence_ok = (
                    valid_access_result(default, surface, digest, False, True)
                    and valid_access_result(explicit, surface, digest, True, False)
                    and has_successful_session_stop_evidence(installation, surface)
                )
                if not evidence_ok:
                    return failed_access_promotion(surface, output)
                report = {
                    "schema_version": 2,
                    "surface": surface,
                    "hook_global_read": True,
                    "hook_global_write": True,
                    "skill_default_read": True,
                    "skill_default_write": False,
                    "skill_default_write_denied": True,
                    "skill_explicit_read": True,
                    "skill_explicit_write": True,
                    "error_codes": [],
                }
                write_access_result(output, report)
                return report
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return failed_access_promotion(surface, output)


def arm_skill_preflight(
    installation: Installation,
    surface: str,
) -> dict[str, object]:
    surface = validate_surface(surface)
    reports = installation.data_root / "reports"
    challenge_path = reports / f"{surface}-skill-challenge.json"
    response_path = reports / f"{surface}-skill-response.json"
    response_path.unlink(missing_ok=True)
    atomic_write_json(
        challenge_path,
        {
            "schema_version": 1,
            "surface": surface,
            "installation_nonce": installation.nonce,
            "challenge": secrets.token_hex(32),
        },
    )
    fsync_directory(reports)
    return {"surface": surface, "armed": True}


def run_skill_preflight(
    installation: Installation,
    surface: str,
) -> dict[str, object]:
    surface = validate_surface(surface)
    reports = installation.data_root / "reports"
    challenge = json.loads(
        (reports / f"{surface}-skill-challenge.json").read_text(encoding="utf-8")
    )
    if (
        challenge.get("surface") != surface
        or challenge.get("installation_nonce") != installation.nonce
        or not isinstance(challenge.get("challenge"), str)
    ):
        raise ValueError("invalid_skill_challenge")
    response_path = reports / f"{surface}-skill-response.json"
    atomic_write_json(
        response_path,
        {
            "schema_version": 1,
            "surface": surface,
            "installation_nonce": installation.nonce,
            "challenge": challenge["challenge"],
        },
    )
    response = json.loads(response_path.read_text(encoding="utf-8"))
    if response != {
        "schema_version": 1,
        "surface": surface,
        "installation_nonce": installation.nonce,
        "challenge": challenge["challenge"],
    }:
        raise ValueError("skill_preflight_round_trip")
    return {"surface": surface, "read": True, "write": True}


def promote_skill_preflight(
    installation: Installation,
    surface: str,
    output: Path,
) -> dict[str, object]:
    surface = validate_surface(surface)
    reports = installation.data_root / "reports"
    passed = False
    try:
        challenge = json.loads(
            (reports / f"{surface}-skill-challenge.json").read_text(encoding="utf-8")
        )
        response = json.loads(
            (reports / f"{surface}-skill-response.json").read_text(encoding="utf-8")
        )
        passed = (
            isinstance(challenge, dict)
            and isinstance(response, dict)
            and challenge.get("schema_version") == 1
            and response.get("schema_version") == 1
            and challenge.get("surface") == surface
            and response.get("surface") == surface
            and challenge.get("installation_nonce") == installation.nonce
            and response.get("installation_nonce") == installation.nonce
            and isinstance(challenge.get("challenge"), str)
            and bool(challenge["challenge"])
            and isinstance(response.get("challenge"), str)
            and bool(response["challenge"])
            and response.get("challenge") == challenge.get("challenge")
        )
    except (KeyError, OSError, json.JSONDecodeError):
        passed = False
    report = {
        "schema_version": 1,
        "surface": surface,
        "read": passed,
        "write": passed,
    }
    atomic_write_json(output.resolve(), report)
    return report


def mark_surface_boundary(
    installation: Installation,
    surface: str,
) -> dict[str, object]:
    surface = validate_surface(surface)
    observations = observation_paths(installation)
    boundary = observations[-1].name if observations else None
    atomic_write_json(
        installation.data_root / "reports" / f"{surface}-boundary.json",
        {"schema_version": 1, "surface": surface, "after": boundary},
    )
    return {"surface": surface, "marked": True}


def surface_observations(
    installation: Installation,
    surface: str,
) -> list[Path]:
    surface = validate_surface(surface)
    observations = observation_paths(installation)
    marker = json.loads(
        (
            installation.data_root / "reports" / f"{surface}-boundary.json"
        ).read_text(encoding="utf-8")
    )
    if marker.get("surface") != surface:
        raise ValueError("surface_boundary_mismatch")
    after = marker.get("after")
    if after is None:
        selected = observations
    else:
        names = [path.name for path in observations]
        if after not in names:
            raise ValueError("surface_boundary_missing")
        selected = observations[names.index(after) + 1 :]
    if len(selected) != 2:
        raise ValueError("surface_observation_count")
    return selected


def mark_session_surface_boundary(
    installation: Installation,
    surface: str,
) -> dict[str, object]:
    surface = validate_surface(surface)
    with v2_state_lock(installation, exclusive=True):
        observations = session_observation_paths(installation)
        boundary = observations[-1].name if observations else None
        atomic_write_json(
            installation.data_root / "reports" / f"{surface}-v2-session-boundary.json",
            {
                "schema_version": 2,
                "surface": surface,
                "installation_nonce": installation.nonce,
                "after": boundary,
            },
        )
    return {"surface": surface, "marked": True}


def session_surface_observations(
    installation: Installation,
    surface: str,
) -> list[Path]:
    surface = validate_surface(surface)
    observations = session_observation_paths(installation)
    marker = read_bounded_private_json(
        installation.data_root / "reports" / f"{surface}-v2-session-boundary.json"
    )
    if (
        not isinstance(marker, dict)
        or set(marker) != {"schema_version", "surface", "installation_nonce", "after"}
        or type(marker["schema_version"]) is not int
        or marker["schema_version"] != 2
        or type(marker["surface"]) is not str
        or marker["surface"] != surface
        or type(marker["installation_nonce"]) is not str
        or marker["installation_nonce"] != installation.nonce
        or (
            marker["after"] is not None
            and (
                type(marker["after"]) is not str
                or Path(marker["after"]).name != marker["after"]
                or not marker["after"].endswith(".json")
            )
        )
    ):
        raise ValueError("session_stop_observation_unavailable")
    after = marker["after"]
    if after is None:
        selected = observations
    else:
        names = [path.name for path in observations]
        if after not in names:
            raise ValueError("session_surface_boundary_missing")
        selected = observations[names.index(after) + 1 :]
    if len(selected) != 2:
        raise ValueError("session_surface_observation_count")
    return selected


def aggregate_required_fields(
    observations: list[dict[str, object]],
) -> dict[str, object]:
    names = ("hook_event_name", "session_id", "turn_id", "cwd", "transcript_path")
    result: dict[str, object] = {}
    for name in names:
        fields = [item["shape"]["required_fields"][name] for item in observations]
        types = {field.get("type") for field in fields}
        result[name] = {
            "present": all(field.get("present") is True for field in fields),
            "type": next(iter(types)) if len(types) == 1 else "mixed",
            "valid": all(field.get("valid") is True for field in fields),
        }
    return result


def session_stop_core_shape() -> dict[str, object]:
    field_types = {
        "cwd": "str",
        "hook_event_name": "str",
        "session_id": "str",
        "transcript_path": "str",
    }
    return {
        "payload_keys": sorted(field_types),
        "field_types": field_types,
        "required_fields": {
            key: {"present": True, "type": "str", "valid": True}
            for key in field_types
        },
    }


def session_transcript_stat_report(
    transcript_infos: list[object],
) -> dict[str, object]:
    present = bool(transcript_infos) and all(
        isinstance(item, dict) for item in transcript_infos
    )
    infos = [item for item in transcript_infos if isinstance(item, dict)]
    return {
        "present": present,
        "regular": present and all(item.get("regular") is True for item in infos),
        "owned_by_current_user": present
        and all(item.get("owned_by_current_user") is True for item in infos),
        "size_positive": present
        and all(type(item.get("size")) is int and item["size"] > 0 for item in infos),
        "has_mtime_ns": present
        and all(type(item.get("mtime_ns")) is int for item in infos),
        "has_device": present
        and all(type(item.get("device")) is int for item in infos),
        "has_inode": present
        and all(type(item.get("inode")) is int for item in infos),
    }


def failed_session_stop_promotion(
    surface: str,
    output: Path,
    code: str,
) -> dict[str, object]:
    report = {
        "schema_version": 2,
        "surface": surface,
        "observation_count": 0,
        "capture_supported": False,
        "distinct_sessions": False,
        "turn_id_optional": True,
        "hook_event_name": "Stop",
        "payload_shapes_stable": False,
        "payload_keys": [],
        "field_types": {},
        "required_fields": {
            name: {"present": False, "type": None, "valid": False}
            for name in ("hook_event_name", "session_id", "cwd", "transcript_path", "turn_id")
        },
        "capture_error_codes": [code],
        "transcript_stat": session_transcript_stat_report([]),
        "shared_nonce_match": False,
    }
    atomic_write_json(output.resolve(), report)
    return report


def _promote_session_stop_v2_locked(
    installation: Installation,
    surface: str,
    output: Path,
) -> dict[str, object]:
    surface = validate_surface(surface)
    try:
        paths = session_surface_observations(installation, surface)
        try:
            observations = [read_bounded_private_json(path) for path in paths]
        except Exception as error:
            raise ValueError("session_stop_observation_unavailable") from error
        if not all(isinstance(item, dict) and item.get("schema_version") == 2 for item in observations):
            raise ValueError("invalid_session_stop_observation")
        if not all(valid_session_stop_observation(item, installation) for item in observations):
            raise ValueError("session_stop_observation_unavailable")
        nonce_matches = all(
            item.get("installation_nonce") == installation.nonce for item in observations
        )
        shapes = [item["shape"] for item in observations]
        if not all(valid_session_hook_shape(shape) for shape in shapes):
            raise ValueError("session_stop_observation_unavailable")
        core_shapes = [session_stop_core_shape() for _shape in shapes]
        transcript_infos = [item.get("transcript_stat") for item in observations]
        required_fields = core_shapes[0]["required_fields"]
        capture_error_codes = sorted(
            {
                str(item["capture_error_code"])
                for item in observations
                if "capture_error_code" in item
            }
        )
        if any(code not in SESSION_CAPTURE_ERROR_CODES for code in capture_error_codes):
            raise ValueError("session_stop_observation_unavailable")
        session_ids = [
            session_stop_event_session_id(item.get("event"))
            for item in observations
        ]
        if any(session_id is None for session_id in session_ids):
            raise ValueError("session_stop_observation_unavailable")
        payload_shapes_stable = core_shapes[0] == core_shapes[1]
        distinct_sessions = len(set(session_ids)) == 2
        transcript_stat = session_transcript_stat_report(transcript_infos)
        if not all(transcript_stat.values()):
            raise ValueError("session_stop_observation_unavailable")
        capture_supported = (
            nonce_matches
            and payload_shapes_stable
            and distinct_sessions
            and not capture_error_codes
            and all(field["valid"] is True for field in required_fields.values())
            and all(transcript_stat.values())
        )
        report = {
            "schema_version": 2,
            "surface": surface,
            "observation_count": len(observations),
            "capture_supported": capture_supported,
            "distinct_sessions": distinct_sessions,
            "turn_id_optional": True,
            "hook_event_name": "Stop",
            "payload_shapes_stable": payload_shapes_stable,
            "payload_keys": core_shapes[0]["payload_keys"],
            "field_types": core_shapes[0]["field_types"],
            "required_fields": required_fields,
            "capture_error_codes": capture_error_codes,
            "transcript_stat": transcript_stat,
            "shared_nonce_match": nonce_matches,
        }
        atomic_write_json(
            installation.data_root / "reports" / f"{surface}-v2-session-observation.json",
            {"schema_version": 2, "surface": surface, "observations": [path.name for path in paths]},
        )
        atomic_write_json(output.resolve(), report)
        return report
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        code = str(error)
        return failed_session_stop_promotion(
            surface,
            output,
            code
            if isinstance(error, ValueError)
            and code in SESSION_STOP_PROMOTION_ERROR_CODES
            else "session_stop_observation_unavailable",
        )


def promote_session_stop_v2(
    installation: Installation,
    surface: str,
    output: Path,
) -> dict[str, object]:
    with v2_state_lock(installation, exclusive=False):
        return _promote_session_stop_v2_locked(installation, surface, output)


def failed_stop_promotion(
    surface: str,
    output: Path,
    code: str,
) -> dict[str, object]:
    report = {
        "schema_version": 1,
        "surface": surface,
        "observation_count": 0,
        "capture_supported": False,
        "distinct_turns": False,
        "hook_event_name": "Stop",
        "payload_shapes_stable": False,
        "payload_keys": [],
        "field_types": {},
        "required_fields": {
            name: {"present": False, "type": None, "valid": False}
            for name in (
                "hook_event_name",
                "session_id",
                "turn_id",
                "cwd",
                "transcript_path",
            )
        },
        "capture_error_codes": [code],
        "transcript_stat": {
            "present": False,
            "regular": False,
            "owned_by_current_user": False,
            "size_positive": False,
            "has_mtime_ns": False,
            "has_device": False,
            "has_inode": False,
        },
        "shared_nonce_match": False,
    }
    atomic_write_json(output.resolve(), report)
    return report


def promote_surface_stop(
    installation: Installation,
    surface: str,
    output: Path,
) -> dict[str, object]:
    surface = validate_surface(surface)
    try:
        paths = surface_observations(installation, surface)
        observations = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in paths
        ]
        if not all(isinstance(item, dict) for item in observations):
            raise ValueError("invalid_stop_observation")
        nonce_matches = all(
            item.get("installation_nonce") == installation.nonce
            for item in observations
        )
        shapes = [item["shape"] for item in observations]
        transcript_infos = [
            item.get("transcript_stat")
            for item in observations
        ]
        transcript_present = all(isinstance(item, dict) for item in transcript_infos)
        turn_identities = {
            (
                item["event"]["session_id"],
                item["event"]["turn_id"],
            )
            for item in observations
            if isinstance(item.get("event"), dict)
        }
        required_fields = aggregate_required_fields(observations)
        capture_error_codes = sorted(
            {
                str(item["capture_error_code"])
                for item in observations
                if "capture_error_code" in item
            }
        )
        payload_shapes_stable = shapes[0] == shapes[1]
        distinct_turns = len(turn_identities) == 2
        capture_supported = (
            nonce_matches
            and payload_shapes_stable
            and distinct_turns
            and not capture_error_codes
            and all(field["valid"] is True for field in required_fields.values())
            and transcript_present
            and all(
                item["regular"] is True
                and item["owned_by_current_user"] is True
                and item["size"] > 0
                and isinstance(item["mtime_ns"], int)
                and isinstance(item["device"], int)
                and isinstance(item["inode"], int)
                for item in transcript_infos
            )
        )
        report = {
            "schema_version": 1,
            "surface": surface,
            "observation_count": len(observations),
            "capture_supported": capture_supported,
            "distinct_turns": distinct_turns,
            "hook_event_name": "Stop",
            "payload_shapes_stable": payload_shapes_stable,
            "payload_keys": sorted(
                set(shapes[0]["payload_keys"]) | set(shapes[1]["payload_keys"])
            ),
            "field_types": (
                shapes[0]["field_types"]
                if shapes[0]["field_types"] == shapes[1]["field_types"]
                else {}
            ),
            "required_fields": required_fields,
            "capture_error_codes": capture_error_codes,
            "transcript_stat": {
                "present": transcript_present,
                "regular": transcript_present
                and all(item["regular"] is True for item in transcript_infos),
                "owned_by_current_user": transcript_present
                and all(item["owned_by_current_user"] is True for item in transcript_infos),
                "size_positive": transcript_present
                and all(item["size"] > 0 for item in transcript_infos),
                "has_mtime_ns": transcript_present
                and all(isinstance(item["mtime_ns"], int) for item in transcript_infos),
                "has_device": transcript_present
                and all(isinstance(item["device"], int) for item in transcript_infos),
                "has_inode": transcript_present
                and all(isinstance(item["inode"], int) for item in transcript_infos),
            },
            "shared_nonce_match": nonce_matches,
        }
        atomic_write_json(
            installation.data_root / "reports" / f"{surface}-observation.json",
            {
                "schema_version": 1,
                "surface": surface,
                "observations": [path.name for path in paths],
            },
        )
        atomic_write_json(output.resolve(), report)
        return report
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        allowed = {
            "invalid_stop_observation",
            "surface_boundary_mismatch",
            "surface_boundary_missing",
            "surface_observation_count",
        }
        code = str(error)
        return failed_stop_promotion(
            surface,
            output,
            code if isinstance(error, ValueError) and code in allowed else "surface_observation_unavailable",
        )


def load_captured_records(
    observation: dict[str, object],
) -> tuple[list[object], int, int]:
    event = observation["event"]
    captured = observation["transcript_stat"]
    captured_size = int(captured["size"])
    if captured_size > MAX_TRANSCRIPT_PROBE_BYTES:
        raise ValueError("oversized_transcript")
    transcript_path = Path(str(event["transcript_path"]))
    if transcript_path.is_symlink():
        raise ValueError("transcript_changed")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(transcript_path), flags)
    try:
        current = os.fstat(descriptor)
        if (
            current.st_dev != captured["device"]
            or current.st_ino != captured["inode"]
            or current.st_size < captured_size
        ):
            raise ValueError("transcript_changed")
        prefix = read_exact_prefix(descriptor, captured_size)
    finally:
        os.close(descriptor)
    if prefix and not prefix.endswith(b"\n"):
        raise ValueError("captured_prefix_partial_record")

    records: list[object] = []
    try:
        for raw_line in prefix.splitlines():
            if not raw_line:
                raise ValueError("unsupported_jsonl")
            records.append(json.loads(raw_line))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("unsupported_jsonl") from error
    return records, captured_size, current.st_size


def _session_event_path(
    observation: dict[str, object], installation: Installation
) -> Path:
    if not valid_session_stop_observation(observation, installation):
        raise ValueError("session_binding_unavailable")
    event = observation["event"]
    assert isinstance(event, dict)
    raw_path = event["transcript_path"]
    assert isinstance(raw_path, str)
    try:
        _transcript_relative_path(raw_path, installation)
    except ValueError as error:
        raise ValueError("session_binding_unavailable") from error
    return Path(raw_path)


def _transcript_relative_path(
    path: str | Path, installation: Installation
) -> tuple[Path, tuple[str, ...]]:
    raw = path if isinstance(path, str) else str(path)
    parts = raw.split("/")
    if (
        not raw.startswith("/")
        or len(parts) < 2
        or any(part in {"", ".", ".."} for part in parts[1:])
    ):
        raise ValueError("session_binding_unavailable")
    for root in installation.transcript_roots:
        root_parts = str(root).split("/")[1:]
        if (
            parts[1 : 1 + len(root_parts)] == root_parts
            and len(parts) > 1 + len(root_parts)
        ):
            return root, tuple(parts[1 + len(root_parts) :])
    raise ValueError("session_binding_unavailable")


def _open_transcript_directory(root: Path, components: tuple[str, ...]) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(root), flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("session_binding_unavailable")
        for component in components:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError("session_binding_unavailable")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_transcript_file(path: Path, installation: Installation) -> int:
    root, components = _transcript_relative_path(path, installation)
    directory = _open_transcript_directory(root, components[:-1])
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(components[-1], flags, dir_fd=directory)
    finally:
        os.close(directory)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("transcript_changed")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _captured_transcript_values(captured: dict[str, object]) -> tuple[int, int, int, int]:
    names = ("size", "mtime_ns", "device", "inode")
    if any(type(captured.get(name)) is not int for name in names):
        raise ValueError("transcript_changed")
    size, mtime, device, inode = (int(captured[name]) for name in names)
    if size < 0 or mtime < 0 or device < 0 or inode < 0:
        raise ValueError("transcript_changed")
    return size, mtime, device, inode


def _open_session_prefix(
    path: Path,
    captured: dict[str, object],
    same_identity: bool,
    installation: Installation,
) -> tuple[list[object], int, int]:
    captured_size, captured_mtime, captured_device, captured_inode = (
        _captured_transcript_values(captured)
    )
    if captured_size > MAX_TRANSCRIPT_PROBE_BYTES:
        raise ValueError("oversized_transcript")
    descriptor = _open_transcript_file(path, installation)
    try:
        before = os.fstat(descriptor)
        if (
            type(before.st_mtime_ns) is not int
            or
            before.st_size < captured_size
            or (
                same_identity
                and (
                    before.st_dev != captured_device
                    or before.st_ino != captured_inode
                    or before.st_mtime_ns < captured_mtime
                    or (
                        before.st_size == captured_size
                        and before.st_mtime_ns != captured_mtime
                    )
                )
            )
        ):
            raise ValueError("transcript_changed")
        prefix = read_exact_prefix(descriptor, captured_size)
        after = os.fstat(descriptor)
        if (
            type(after.st_mtime_ns) is not int
            or
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_mode != before.st_mode
            or after.st_uid != before.st_uid
        ):
            raise ValueError("transcript_changed")
    finally:
        os.close(descriptor)
    if prefix and not prefix.endswith(b"\n"):
        raise ValueError("captured_prefix_partial_record")
    records: list[object] = []
    try:
        for raw_line in prefix.splitlines():
            if not raw_line:
                raise ValueError("unsupported_jsonl")
            records.append(json.loads(raw_line.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("unsupported_jsonl") from error
    return records, captured_size, after.st_size


def _same_inode_path(
    installation: Installation, device: int, inode: int, max_entries: int
) -> Optional[Path]:
    if type(max_entries) is not int or max_entries < 0:
        raise ValueError("session_binding_unavailable")
    visited = 0
    pending = [(root, ()) for root in reversed(installation.transcript_roots)]
    while pending:
        root, components = pending.pop()
        try:
            descriptor = _open_transcript_directory(root, components)
        except (OSError, ValueError):
            continue
        try:
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    visited += 1
                    if visited > max_entries:
                        return None
                    if entry.is_symlink():
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append((root, components + (entry.name,)))
                            continue
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if (
                        stat.S_ISREG(info.st_mode)
                        and info.st_uid == os.getuid()
                        and info.st_dev == device
                        and info.st_ino == inode
                    ):
                        return root.joinpath(*components, entry.name)
        finally:
            os.close(descriptor)
    return None


def _has_embedded_session_id(records: list[object], session_id: str) -> bool:
    return any(
        pointer.rsplit("/", 1)[-1] == "session_id" and scalar == session_id
        for record in records
        for pointer, _identity, scalar in _walk_scalar_entries(record)
    )


def resolve_session_transcript(
    observation: dict[str, object],
    installation: Installation,
    max_entries: int = MAX_TRANSCRIPT_LOOKUP_ENTRIES,
) -> ResolvedSessionTranscript:
    original = _session_event_path(observation, installation)
    captured = observation["transcript_stat"]
    event = observation["event"]
    assert isinstance(captured, dict) and isinstance(event, dict)
    try:
        _open_session_prefix(original, captured, True, installation)
    except (FileNotFoundError, OSError, ValueError):
        relocated = _same_inode_path(
            installation, int(captured["device"]), int(captured["inode"]), max_entries
        )
        if relocated is not None:
            _open_session_prefix(relocated, captured, True, installation)
            return ResolvedSessionTranscript(relocated, "same_inode_lookup", False)
        try:
            descriptor = _open_transcript_file(original, installation)
            try:
                current = os.fstat(descriptor)
            finally:
                os.close(descriptor)
        except (OSError, ValueError) as error:
            raise ValueError("session_binding_unavailable") from error
        if current.st_dev == captured["device"] and current.st_ino == captured["inode"]:
            raise ValueError("transcript_changed")
        try:
            records, _size, _current = _open_session_prefix(
                original, captured, False, installation
            )
        except (OSError, ValueError) as error:
            raise ValueError("session_binding_unavailable") from error
        if _has_embedded_session_id(records, str(event["session_id"])):
            return ResolvedSessionTranscript(original, "embedded_session_id", True)
        raise ValueError("session_binding_unavailable")
    return ResolvedSessionTranscript(original, "same_file_identity", False)


def discover_session_structure(
    records: list[object],
    session_id: str,
    require_embedded_binding: bool,
    layout_identity: Optional[dict[str, object]] = None,
) -> dict[str, object]:
    session_matches: list[tuple[str, tuple[object, ...]]] = []
    provenance: list[tuple[str, tuple[object, ...], str]] = []
    for record in records:
        for pointer, identity, scalar in _walk_scalar_entries(record):
            leaf = pointer.rsplit("/", 1)[-1]
            if leaf == "session_id" and scalar == session_id:
                session_matches.append((pointer, identity))
            if leaf in PROVENANCE_KEYS and scalar in PROVENANCE_VALUES:
                provenance.append((pointer, identity, str(scalar)))
    if require_embedded_binding and not session_matches:
        raise ValueError("session_binding_unavailable")
    if layout_identity is not None:
        layout_identity["session"] = frozenset(
            identity for _pointer, identity in session_matches
        )
        layout_identity["provenance"] = frozenset(
            identity for _pointer, identity, _value in provenance
        )
    return {
        "session_id_pointer_paths": sorted(
            {pointer for pointer, _identity in session_matches}
        ),
        "provenance_pointer_paths": sorted(
            {pointer for pointer, _identity, _value in provenance}
        ),
        "provenance_values": sorted({value for _pointer, _identity, value in provenance}),
    }


def inspect_session_structure_v2(
    observation: dict[str, object],
    surface: str,
    installation: Installation,
    layout_identity: Optional[dict[str, object]] = None,
) -> dict[str, object]:
    surface = validate_surface(surface)
    resolved = resolve_session_transcript(observation, installation)
    captured = observation["transcript_stat"]
    event = observation["event"]
    assert isinstance(captured, dict) and isinstance(event, dict)
    records, captured_size, current_size = _open_session_prefix(
        resolved.path,
        captured,
        resolved.binding_mode != "embedded_session_id",
        installation,
    )
    structure = discover_session_structure(
        records,
        str(event["session_id"]),
        resolved.binding_mode == "embedded_session_id",
        layout_identity,
    )
    return {
        "schema_version": 2,
        "surface": surface,
        "supported": True,
        "format": "jsonl",
        "record_count": len(records),
        "suffix_ignored": current_size > captured_size,
        "read_past_boundary": False,
        "binding_mode": resolved.binding_mode,
        "epoch_reset": resolved.epoch_reset,
        **structure,
    }


def safe_inspect_session_structure_v2(
    observation: dict[str, object],
    surface: str,
    installation: Installation,
    layout_identity: Optional[dict[str, object]] = None,
) -> dict[str, object]:
    try:
        return inspect_session_structure_v2(
            observation, surface, installation, layout_identity
        )
    except (KeyError, OSError, TypeError, ValueError):
        return {
            "schema_version": 2,
            "surface": surface if surface in {"cli", "desktop"} else "cli",
            "supported": False,
            "error_code": "session_transcript_unavailable",
        }


def discover_turn_structure(
    records: list[object],
    turn_id: str,
    layout_identity: Optional[dict[str, object]] = None,
) -> dict[str, object]:
    turn_matches: list[tuple[int, str, tuple[object, ...]]] = []
    provenance: list[tuple[int, str, tuple[object, ...], str]] = []
    for index, record in enumerate(records):
        for pointer, identity, scalar in _walk_scalar_entries(record):
            leaf = pointer.rsplit("/", 1)[-1]
            if scalar == turn_id:
                turn_matches.append((index, pointer, identity))
            if leaf in PROVENANCE_KEYS and scalar in PROVENANCE_VALUES:
                provenance.append((index, pointer, identity, str(scalar)))
    if not turn_matches:
        raise ValueError("turn_id_not_found")

    turn_indices = sorted({index for index, _pointer, _identity in turn_matches})
    start, end = turn_indices[0], turn_indices[-1]
    contiguous = turn_indices == list(range(start, end + 1))
    relevant_provenance = [
        (index, pointer, identity, value)
        for index, pointer, identity, value in provenance
        if start <= index <= end
    ]
    provenance_values = sorted(
        {value for _index, _pointer, _identity, value in relevant_provenance}
    )
    if not {"user", "assistant"}.issubset(provenance_values):
        raise ValueError("provenance_not_found")

    if layout_identity is not None:
        layout_identity["turn"] = frozenset(
            identity for _index, _pointer, identity in turn_matches
        )
        layout_identity["provenance"] = frozenset(
            identity
            for _index, _pointer, identity, _value in relevant_provenance
        )

    return {
        "turn_occurrence_count": len(turn_matches),
        "turn_record_span": [start, end],
        "turn_record_span_contiguous": contiguous,
        "turn_id_pointer_paths": sorted(
            {pointer for _index, pointer, _identity in turn_matches}
        ),
        "provenance_pointer_paths": sorted(
            {pointer for _index, pointer, _identity, _value in relevant_provenance}
        ),
        "provenance_values": provenance_values,
    }


def inspect_transcript_structure(
    observation: dict[str, object],
    surface: str,
    layout_identity: Optional[dict[str, object]] = None,
) -> dict[str, object]:
    records, captured_size, current_size = load_captured_records(observation)
    turn = discover_turn_structure(
        records,
        str(observation["event"]["turn_id"]),
        layout_identity,
    )
    return {
        "schema_version": 1,
        "surface": surface,
        "supported": True,
        "format": "jsonl",
        "record_count": len(records),
        "captured_size": captured_size,
        "current_size": current_size,
        "suffix_ignored": current_size > captured_size,
        "read_past_boundary": False,
        **turn,
    }


def safe_inspect_transcript_structure(
    observation: dict[str, object],
    surface: str,
    layout_identity: Optional[dict[str, object]] = None,
) -> dict[str, object]:
    if "event" not in observation or "transcript_stat" not in observation:
        code = observation.get("capture_error_code")
        return {
            "schema_version": 1,
            "surface": surface,
            "supported": False,
            "error_code": (
                str(code)
                if isinstance(code, str) and code in CAPTURE_ERROR_CODES
                else "capture_invalid"
            ),
        }
    try:
        return inspect_transcript_structure(observation, surface, layout_identity)
    except (KeyError, OSError, TypeError, ValueError) as error:
        allowed = {
            "captured_prefix_partial_record",
            "oversized_transcript",
            "provenance_not_found",
            "transcript_changed",
            "turn_id_not_found",
            "unsupported_jsonl",
        }
        code = str(error)
        return {
            "schema_version": 1,
            "surface": surface,
            "supported": False,
            "error_code": (
                code
                if isinstance(error, ValueError) and code in allowed
                else "transcript_unavailable"
            ),
        }


def load_surface_observations(
    installation: Installation,
    surface: str,
) -> list[dict[str, object]]:
    surface = validate_surface(surface)
    mapping_path = installation.data_root / "reports" / f"{surface}-observation.json"
    validate_observation(mapping_path)
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    filenames = mapping.get("observations")
    if (
        mapping.get("surface") != surface
        or not isinstance(filenames, list)
        or len(filenames) != 2
        or len(set(filenames)) != 2
    ):
        raise ValueError("invalid_surface_mapping")
    observations: list[dict[str, object]] = []
    for value in filenames:
        filename = str(value)
        if Path(filename).name != filename:
            raise ValueError("invalid_observation_name")
        observation_path = installation.data_root / "incoming" / filename
        validate_observation(observation_path)
        observations.append(json.loads(observation_path.read_text(encoding="utf-8")))
    return observations


def failed_transcript_promotion(
    surface: str,
    code: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "surface": surface,
        "observation_count": 0,
        "supported": False,
        "layouts_stable": False,
        "error_codes": [code],
    }


def promote_transcript_structure(
    installation: Installation,
    surface: str,
    output: Path,
) -> dict[str, object]:
    surface = validate_surface(surface)
    try:
        observations = load_surface_observations(installation, surface)
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        report = failed_transcript_promotion(surface, "surface_observation_unavailable")
        atomic_write_json(output.resolve(), report)
        return report
    if not all(
        observation.get("installation_nonce") == installation.nonce
        for observation in observations
    ):
        report = failed_transcript_promotion(surface, "shared_nonce_mismatch")
        atomic_write_json(output.resolve(), report)
        return report

    identities: list[dict[str, object]] = []
    individual = []
    for observation in observations:
        layout_identity: dict[str, object] = {}
        individual.append(
            safe_inspect_transcript_structure(observation, surface, layout_identity)
        )
        identities.append(layout_identity)
    all_supported = all(item.get("supported") is True for item in individual)
    turn_layouts = [
        tuple(item.get("turn_id_pointer_paths", []))
        for item in individual
    ]
    provenance_layouts = [
        tuple(item.get("provenance_pointer_paths", []))
        for item in individual
    ]
    layouts_stable = (
        all_supported
        and turn_layouts[0] == turn_layouts[1]
        and provenance_layouts[0] == provenance_layouts[1]
        and identities[0] == identities[1]
    )
    spans_contiguous = all(
        item.get("turn_record_span_contiguous") is True
        for item in individual
    )
    supported = all_supported and layouts_stable and spans_contiguous
    provenance_values = (
        sorted(
            set(individual[0].get("provenance_values", []))
            & set(individual[1].get("provenance_values", []))
        )
        if all_supported
        else []
    )
    error_codes = sorted(
        {
            str(item["error_code"])
            for item in individual
            if "error_code" in item
        }
    )
    if all_supported and not layouts_stable:
        error_codes.append("layout_unstable")
    if all_supported and not spans_contiguous:
        error_codes.append("turn_span_not_contiguous")
    report = {
        "schema_version": 1,
        "surface": surface,
        "observation_count": len(individual),
        "supported": supported,
        "layouts_stable": layouts_stable,
        "format": "jsonl" if all_supported else None,
        "read_past_boundary": any(
            item.get("read_past_boundary") is not False
            for item in individual
        ),
        "suffix_ignored": any(
            item.get("suffix_ignored") is True
            for item in individual
        ),
        "turn_occurrence_counts": [
            item.get("turn_occurrence_count", 0)
            for item in individual
        ],
        "turn_record_spans_contiguous": spans_contiguous,
        "turn_id_pointer_paths": list(turn_layouts[0]) if layouts_stable else [],
        "provenance_pointer_paths": (
            list(provenance_layouts[0]) if layouts_stable else []
        ),
        "provenance_values": provenance_values,
        "error_codes": error_codes,
    }
    atomic_write_json(output.resolve(), report)
    return report


def failed_session_transcript_promotion(surface: str, code: str) -> dict[str, object]:
    return {
        "schema_version": 2,
        "surface": surface,
        "observation_count": 0,
        "supported": False,
        "distinct_sessions": False,
        "layouts_stable": False,
        "format": None,
        "suffix_ignored": False,
        "read_past_boundary": True,
        "binding_modes": [],
        "epoch_reset": False,
        "session_id_pointer_paths": [],
        "provenance_pointer_paths": [],
        "provenance_values": [],
        "error_codes": [code],
    }


def _promote_session_transcript_v2_locked(
    installation: Installation,
    surface: str,
    output: Path,
) -> dict[str, object]:
    surface = validate_surface(surface)
    try:
        paths = session_surface_observations(installation, surface)
        observations = [read_bounded_private_json(path) for path in paths]
        if len(observations) != 2 or not all(
            valid_session_stop_observation(observation, installation)
            for observation in observations
        ):
            raise ValueError("session_transcript_unavailable")
        raw_sessions = [
            session_stop_event_session_id(observation["event"])
            for observation in observations
        ]
        if any(session_id is None for session_id in raw_sessions):
            raise ValueError("session_transcript_unavailable")
    except (OSError, TypeError, ValueError):
        report = failed_session_transcript_promotion(
            surface, "session_transcript_unavailable"
        )
        atomic_write_json(output.resolve(), report)
        return report

    identities: list[dict[str, object]] = []
    individual: list[dict[str, object]] = []
    for observation in observations:
        identity: dict[str, object] = {}
        individual.append(
            safe_inspect_session_structure_v2(
                observation, surface, installation, identity
            )
        )
        identities.append(identity)
    all_supported = all(item.get("supported") is True for item in individual)
    binding_modes = (
        sorted({str(item.get("binding_mode")) for item in individual})
        if all_supported
        else []
    )
    layouts_stable = (
        all_supported
        and identities[0] == identities[1]
        and tuple(individual[0].get("session_id_pointer_paths", []))
        == tuple(individual[1].get("session_id_pointer_paths", []))
        and tuple(individual[0].get("provenance_pointer_paths", []))
        == tuple(individual[1].get("provenance_pointer_paths", []))
    )
    distinct_sessions = len(set(raw_sessions)) == 2
    provenance_values = (
        sorted(
            set(individual[0].get("provenance_values", []))
            & set(individual[1].get("provenance_values", []))
        )
        if all_supported else []
    )
    errors = sorted(
        {str(item["error_code"]) for item in individual if "error_code" in item}
    )
    if all_supported and not layouts_stable:
        errors.append("layout_unstable")
    if not distinct_sessions:
        errors.append("session_binding_unavailable")
    supported = (
        all_supported
        and layouts_stable
        and distinct_sessions
        and set(binding_modes).issubset(
            {"same_file_identity", "same_inode_lookup", "embedded_session_id"}
        )
        and {"user", "assistant"}.issubset(provenance_values)
        and all(item.get("read_past_boundary") is False for item in individual)
    )
    if all_supported and not {"user", "assistant"}.issubset(provenance_values):
        errors.append("provenance_not_found")
    report = {
        "schema_version": 2,
        "surface": surface,
        "observation_count": len(individual),
        "supported": supported,
        "distinct_sessions": distinct_sessions,
        "layouts_stable": layouts_stable,
        "format": "jsonl" if all_supported else None,
        "suffix_ignored": any(item.get("suffix_ignored") is True for item in individual),
        "read_past_boundary": any(item.get("read_past_boundary") is not False for item in individual),
        "binding_modes": binding_modes,
        "epoch_reset": any(item.get("epoch_reset") is True for item in individual),
        "session_id_pointer_paths": (
            list(individual[0].get("session_id_pointer_paths", []))
            if layouts_stable
            else []
        ),
        "provenance_pointer_paths": (
            list(individual[0].get("provenance_pointer_paths", []))
            if layouts_stable
            else []
        ),
        "provenance_values": provenance_values,
        "error_codes": sorted(set(errors)),
    }
    atomic_write_json(output.resolve(), report)
    return report


def promote_session_transcript_v2(
    installation: Installation,
    surface: str,
    output: Path,
) -> dict[str, object]:
    with v2_state_lock(installation, exclusive=False):
        return _promote_session_transcript_v2_locked(installation, surface, output)


def is_safe_payload_key(value: object) -> bool:
    return isinstance(value, str) and value in REVIEWED_STOP_PAYLOAD_KEYS


def is_safe_pointer_path(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or len(value) > 4_096
        or not value.isascii()
    ):
        return False
    return all(
        segment in POINTER_SEGMENT_ALLOWLIST
        or segment.isdecimal()
        or (
            segment.startswith("_redacted_")
            and segment.removeprefix("_redacted_").isdecimal()
        )
        for segment in value[1:].split("/")
    )


def stop_fixture_passes(
    fixture: dict[str, object],
    expected_surface: str,
) -> bool:
    required_names = {
        "hook_event_name",
        "session_id",
        "turn_id",
        "cwd",
        "transcript_path",
    }
    transcript_stat_names = {
        "present",
        "regular",
        "owned_by_current_user",
        "size_positive",
        "has_mtime_ns",
        "has_device",
        "has_inode",
    }
    payload_keys = fixture.get("payload_keys")
    field_types = fixture.get("field_types")
    required = fixture.get("required_fields")
    capture_errors = fixture.get("capture_error_codes")
    transcript_stat = fixture.get("transcript_stat")
    return (
        set(fixture)
        == {
            "schema_version",
            "surface",
            "observation_count",
            "capture_supported",
            "distinct_turns",
            "hook_event_name",
            "payload_shapes_stable",
            "payload_keys",
            "field_types",
            "required_fields",
            "capture_error_codes",
            "transcript_stat",
            "shared_nonce_match",
        }
        and type(fixture.get("schema_version")) is int
        and fixture["schema_version"] == 1
        and fixture.get("surface") == expected_surface
        and type(fixture.get("observation_count")) is int
        and fixture["observation_count"] == 2
        and fixture.get("capture_supported") is True
        and fixture.get("distinct_turns") is True
        and fixture.get("payload_shapes_stable") is True
        and fixture.get("hook_event_name") == "Stop"
        and fixture.get("shared_nonce_match") is True
        and isinstance(payload_keys, list)
        and all(is_safe_payload_key(value) for value in payload_keys)
        and len(set(payload_keys)) == len(payload_keys)
        and set(payload_keys) == REVIEWED_STOP_PAYLOAD_KEYS
        and isinstance(field_types, dict)
        and all(
            is_safe_payload_key(key)
            and isinstance(value, str)
            and value in HOOK_FIELD_TYPE_NAMES
            for key, value in field_types.items()
        )
        and set(field_types) == set(payload_keys)
        and all(field_types.get(name) == "str" for name in required_names)
        and isinstance(required, dict)
        and set(required) == required_names
        and all(
            isinstance(value, dict)
            and set(value) == {"present", "type", "valid"}
            and value.get("present") is True
            and value.get("type") == "str"
            and value.get("valid") is True
            for value in required.values()
        )
        and isinstance(capture_errors, list)
        and not capture_errors
        and isinstance(transcript_stat, dict)
        and set(transcript_stat) == transcript_stat_names
        and all(transcript_stat.get(name) is True for name in transcript_stat_names)
    )


def transcript_fixture_passes(
    fixture: dict[str, object],
    expected_surface: str,
) -> bool:
    occurrences = fixture.get("turn_occurrence_counts")
    turn_paths = fixture.get("turn_id_pointer_paths")
    provenance_paths = fixture.get("provenance_pointer_paths")
    provenance_values = fixture.get("provenance_values")
    error_codes = fixture.get("error_codes")
    return (
        set(fixture)
        == {
            "schema_version",
            "surface",
            "observation_count",
            "supported",
            "layouts_stable",
            "format",
            "suffix_ignored",
            "read_past_boundary",
            "turn_occurrence_counts",
            "turn_record_spans_contiguous",
            "turn_id_pointer_paths",
            "provenance_pointer_paths",
            "provenance_values",
            "error_codes",
        }
        and type(fixture.get("schema_version")) is int
        and fixture["schema_version"] == 1
        and fixture.get("surface") == expected_surface
        and type(fixture.get("observation_count")) is int
        and fixture["observation_count"] == 2
        and fixture.get("supported") is True
        and fixture.get("layouts_stable") is True
        and fixture.get("format") == "jsonl"
        and fixture.get("suffix_ignored") is True
        and fixture.get("read_past_boundary") is False
        and isinstance(occurrences, list)
        and len(occurrences) == 2
        and all(type(value) is int and value > 0 for value in occurrences)
        and fixture.get("turn_record_spans_contiguous") is True
        and isinstance(turn_paths, list)
        and bool(turn_paths)
        and all(is_safe_pointer_path(value) for value in turn_paths)
        and len(set(turn_paths)) == len(turn_paths)
        and isinstance(provenance_paths, list)
        and bool(provenance_paths)
        and all(is_safe_pointer_path(value) for value in provenance_paths)
        and len(set(provenance_paths)) == len(provenance_paths)
        and isinstance(provenance_values, list)
        and all(isinstance(value, str) for value in provenance_values)
        and set(provenance_values).issubset(PROVENANCE_VALUES)
        and {"user", "assistant"}.issubset(set(provenance_values))
        and isinstance(error_codes, list)
        and not error_codes
    )


def access_fixture_passes(
    fixture: dict[str, object],
    expected_surface: str,
) -> bool:
    return (
        set(fixture) == {"schema_version", "surface", "read", "write"}
        and type(fixture.get("schema_version")) is int
        and fixture["schema_version"] == 1
        and fixture.get("surface") == expected_surface
        and fixture.get("read") is True
        and fixture.get("write") is True
    )


def session_stop_fixture_v2_passes(
    fixture: dict[str, object],
    expected_surface: str,
) -> bool:
    if type(fixture) is not dict or type(expected_surface) is not str:
        return False
    required = fixture.get("required_fields")
    field_types = fixture.get("field_types")
    payload_keys = fixture.get("payload_keys")
    transcript_stat = fixture.get("transcript_stat")
    stat_names = {
        "present", "regular", "owned_by_current_user", "size_positive",
        "has_mtime_ns", "has_device", "has_inode",
    }
    return (
        set(fixture)
        == {
            "schema_version", "surface", "observation_count", "capture_supported",
            "distinct_sessions", "turn_id_optional", "hook_event_name",
            "payload_shapes_stable", "payload_keys", "field_types",
            "required_fields", "capture_error_codes", "transcript_stat",
            "shared_nonce_match",
        }
        and type(fixture.get("schema_version")) is int
        and fixture["schema_version"] == 2
        and type(fixture.get("surface")) is str
        and fixture["surface"] == expected_surface
        and type(fixture.get("observation_count")) is int
        and fixture["observation_count"] == 2
        and fixture.get("capture_supported") is True
        and fixture.get("distinct_sessions") is True
        and fixture.get("turn_id_optional") is True
        and fixture.get("hook_event_name") == "Stop"
        and fixture.get("payload_shapes_stable") is True
        and type(payload_keys) is list
        and all(type(value) is str for value in payload_keys)
        and set(payload_keys) == V2_SESSION_STOP_KEYS
        and len(payload_keys) == len(V2_SESSION_STOP_KEYS)
        and type(field_types) is dict
        and set(field_types) == V2_SESSION_STOP_KEYS
        and all(type(value) is str and value == "str" for value in field_types.values())
        and type(required) is dict
        and set(required) == V2_SESSION_STOP_KEYS
        and all(
            type(value) is dict
            and set(value) == {"present", "type", "valid"}
            and value.get("present") is True
            and value.get("type") == "str"
            and value.get("valid") is True
            for value in required.values()
        )
        and type(fixture.get("capture_error_codes")) is list
        and fixture["capture_error_codes"] == []
        and type(transcript_stat) is dict
        and set(transcript_stat) == stat_names
        and all(transcript_stat.get(name) is True for name in stat_names)
        and fixture.get("shared_nonce_match") is True
    )


def access_fixture_v2_passes(
    fixture: dict[str, object],
    expected_surface: str,
) -> bool:
    if type(fixture) is not dict or type(expected_surface) is not str:
        return False
    return (
        set(fixture)
        == {
            "schema_version", "surface", "hook_global_read", "hook_global_write",
            "skill_default_read", "skill_default_write",
            "skill_default_write_denied", "skill_explicit_read",
            "skill_explicit_write", "error_codes",
        }
        and type(fixture.get("schema_version")) is int
        and fixture["schema_version"] == 2
        and type(fixture.get("surface")) is str
        and fixture["surface"] == expected_surface
        and fixture.get("hook_global_read") is True
        and fixture.get("hook_global_write") is True
        and fixture.get("skill_default_read") is True
        and fixture.get("skill_default_write") is False
        and fixture.get("skill_default_write_denied") is True
        and fixture.get("skill_explicit_read") is True
        and fixture.get("skill_explicit_write") is True
        and type(fixture.get("error_codes")) is list
        and fixture["error_codes"] == []
    )


def session_transcript_fixture_v2_passes(
    fixture: dict[str, object],
    expected_surface: str,
) -> bool:
    if type(fixture) is not dict or type(expected_surface) is not str:
        return False
    binding_modes = fixture.get("binding_modes")
    session_paths = fixture.get("session_id_pointer_paths")
    provenance_paths = fixture.get("provenance_pointer_paths")
    provenance_values = fixture.get("provenance_values")
    return (
        set(fixture)
        == {
            "schema_version", "surface", "observation_count", "supported",
            "distinct_sessions", "layouts_stable", "format", "suffix_ignored",
            "read_past_boundary", "binding_modes", "epoch_reset",
            "session_id_pointer_paths", "provenance_pointer_paths",
            "provenance_values", "error_codes",
        }
        and type(fixture.get("schema_version")) is int
        and fixture["schema_version"] == 2
        and type(fixture.get("surface")) is str
        and fixture["surface"] == expected_surface
        and type(fixture.get("observation_count")) is int
        and fixture["observation_count"] == 2
        and fixture.get("supported") is True
        and fixture.get("distinct_sessions") is True
        and fixture.get("layouts_stable") is True
        and fixture.get("format") == "jsonl"
        and fixture.get("suffix_ignored") is True
        and fixture.get("read_past_boundary") is False
        and type(binding_modes) is list
        and bool(binding_modes)
        and all(type(value) is str and value in V2_TRANSCRIPT_BINDING_MODES for value in binding_modes)
        and len(binding_modes) == len(set(binding_modes))
        and type(fixture.get("epoch_reset")) is bool
        and type(session_paths) is list
        and bool(session_paths)
        and all(is_safe_pointer_path(value) for value in session_paths)
        and len(session_paths) == len(set(session_paths))
        and type(provenance_paths) is list
        and bool(provenance_paths)
        and all(is_safe_pointer_path(value) for value in provenance_paths)
        and len(provenance_paths) == len(set(provenance_paths))
        and type(provenance_values) is list
        and all(type(value) is str and value in PROVENANCE_VALUES for value in provenance_values)
        and {"user", "assistant"}.issubset(set(provenance_values))
        and type(fixture.get("error_codes")) is list
        and fixture["error_codes"] == []
    )


def evaluate_feasibility_gate_v2(
    cli_stop: dict[str, object],
    desktop_stop: dict[str, object],
    cli_transcript: dict[str, object],
    desktop_transcript: dict[str, object],
    cli_access: dict[str, object],
    desktop_access: dict[str, object],
    predecessor_sha256: str,
) -> dict[str, object]:
    checks = {
        "cli_session_stop_contract": session_stop_fixture_v2_passes(cli_stop, "cli"),
        "desktop_session_stop_contract": session_stop_fixture_v2_passes(desktop_stop, "desktop"),
        "cli_asymmetric_access": access_fixture_v2_passes(cli_access, "cli"),
        "desktop_asymmetric_access": access_fixture_v2_passes(desktop_access, "desktop"),
        "cli_session_transcript_supported": session_transcript_fixture_v2_passes(cli_transcript, "cli"),
        "desktop_session_transcript_supported": session_transcript_fixture_v2_passes(desktop_transcript, "desktop"),
    }

    def safe_list(value: object, validator) -> list[str]:
        return [item for item in value if validator(item)] if type(value) is list else []

    def surface_summary(stop: dict[str, object], transcript: dict[str, object]) -> dict[str, object]:
        return {
            "stop_keys": safe_list(stop.get("payload_keys"), is_safe_payload_key),
            "session_id_pointer_paths": safe_list(
                transcript.get("session_id_pointer_paths"), is_safe_pointer_path
            ),
            "provenance_pointer_paths": safe_list(
                transcript.get("provenance_pointer_paths"), is_safe_pointer_path
            ),
            "binding_modes": safe_list(
                transcript.get("binding_modes"),
                lambda value: type(value) is str and value in V2_TRANSCRIPT_BINDING_MODES,
            ),
        }

    return {
        "schema_version": 2,
        "decision": "PASS" if all(value is True for value in checks.values()) else "FAIL",
        "checks": checks,
        "predecessor": {
            "path": V2_PREDECESSOR_PATH,
            "sha256": predecessor_sha256,
        },
        "surfaces": {
            "cli": surface_summary(cli_stop, cli_transcript),
            "desktop": surface_summary(desktop_stop, desktop_transcript),
        },
        "next_action": (
            "write_session_runtime_queue_plan"
            if all(value is True for value in checks.values())
            else "amend_design_for_session_level_queue"
        ),
    }


def structural_differences(
    cli_stop: dict[str, object],
    desktop_stop: dict[str, object],
    cli_transcript: dict[str, object],
    desktop_transcript: dict[str, object],
) -> dict[str, object]:
    def only(
        left: object,
        right: object,
        validator,
    ) -> list[str]:
        left_values = {
            value for value in left if validator(value)
        } if isinstance(left, list) else set()
        right_values = {
            value for value in right if validator(value)
        } if isinstance(right, list) else set()
        return sorted(left_values - right_values)

    return {
        "stop_keys_only_cli": only(
            cli_stop.get("payload_keys"),
            desktop_stop.get("payload_keys"),
            is_safe_payload_key,
        ),
        "stop_keys_only_desktop": only(
            desktop_stop.get("payload_keys"),
            cli_stop.get("payload_keys"),
            is_safe_payload_key,
        ),
        "turn_paths_only_cli": only(
            cli_transcript.get("turn_id_pointer_paths"),
            desktop_transcript.get("turn_id_pointer_paths"),
            is_safe_pointer_path,
        ),
        "turn_paths_only_desktop": only(
            desktop_transcript.get("turn_id_pointer_paths"),
            cli_transcript.get("turn_id_pointer_paths"),
            is_safe_pointer_path,
        ),
        "provenance_paths_only_cli": only(
            cli_transcript.get("provenance_pointer_paths"),
            desktop_transcript.get("provenance_pointer_paths"),
            is_safe_pointer_path,
        ),
        "provenance_paths_only_desktop": only(
            desktop_transcript.get("provenance_pointer_paths"),
            cli_transcript.get("provenance_pointer_paths"),
            is_safe_pointer_path,
        ),
    }


def evaluate_feasibility_gate(
    cli_stop: dict[str, object],
    desktop_stop: dict[str, object],
    cli_transcript: dict[str, object],
    desktop_transcript: dict[str, object],
    cli_access: dict[str, object],
    desktop_access: dict[str, object],
) -> dict[str, object]:
    checks = {
        "cli_stop_contract": stop_fixture_passes(cli_stop, "cli"),
        "desktop_stop_contract": stop_fixture_passes(desktop_stop, "desktop"),
        "cli_shared_data_root": cli_stop.get("shared_nonce_match") is True,
        "desktop_shared_data_root": desktop_stop.get("shared_nonce_match") is True,
        "cli_skill_data_root": access_fixture_passes(cli_access, "cli"),
        "desktop_skill_data_root": access_fixture_passes(desktop_access, "desktop"),
        "cli_transcript_supported": transcript_fixture_passes(
            cli_transcript,
            "cli",
        ),
        "desktop_transcript_supported": transcript_fixture_passes(
            desktop_transcript,
            "desktop",
        ),
    }
    decision = "PASS" if all(checks.values()) else "FAIL"

    def string_list(value: object, validator) -> list[str]:
        return (
            [item for item in value if validator(item)]
            if isinstance(value, list)
            else []
        )

    return {
        "schema_version": 1,
        "decision": decision,
        "checks": checks,
        "schema_differences": structural_differences(
            cli_stop,
            desktop_stop,
            cli_transcript,
            desktop_transcript,
        ),
        "surfaces": {
            "cli": {
                "stop_keys": string_list(
                    cli_stop.get("payload_keys"),
                    is_safe_payload_key,
                ),
                "turn_id_pointer_paths": string_list(
                    cli_transcript.get("turn_id_pointer_paths"),
                    is_safe_pointer_path,
                ),
                "provenance_pointer_paths": string_list(
                    cli_transcript.get("provenance_pointer_paths"),
                    is_safe_pointer_path,
                ),
            },
            "desktop": {
                "stop_keys": string_list(
                    desktop_stop.get("payload_keys"),
                    is_safe_payload_key,
                ),
                "turn_id_pointer_paths": string_list(
                    desktop_transcript.get("turn_id_pointer_paths"),
                    is_safe_pointer_path,
                ),
                "provenance_pointer_paths": string_list(
                    desktop_transcript.get("provenance_pointer_paths"),
                    is_safe_pointer_path,
                ),
            },
        },
        "next_action": (
            "write_read_only_mvp_plan"
            if decision == "PASS"
            else "amend_design_for_session_level_queue"
        ),
    }


def render_gate_markdown(report: dict[str, object]) -> str:
    lines = [
        "# Skill Evolver Feasibility Report",
        "",
        f"Decision: **{report['decision']}**",
        "",
        "The gate checks Codex CLI and Desktop against the same bounded Stop and transcript contract.",
        "",
        "## Checks",
        "",
    ]
    for name, passed in report["checks"].items():
        lines.append(f"- [{'x' if passed else ' '}] `{name}`")
    lines.extend(["", "## CLI/Desktop schema differences", ""])
    for name, values in report["schema_differences"].items():
        lines.append(
            f"- `{name}`: `{json.dumps(values, ensure_ascii=False, sort_keys=True)}`"
        )
    lines.extend(
        [
            "",
            "## Next action",
            "",
            (
                "Write the Read-only MVP plan using the recorded JSON-pointer paths."
                if report["decision"] == "PASS"
                else "Stop implementation and amend the design to use a session-level queue."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def atomic_write_text(path: Path, value: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, mode)
        fsync_directory(path.parent)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def reject_symlink_components(path: Path, error_code: str) -> None:
    absolute = Path(os.path.abspath(str(path.expanduser())))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        if current.is_symlink():
            raise ValueError(error_code)
        if not current.exists():
            return


def resolve_report_output(path: Path) -> Path:
    requested = Path(os.path.abspath(str(path.expanduser())))
    reject_symlink_components(
        requested.parent,
        "report_output_parent_symlink",
    )
    if requested.is_symlink():
        raise ValueError("report_output_symlink")
    parent = requested.parent
    cursor = parent
    while not cursor.exists():
        if cursor.is_symlink():
            raise ValueError("report_output_parent_symlink")
        if cursor == cursor.parent:
            raise ValueError("report_output_parent_missing")
        cursor = cursor.parent
    if cursor.is_symlink():
        raise ValueError("report_output_parent_symlink")
    parent_info = cursor.lstat()
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != os.getuid()
        or stat.S_IMODE(parent_info.st_mode) & 0o022
    ):
        raise ValueError("report_output_parent_permissions")
    if requested.exists():
        info = requested.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("report_output_not_regular")
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
            raise ValueError("report_output_permissions")
    return requested


def paths_alias(left: Path, right: Path) -> bool:
    if path_identity(left) == path_identity(right):
        return True
    try:
        return left.exists() and right.exists() and left.samefile(right)
    except OSError:
        return False


def _stable_private_file_bytes(path: Path, *, fixture: bool) -> bytes:
    try:
        info = path.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
        ):
            raise ValueError("gate_inputs_invalid")
        mode = stat.S_IMODE(info.st_mode)
        if info.st_uid != os.getuid() or (mode != 0o600 if fixture else mode & 0o022):
            raise ValueError("gate_inputs_invalid")
        if info.st_size < 0 or info.st_size > MAX_GATE_FIXTURE_BYTES:
            raise ValueError("gate_inputs_invalid")
        descriptor = os.open(
            str(path), os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != info.st_uid
                or stat.S_IMODE(before.st_mode) != mode
                or before.st_dev != info.st_dev
                or before.st_ino != info.st_ino
                or before.st_size != info.st_size
                or before.st_mtime_ns != info.st_mtime_ns
                or before.st_nlink != 1
            ):
                raise ValueError("gate_inputs_invalid")
            raw = read_exact_prefix(descriptor, info.st_size)
            after = os.fstat(descriptor)
            if (
                after.st_dev != before.st_dev
                or after.st_ino != before.st_ino
                or after.st_size != before.st_size
                or after.st_mtime_ns != before.st_mtime_ns
                or after.st_mode != before.st_mode
                or after.st_uid != before.st_uid
                or after.st_nlink != 1
            ):
                raise ValueError("gate_inputs_invalid")
            return raw
        finally:
            os.close(descriptor)
    except (OSError, ValueError):
        raise ValueError("gate_inputs_invalid") from None


def _v2_fixture_inventory(fixture_root: Path) -> tuple[Path, dict[str, dict[str, object]]]:
    requested = Path(os.path.abspath(str(fixture_root.expanduser())))
    try:
        reject_symlink_components(requested, "gate_inputs_invalid")
        root = requested.resolve(strict=True)
        root_info = root.stat()
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or root_info.st_uid != os.getuid()
            or stat.S_IMODE(root_info.st_mode) & 0o022
        ):
            raise ValueError("gate_inputs_invalid")
        names = {path.name for path in root.iterdir()}
        if names != set(GATE_V2_FIXTURE_NAMES):
            raise ValueError("gate_inputs_invalid")
        paths = [root / name for name in GATE_V2_FIXTURE_NAMES]
        identities = {(path.stat().st_dev, path.stat().st_ino) for path in paths}
        if len(identities) != len(paths):
            raise ValueError("gate_inputs_invalid")
        fixtures: dict[str, dict[str, object]] = {}
        for path in paths:
            value = json.loads(_stable_private_file_bytes(path, fixture=True).decode("utf-8"))
            if type(value) is not dict:
                raise ValueError("gate_inputs_invalid")
            expected_surface = "desktop" if "desktop" in path.name else "cli"
            if value.get("surface") != expected_surface:
                raise ValueError("gate_inputs_invalid")
            fixtures[path.name] = value
        return root, fixtures
    except (OSError, RecursionError, TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise ValueError("gate_inputs_invalid") from None


def v2_predecessor_binding() -> tuple[Path, str]:
    return (
        Path(__file__).resolve().parents[3] / V2_PREDECESSOR_PATH,
        V2_PREDECESSOR_SHA256,
    )


def _v2_predecessor_digest(predecessor_json: Path) -> str:
    requested = Path(os.path.abspath(str(predecessor_json.expanduser())))
    try:
        reject_symlink_components(requested, "gate_inputs_invalid")
        expected_path, expected_digest = v2_predecessor_binding()
        if requested != expected_path:
            raise ValueError("gate_inputs_invalid")
        raw = _stable_private_file_bytes(requested, fixture=False)
        value = json.loads(raw.decode("utf-8"))
        if (
            type(value) is not dict
            or set(value)
            != {
                "checks", "decision", "next_action", "schema_differences",
                "schema_version", "surfaces",
            }
            or type(value.get("schema_version")) is not int
            or value["schema_version"] != 1
            or value.get("decision") != "FAIL"
            or value.get("checks") != V1_PREDECESSOR_CHECKS
        ):
            raise ValueError("gate_inputs_invalid")
        digest = hashlib.sha256(raw).hexdigest()
        if digest != expected_digest:
            raise ValueError("gate_inputs_invalid")
        return digest
    except (RecursionError, TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise ValueError("gate_inputs_invalid") from None


def render_gate_markdown_v2(report: dict[str, object]) -> str:
    checks = report.get("checks")
    lines = [
        "# Skill Evolver Session Feasibility Report",
        "",
        f"Decision: **{report.get('decision', 'FAIL')}**",
        "",
        "## Checks",
        "",
    ]
    if type(checks) is dict:
        for name, passed in checks.items():
            if type(name) is str and type(passed) is bool:
                lines.append(f"- [{'x' if passed else ' '}] `{name}`")
    lines.extend(["", "## Predecessor", ""])
    predecessor = report.get("predecessor")
    if type(predecessor) is dict and type(predecessor.get("sha256")) is str:
        lines.append(f"- `{V2_PREDECESSOR_PATH}` SHA-256: `{predecessor['sha256']}`")
    lines.extend(["", "## Next action", "", "Use the recorded session contract only after a PASS.", ""])
    return "\n".join(lines)


def _stage_gate_v2_file(parent: Path, name: str, raw: bytes, mode: int) -> Path:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{name}.", dir=parent)
    staged = Path(temporary)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        return staged
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        staged.unlink(missing_ok=True)
        raise


def _stage_gate_v2_backup(path: Path, parent: Path) -> tuple[Optional[Path], Optional[int]]:
    if not path.exists():
        return None, None
    info = path.lstat()
    return (
        _stage_gate_v2_file(
            parent,
            f"{path.name}.backup",
            _stable_private_file_bytes(path, fixture=False),
            stat.S_IMODE(info.st_mode),
        ),
        stat.S_IMODE(info.st_mode),
    )


def _gate_v2_transaction_name(value: object, destination: Path, kind: str) -> bool:
    return (
        type(value) is str
        and Path(value).name == value
        and value.startswith(f".{destination.name}.{kind}.")
        and len(value) <= 255
    )


def _gate_v2_parent_lock(parent: Path):
    descriptor = os.open(
        str(parent),
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise ValueError("gate_inputs_invalid")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _gate_v2_transaction_payload(
    json_path: Path,
    markdown_path: Path,
    stages: dict[Path, Path],
    backups: dict[Path, tuple[Optional[Path], Optional[int]]],
) -> dict[str, object]:
    def entry(path: Path) -> dict[str, object]:
        backup, prior_mode = backups[path]
        return {
            "stage": stages[path].name,
            "backup": backup.name if backup is not None else None,
            "prior_present": backup is not None,
            "prior_mode": prior_mode,
        }

    return {
        "schema_version": 1,
        "kind": "gate_v2_pair_transaction",
        "json": entry(json_path),
        "markdown": entry(markdown_path),
    }


def _load_gate_v2_transaction(
    parent: Path,
    json_path: Path,
    markdown_path: Path,
) -> Optional[dict[str, dict[str, object]]]:
    marker = parent / GATE_V2_TRANSACTION_NAME
    if not marker.exists() and not marker.is_symlink():
        return None
    try:
        value = json.loads(_stable_private_file_bytes(marker, fixture=True).decode("utf-8"))
        if (
            type(value) is not dict
            or set(value) != {"schema_version", "kind", "json", "markdown"}
            or type(value.get("schema_version")) is not int
            or value["schema_version"] != 1
            or value.get("kind") != "gate_v2_pair_transaction"
        ):
            raise ValueError("gate_inputs_invalid")
        entries: dict[str, dict[str, object]] = {}
        for name, destination in (("json", json_path), ("markdown", markdown_path)):
            entry = value.get(name)
            if (
                type(entry) is not dict
                or set(entry) != {"stage", "backup", "prior_present", "prior_mode"}
                or not _gate_v2_transaction_name(entry.get("stage"), destination, "stage")
                or type(entry.get("prior_present")) is not bool
            ):
                raise ValueError("gate_inputs_invalid")
            prior_present = entry["prior_present"]
            backup = entry.get("backup")
            prior_mode = entry.get("prior_mode")
            if prior_present:
                if (
                    not _gate_v2_transaction_name(backup, destination, "backup")
                    or type(prior_mode) is not int
                    or prior_mode & 0o022
                    or prior_mode < 0
                    or prior_mode > 0o777
                ):
                    raise ValueError("gate_inputs_invalid")
            elif backup is not None or prior_mode is not None:
                raise ValueError("gate_inputs_invalid")
            entries[name] = entry
        return entries
    except (OSError, RecursionError, TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise ValueError("gate_inputs_invalid") from None


def _clean_gate_v2_transaction_file(parent: Path, name: object) -> None:
    if type(name) is not str:
        return
    path = parent / name
    if not path.exists() and not path.is_symlink():
        return
    info = path.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        raise ValueError("gate_inputs_invalid")
    path.unlink()


def _recover_gate_v2_transaction(
    parent: Path,
    json_path: Path,
    markdown_path: Path,
) -> bool:
    entries = _load_gate_v2_transaction(parent, json_path, markdown_path)
    if entries is None:
        return False
    marker = parent / GATE_V2_TRANSACTION_NAME
    restore_stages: dict[Path, Path] = {}
    backups: dict[Path, bytes] = {}
    destinations = (("json", json_path), ("markdown", markdown_path))
    try:
        for name, destination in destinations:
            entry = entries[name]
            if entry["prior_present"] is True:
                backup = parent / str(entry["backup"])
                info = backup.lstat()
                if stat.S_IMODE(info.st_mode) != int(entry["prior_mode"]):
                    raise ValueError("gate_inputs_invalid")
                backups[destination] = _stable_private_file_bytes(backup, fixture=False)
        for name, destination in destinations:
            entry = entries[name]
            if entry["prior_present"] is True:
                restore_stages[destination] = _stage_gate_v2_file(
                    parent,
                    f"{destination.name}.restore",
                    backups[destination],
                    int(entry["prior_mode"]),
                )
        fsync_directory(parent)
        for name, destination in destinations:
            entry = entries[name]
            if entry["prior_present"] is True:
                os.replace(restore_stages[destination], destination)
                os.chmod(destination, int(entry["prior_mode"]))
            else:
                destination.unlink(missing_ok=True)
        fsync_directory(parent)
        marker.unlink()
        fsync_directory(parent)
        for entry in entries.values():
            _clean_gate_v2_transaction_file(parent, entry["stage"])
            _clean_gate_v2_transaction_file(parent, entry["backup"])
        fsync_directory(parent)
        return True
    except OSError:
        raise ValueError("gate_inputs_invalid") from None
    finally:
        for stage in restore_stages.values():
            stage.unlink(missing_ok=True)


def _publish_gate_v2_pair(
    json_path: Path,
    json_raw: bytes,
    markdown_path: Path,
    markdown_raw: bytes,
) -> None:
    parent = json_path.parent
    stages: dict[Path, Path] = {}
    backups: dict[Path, tuple[Optional[Path], Optional[int]]] = {}
    modes = {json_path: 0o600, markdown_path: 0o644}
    lock = _gate_v2_parent_lock(parent)
    try:
        _recover_gate_v2_transaction(parent, json_path, markdown_path)
        stages[json_path] = _stage_gate_v2_file(
            parent, f"{json_path.name}.stage", json_raw, modes[json_path]
        )
        stages[markdown_path] = _stage_gate_v2_file(
            parent, f"{markdown_path.name}.stage", markdown_raw, modes[markdown_path]
        )
        fsync_directory(parent)
        backups[json_path] = _stage_gate_v2_backup(json_path, parent)
        backups[markdown_path] = _stage_gate_v2_backup(markdown_path, parent)
        fsync_directory(parent)
        atomic_write_json(
            parent / GATE_V2_TRANSACTION_NAME,
            _gate_v2_transaction_payload(json_path, markdown_path, stages, backups),
            mode=0o600,
        )
        for destination in (json_path, markdown_path):
            os.replace(stages[destination], destination)
            os.chmod(destination, modes[destination])
            fsync_directory(parent)
        (parent / GATE_V2_TRANSACTION_NAME).unlink()
        fsync_directory(parent)
        for backup, _mode in backups.values():
            if backup is not None:
                _clean_gate_v2_transaction_file(parent, backup.name)
        fsync_directory(parent)
    except BaseException:
        if (parent / GATE_V2_TRANSACTION_NAME).exists() or (parent / GATE_V2_TRANSACTION_NAME).is_symlink():
            _recover_gate_v2_transaction(parent, json_path, markdown_path)
        raise
    finally:
        for staged in stages.values():
            staged.unlink(missing_ok=True)
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)


def write_gate_report_v2(
    fixture_root: Path,
    predecessor_json: Path,
    output_json: Path,
    output_markdown: Path,
) -> dict[str, object]:
    json_path = resolve_report_output(output_json)
    markdown_path = resolve_report_output(output_markdown)
    if (
        paths_alias(json_path, markdown_path)
        or path_identity(json_path.parent) != path_identity(markdown_path.parent)
        or json_path.name != GATE_V2_JSON_NAME
        or markdown_path.name != GATE_V2_MARKDOWN_NAME
    ):
        raise ValueError("gate_inputs_invalid")
    root, fixtures = _v2_fixture_inventory(fixture_root)
    predecessor = Path(os.path.abspath(str(predecessor_json.expanduser())))
    fixture_paths = [root / name for name in GATE_V2_FIXTURE_NAMES]
    if any(
        paths_alias(output, protected)
        for output in (json_path, markdown_path)
        for protected in [*fixture_paths, predecessor]
    ):
        raise ValueError("gate_inputs_invalid")
    digest = _v2_predecessor_digest(predecessor)
    report = evaluate_feasibility_gate_v2(
        fixtures["session-stop-cli.v2.structure.json"],
        fixtures["session-stop-desktop.v2.structure.json"],
        fixtures["session-transcript-cli.v2.structure.json"],
        fixtures["session-transcript-desktop.v2.structure.json"],
        fixtures["access-cli.v2.structure.json"],
        fixtures["access-desktop.v2.structure.json"],
        digest,
    )
    json_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    json_path = resolve_report_output(json_path)
    markdown_path = resolve_report_output(markdown_path)
    _publish_gate_v2_pair(
        json_path,
        (json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
        markdown_path,
        render_gate_markdown_v2(report).encode("utf-8"),
    )
    return report


def write_gate_report(
    fixture_root: Path,
    output_json: Path,
    output_markdown: Path,
) -> dict[str, object]:
    json_path = resolve_report_output(output_json)
    markdown_path = resolve_report_output(output_markdown)
    if paths_alias(json_path, markdown_path):
        raise ValueError("report_output_alias")
    fixture_base = fixture_root.expanduser().resolve(strict=False)
    fixture_paths = [
        fixture_base / name
        for name in GATE_FIXTURE_NAMES
    ]
    if any(
        paths_alias(output, fixture)
        for output in (json_path, markdown_path)
        for fixture in fixture_paths
    ):
        raise ValueError("report_output_alias")

    def load(root: Path, name: str) -> dict[str, object]:
        path = root / name
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("gate_fixture_symlink")
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("gate_fixture_not_regular")
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
            raise ValueError("gate_fixture_permissions")
        if info.st_size > MAX_GATE_FIXTURE_BYTES:
            raise ValueError("gate_fixture_too_large")
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(str(path), flags)
        try:
            opened = os.fstat(descriptor)
            if (
                opened.st_dev != info.st_dev
                or opened.st_ino != info.st_ino
                or opened.st_size != info.st_size
            ):
                raise ValueError("gate_fixture_changed")
            raw = read_exact_prefix(descriptor, info.st_size)
            if os.fstat(descriptor).st_size != info.st_size:
                raise ValueError("gate_fixture_changed")
        finally:
            os.close(descriptor)
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("gate_fixture_not_object")
        return value

    try:
        requested_root = Path(
            os.path.abspath(str(fixture_root.expanduser()))
        )
        reject_symlink_components(
            requested_root,
            "gate_fixture_root_symlink",
        )
        root = requested_root.resolve(strict=True)
        root_info = root.stat()
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or root_info.st_uid != os.getuid()
            or stat.S_IMODE(root_info.st_mode) & 0o022
        ):
            raise ValueError("gate_fixture_root_permissions")
        structural_names = {
            path.name
            for path in root.iterdir()
            if path.name.endswith(".structure.json")
        }
        if structural_names != set(GATE_FIXTURE_NAMES):
            raise ValueError("gate_fixture_inventory")
        report = evaluate_feasibility_gate(
            load(root, "stop-cli.structure.json"),
            load(root, "stop-desktop.structure.json"),
            load(root, "transcript-cli.structure.json"),
            load(root, "transcript-desktop.structure.json"),
            load(root, "access-cli.structure.json"),
            load(root, "access-desktop.structure.json"),
        )
    except (KeyError, OSError, RecursionError, TypeError, ValueError):
        report = {
            "schema_version": 1,
            "decision": "FAIL",
            "checks": {"gate_inputs_valid": False},
            "schema_differences": {},
            "surfaces": {},
            "next_action": "amend_design_for_session_level_queue",
        }
    atomic_write_text(markdown_path, render_gate_markdown(report))
    atomic_write_json(json_path, report)
    return report


def cmd_probe_gate(args: argparse.Namespace) -> int:
    report = write_gate_report(
        Path(args.fixture_root),
        Path(args.output_json),
        Path(args.output_markdown),
    )
    write_json_stdout(report)
    return 0 if report["decision"] == "PASS" else 2


def cmd_probe_v2_gate(args: argparse.Namespace) -> int:
    try:
        report = write_gate_report_v2(
            Path(args.fixture_root),
            Path(args.predecessor_json),
            Path(args.output_json),
            Path(args.output_markdown),
        )
        write_json_stdout(report)
        return 0 if report["decision"] == "PASS" else 2
    except Exception:
        write_json_stdout(
            {
                "schema_version": 2,
                "decision": "FAIL",
                "checks": {"gate_inputs_valid": False},
                "error_codes": ["gate_inputs_invalid"],
                "predecessor": {"path": V2_PREDECESSOR_PATH, "sha256": None},
            }
        )
        return 2


def validate_scrub_target(path: Path, expected_parent: Path) -> Path:
    if path.parent != expected_parent:
        raise ValueError("scrub_target_outside_root")
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise ValueError("scrub_target_symlink")
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("scrub_target_not_regular")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("scrub_target_permissions")
    return path


def open_private_directory_descriptor(root_descriptor: int, name: str) -> int:
    descriptor = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=root_descriptor,
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise ValueError("data_child_permissions")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def write_v2_scrub_marker(root_descriptor: int) -> None:
    descriptor = os.open(
        V2_SCRUB_MARKER,
        os.O_RDWR | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=root_descriptor,
    )
    try:
        os.fchmod(descriptor, 0o600)
        info = ORIGINAL_FSTAT(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ValueError("v2_lifecycle_lock_unavailable")
        os.write(descriptor, b'{"schema_version":2,"scrubbed":true}\n')
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.fsync(root_descriptor)


def scrub_directory_targets(
    descriptor: int, patterns: tuple[str, ...]
) -> list[tuple[int, str, os.stat_result]]:
    targets: list[tuple[int, str, os.stat_result]] = []
    for name in sorted(name for name in os.listdir(descriptor) if any(
        fnmatch.fnmatch(name, pattern) for pattern in patterns
    )):
        info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("scrub_target_symlink")
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ValueError(
                "scrub_target_not_regular"
                if not stat.S_ISREG(info.st_mode)
                else "scrub_target_permissions"
            )
        targets.append((descriptor, name, info))
    return targets


def unlink_scrub_target(descriptor: int, name: str, expected: os.stat_result) -> None:
    info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or (info.st_dev, info.st_ino) != (expected.st_dev, expected.st_ino)
    ):
        raise ValueError("scrub_target_changed")
    os.unlink(name, dir_fd=descriptor)


def scrub_probe_raw(
    installation: Installation,
    confirmation: str,
) -> dict[str, int]:
    if confirmation != SCRUB_CONFIRMATION:
        raise ValueError("confirmation_mismatch")
    with v2_state_lock(installation, exclusive=True, allow_scrubbed=True) as root_descriptor:
        write_v2_scrub_marker(root_descriptor)
        descriptors: list[int] = []
        try:
            for child in ("incoming", "incoming-v2", "reports"):
                descriptors.append(
                    open_private_directory_descriptor(root_descriptor, child)
                )
            observations = (
                scrub_directory_targets(descriptors[0], ("*.json", ".*.json.*"))
                + scrub_directory_targets(descriptors[1], ("*.json", ".*.json.*"))
            )
            ephemeral_reports = scrub_directory_targets(
                descriptors[2],
                (
                    "*-observation.json", ".*-observation.json.*",
                    "*-boundary.json", ".*-boundary.json.*",
                    "*-skill-challenge.json", ".*-skill-challenge.json.*",
                    "*-skill-response.json", ".*-skill-response.json.*",
                    "*-v2-session-boundary.json", ".*-v2-session-boundary.json.*",
                    "*-v2-session-mapping.json", ".*-v2-session-mapping.json.*",
                    "*-v2-access-challenge.json", ".*-v2-access-challenge.json.*",
                    "*-v2-default-response.json", ".*-v2-default-response.json.*",
                    "*-v2-explicit-response.json", ".*-v2-explicit-response.json.*",
                    "*-v2-access.lock",
                ),
            )
            for descriptor, name, info in observations + ephemeral_reports:
                unlink_scrub_target(descriptor, name, info)
            for descriptor in descriptors:
                os.fsync(descriptor)
            return {
                "observations_deleted": len(observations),
                "ephemeral_reports_deleted": len(ephemeral_reports),
            }
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)


def cmd_probe_scrub(args: argparse.Namespace) -> int:
    if not sys.stdin.isatty():
        raise ValueError("tty_required")
    sys.stdout.write(
        f"Type {SCRUB_CONFIRMATION} to delete private raw observations: "
    )
    sys.stdout.flush()
    typed = sys.stdin.readline(len(SCRUB_CONFIRMATION) + 2).removesuffix("\n")
    installation = load_installation(Path(args.installation))
    result = scrub_probe_raw(installation, typed)
    write_json_stdout(result)
    return 0


def cmd_probe_status(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    observations = observation_paths(installation)
    write_json_stdout(
        {
            "status": "ready",
            "shared_nonce_present": bool(installation.nonce),
            "observation_count": len(observations),
            "latest_observation": observations[-1].name if observations else None,
        }
    )
    return 0


def cmd_probe_list(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    write_json_stdout(
        {
            "observations": [
                {"name": path.name, "size": path.stat().st_size}
                for path in observation_paths(installation)
            ]
        }
    )
    return 0


def cmd_probe_v2_status(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    observations = session_observation_paths(installation)
    write_json_stdout(
        {
            "status": "ready",
            "shared_nonce_present": bool(installation.nonce),
            "observation_count": len(observations),
            "latest_observation": observations[-1].name if observations else None,
        }
    )
    return 0


def cmd_probe_v2_list(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    write_json_stdout(
        {
            "observations": [
                {"name": path.name, "size": path.stat().st_size}
                for path in session_observation_paths(installation)
            ]
        }
    )
    return 0


def cmd_probe_arm_skill_preflight(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    write_json_stdout(arm_skill_preflight(installation, args.surface))
    return 0


def cmd_probe_skill_preflight(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    write_json_stdout(run_skill_preflight(installation, args.surface))
    return 0


def cmd_probe_promote_access(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    report = promote_skill_preflight(installation, args.surface, Path(args.output))
    write_json_stdout(report)
    return 0 if report["read"] and report["write"] else 2


def cmd_probe_mark_surface(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    write_json_stdout(mark_surface_boundary(installation, args.surface))
    return 0


def cmd_probe_promote_stop(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    report = promote_surface_stop(installation, args.surface, Path(args.output))
    write_json_stdout(report)
    return 0 if report["capture_supported"] else 2


def cmd_probe_v2_mark_surface(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    write_json_stdout(mark_session_surface_boundary(installation, args.surface))
    return 0


def cmd_probe_v2_promote_stop(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    report = promote_session_stop_v2(installation, args.surface, Path(args.output))
    write_json_stdout(report)
    return 0 if report["capture_supported"] else 2


def cmd_probe_v2_arm_access(args: argparse.Namespace) -> int:
    try:
        installation = load_installation(Path(args.installation))
        write_json_stdout(arm_access_v2(installation, args.surface))
        return 0
    except Exception:
        write_json_stdout(
            {
                "schema_version": 2,
                "surface": args.surface,
                "armed": False,
                "challenge_digest": None,
                "error_codes": [ACCESS_EVIDENCE_ERROR],
            }
        )
        return 2


def cmd_probe_v2_default_access(args: argparse.Namespace) -> int:
    try:
        installation = load_installation(Path(args.installation))
        report = run_default_access_v2(installation, args.surface, Path(args.output))
        write_json_stdout(report)
        return 0 if report["challenge_read"] and report["write_denied"] else 2
    except Exception:
        write_json_stdout(
            access_result(
                args.surface, False, False, False, None, [ACCESS_EVIDENCE_ERROR]
            )
        )
        return 2


def cmd_probe_v2_explicit_access(args: argparse.Namespace) -> int:
    try:
        installation = load_installation(Path(args.installation))
        report = run_explicit_access_v2(installation, args.surface)
        write_json_stdout(report)
        return 0 if report["challenge_read"] and report["global_write"] else 2
    except Exception:
        write_json_stdout(
            access_result(
                args.surface, False, False, False, None, [ACCESS_EVIDENCE_ERROR]
            )
        )
        return 2


def cmd_probe_v2_promote_access(args: argparse.Namespace) -> int:
    try:
        installation = load_installation(Path(args.installation))
        report = promote_access_v2(
            installation,
            args.surface,
            Path(args.default_response),
            Path(args.output),
        )
        write_json_stdout(report)
        return 0 if not report["error_codes"] else 2
    except Exception:
        write_json_stdout(failed_access_report(args.surface))
        return 2


def cmd_probe_promote_transcript(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    report = promote_transcript_structure(
        installation,
        args.surface,
        Path(args.output),
    )
    write_json_stdout(report)
    return 0 if report["supported"] else 2


def cmd_probe_v2_promote_transcript(args: argparse.Namespace) -> int:
    try:
        installation = load_installation(Path(args.installation))
        report = promote_session_transcript_v2(
            installation,
            args.surface,
            Path(args.output),
        )
        write_json_stdout(report)
        return 0 if report["supported"] else 2
    except Exception:
        write_json_stdout(
            failed_session_transcript_promotion(
                args.surface, "session_transcript_unavailable"
            )
        )
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = SanitizedArgumentParser(prog="evolver.py")
    parser.add_argument("--version", action="version", version=VERSION)
    subparsers = parser.add_subparsers(
        dest="command", required=True, parser_class=SanitizedArgumentParser
    )
    probe_stop = subparsers.add_parser("probe-stop")
    probe_stop.add_argument("--installation", required=True)
    probe_stop.set_defaults(handler=cmd_probe_stop)

    probe_v2_stop = subparsers.add_parser("probe-v2-stop")
    probe_v2_stop.add_argument("--installation", required=True)
    probe_v2_stop.set_defaults(handler=cmd_probe_v2_stop)

    probe_init = subparsers.add_parser("probe-init")
    probe_init.add_argument("--data-root", required=True)
    probe_init.add_argument("--transcript-root", action="append", required=True)
    probe_init.set_defaults(handler=cmd_probe_init)

    probe_status = subparsers.add_parser("probe-status")
    probe_status.add_argument("--installation", required=True)
    probe_status.set_defaults(handler=cmd_probe_status)

    probe_list = subparsers.add_parser("probe-list")
    probe_list.add_argument("--installation", required=True)
    probe_list.set_defaults(handler=cmd_probe_list)

    probe_v2_status = subparsers.add_parser("probe-v2-status")
    probe_v2_status.add_argument("--installation", required=True)
    probe_v2_status.set_defaults(handler=cmd_probe_v2_status)

    probe_v2_list = subparsers.add_parser("probe-v2-list")
    probe_v2_list.add_argument("--installation", required=True)
    probe_v2_list.set_defaults(handler=cmd_probe_v2_list)

    arm_preflight = subparsers.add_parser("probe-arm-skill-preflight")
    arm_preflight.add_argument("--installation", required=True)
    arm_preflight.add_argument("--surface", choices=("cli", "desktop"), required=True)
    arm_preflight.set_defaults(handler=cmd_probe_arm_skill_preflight)

    skill_preflight = subparsers.add_parser("probe-skill-preflight")
    skill_preflight.add_argument("--installation", required=True)
    skill_preflight.add_argument("--surface", choices=("cli", "desktop"), required=True)
    skill_preflight.set_defaults(handler=cmd_probe_skill_preflight)

    promote_access = subparsers.add_parser("probe-promote-access")
    promote_access.add_argument("--installation", required=True)
    promote_access.add_argument("--surface", choices=("cli", "desktop"), required=True)
    promote_access.add_argument("--output", required=True)
    promote_access.set_defaults(handler=cmd_probe_promote_access)

    mark_surface = subparsers.add_parser("probe-mark-surface")
    mark_surface.add_argument("--installation", required=True)
    mark_surface.add_argument("--surface", choices=("cli", "desktop"), required=True)
    mark_surface.set_defaults(handler=cmd_probe_mark_surface)

    mark_v2_surface = subparsers.add_parser("probe-v2-mark-surface")
    mark_v2_surface.add_argument("--installation", required=True)
    mark_v2_surface.add_argument("--surface", choices=("cli", "desktop"), required=True)
    mark_v2_surface.set_defaults(handler=cmd_probe_v2_mark_surface)

    promote_stop = subparsers.add_parser("probe-promote-stop")
    promote_stop.add_argument("--installation", required=True)
    promote_stop.add_argument("--surface", choices=("cli", "desktop"), required=True)
    promote_stop.add_argument("--output", required=True)
    promote_stop.set_defaults(handler=cmd_probe_promote_stop)

    promote_v2_stop = subparsers.add_parser("probe-v2-promote-stop")
    promote_v2_stop.add_argument("--installation", required=True)
    promote_v2_stop.add_argument("--surface", choices=("cli", "desktop"), required=True)
    promote_v2_stop.add_argument("--output", required=True)
    promote_v2_stop.set_defaults(handler=cmd_probe_v2_promote_stop)

    arm_v2_access = subparsers.add_parser("probe-v2-arm-access")
    arm_v2_access.add_argument("--installation", required=True)
    arm_v2_access.add_argument("--surface", type=parse_access_surface, required=True)
    arm_v2_access.set_defaults(handler=cmd_probe_v2_arm_access)

    default_v2_access = subparsers.add_parser("probe-v2-default-access")
    default_v2_access.add_argument("--installation", required=True)
    default_v2_access.add_argument("--surface", type=parse_access_surface, required=True)
    default_v2_access.add_argument("--output", required=True)
    default_v2_access.set_defaults(handler=cmd_probe_v2_default_access)

    explicit_v2_access = subparsers.add_parser("probe-v2-explicit-access")
    explicit_v2_access.add_argument("--installation", required=True)
    explicit_v2_access.add_argument("--surface", type=parse_access_surface, required=True)
    explicit_v2_access.set_defaults(handler=cmd_probe_v2_explicit_access)

    promote_v2_access = subparsers.add_parser("probe-v2-promote-access")
    promote_v2_access.add_argument("--installation", required=True)
    promote_v2_access.add_argument("--surface", type=parse_access_surface, required=True)
    promote_v2_access.add_argument("--default-response", required=True)
    promote_v2_access.add_argument("--output", required=True)
    promote_v2_access.set_defaults(handler=cmd_probe_v2_promote_access)

    promote_transcript = subparsers.add_parser("probe-promote-transcript")
    promote_transcript.add_argument("--installation", required=True)
    promote_transcript.add_argument("--surface", choices=("cli", "desktop"), required=True)
    promote_transcript.add_argument("--output", required=True)
    promote_transcript.set_defaults(handler=cmd_probe_promote_transcript)

    promote_v2_transcript = subparsers.add_parser("probe-v2-promote-transcript")
    promote_v2_transcript.add_argument("--installation", required=True)
    promote_v2_transcript.add_argument("--surface", choices=("cli", "desktop"), required=True)
    promote_v2_transcript.add_argument("--output", required=True)
    promote_v2_transcript.set_defaults(handler=cmd_probe_v2_promote_transcript)

    probe_gate = subparsers.add_parser("probe-gate")
    probe_gate.add_argument("--fixture-root", required=True)
    probe_gate.add_argument("--output-json", required=True)
    probe_gate.add_argument("--output-markdown", required=True)
    probe_gate.set_defaults(handler=cmd_probe_gate)

    probe_v2_gate = subparsers.add_parser("probe-v2-gate")
    probe_v2_gate.add_argument("--fixture-root", required=True)
    probe_v2_gate.add_argument("--predecessor-json", required=True)
    probe_v2_gate.add_argument("--output-json", required=True)
    probe_v2_gate.add_argument("--output-markdown", required=True)
    probe_v2_gate.set_defaults(handler=cmd_probe_v2_gate)

    probe_scrub = subparsers.add_parser("probe-scrub")
    probe_scrub.add_argument("--installation", required=True)
    probe_scrub.set_defaults(handler=cmd_probe_scrub)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
