#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator, Optional, Sequence, TextIO

VERSION = "skill-evolver feasibility 0.0.1"
MAX_STDIN_BYTES = 65_536
REQUIRED_HOOK_FIELDS = {"hook_event_name": str, "session_id": str, "turn_id": str, "cwd": str}
REQUIRED_SESSION_HOOK_FIELDS = {
    "hook_event_name": str,
    "session_id": str,
    "cwd": str,
}
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
    "source_kind",
    "turn_id",
}
MAX_TRANSCRIPT_PROBE_BYTES = 2_097_152
MAX_GATE_FIXTURE_BYTES = 65_536
SCRUB_CONFIRMATION = "DELETE-FEASIBILITY-RAW"
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
    for child in ("incoming", "reports"):
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


def capture_session_stop(installation: Installation, raw: bytes) -> Path:
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
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
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


def observation_paths(installation: Installation) -> list[Path]:
    return [
        validate_observation(path)
        for path in sorted((installation.data_root / "incoming").glob("*.json"))
    ]


def session_observation_paths(installation: Installation) -> list[Path]:
    return [
        validate_observation(path)
        for path in sorted(session_incoming_directory(installation).glob("*.json"))
    ]


def validate_surface(surface: str) -> str:
    if surface not in {"cli", "desktop"}:
        raise ValueError("invalid_surface")
    return surface


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
    observations = session_observation_paths(installation)
    boundary = observations[-1].name if observations else None
    atomic_write_json(
        installation.data_root / "reports" / f"{surface}-v2-session-boundary.json",
        {"schema_version": 2, "surface": surface, "after": boundary},
    )
    return {"surface": surface, "marked": True}


def session_surface_observations(
    installation: Installation,
    surface: str,
) -> list[Path]:
    surface = validate_surface(surface)
    observations = session_observation_paths(installation)
    marker = json.loads(
        (
            installation.data_root / "reports" / f"{surface}-v2-session-boundary.json"
        ).read_text(encoding="utf-8")
    )
    if marker.get("schema_version") != 2 or marker.get("surface") != surface:
        raise ValueError("session_surface_boundary_mismatch")
    after = marker.get("after")
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


def aggregate_session_required_fields(
    observations: list[dict[str, object]],
) -> dict[str, object]:
    names = ("hook_event_name", "session_id", "cwd", "transcript_path", "turn_id")
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
        and all(isinstance(item.get("size"), int) and item["size"] > 0 for item in infos),
        "has_mtime_ns": present
        and all(isinstance(item.get("mtime_ns"), int) for item in infos),
        "has_device": present
        and all(isinstance(item.get("device"), int) for item in infos),
        "has_inode": present
        and all(isinstance(item.get("inode"), int) for item in infos),
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


def promote_session_stop_v2(
    installation: Installation,
    surface: str,
    output: Path,
) -> dict[str, object]:
    surface = validate_surface(surface)
    try:
        paths = session_surface_observations(installation, surface)
        observations = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
        if not all(isinstance(item, dict) and item.get("schema_version") == 2 for item in observations):
            raise ValueError("invalid_session_stop_observation")
        nonce_matches = all(
            item.get("installation_nonce") == installation.nonce for item in observations
        )
        shapes = [item["shape"] for item in observations]
        transcript_infos = [item.get("transcript_stat") for item in observations]
        required_fields = aggregate_session_required_fields(observations)
        capture_error_codes = sorted(
            {
                str(item["capture_error_code"])
                for item in observations
                if "capture_error_code" in item
            }
        )
        if any(code not in SESSION_CAPTURE_ERROR_CODES for code in capture_error_codes):
            raise ValueError("session_stop_observation_unavailable")
        session_ids = {
            item["event"]["session_id"]
            for item in observations
            if isinstance(item.get("event"), dict)
            and isinstance(item["event"].get("session_id"), str)
        }
        payload_shapes_stable = shapes[0] == shapes[1]
        distinct_sessions = len(session_ids) == 2
        transcript_stat = session_transcript_stat_report(transcript_infos)
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


def scrub_probe_raw(
    installation: Installation,
    confirmation: str,
) -> dict[str, int]:
    if confirmation != SCRUB_CONFIRMATION:
        raise ValueError("confirmation_mismatch")
    validate_private_directory(installation.data_root)
    incoming = validate_private_child_directory(
        installation.data_root / "incoming"
    )
    reports = validate_private_child_directory(
        installation.data_root / "reports"
    )
    observations = sorted(
        {
            path
            for pattern in ("*.json", ".*.json.*")
            for path in incoming.glob(pattern)
        }
    )
    ephemeral_reports = sorted(
        {
            path
            for pattern in (
                "*-observation.json",
                ".*-observation.json.*",
                "*-boundary.json",
                ".*-boundary.json.*",
                "*-skill-challenge.json",
                ".*-skill-challenge.json.*",
                "*-skill-response.json",
                ".*-skill-response.json.*",
            )
            for path in reports.glob(pattern)
        }
    )
    observations = [
        validate_scrub_target(path, incoming)
        for path in observations
    ]
    ephemeral_reports = [
        validate_scrub_target(path, reports)
        for path in ephemeral_reports
    ]
    for path in observations:
        path.unlink()
    for path in ephemeral_reports:
        path.unlink()
    fsync_directory(incoming)
    fsync_directory(reports)
    return {
        "observations_deleted": len(observations),
        "ephemeral_reports_deleted": len(ephemeral_reports),
    }


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


def cmd_probe_promote_transcript(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    report = promote_transcript_structure(
        installation,
        args.surface,
        Path(args.output),
    )
    write_json_stdout(report)
    return 0 if report["supported"] else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evolver.py")
    parser.add_argument("--version", action="version", version=VERSION)
    subparsers = parser.add_subparsers(dest="command", required=True)
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

    promote_transcript = subparsers.add_parser("probe-promote-transcript")
    promote_transcript.add_argument("--installation", required=True)
    promote_transcript.add_argument("--surface", choices=("cli", "desktop"), required=True)
    promote_transcript.add_argument("--output", required=True)
    promote_transcript.set_defaults(handler=cmd_probe_promote_transcript)

    probe_gate = subparsers.add_parser("probe-gate")
    probe_gate.add_argument("--fixture-root", required=True)
    probe_gate.add_argument("--output-json", required=True)
    probe_gate.add_argument("--output-markdown", required=True)
    probe_gate.set_defaults(handler=cmd_probe_gate)

    probe_scrub = subparsers.add_parser("probe-scrub")
    probe_scrub.add_argument("--installation", required=True)
    probe_scrub.set_defaults(handler=cmd_probe_scrub)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
