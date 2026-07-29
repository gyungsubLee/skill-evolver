#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import quote

VERSION = "skill-evolver 0.1.0"
SCHEMA_VERSION = 1
MAX_HOOK_BYTES = 65_536

DEFAULTS = {
    "pending_retention_days": 14,
    "pending_limit_sessions": 200,
    "raw_metadata_ttl_days": 30,
    "session_dedupe_days": 180,
    "spool_limit_files": 200,
    "spool_limit_bytes": 10_485_760,
    "review_batch_sessions": 5,
    "max_transcript_bytes": 2_097_152,
    "max_transcript_records": 100,
    "max_review_batch_bytes": 8_388_608,
    "max_candidates_per_session": 1,
    "max_candidates_per_batch": 3,
    "lease_seconds": 600,
    "lease_heartbeat_seconds": 60,
}

SCHEMA_SQL = """
CREATE TABLE review_batches (
    id INTEGER PRIMARY KEY,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    session_count INTEGER NOT NULL DEFAULT 0,
    generation_count INTEGER NOT NULL DEFAULT 0,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    exclusion_counts_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE review_items (
    id INTEGER PRIMARY KEY,
    session_key TEXT NOT NULL UNIQUE,
    raw_session_id TEXT,
    diagnostic_turn_id TEXT,
    generation INTEGER NOT NULL DEFAULT 1 CHECK(generation >= 1),
    transcript_epoch INTEGER NOT NULL DEFAULT 0 CHECK(transcript_epoch >= 0),
    status TEXT NOT NULL,
    binding_status TEXT NOT NULL DEFAULT 'accepted',
    cwd TEXT,
    transcript_path TEXT,
    transcript_size INTEGER,
    transcript_mtime_ns INTEGER,
    transcript_device INTEGER,
    transcript_inode INTEGER,
    observed_boundary INTEGER NOT NULL DEFAULT 0 CHECK(observed_boundary >= 0),
    last_stop_ns INTEGER NOT NULL CHECK(last_stop_ns >= 0),
    reviewed_boundary INTEGER NOT NULL DEFAULT 0 CHECK(reviewed_boundary >= 0),
    frozen_epoch INTEGER,
    frozen_from INTEGER,
    frozen_to INTEGER,
    frozen_locator_json TEXT,
    batch_id INTEGER REFERENCES review_batches(id),
    first_stop_at TEXT NOT NULL,
    last_stop_at TEXT NOT NULL,
    pending_since TEXT,
    review_started_at TEXT,
    reviewed_at TEXT,
    excluded_reason TEXT,
    error_code TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    raw_metadata_expires_at TEXT NOT NULL,
    dedupe_expires_at TEXT NOT NULL,
    raw_redacted_at TEXT
);
CREATE TABLE candidates (
    id INTEGER PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE,
    target_identity TEXT NOT NULL,
    target_skill TEXT NOT NULL,
    target_path TEXT,
    problem_category TEXT NOT NULL,
    target_locator TEXT NOT NULL,
    proposal_intent TEXT NOT NULL,
    conflict_group TEXT,
    problem_summary TEXT NOT NULL,
    proposal_summary TEXT NOT NULL,
    validation_plan TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    status TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    tombstone_until TEXT
);
CREATE TABLE candidate_evidence (
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    review_item_id INTEGER REFERENCES review_items(id) ON DELETE SET NULL,
    session_key TEXT NOT NULL,
    generation INTEGER NOT NULL,
    signal_type TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(candidate_id, session_key, signal_type)
);
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX idx_review_items_status_pending
    ON review_items(status, pending_since, id);
CREATE INDEX idx_review_items_lease
    ON review_items(status, lease_expires_at);
CREATE INDEX idx_review_items_dedupe
    ON review_items(dedupe_expires_at);
CREATE INDEX idx_candidates_status_updated
    ON candidates(status, updated_at);
"""


@dataclass(frozen=True)
class Installation:
    data_root: Path
    transcript_roots: tuple[Path, ...]
    python: Path
    config_path: Path
    identity_key: Path
    database: Path
    spool: Path


@dataclass(frozen=True)
class Config:
    capture_paused: bool
    exclude_roots: tuple[Path, ...]
    pending_retention_days: int
    pending_limit_sessions: int
    raw_metadata_ttl_days: int
    session_dedupe_days: int
    spool_limit_files: int
    spool_limit_bytes: int
    review_batch_sessions: int
    max_transcript_bytes: int
    max_transcript_records: int
    max_review_batch_bytes: int
    max_candidates_per_session: int
    max_candidates_per_batch: int
    lease_seconds: int
    lease_heartbeat_seconds: int


@dataclass(frozen=True)
class CapturedSessionStop:
    session_id: str
    diagnostic_turn_id: Optional[str]
    cwd: Path
    transcript_path: Path
    transcript_size: int
    transcript_mtime_ns: int
    transcript_device: int
    transcript_inode: int
    observed_at_ns: int


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def fsync_directory(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_bytes(path: Path, value: bytes, mode: int = 0o600) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, mode)
        fsync_directory(path.parent)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, payload: object, mode: int = 0o600) -> None:
    atomic_write_bytes(path, canonical_json_bytes(payload) + b"\n", mode)


def private_directory(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("private_directory_symlink")
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ValueError("private_directory_permissions")
    return resolved


def private_file(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("private_file_symlink")
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ValueError("private_file_permissions")
    return resolved


def canonical_roots(values: object, *, allow_empty: bool) -> tuple[Path, ...]:
    if not isinstance(values, list) and not isinstance(values, tuple):
        raise ValueError("invalid_root_list")
    if not allow_empty and not values:
        raise ValueError("invalid_root_list")
    roots: list[Path] = []
    for value in values:
        requested = Path(str(value)).expanduser()
        if requested.is_symlink():
            raise ValueError("root_symlink")
        root = requested.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("invalid_root")
        roots.append(root)
    return tuple(roots)


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


def initialize_runtime(
    data_root: Path,
    transcript_roots: tuple[Path, ...],
    config: dict[str, object],
) -> Path:
    requested = data_root.expanduser()
    if requested.is_symlink():
        raise ValueError("data_root_symlink")
    root = requested.parent.resolve(strict=True) / requested.name
    fixed_transcripts = tuple(
        validate_transcript_root(path)
        for path in canonical_roots(transcript_roots, allow_empty=False)
    )
    validate_transcript_separation(root, fixed_transcripts)
    if root.exists():
        private_directory(root)
        raise ValueError("runtime_already_initialized")
    excludes = canonical_roots(config.get("exclude_roots", []), allow_empty=True)
    allowed = set(DEFAULTS) | {"capture_paused", "exclude_roots"}
    if set(config) - allowed:
        raise ValueError("invalid_config_keys")
    paused = config.get("capture_paused", False)
    if type(paused) is not bool:
        raise ValueError("invalid_config_capture_paused")
    merged: dict[str, object] = {
        **DEFAULTS,
        "capture_paused": paused,
        "exclude_roots": [str(path) for path in excludes],
    }
    for key in DEFAULTS:
        if key in config:
            merged[key] = config[key]
    for key in DEFAULTS:
        value = merged[key]
        if type(value) is not int or value <= 0:
            raise ValueError(f"invalid_config_{key}")
    if merged["max_candidates_per_session"] != 1:
        raise ValueError("invalid_config_max_candidates_per_session")

    staging = Path(
        tempfile.mkdtemp(prefix=f".{root.name}.", dir=root.parent)
    )
    placed = False
    try:
        staging = private_directory(staging)
        spool = staging / "spool"
        spool.mkdir(mode=0o700)
        private_directory(spool)
        staging_installation = staging / "installation.json"
        installation_payload = {
            "schema_version": 1,
            "data_root": str(staging),
            "transcript_roots": [
                str(path) for path in fixed_transcripts
            ],
            "python": "/usr/bin/python3",
        }
        atomic_write_json(staging_installation, installation_payload)
        atomic_write_json(staging / "config.json", merged)
        atomic_write_bytes(
            staging / "identity.key", secrets.token_bytes(32)
        )
        installation = load_installation(staging_installation)
        connection = open_database(installation)
        connection.close()
        atomic_write_json(
            staging_installation,
            {**installation_payload, "data_root": str(root)},
        )
        fsync_directory(staging)
        os.replace(staging, root)
        placed = True
        fsync_directory(root.parent)

        installation = load_installation(root / "installation.json")
        status_connection = open_database(installation, read_only=True)
        status_connection.close()
        fsync_directory(root)
    except BaseException:
        partial = root if placed else staging
        if partial.exists():
            shutil.rmtree(partial)
        fsync_directory(root.parent)
        raise
    return root / "installation.json"


def load_installation(path: Path) -> Installation:
    installation_path = private_file(path)
    payload = json.loads(installation_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or payload.get("python") != "/usr/bin/python3":
        raise ValueError("unsupported_installation")
    fixed_data_root = payload.get("data_root")
    if (
        not isinstance(fixed_data_root, str)
        or not fixed_data_root
        or not Path(fixed_data_root).is_absolute()
    ):
        raise ValueError("invalid_data_root")
    requested_root = Path(fixed_data_root)
    root = private_directory(requested_root)
    if requested_root != root:
        raise ValueError("invalid_data_root")
    if installation_path != root / "installation.json":
        raise ValueError("installation_root_mismatch")
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
    installation = Installation(
        data_root=root,
        transcript_roots=transcript_roots,
        python=Path("/usr/bin/python3"),
        config_path=root / "config.json",
        identity_key=root / "identity.key",
        database=root / "evolver.db",
        spool=root / "spool",
    )
    private_file(installation.config_path)
    if len(private_file(installation.identity_key).read_bytes()) != 32:
        raise ValueError("invalid_identity_key")
    private_directory(installation.spool)
    return installation


def load_config(installation: Installation) -> Config:
    payload = json.loads(installation.config_path.read_text(encoding="utf-8"))
    allowed = set(DEFAULTS) | {"capture_paused", "exclude_roots"}
    if not isinstance(payload, dict) or set(payload) != allowed:
        raise ValueError("invalid_config_keys")
    paused = payload["capture_paused"]
    if type(paused) is not bool:
        raise ValueError("invalid_config_capture_paused")
    values: dict[str, int] = {}
    for key in DEFAULTS:
        value = payload[key]
        if type(value) is not int or value <= 0:
            raise ValueError(f"invalid_config_{key}")
        values[key] = value
    if values["max_candidates_per_session"] != 1:
        raise ValueError("invalid_config_max_candidates_per_session")
    return Config(
        capture_paused=paused,
        exclude_roots=canonical_roots(
            payload["exclude_roots"], allow_empty=True
        ),
        **values,
    )


def open_database(
    installation: Installation,
    read_only: bool = False,
) -> sqlite3.Connection:
    if installation.database.exists():
        private_file(installation.database)
    if read_only:
        if not installation.database.exists():
            raise ValueError("database_missing")
        database = (
            f"file:{quote(str(installation.database), safe='/')}?mode=ro"
        )
        connection = sqlite3.connect(
            database,
            uri=True,
            timeout=0,
            isolation_level=None,
        )
    else:
        connection = sqlite3.connect(
            str(installation.database),
            timeout=0,
            isolation_level=None,
        )
        os.chmod(installation.database, 0o600)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 0")
        if read_only:
            connection.execute("PRAGMA query_only = ON")
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise ValueError("unsupported_database_schema")
        mode = str(
            connection.execute(
                "PRAGMA journal_mode"
                if read_only or version
                else "PRAGMA journal_mode = DELETE"
            ).fetchone()[0]
        )
        if mode.lower() != "delete":
            raise ValueError("delete_journal_required")
        if version == 0:
            if read_only:
                raise ValueError("database_uninitialized")
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + SCHEMA_SQL
                + f"\nPRAGMA user_version = {SCHEMA_VERSION};\n"
                + "COMMIT;\n"
            )
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        connection.close()
        raise
    return connection


def within(path: Path, roots: tuple[Path, ...]) -> bool:
    for root in roots:
        try:
            if os.path.commonpath((str(path), str(root))) == str(root):
                return True
        except ValueError:
            continue
    return False


def bounded_string(
    payload: dict[str, object],
    name: str,
    maximum: int,
    *,
    required: bool = True,
) -> Optional[str]:
    value = payload.get(name)
    if value is None and not required:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
    ):
        raise ValueError(f"invalid_{name}")
    return value


def parse_session_stop(
    raw: bytes,
    installation: Installation,
    config: Config,
) -> Optional[CapturedSessionStop]:
    if len(raw) > MAX_HOOK_BYTES:
        raise ValueError("hook_input_too_large")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("hook_event_name") != "Stop":
        raise ValueError("not_stop_event")
    if config.capture_paused:
        return None
    cwd_text = bounded_string(payload, "cwd", 4_096)
    assert cwd_text is not None
    cwd_value = Path(cwd_text).expanduser()
    if cwd_value.is_symlink():
        raise ValueError("cwd_symlink")
    cwd = cwd_value.resolve(strict=True)
    if within(cwd, config.exclude_roots):
        return None
    transcript_text = bounded_string(payload, "transcript_path", 4_096)
    assert transcript_text is not None
    transcript_value = Path(transcript_text).expanduser()
    if transcript_value.is_symlink():
        raise ValueError("transcript_symlink")
    transcript = transcript_value.resolve(strict=True)
    if not within(transcript, installation.transcript_roots):
        raise ValueError("transcript_outside_roots")
    observed_at_ns = time.time_ns()
    descriptor = os.open(
        str(transcript),
        os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        info = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("transcript_owner_or_type")
    raw_session_id = bounded_string(payload, "session_id", 512)
    assert raw_session_id is not None
    return CapturedSessionStop(
        session_id=raw_session_id,
        diagnostic_turn_id=bounded_string(
            payload, "turn_id", 512, required=False
        ),
        cwd=cwd,
        transcript_path=transcript,
        transcript_size=info.st_size,
        transcript_mtime_ns=info.st_mtime_ns,
        transcript_device=info.st_dev,
        transcript_inode=info.st_ino,
        observed_at_ns=observed_at_ns,
    )


def session_key(installation: Installation, session_id: str) -> str:
    return hmac.new(
        installation.identity_key.read_bytes(),
        b"session\0" + session_id.encode("utf-8"),
        "sha256",
    ).hexdigest()


def iso_utc(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


RAW_CLEAR_ASSIGNMENTS = """
raw_session_id=NULL,
diagnostic_turn_id=NULL,
cwd=NULL,
transcript_path=NULL,
transcript_size=NULL,
transcript_mtime_ns=NULL,
transcript_device=NULL,
transcript_inode=NULL,
error_code=NULL
"""


def expire_session_ids(
    connection: sqlite3.Connection,
    ids: list[int],
    reason: str,
    now: float,
) -> int:
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    changed = connection.execute(
        f"""
        UPDATE review_items
        SET status='expired', excluded_reason=?, reviewed_at=?,
            raw_redacted_at=?, {RAW_CLEAR_ASSIGNMENTS}
        WHERE id IN ({marks}) AND status='pending'
        """,
        (reason, iso_utc(now), iso_utc(now), *ids),
    ).rowcount
    if changed != len(ids):
        raise sqlite3.IntegrityError("session_expiry_race")
    return changed


def reserve_pending_session(
    connection: sqlite3.Connection,
    config: Config,
    now: float,
) -> int:
    count = int(
        connection.execute(
            "SELECT COUNT(*) FROM review_items WHERE status='pending'"
        ).fetchone()[0]
    )
    needed = max(count - config.pending_limit_sessions + 1, 0)
    if not needed:
        return 0
    ids = [
        int(row["id"])
        for row in connection.execute(
            """
            SELECT id FROM review_items
            WHERE status='pending'
            ORDER BY pending_since, id
            LIMIT ?
            """,
            (needed,),
        )
    ]
    changed = expire_session_ids(connection, ids, "capacity", now)
    connection.execute(
        """
        INSERT INTO metadata(key,value) VALUES('capacity_expired_count',?)
        ON CONFLICT(key) DO UPDATE SET
          value=CAST(CAST(value AS INTEGER)+excluded.value AS TEXT)
        """,
        (str(changed),),
    )
    return changed


def upsert_session(
    connection: sqlite3.Connection,
    event: CapturedSessionStop,
    key: str,
    config: Config,
    now: float,
) -> str:
    now_text = iso_utc(now)
    event_time_ns = event.observed_at_ns
    if type(event_time_ns) is not int or event_time_ns < 0:
        raise ValueError("invalid_observed_at_ns")
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            "SELECT * FROM review_items WHERE session_key=?",
            (key,),
        ).fetchone()
        outcome = "duplicate"
        if row is None:
            reserve_pending_session(connection, config, now)
            connection.execute(
                """
                INSERT INTO review_items(
                  session_key,raw_session_id,diagnostic_turn_id,generation,
                  transcript_epoch,status,binding_status,cwd,transcript_path,
                  transcript_size,transcript_mtime_ns,transcript_device,
                  transcript_inode,observed_boundary,last_stop_ns,
                  reviewed_boundary,
                  first_stop_at,last_stop_at,pending_since,
                  raw_metadata_expires_at,dedupe_expires_at
                ) VALUES(
                  ?,?,?,1,0,'pending','accepted',?,?,?,?,?,?,?,?,0,?,?,?,?,?
                )
                """,
                (
                    key,
                    event.session_id,
                    event.diagnostic_turn_id,
                    str(event.cwd),
                    str(event.transcript_path),
                    event.transcript_size,
                    event.transcript_mtime_ns,
                    event.transcript_device,
                    event.transcript_inode,
                    event.transcript_size,
                    event_time_ns,
                    now_text,
                    now_text,
                    now_text,
                    iso_utc(now + config.raw_metadata_ttl_days * 86_400),
                    iso_utc(now + config.session_dedupe_days * 86_400),
                ),
            )
            outcome = "inserted"
        elif row["status"] != "expired" and row["raw_redacted_at"] is None:
            same_identity = (
                int(row["transcript_device"]) == event.transcript_device
                and int(row["transcript_inode"]) == event.transcript_inode
            )
            observed_boundary = int(row["observed_boundary"])
            same_boundary = event.transcript_size == observed_boundary
            same_path = str(row["transcript_path"]) == str(
                event.transcript_path
            )
            same_mtime = (
                int(row["transcript_mtime_ns"])
                == event.transcript_mtime_ns
            )
            prior_time_ns = int(row["last_stop_ns"])
            # ponytail: an equal clock tick keeps the current locator unless
            # the same inode grew or its same-size locator changed; add a
            # per-process sequence only if future platforms cannot provide
            # sufficient timestamp precision.
            stale_observation = event_time_ns < prior_time_ns or (
                event_time_ns == prior_time_ns
                and (
                    not same_identity
                    or event.transcript_size < observed_boundary
                    or (same_boundary and same_path and same_mtime)
                )
            )
            if stale_observation:
                outcome = "stale"
            else:
                shrank = same_identity and (
                    event.transcript_size < observed_boundary
                )
                pending_binding = row["binding_status"] == "pending_epoch"
                same_size_locator_change = (
                    same_identity
                    and same_boundary
                    and (not same_path or not same_mtime)
                )
                needs_rebind = (
                    pending_binding
                    or not same_identity
                    or shrank
                    or same_size_locator_change
                )
                new_work = needs_rebind or (
                    same_identity
                    and event.transcript_size > int(row["reviewed_boundary"])
                )
                status = str(row["status"])
                generation = int(row["generation"])
                pending_since = row["pending_since"]
                if status not in {"pending", "reviewing"} and new_work:
                    reserve_pending_session(connection, config, now)
                    status = "pending"
                    generation += 1
                    pending_since = now_text
                elif status == "pending" and pending_since is None:
                    pending_since = now_text
                binding_status = (
                    "pending_epoch" if needs_rebind else "accepted"
                )
                error_code = (
                    "transcript_rebind_required" if needs_rebind else None
                )
                connection.execute(
                    """
                    UPDATE review_items
                    SET raw_session_id=?,diagnostic_turn_id=?,generation=?,
                        status=?,binding_status=?,cwd=?,transcript_path=?,
                        transcript_size=?,transcript_mtime_ns=?,
                        transcript_device=?,transcript_inode=?,
                        observed_boundary=?,last_stop_ns=?,last_stop_at=?,
                        pending_since=?,error_code=?
                    WHERE session_key=?
                    """,
                    (
                        event.session_id,
                        event.diagnostic_turn_id,
                        generation,
                        status,
                        binding_status,
                        str(event.cwd),
                        str(event.transcript_path),
                        event.transcript_size,
                        event.transcript_mtime_ns,
                        event.transcript_device,
                        event.transcript_inode,
                        event.transcript_size,
                        event_time_ns,
                        now_text,
                        pending_since,
                        error_code,
                        key,
                    ),
                )
                outcome = "advanced" if new_work else "duplicate"
        connection.execute(
            """
            INSERT INTO metadata(key,value) VALUES('last_hook_success_at',?)
            ON CONFLICT(key) DO UPDATE SET value=MAX(value,excluded.value)
            """,
            (now_text,),
        )
        pending = int(
            connection.execute(
                "SELECT COUNT(*) FROM review_items WHERE status='pending'"
            ).fetchone()[0]
        )
        if pending > config.pending_limit_sessions:
            raise sqlite3.IntegrityError("pending_session_capacity")
        connection.commit()
        return outcome
    except BaseException:
        connection.rollback()
        raise


def adopt_transcript_epoch(
    connection: sqlite3.Connection,
    session_key_value: str,
    embedded_session_key: str,
    path: Path,
    mtime_ns: int,
    device: int,
    inode: int,
    boundary: int,
    binding_mode: str,
    now: float,
) -> int:
    if binding_mode != "embedded_session_id":
        raise ValueError("invalid_epoch_binding_mode")
    if not hmac.compare_digest(session_key_value, embedded_session_key):
        raise ValueError("embedded_session_mismatch")
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or any(
            type(value) is not int or value < 0
            for value in (mtime_ns, device, inode, boundary)
        )
    ):
        raise ValueError("invalid_epoch_locator")
    descriptor = -1
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            "SELECT * FROM review_items WHERE session_key=?",
            (session_key_value,),
        ).fetchone()
        if row is None or row["status"] != "pending":
            raise ValueError("session_not_pending")
        if row["binding_status"] != "pending_epoch":
            raise ValueError("epoch_adoption_not_required")
        if (
            str(row["transcript_path"]) != str(path)
            or int(row["transcript_mtime_ns"]) != mtime_ns
            or int(row["transcript_device"]) != device
            or int(row["transcript_inode"]) != inode
            or int(row["transcript_size"]) != boundary
            or int(row["observed_boundary"]) != boundary
        ):
            raise ValueError("epoch_locator_changed")
        try:
            if path.is_symlink() or path.resolve(strict=True) != path:
                raise ValueError("epoch_locator_changed")
            descriptor = os.open(
                str(path),
                os.O_RDONLY
                | os.O_NONBLOCK
                | getattr(os, "O_NOFOLLOW", 0),
            )
            info = os.fstat(descriptor)
        except OSError:
            raise ValueError("epoch_locator_changed") from None
        expected_stat = (boundary, mtime_ns, device, inode)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or (
                info.st_size,
                info.st_mtime_ns,
                info.st_dev,
                info.st_ino,
            )
            != expected_stat
        ):
            raise ValueError("epoch_locator_changed")
        epoch = int(row["transcript_epoch"]) + 1
        connection.execute(
            """
            UPDATE review_items
            SET transcript_epoch=?,reviewed_boundary=0,
                binding_status='accepted',error_code=NULL,pending_since=?
            WHERE session_key=?
            """,
            (epoch, iso_utc(now), session_key_value),
        )
        final_info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(final_info.st_mode)
            or final_info.st_uid != os.getuid()
            or (
                final_info.st_size,
                final_info.st_mtime_ns,
                final_info.st_dev,
                final_info.st_ino,
            )
            != expected_stat
        ):
            raise ValueError("epoch_locator_changed")
        connection.commit()
        return epoch
    except BaseException:
        connection.rollback()
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _recover_expired_review_leases(
    connection: sqlite3.Connection,
    now: float,
) -> int:
    return connection.execute(
        """
        UPDATE review_items
        SET status='pending',batch_id=NULL,review_started_at=NULL,
            frozen_epoch=NULL,frozen_from=NULL,frozen_to=NULL,
            frozen_locator_json=NULL,lease_owner=NULL,lease_expires_at=NULL,
            pending_since=COALESCE(pending_since,?)
        WHERE status='reviewing' AND lease_expires_at < ?
        """,
        (iso_utc(now), iso_utc(now)),
    ).rowcount


def recover_expired_review_leases(
    connection: sqlite3.Connection,
    now: float,
) -> int:
    connection.execute("BEGIN IMMEDIATE")
    try:
        changed = _recover_expired_review_leases(connection, now)
        connection.commit()
        return changed
    except BaseException:
        connection.rollback()
        raise


def claim_review_generation(
    connection: sqlite3.Connection,
    session_key_value: str,
    owner: str,
    now: float,
    config: Config,
) -> dict[str, object]:
    if not owner or len(owner.encode("utf-8")) > 128:
        raise ValueError("invalid_lease_owner")
    connection.execute("BEGIN IMMEDIATE")
    try:
        _recover_expired_review_leases(connection, now)
        row = connection.execute(
            "SELECT * FROM review_items WHERE session_key=?",
            (session_key_value,),
        ).fetchone()
        if row is None or row["status"] != "pending":
            raise ValueError("session_not_pending")
        if row["binding_status"] != "accepted":
            raise ValueError("transcript_binding_required")
        review_from = int(row["reviewed_boundary"])
        review_to = int(row["observed_boundary"])
        if review_to <= review_from:
            raise ValueError("empty_generation")
        locator = {
            "path": str(row["transcript_path"]),
            "size": int(row["transcript_size"]),
            "mtime_ns": int(row["transcript_mtime_ns"]),
            "device": int(row["transcript_device"]),
            "inode": int(row["transcript_inode"]),
        }
        changed = connection.execute(
            """
            UPDATE review_items
            SET status='reviewing',review_started_at=?,frozen_epoch=?,
                frozen_from=?,frozen_to=?,frozen_locator_json=?,
                lease_owner=?,lease_expires_at=?
            WHERE session_key=? AND status='pending'
            """,
            (
                iso_utc(now),
                int(row["transcript_epoch"]),
                review_from,
                review_to,
                canonical_json_bytes(locator).decode("utf-8"),
                owner,
                iso_utc(now + config.lease_seconds),
                session_key_value,
            ),
        ).rowcount
        if changed != 1:
            raise sqlite3.IntegrityError("review_claim_race")
        connection.commit()
        return {
            "session_key": session_key_value,
            "generation": int(row["generation"]),
            "transcript_epoch": int(row["transcript_epoch"]),
            "review_from": review_from,
            "review_to": review_to,
            "locator": locator,
            "lease_owner": owner,
            "lease_expires_at": iso_utc(now + config.lease_seconds),
        }
    except BaseException:
        connection.rollback()
        raise


def heartbeat_review_generation(
    connection: sqlite3.Connection,
    session_key_value: str,
    owner: str,
    now: float,
    config: Config,
) -> bool:
    changed = connection.execute(
        """
        UPDATE review_items
        SET lease_expires_at=MAX(lease_expires_at,?)
        WHERE session_key=? AND status='reviewing' AND lease_owner=?
          AND lease_expires_at>=?
        """,
        (
            iso_utc(now + config.lease_seconds),
            session_key_value,
            owner,
            iso_utc(now),
        ),
    ).rowcount
    return changed == 1


def complete_review_generation(
    connection: sqlite3.Connection,
    session_key_value: str,
    owner: str,
    outcome: str,
    reason: Optional[str],
    now: float,
) -> dict[str, object]:
    if outcome not in {"reviewed", "excluded"}:
        raise ValueError("invalid_review_outcome")
    if outcome == "excluded" and not reason:
        raise ValueError("missing_exclusion_reason")
    if not connection.in_transaction:
        raise ValueError("active_review_transaction_required")
    row = connection.execute(
        """
        SELECT * FROM review_items
        WHERE session_key=? AND status='reviewing' AND lease_owner=?
          AND lease_expires_at>=?
        """,
        (session_key_value, owner, iso_utc(now)),
    ).fetchone()
    if row is None:
        raise ValueError("review_lease_unavailable")
    frozen_to = int(row["frozen_to"])
    current_locator_json = canonical_json_bytes(
        {
            "path": str(row["transcript_path"]),
            "size": int(row["transcript_size"]),
            "mtime_ns": int(row["transcript_mtime_ns"]),
            "device": int(row["transcript_device"]),
            "inode": int(row["transcript_inode"]),
        }
    ).decode("utf-8")
    new_work = (
        row["binding_status"] != "accepted"
        or int(row["transcript_epoch"]) != int(row["frozen_epoch"])
        or int(row["observed_boundary"]) > frozen_to
        or row["frozen_locator_json"] != current_locator_json
    )
    status = "pending" if new_work else outcome
    generation = int(row["generation"]) + int(new_work)
    pending_since = iso_utc(now) if new_work else None
    connection.execute(
        """
        UPDATE review_items
        SET status=?,generation=?,reviewed_boundary=?,reviewed_at=?,
            pending_since=?,excluded_reason=?,batch_id=NULL,
            review_started_at=NULL,frozen_epoch=NULL,frozen_from=NULL,
            frozen_to=NULL,frozen_locator_json=NULL,lease_owner=NULL,
            lease_expires_at=NULL
        WHERE session_key=?
        """,
        (
            status,
            generation,
            frozen_to,
            iso_utc(now),
            pending_since,
            reason if outcome == "excluded" and not new_work else None,
            session_key_value,
        ),
    )
    return {
        "session_key": session_key_value,
        "status": status,
        "generation": generation,
        "reviewed_boundary": frozen_to,
    }


def record_candidate_evidence(
    connection: sqlite3.Connection,
    candidate_id: int,
    review_item_id: int,
    owner: str,
    expected_generation: int,
    signal_type: str,
    source_kind: str,
    summary: str,
    now: float,
) -> bool:
    if not connection.in_transaction:
        raise ValueError("active_review_transaction_required")
    if (
        not isinstance(owner, str)
        or not owner
        or len(owner.encode("utf-8")) > 128
        or type(expected_generation) is not int
        or expected_generation < 1
    ):
        raise ValueError("review_evidence_lease_unavailable")
    row = connection.execute(
        """
        SELECT session_key,generation FROM review_items
        WHERE id=? AND status='reviewing' AND lease_owner=?
          AND generation=? AND lease_expires_at>=?
        """,
        (review_item_id, owner, expected_generation, iso_utc(now)),
    ).fetchone()
    if row is None:
        raise ValueError("review_evidence_lease_unavailable")
    changed = connection.execute(
        """
        INSERT OR IGNORE INTO candidate_evidence(
          candidate_id,review_item_id,session_key,generation,signal_type,
          source_kind,summary,created_at
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            candidate_id,
            review_item_id,
            str(row["session_key"]),
            int(row["generation"]),
            signal_type,
            source_kind,
            summary,
            iso_utc(now),
        ),
    ).rowcount
    return changed == 1


def spooled_stop_payload(
    event: CapturedSessionStop,
    key: str,
    config: Config,
    now: float,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "session_key": key,
        "raw_session_id": event.session_id,
        "diagnostic_turn_id": event.diagnostic_turn_id,
        "cwd": str(event.cwd),
        "transcript_path": str(event.transcript_path),
        "transcript_size": event.transcript_size,
        "transcript_mtime_ns": event.transcript_mtime_ns,
        "transcript_device": event.transcript_device,
        "transcript_inode": event.transcript_inode,
        "created_at": iso_utc(event.observed_at_ns / 1_000_000_000),
        "created_at_ns": event.observed_at_ns,
        "expires_at": iso_utc(now + config.pending_retention_days * 86_400),
    }


OVERFLOW_EVENT = b"1\n"
MAX_OVERFLOW_EVENT_BYTES = 65_536
MAX_SPOOL_SCAN_ENTRIES = 201


def record_spool_overflow(installation: Installation) -> None:
    path = installation.spool / "overflow.events"
    descriptor = os.open(
        str(path),
        os.O_WRONLY
        | os.O_APPEND
        | os.O_CREAT
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ValueError("spool_overflow_permissions")
        if not acquire_spool_lock(descriptor, timeout_seconds=0.005):
            return
        info = os.fstat(descriptor)
        if info.st_size + len(OVERFLOW_EVENT) <= MAX_OVERFLOW_EVENT_BYTES:
            os.write(descriptor, OVERFLOW_EVENT)
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


def acquire_spool_lock(
    descriptor: int, timeout_seconds: float = 0.05
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.005, remaining))


def spool_session_stop(
    installation: Installation,
    config: Config,
    event: CapturedSessionStop,
    key: str,
    now: float,
) -> bool:
    if type(event.observed_at_ns) is not int or event.observed_at_ns < 0:
        raise ValueError("invalid_observed_at_ns")
    lock_path = installation.spool / ".lock"
    if lock_path.is_symlink():
        raise ValueError("spool_lock_symlink")
    lock_path.touch(mode=0o600, exist_ok=True)
    with private_file(lock_path).open("r+b") as lock:
        if not acquire_spool_lock(lock.fileno()):
            record_spool_overflow(installation)
            return False
        files: list[Path] = []
        with os.scandir(installation.spool) as entries:
            for scanned, entry in enumerate(entries, start=1):
                # Seeing the 201st entry is enough to fail conservatively; do
                # not stat it or continue through an attacker-inflated spool.
                if scanned >= MAX_SPOOL_SCAN_ENTRIES:
                    record_spool_overflow(installation)
                    return False
                if not entry.name.endswith(".json"):
                    continue
                files.append(Path(entry.path))
                if len(files) >= config.spool_limit_files:
                    record_spool_overflow(installation)
                    return False
        total = 0
        for path in files:
            if path.is_symlink():
                raise ValueError("spool_payload_symlink")
            total += private_file(path).stat().st_size
        encoded = (
            canonical_json_bytes(
                spooled_stop_payload(event, key, config, now)
            )
            + b"\n"
        )
        if (
            len(files) >= config.spool_limit_files
            or total + len(encoded) > config.spool_limit_bytes
        ):
            record_spool_overflow(installation)
            return False
        destination = installation.spool / (
            f"{time.time_ns()}-{os.getpid()}-{secrets.token_hex(4)}.json"
        )
        atomic_write_bytes(destination, encoded)
        return True


def enqueue_stop(
    installation: Installation,
    config: Config,
    raw: bytes,
) -> str:
    event = parse_session_stop(raw, installation, config)
    if event is None:
        return "ignored"
    now = time.time()
    key = session_key(installation, event.session_id)
    try:
        connection = open_database(installation)
        try:
            return upsert_session(
                connection,
                event,
                key,
                config,
                now,
            )
        finally:
            connection.close()
    except sqlite3.Error:
        return (
            "spooled"
            if spool_session_stop(
                installation,
                config,
                event,
                key,
                now,
            )
            else "overflow"
        )


def write_json_stdout(value: object) -> None:
    sys.stdout.buffer.write(canonical_json_bytes(value) + b"\n")


def cmd_init(args: argparse.Namespace) -> int:
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    path = initialize_runtime(
        Path(args.data_root),
        tuple(Path(value) for value in args.transcript_root),
        config,
    )
    write_json_stdout({"status": "initialized", "installation": str(path)})
    return 0


def cmd_enqueue_stop(args: argparse.Namespace) -> int:
    try:
        installation = load_installation(Path(args.installation))
        config = load_config(installation)
        raw = sys.stdin.buffer.read(MAX_HOOK_BYTES + 1)
        enqueue_stop(installation, config, raw)
    except Exception:
        pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evolver.py")
    parser.add_argument("--version", action="version", version=VERSION)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--data-root", required=True)
    init.add_argument("--transcript-root", action="append", required=True)
    init.add_argument("--config", required=True)
    init.set_defaults(handler=cmd_init)
    enqueue = commands.add_parser("enqueue-stop")
    enqueue.add_argument("--installation", required=True)
    enqueue.set_defaults(handler=cmd_enqueue_stop)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
