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
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Optional, Sequence, TextIO

VERSION = "skill-evolver feasibility 0.0.1"
MAX_STDIN_BYTES = 65_536
REQUIRED_HOOK_FIELDS = {"hook_event_name": str, "session_id": str, "turn_id": str, "cwd": str}
INSTALLATION_SCHEMA = 1


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
    if root.exists():
        root = validate_private_directory(root)
    else:
        root.mkdir(mode=0o700)
        root = validate_private_directory(root)
    installation_path = root / "installation.json"
    nonce_path = root / "nonce.json"
    if any(path.exists() or path.is_symlink() for path in (installation_path, nonce_path)):
        raise ValueError("existing_installation")
    for child in ("incoming", "reports"):
        directory = root / child
        if directory.is_symlink():
            raise ValueError("data_child_symlink")
        if not directory.exists():
            directory.mkdir(mode=0o700)
        validate_private_child_directory(directory)
    canonical_transcripts = tuple(item.expanduser().resolve(strict=True) for item in transcript_roots)
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
    root = validate_private_directory(Path(str(payload["data_root"])))
    transcript_roots = tuple(
        Path(str(item)).resolve(strict=True) for item in payload["transcript_roots"]
    )
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


def stat_transcript(path: Path) -> dict[str, object]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
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


def cmd_probe_stop(args: argparse.Namespace) -> int:
    try:
        installation = load_installation(Path(args.installation))
        raw = read_bounded_stdin(sys.stdin.buffer)
        capture_stop(installation, raw)
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
            challenge.get("surface") == surface
            and response.get("surface") == surface
            and challenge.get("installation_nonce") == installation.nonce
            and response.get("installation_nonce") == installation.nonce
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evolver.py")
    parser.add_argument("--version", action="version", version=VERSION)
    subparsers = parser.add_subparsers(dest="command", required=True)
    probe_stop = subparsers.add_parser("probe-stop")
    probe_stop.add_argument("--installation", required=True)
    probe_stop.set_defaults(handler=cmd_probe_stop)

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

    promote_stop = subparsers.add_parser("probe-promote-stop")
    promote_stop.add_argument("--installation", required=True)
    promote_stop.add_argument("--surface", choices=("cli", "desktop"), required=True)
    promote_stop.add_argument("--output", required=True)
    promote_stop.set_defaults(handler=cmd_probe_promote_stop)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
