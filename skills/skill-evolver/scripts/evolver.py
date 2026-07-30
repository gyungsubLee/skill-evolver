#!/usr/bin/env python3
from __future__ import annotations

import argparse
import calendar
import errno
import fcntl
import hashlib
import hmac
import json
import os
import re
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
from typing import Mapping, Optional, Sequence, TypedDict
from urllib.parse import quote

VERSION = "skill-evolver 0.1.0"
SCHEMA_VERSION = 1
MAX_HOOK_BYTES = 65_536
SQLITE_INTEGER_MAX = 9_223_372_036_854_775_807

SKILL_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_REFERENCE_PATH = SKILL_ROOT / "references/runtime.json"
POLICY_PATH = SKILL_ROOT / "references/improvement-policy.md"

REVIEW_BATCH_SESSIONS_MAX = 5
TRANSCRIPT_SESSION_MAX_BYTES = 2_097_152
TRANSCRIPT_SESSION_MAX_RECORDS = 100
REVIEW_BATCH_MAX_BYTES = 8_388_608
MODEL_ENVELOPE_MAX_BYTES = 131_072
CATALOG_MAX_SKILLS = 512
CATALOG_FRONTMATTER_MAX_BYTES = 65_536
CATALOG_INSPECT_MAX_BYTES = 65_536
CATALOG_EXPORT_MAX_BYTES = 49_152
CATALOG_IDENTITY_MAX_BYTES = 272
CATALOG_DISPLAY_NAME_MAX_BYTES = 128
CATALOG_DESCRIPTION_MAX_BYTES = 384
RUNTIME_REFERENCE_MAX_BYTES = 8_192
POLICY_MAX_BYTES = 8_192
RESULT_SCHEMA_INSTRUCTIONS_MAX_BYTES = 8_192
CLAIM_CONTRACT_OVERHEAD_MAX_BYTES = 8_192
FIXED_MUTABLE_SKILL_ROOTS = (Path("/Users/igyeongseob/.codex/skills"),)

REVIEW_RUNTIME_FIXED = {
    "review_batch_sessions": REVIEW_BATCH_SESSIONS_MAX,
    "max_transcript_bytes": TRANSCRIPT_SESSION_MAX_BYTES,
    "max_transcript_records": TRANSCRIPT_SESSION_MAX_RECORDS,
    "max_review_batch_bytes": REVIEW_BATCH_MAX_BYTES,
    "max_candidates_per_session": 1,
    "max_candidates_per_batch": 3,
    "model_envelope_max_bytes": MODEL_ENVELOPE_MAX_BYTES,
    "catalog_max_skills": CATALOG_MAX_SKILLS,
    "catalog_frontmatter_max_bytes": CATALOG_FRONTMATTER_MAX_BYTES,
    "catalog_inspect_max_bytes": CATALOG_INSPECT_MAX_BYTES,
    "catalog_export_max_bytes": CATALOG_EXPORT_MAX_BYTES,
    "catalog_identity_max_bytes": CATALOG_IDENTITY_MAX_BYTES,
    "catalog_display_name_max_bytes": CATALOG_DISPLAY_NAME_MAX_BYTES,
    "catalog_description_max_bytes": CATALOG_DESCRIPTION_MAX_BYTES,
    "policy_max_bytes": POLICY_MAX_BYTES,
    "result_schema_instructions_max_bytes": (
        RESULT_SCHEMA_INSTRUCTIONS_MAX_BYTES
    ),
    "claim_contract_overhead_max_bytes": (
        CLAIM_CONTRACT_OVERHEAD_MAX_BYTES
    ),
}

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
HARD_LIMITS = {
    "spool_limit_files": 200,
    "spool_limit_bytes": 10_485_760,
    "review_batch_sessions": REVIEW_BATCH_SESSIONS_MAX,
    "max_transcript_bytes": TRANSCRIPT_SESSION_MAX_BYTES,
    "max_transcript_records": TRANSCRIPT_SESSION_MAX_RECORDS,
    "max_review_batch_bytes": REVIEW_BATCH_MAX_BYTES,
    "max_candidates_per_session": 1,
    "max_candidates_per_batch": 3,
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
class ReviewRuntime:
    mutable_skill_roots: tuple[Path, ...]
    review_batch_sessions: int
    max_transcript_bytes: int
    max_transcript_records: int
    max_review_batch_bytes: int
    max_candidates_per_session: int
    max_candidates_per_batch: int
    model_envelope_max_bytes: int
    catalog_max_skills: int
    catalog_frontmatter_max_bytes: int
    catalog_inspect_max_bytes: int
    catalog_export_max_bytes: int
    catalog_identity_max_bytes: int
    catalog_display_name_max_bytes: int
    catalog_description_max_bytes: int
    policy_max_bytes: int
    result_schema_instructions_max_bytes: int
    claim_contract_overhead_max_bytes: int


@dataclass(frozen=True)
class CatalogEntry:
    identity: str
    display_name: str
    description: str
    skill_dir: Path
    skill_sha256: str


@dataclass(frozen=True)
class CatalogSnapshot:
    entries: tuple[CatalogEntry, ...]
    export_bytes: bytes
    snapshot_digest: str
    rejected_count: int


class CatalogAdapterError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


SESSION_META_MAX_BYTES = 65_536
TRANSCRIPT_RELOCATION_SCAN_MAX_ENTRIES = 4_096
TRANSCRIPT_RELOCATION_SCAN_MAX_DEPTH = 8
TRANSCRIPT_EVIDENCE_SHAPE_MAX_NODES = 4_096
TRANSCRIPT_EVIDENCE_SHAPE_MAX_DEPTH = 64
TRANSCRIPT_RETRYABLE_CODES = frozenset(
    {"transcript_missing", "transcript_changed", "transcript_partial"}
)
TRANSCRIPT_TERMINAL_CODES = frozenset(
    {"oversized_session", "unsupported_transcript"}
)


@dataclass(frozen=True)
class TranscriptLocator:
    path: Path
    size: int
    mtime_ns: int
    device: int
    inode: int


@dataclass(frozen=True)
class FrozenTranscript:
    review_item_id: int
    session_key: str
    generation: int
    transcript_epoch: int
    frozen_from: int
    frozen_to: int
    locator: TranscriptLocator
    read_path: Path


@dataclass(frozen=True)
class TranscriptRecord:
    source_kind: str
    text: str
    evidence_eligible: bool
    scope: str
    byte_start: int
    byte_end: int


@dataclass(frozen=True)
class TranscriptExport:
    records: tuple[TranscriptRecord, ...]
    delta_source_bytes: int
    context_source_bytes: int
    canonical_records_bytes: int
    read_path_changed: bool


class TranscriptAdapterError(ValueError):
    def __init__(self, code: str, *, retryable: bool):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


def _transcript_error(code: str) -> TranscriptAdapterError:
    if code in TRANSCRIPT_RETRYABLE_CODES:
        return TranscriptAdapterError(code, retryable=True)
    if code in TRANSCRIPT_TERMINAL_CODES:
        return TranscriptAdapterError(code, retryable=False)
    raise ValueError("invalid_transcript_error_code")


def transcript_locator_payload(
    locator: TranscriptLocator,
) -> dict[str, object]:
    return {
        "path": str(locator.path),
        "size": locator.size,
        "mtime_ns": locator.mtime_ns,
        "device": locator.device,
        "inode": locator.inode,
    }


def transcript_locator_digest(locator: TranscriptLocator) -> str:
    return sha256_json(transcript_locator_payload(locator))


def frozen_transcript_from_row(row: sqlite3.Row) -> FrozenTranscript:
    try:
        locator_payload = json.loads(str(row["frozen_locator_json"]))
        if (
            not isinstance(locator_payload, dict)
            or set(locator_payload)
            != {"path", "size", "mtime_ns", "device", "inode"}
        ):
            raise ValueError("invalid_frozen_transcript")
        locator = TranscriptLocator(
            path=Path(locator_payload["path"]),
            size=locator_payload["size"],
            mtime_ns=locator_payload["mtime_ns"],
            device=locator_payload["device"],
            inode=locator_payload["inode"],
        )
        frozen = FrozenTranscript(
            review_item_id=int(row["id"]),
            session_key=str(row["session_key"]),
            generation=int(row["generation"]),
            transcript_epoch=int(row["frozen_epoch"]),
            frozen_from=int(row["frozen_from"]),
            frozen_to=int(row["frozen_to"]),
            locator=locator,
            read_path=Path(str(row["transcript_path"])),
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        raise ValueError("invalid_frozen_transcript") from None
    integers = (
        frozen.review_item_id,
        frozen.generation,
        frozen.transcript_epoch,
        frozen.frozen_from,
        frozen.frozen_to,
        locator.size,
        locator.mtime_ns,
        locator.device,
        locator.inode,
    )
    if (
        frozen.review_item_id < 1
        or frozen.generation < 1
        or not frozen.session_key
        or not frozen.read_path.is_absolute()
        or not locator.path.is_absolute()
        or any(type(value) is not int or value < 0 for value in integers)
        or frozen.frozen_to <= frozen.frozen_from
        or locator.size != frozen.frozen_to
    ):
        raise ValueError("invalid_frozen_transcript")
    return frozen


def transcript_adapter_contract(
    runtime: ReviewRuntime,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "format": "current-codex-jsonl-v1",
        "boundary": "half-open",
        "session_binding": "session-meta-hmac",
        "text_encoding": "strict-utf-8",
        "read_past_frozen_to": False,
        "context": "bounded-reverse-complete-records",
        "content_identity_fields": [
            "size",
            "mtime_ns",
            "device",
            "inode",
        ],
        "source_kinds": ["assistant", "tool_output", "user_direct"],
        "evidence_scope": "delta-only",
        "retryable_codes": sorted(TRANSCRIPT_RETRYABLE_CODES),
        "terminal_codes": sorted(TRANSCRIPT_TERMINAL_CODES),
        "relocation": {
            "roots": "installation-transcript-roots",
            "descriptor_relative": True,
            "current_owner_only": True,
            "same_device_inode_only": True,
            "scan_max_entries": (
                TRANSCRIPT_RELOCATION_SCAN_MAX_ENTRIES
            ),
            "scan_max_depth": TRANSCRIPT_RELOCATION_SCAN_MAX_DEPTH,
        },
        "limits": {
            "session_bytes": runtime.max_transcript_bytes,
            "session_records": runtime.max_transcript_records,
            "session_meta_bytes": SESSION_META_MAX_BYTES,
            "evidence_shape_nodes": (
                TRANSCRIPT_EVIDENCE_SHAPE_MAX_NODES
            ),
            "evidence_shape_depth": (
                TRANSCRIPT_EVIDENCE_SHAPE_MAX_DEPTH
            ),
        },
        "recognized": {
            "export": [
                "response_item/message/assistant",
                "response_item/message/user",
                "response_item/function_call_output",
                "response_item/custom_tool_call_output",
            ],
            "ignore": [
                "agent_message",
                "compacted",
                "event_msg",
                "inter_agent_communication_metadata",
                "response_item/agent_message",
                "response_item/function_call",
                "response_item/reasoning",
                "response_item/tool_search_call",
                "response_item/tool_search_output",
                "tool_search_call",
                "tool_search_output",
                "turn_context",
                "world_state",
            ],
        },
    }


def transcript_adapter_digest(runtime: ReviewRuntime) -> str:
    return sha256_json(transcript_adapter_contract(runtime))


TRANSCRIPT_IGNORED_TYPES = frozenset(
    {
        "agent_message",
        "compacted",
        "event_msg",
        "inter_agent_communication_metadata",
        "tool_search_call",
        "tool_search_output",
        "turn_context",
        "world_state",
    }
)
RESPONSE_ITEM_IGNORED_TYPES = frozenset(
    {
        "agent_message",
        "function_call",
        "reasoning",
        "tool_search_call",
        "tool_search_output",
    }
)


class TranscriptRecordMapping(TypedDict):
    source_kind: str
    text: str
    evidence_eligible: bool
    scope: str


def _validated_transcript_text(
    value: object,
    *,
    maximum_bytes: Optional[int] = None,
    allow_empty: bool = True,
) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise _transcript_error("unsupported_transcript")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise _transcript_error("unsupported_transcript") from None
    if maximum_bytes is not None and len(encoded) > maximum_bytes:
        raise _transcript_error("unsupported_transcript")
    return value


def _canonical_transcript_records(
    records: Sequence[TranscriptRecord],
) -> bytes:
    payload: list[TranscriptRecordMapping] = [
        {
            "source_kind": _validated_transcript_text(
                record.source_kind
            ),
            "text": _validated_transcript_text(record.text),
            "evidence_eligible": record.evidence_eligible,
            "scope": _validated_transcript_text(record.scope),
        }
        for record in records
    ]
    try:
        return canonical_json_bytes(payload)
    except UnicodeEncodeError:
        raise _transcript_error("unsupported_transcript") from None


def _contains_evidence_shape(value: object) -> bool:
    pending: list[tuple[object, int]] = [(value, 0)]
    scheduled = 1
    while pending:
        current, depth = pending.pop()
        if depth > TRANSCRIPT_EVIDENCE_SHAPE_MAX_DEPTH:
            raise _transcript_error("unsupported_transcript")
        if isinstance(current, dict):
            role = current.get("role")
            if role in {"user", "assistant"}:
                return True
            if any(
                key in current
                for key in ("message", "content", "output")
            ):
                return True
            children = current.values()
        elif isinstance(current, list):
            children = current
        else:
            continue
        for child in children:
            scheduled += 1
            if scheduled > TRANSCRIPT_EVIDENCE_SHAPE_MAX_NODES:
                raise _transcript_error("unsupported_transcript")
            child_depth = depth + 1
            if child_depth > TRANSCRIPT_EVIDENCE_SHAPE_MAX_DEPTH:
                raise _transcript_error("unsupported_transcript")
            pending.append((child, child_depth))
    return False


def _validate_session_meta(
    value: object,
    installation: Installation,
    expected_session_key: str,
) -> None:
    if not isinstance(value, dict):
        raise _transcript_error("unsupported_transcript")
    raw_session_id = _validated_transcript_text(
        value.get("session_id"),
        maximum_bytes=512,
        allow_empty=False,
    )
    if not hmac.compare_digest(
        session_key(installation, raw_session_id),
        expected_session_key,
    ):
        raise _transcript_error("unsupported_transcript")


def _message_texts(payload: Mapping[str, object]) -> list[str]:
    content = payload.get("content")
    if not isinstance(content, list):
        raise _transcript_error("unsupported_transcript")
    texts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            raise _transcript_error("unsupported_transcript")
        item_type = item.get("type")
        text = item.get("text")
        if item_type in {"input_text", "output_text"}:
            texts.append(_validated_transcript_text(text))
        elif item_type == "encrypted_content":
            continue
        else:
            raise _transcript_error("unsupported_transcript")
    return texts


def _classify_transcript_object(
    value: object,
    installation: Installation,
    frozen: FrozenTranscript,
    *,
    evidence_eligible: bool,
    byte_start: int,
    byte_end: int,
) -> list[TranscriptRecord]:
    if not isinstance(value, dict):
        if evidence_eligible:
            raise _transcript_error("unsupported_transcript")
        return []
    record_type = value.get("type")
    payload = value.get("payload")
    if record_type == "session_meta":
        _validate_session_meta(
            payload, installation, frozen.session_key
        )
        return []
    if record_type in TRANSCRIPT_IGNORED_TYPES:
        return []
    if record_type != "response_item":
        if evidence_eligible and _contains_evidence_shape(value):
            raise _transcript_error("unsupported_transcript")
        return []
    if not isinstance(payload, dict):
        if evidence_eligible:
            raise _transcript_error("unsupported_transcript")
        return []
    item_type = payload.get("type")
    if item_type in RESPONSE_ITEM_IGNORED_TYPES:
        return []
    scope = "delta" if evidence_eligible else "context_only"
    if item_type == "message":
        role = payload.get("role")
        if role in {"developer", "system"}:
            return []
        if role not in {"user", "assistant"}:
            if evidence_eligible and _contains_evidence_shape(payload):
                raise _transcript_error("unsupported_transcript")
            return []
        source_kind = "user_direct" if role == "user" else "assistant"
        return [
            TranscriptRecord(
                source_kind=source_kind,
                text=text,
                evidence_eligible=evidence_eligible,
                scope=scope,
                byte_start=byte_start,
                byte_end=byte_end,
            )
            for text in _message_texts(payload)
        ]
    if item_type in {
        "function_call_output",
        "custom_tool_call_output",
    }:
        output_value = payload.get("output")
        if not isinstance(output_value, str):
            if evidence_eligible:
                raise _transcript_error("unsupported_transcript")
            return []
        output = _validated_transcript_text(output_value)
        return [
            TranscriptRecord(
                source_kind="tool_output",
                text=output,
                evidence_eligible=evidence_eligible,
                scope=scope,
                byte_start=byte_start,
                byte_end=byte_end,
            )
        ]
    if evidence_eligible:
        raise _transcript_error("unsupported_transcript")
    return []


def _parse_jsonl_records(
    raw: bytes,
    base_offset: int,
    installation: Installation,
    frozen: FrozenTranscript,
    *,
    evidence_eligible: bool,
) -> list[TranscriptRecord]:
    records: list[TranscriptRecord] = []
    cursor = 0
    for line in raw.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            raise _transcript_error(
                "transcript_partial"
                if evidence_eligible
                else "unsupported_transcript"
            )
        try:
            value = json.loads(line)
        except (
            RecursionError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            if evidence_eligible:
                raise _transcript_error("unsupported_transcript") from None
            cursor += len(line)
            continue
        records.extend(
            _classify_transcript_object(
                value,
                installation,
                frozen,
                evidence_eligible=evidence_eligible,
                byte_start=base_offset + cursor,
                byte_end=base_offset + cursor + len(line),
            )
        )
        cursor += len(line)
    return records


def _read_exact_at(
    descriptor: int,
    start: int,
    length: int,
) -> bytes:
    chunks: list[bytes] = []
    offset = start
    remaining = length
    while remaining:
        try:
            chunk = os.pread(descriptor, remaining, offset)
        except (OSError, RuntimeError):
            raise _transcript_error("transcript_changed") from None
        if not chunk:
            raise _transcript_error("transcript_changed")
        chunks.append(chunk)
        offset += len(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _initial_session_meta(
    descriptor: int,
    installation: Installation,
    frozen: FrozenTranscript,
) -> None:
    chunks: list[bytes] = []
    offset = 0
    line = b""
    while offset < frozen.frozen_to:
        length = min(
            4_096,
            frozen.frozen_to - offset,
            SESSION_META_MAX_BYTES + 1 - offset,
        )
        if length <= 0:
            break
        chunk = _read_exact_at(descriptor, offset, length)
        chunks.append(chunk)
        combined = b"".join(chunks)
        newline = combined.find(b"\n")
        if newline >= 0:
            if newline + 1 > SESSION_META_MAX_BYTES:
                raise _transcript_error("unsupported_transcript")
            line = combined[: newline + 1]
            break
        offset += len(chunk)
    if not line:
        raise _transcript_error(
            "transcript_partial"
            if frozen.frozen_to <= SESSION_META_MAX_BYTES
            else "unsupported_transcript"
        )
    try:
        value = json.loads(line)
    except (
        RecursionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        raise _transcript_error("unsupported_transcript") from None
    if not isinstance(value, dict) or value.get("type") != "session_meta":
        raise _transcript_error("unsupported_transcript")
    _validate_session_meta(
        value.get("payload"), installation, frozen.session_key
    )


def _bounded_reverse_context(
    descriptor: int,
    frozen_from: int,
    maximum: int,
) -> tuple[int, bytes]:
    if frozen_from <= 0 or maximum <= 0:
        return frozen_from, b""
    start = max(0, frozen_from - maximum)
    raw = _read_exact_at(descriptor, start, frozen_from - start)
    if start:
        preceding = _read_exact_at(descriptor, start - 1, 1)
        if preceding != b"\n":
            newline = raw.find(b"\n")
            if newline < 0:
                return frozen_from, b""
            start += newline + 1
            raw = raw[newline + 1 :]
    if raw and not raw.endswith(b"\n"):
        return frozen_from, b""
    return start, raw


def _close_transcript_descriptor(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except (OSError, RuntimeError):
        pass


def _open_matching_transcript(
    path: Path,
    installation: Installation,
    frozen: FrozenTranscript,
) -> Optional[int]:
    try:
        if (
            path.is_symlink()
            or path.resolve(strict=True) != path
            or not within(path, installation.transcript_roots)
        ):
            raise _transcript_error("transcript_changed")
    except FileNotFoundError:
        return None
    except TranscriptAdapterError:
        raise
    except (OSError, RuntimeError):
        raise _transcript_error("transcript_changed") from None
    try:
        descriptor = os.open(
            str(path),
            os.O_RDONLY
            | os.O_NONBLOCK
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except FileNotFoundError:
        return None
    except (OSError, RuntimeError):
        raise _transcript_error("transcript_changed") from None
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or (info.st_dev, info.st_ino)
            != (frozen.locator.device, frozen.locator.inode)
        ):
            raise _transcript_error("transcript_changed")
    except TranscriptAdapterError:
        _close_transcript_descriptor(descriptor)
        raise
    except (OSError, RuntimeError):
        _close_transcript_descriptor(descriptor)
        raise _transcript_error("transcript_changed") from None
    except BaseException:
        _close_transcript_descriptor(descriptor)
        raise
    return descriptor


def _find_relocated_transcript(
    installation: Installation,
    frozen: FrozenTranscript,
) -> tuple[int, Path]:
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = (
        os.O_RDONLY
        | os.O_NONBLOCK
        | getattr(os, "O_NOFOLLOW", 0)
    )

    def open_directory(
        name: str,
        *,
        parent_descriptor: Optional[int] = None,
    ) -> int:
        try:
            if parent_descriptor is None:
                descriptor = os.open(name, directory_flags)
            else:
                descriptor = os.open(
                    name,
                    directory_flags,
                    dir_fd=parent_descriptor,
                )
        except (OSError, RuntimeError):
            raise _transcript_error("transcript_changed") from None
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
            ):
                raise _transcript_error("transcript_changed")
        except TranscriptAdapterError:
            _close_transcript_descriptor(descriptor)
            raise
        except (OSError, RuntimeError):
            _close_transcript_descriptor(descriptor)
            raise _transcript_error("transcript_changed") from None
        except BaseException:
            _close_transcript_descriptor(descriptor)
            raise
        return descriptor

    def open_candidate(
        directory_descriptor: int,
        name: str,
    ) -> int:
        try:
            descriptor = os.open(
                name,
                file_flags,
                dir_fd=directory_descriptor,
            )
        except (OSError, RuntimeError):
            raise _transcript_error("transcript_changed") from None
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or (info.st_dev, info.st_ino)
                != (frozen.locator.device, frozen.locator.inode)
            ):
                raise _transcript_error("transcript_changed")
        except TranscriptAdapterError:
            _close_transcript_descriptor(descriptor)
            raise
        except (OSError, RuntimeError):
            _close_transcript_descriptor(descriptor)
            raise _transcript_error("transcript_changed") from None
        except BaseException:
            _close_transcript_descriptor(descriptor)
            raise
        return descriptor

    scanned = 0

    def scan_directory(
        directory_descriptor: int,
        root: Path,
        components: tuple[str, ...],
        depth: int,
    ) -> Optional[tuple[int, Path]]:
        nonlocal scanned
        try:
            entries = os.scandir(directory_descriptor)
        except (OSError, RuntimeError):
            raise _transcript_error("transcript_changed") from None
        try:
            with entries:
                while True:
                    try:
                        entry = next(entries)
                    except StopIteration:
                        break
                    except (OSError, RuntimeError):
                        raise _transcript_error(
                            "transcript_changed"
                        ) from None
                    scanned += 1
                    if scanned > TRANSCRIPT_RELOCATION_SCAN_MAX_ENTRIES:
                        raise _transcript_error("transcript_changed")
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except (OSError, RuntimeError):
                        raise _transcript_error(
                            "transcript_changed"
                        ) from None
                    if stat.S_ISLNK(info.st_mode):
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        if info.st_uid != os.getuid():
                            raise _transcript_error(
                                "transcript_changed"
                            )
                        if depth >= TRANSCRIPT_RELOCATION_SCAN_MAX_DEPTH:
                            continue
                        child_descriptor = open_directory(
                            entry.name,
                            parent_descriptor=directory_descriptor,
                        )
                        try:
                            found = scan_directory(
                                child_descriptor,
                                root,
                                components + (entry.name,),
                                depth + 1,
                            )
                        finally:
                            _close_transcript_descriptor(
                                child_descriptor
                            )
                        if found is not None:
                            return found
                        continue
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or (info.st_dev, info.st_ino)
                        != (
                            frozen.locator.device,
                            frozen.locator.inode,
                        )
                    ):
                        continue
                    if info.st_uid != os.getuid():
                        raise _transcript_error("transcript_changed")
                    resolved_path = root.joinpath(
                        *components, entry.name
                    )
                    return (
                        open_candidate(
                            directory_descriptor, entry.name
                        ),
                        resolved_path,
                    )
        except TranscriptAdapterError:
            raise
        except (OSError, RuntimeError):
            raise _transcript_error("transcript_changed") from None
        return None

    for root in installation.transcript_roots:
        root_descriptor = open_directory(str(root))
        try:
            found = scan_directory(root_descriptor, root, (), 0)
        finally:
            _close_transcript_descriptor(root_descriptor)
        if found is not None:
            return found
    raise _transcript_error("transcript_missing")


def _open_frozen_transcript(
    installation: Installation,
    frozen: FrozenTranscript,
) -> tuple[int, Path]:
    descriptor = _open_matching_transcript(
        frozen.read_path, installation, frozen
    )
    if descriptor is not None:
        return descriptor, frozen.read_path
    return _find_relocated_transcript(installation, frozen)


def _stable_frozen_stat(
    info: os.stat_result,
    frozen: FrozenTranscript,
) -> tuple[int, int, int, int]:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or (info.st_dev, info.st_ino)
        != (frozen.locator.device, frozen.locator.inode)
        or info.st_size < frozen.frozen_to
        or (
            info.st_size == frozen.locator.size
            and info.st_mtime_ns != frozen.locator.mtime_ns
        )
    ):
        raise _transcript_error("transcript_changed")
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
    )


def _stable_frozen_descriptor_stat(
    descriptor: int,
    frozen: FrozenTranscript,
) -> tuple[int, int, int, int]:
    try:
        info = os.fstat(descriptor)
    except (OSError, RuntimeError):
        raise _transcript_error("transcript_changed") from None
    return _stable_frozen_stat(info, frozen)


def read_frozen_transcript(
    installation: Installation,
    frozen: FrozenTranscript,
    config: Config,
    runtime: ReviewRuntime,
) -> TranscriptExport:
    byte_limit = min(
        config.max_transcript_bytes, runtime.max_transcript_bytes
    )
    record_limit = min(
        config.max_transcript_records, runtime.max_transcript_records
    )
    delta_length = frozen.frozen_to - frozen.frozen_from
    descriptor, resolved_path = _open_frozen_transcript(
        installation, frozen
    )
    try:
        before = _stable_frozen_descriptor_stat(descriptor, frozen)
        header_error: Optional[TranscriptAdapterError] = None
        try:
            _initial_session_meta(descriptor, installation, frozen)
        except TranscriptAdapterError as error:
            header_error = error
        after_header = _stable_frozen_descriptor_stat(
            descriptor, frozen
        )
        if after_header != before:
            raise _transcript_error("transcript_changed")
        if header_error is not None:
            raise header_error
        data_error: Optional[TranscriptAdapterError] = None
        delta = b""
        context = b""
        delta_records: list[TranscriptRecord] = []
        context_records: list[TranscriptRecord] = []
        try:
            if delta_length > byte_limit:
                raise _transcript_error("oversized_session")
            delta = _read_exact_at(
                descriptor, frozen.frozen_from, delta_length
            )
            if not delta.endswith(b"\n"):
                raise _transcript_error("transcript_partial")
            delta_records = _parse_jsonl_records(
                delta,
                frozen.frozen_from,
                installation,
                frozen,
                evidence_eligible=True,
            )
            if len(delta_records) > record_limit:
                raise _transcript_error("oversized_session")
            context_start, context = _bounded_reverse_context(
                descriptor,
                frozen.frozen_from,
                byte_limit - len(delta),
            )
            context_records = _parse_jsonl_records(
                context,
                context_start,
                installation,
                frozen,
                evidence_eligible=False,
            )
            remaining_records = record_limit - len(delta_records)
            if len(context_records) > remaining_records:
                context_records = context_records[-remaining_records:]
                if not remaining_records:
                    context_records = []
        except TranscriptAdapterError as error:
            data_error = error
        after = _stable_frozen_descriptor_stat(descriptor, frozen)
        if after != before:
            raise _transcript_error("transcript_changed")
        if data_error is not None:
            raise data_error
    finally:
        _close_transcript_descriptor(descriptor)
    records = tuple([*context_records, *delta_records])
    canonical = _canonical_transcript_records(records)
    return TranscriptExport(
        records=records,
        delta_source_bytes=len(delta),
        context_source_bytes=len(context),
        canonical_records_bytes=len(canonical),
        read_path_changed=resolved_path != frozen.locator.path,
    )


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


def load_review_runtime() -> ReviewRuntime:
    with RUNTIME_REFERENCE_PATH.open("rb") as stream:
        encoded = stream.read(RUNTIME_REFERENCE_MAX_BYTES + 1)
    if len(encoded) > RUNTIME_REFERENCE_MAX_BYTES:
        raise ValueError("review_runtime_too_large")
    try:
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("invalid_review_runtime") from None
    if (
        type(payload) is not dict
        or set(payload)
        != {
            "schema_version",
            "version",
            "installation",
            "mutable_skill_roots",
            "review_limits",
        }
    ):
        raise ValueError("invalid_review_runtime")
    review_limits = payload["review_limits"]
    if (
        type(payload["schema_version"]) is not int
        or payload["schema_version"] != 1
        or type(payload["version"]) is not str
        or payload["version"] != "0.1.0"
        or type(payload["installation"]) is not str
        or payload["installation"]
        != "/Users/igyeongseob/.codex/skill-evolver/installation.json"
        or type(payload["mutable_skill_roots"]) is not list
        or any(
            type(value) is not str
            for value in payload["mutable_skill_roots"]
        )
        or payload["mutable_skill_roots"]
        != [str(path) for path in FIXED_MUTABLE_SKILL_ROOTS]
        or type(review_limits) is not dict
        or set(review_limits) != set(REVIEW_RUNTIME_FIXED)
        or any(type(value) is not int for value in review_limits.values())
        or review_limits != REVIEW_RUNTIME_FIXED
    ):
        raise ValueError("invalid_review_runtime")
    return ReviewRuntime(
        mutable_skill_roots=FIXED_MUTABLE_SKILL_ROOTS,
        **REVIEW_RUNTIME_FIXED,
    )


def load_improvement_policy(runtime: ReviewRuntime) -> bytes:
    with POLICY_PATH.open("rb") as stream:
        encoded = stream.read(runtime.policy_max_bytes + 1)
    if len(encoded) > runtime.policy_max_bytes:
        raise ValueError("improvement_policy_too_large")
    try:
        text = encoded.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("invalid_improvement_policy") from None
    if not text.strip():
        raise ValueError("invalid_improvement_policy")
    return encoded


def improvement_policy_digest(policy: bytes) -> str:
    return hashlib.sha256(policy).hexdigest()


FRONTMATTER_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z")
FRONTMATTER_FORBIDDEN_PREFIXES = (
    "!",
    "&",
    "*",
    "{",
    "[",
    "]",
    "}",
    ",",
    "#",
    "|",
    ">",
    "%",
    "@",
    "`",
)
FRONTMATTER_IMPLICIT_NON_STRINGS = frozenset(
    {"null", "true", "false", "yes", "no", "on", "off", "y", "n"}
)


def _frontmatter_scalar(
    lines: list[str],
    index: int,
    encoded: str,
    *,
    require_string: bool,
) -> tuple[str, int]:
    value = encoded.strip()
    if value in {">", ">-", "|", "|-"}:
        parts: list[str] = []
        cursor = index + 1
        while cursor < len(lines):
            line = lines[cursor]
            if line and not line[0].isspace():
                break
            if line.strip():
                parts.append(line.strip())
            cursor += 1
        if not parts:
            raise ValueError("invalid_skill_frontmatter")
        return " ".join(parts), cursor
    if not value or value.startswith(FRONTMATTER_FORBIDDEN_PREFIXES):
        raise ValueError("invalid_skill_frontmatter")
    if value.startswith('"'):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            raise ValueError("invalid_skill_frontmatter") from None
        if not isinstance(decoded, str):
            raise ValueError("invalid_skill_frontmatter")
        return decoded, index + 1
    if value.startswith("'"):
        if not re.fullmatch(r"'(?:[^']|'')*'", value):
            raise ValueError("invalid_skill_frontmatter")
        return value[1:-1].replace("''", "'"), index + 1
    if (
        re.search(r"\s#", value)
        or re.search(r":(?:\s|$)", value)
        or (
            value[0] in "-?"
            and (len(value) == 1 or value[1].isspace())
        )
        or (
            require_string
            and (
                not (value[0].isalpha() or value[0] == "_")
                or value.casefold() in FRONTMATTER_IMPLICIT_NON_STRINGS
            )
        )
    ):
        raise ValueError("invalid_skill_frontmatter")
    return value, index + 1


def parse_frontmatter_scalars(
    raw: bytes,
    maximum: int,
) -> tuple[str, str]:
    if type(maximum) is not int or maximum <= 0:
        raise ValueError("invalid_frontmatter_limit")
    if len(raw) > maximum:
        raise ValueError("skill_frontmatter_too_large")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("invalid_skill_frontmatter") from None
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("invalid_skill_frontmatter")
    try:
        closing = lines.index("---", 1)
    except ValueError:
        raise ValueError("invalid_skill_frontmatter") from None
    header = lines[1:closing]
    fields: dict[str, str] = {}
    cursor = 0
    while cursor < len(header):
        line = header[cursor]
        if not line.strip() or line.lstrip().startswith("#"):
            cursor += 1
            continue
        if line[0].isspace() or ":" not in line:
            raise ValueError("invalid_skill_frontmatter")
        key, encoded = line.split(":", 1)
        if not FRONTMATTER_KEY.fullmatch(key) or key in fields:
            raise ValueError("invalid_skill_frontmatter")
        value, cursor = _frontmatter_scalar(
            header,
            cursor,
            encoded,
            require_string=key in {"name", "description"},
        )
        normalized = unicodedata.normalize(
            "NFC", " ".join(value.split())
        )
        if not normalized:
            raise ValueError("invalid_skill_frontmatter")
        fields[key] = normalized
    try:
        name = fields["name"]
        description = fields["description"]
    except KeyError:
        raise ValueError("invalid_skill_frontmatter") from None
    if not name or not description:
        raise ValueError("invalid_skill_frontmatter")
    return name, description


CATALOG_EXCLUDED_NAMES = frozenset({".system", "skill-evolver"})


def catalog_adapter_contract(
    runtime: ReviewRuntime,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "roots": [str(path) for path in runtime.mutable_skill_roots],
        "direct_children_only": True,
        "current_user_only": True,
        "no_symlinks": True,
        "forbidden_mode_mask": 0o022,
        "excluded_names": sorted(CATALOG_EXCLUDED_NAMES),
        "required_file": "SKILL.md",
        "frontmatter_parser": "stdlib-scalar-v1",
        "limits": {
            "skills": runtime.catalog_max_skills,
            "frontmatter_bytes": runtime.catalog_frontmatter_max_bytes,
            "inspect_bytes": runtime.catalog_inspect_max_bytes,
            "export_bytes": runtime.catalog_export_max_bytes,
            "identity_bytes": runtime.catalog_identity_max_bytes,
            "display_name_bytes": (
                runtime.catalog_display_name_max_bytes
            ),
            "description_bytes": runtime.catalog_description_max_bytes,
        },
        "export_fields": ["description", "display_name", "identity"],
        "snapshot_fields": ["identity", "path", "skill_sha256"],
    }


def catalog_adapter_digest(runtime: ReviewRuntime) -> str:
    return sha256_json(catalog_adapter_contract(runtime))


def _catalog_root_descriptor(root: Path) -> int:
    try:
        if (
            not root.is_absolute()
            or root.is_symlink()
            or root.resolve(strict=True) != root
        ):
            raise CatalogAdapterError("catalog_root_invalid")
        descriptor = os.open(
            str(root),
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except (OSError, RuntimeError):
        raise CatalogAdapterError("catalog_root_invalid") from None
    info = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o022
    ):
        os.close(descriptor)
        raise CatalogAdapterError("catalog_root_invalid")
    return descriptor


def _read_catalog_entry(
    runtime: ReviewRuntime,
    root: Path,
    root_descriptor: int,
    name: str,
) -> tuple[CatalogEntry, bytes]:
    directory_descriptor = os.open(
        name,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=root_descriptor,
    )
    try:
        directory_info = os.fstat(directory_descriptor)
        if (
            not stat.S_ISDIR(directory_info.st_mode)
            or directory_info.st_uid != os.getuid()
            or directory_info.st_mode & 0o022
        ):
            raise ValueError("unsafe_catalog_directory")
        skill_descriptor = os.open(
            "SKILL.md",
            os.O_RDONLY
            | os.O_NONBLOCK
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_descriptor,
        )
        try:
            skill_info = os.fstat(skill_descriptor)
            if (
                not stat.S_ISREG(skill_info.st_mode)
                or skill_info.st_uid != os.getuid()
                or skill_info.st_mode & 0o022
            ):
                raise ValueError("unsafe_catalog_file")
            if skill_info.st_size > runtime.catalog_inspect_max_bytes:
                raise CatalogAdapterError("catalog_inspect_too_large")
            chunks: list[bytes] = []
            remaining = runtime.catalog_inspect_max_bytes + 1
            while remaining:
                chunk = os.read(skill_descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            if len(raw) > runtime.catalog_inspect_max_bytes:
                raise CatalogAdapterError("catalog_inspect_too_large")
            after = os.fstat(skill_descriptor)
            if (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ) != (
                skill_info.st_dev,
                skill_info.st_ino,
                skill_info.st_size,
                skill_info.st_mtime_ns,
            ):
                raise ValueError("changed_catalog_file")
        finally:
            os.close(skill_descriptor)
    finally:
        os.close(directory_descriptor)
    display_name, description = parse_frontmatter_scalars(
        raw, runtime.catalog_frontmatter_max_bytes
    )
    identity = f"user-skill:{name}"
    if (
        len(identity.encode("utf-8"))
        > runtime.catalog_identity_max_bytes
        or len(display_name.encode("utf-8"))
        > runtime.catalog_display_name_max_bytes
        or len(description.encode("utf-8"))
        > runtime.catalog_description_max_bytes
    ):
        raise ValueError("catalog_field_too_large")
    return (
        CatalogEntry(
            identity=identity,
            display_name=display_name,
            description=description,
            skill_dir=root / name,
            skill_sha256=hashlib.sha256(raw).hexdigest(),
        ),
        raw,
    )


def catalog_export_payload(
    snapshot: CatalogSnapshot,
) -> list[dict[str, str]]:
    return [
        {
            "identity": entry.identity,
            "display_name": entry.display_name,
            "description": entry.description,
        }
        for entry in snapshot.entries
    ]


def build_catalog_snapshot(runtime: ReviewRuntime) -> CatalogSnapshot:
    if len(runtime.mutable_skill_roots) != 1:
        raise CatalogAdapterError("catalog_root_invalid")
    root = runtime.mutable_skill_roots[0]
    descriptor = _catalog_root_descriptor(root)
    entries: list[CatalogEntry] = []
    rejected = scanned = 0
    try:
        with os.scandir(descriptor) as children:
            for child in children:
                if child.name in CATALOG_EXCLUDED_NAMES:
                    continue
                scanned += 1
                if scanned > runtime.catalog_max_skills:
                    raise CatalogAdapterError(
                        "catalog_inventory_saturated"
                    )
                try:
                    entry, _ = _read_catalog_entry(
                        runtime, root, descriptor, child.name
                    )
                except (
                    CatalogAdapterError,
                    OSError,
                    UnicodeError,
                    ValueError,
                ):
                    rejected += 1
                    continue
                entries.append(entry)
    finally:
        os.close(descriptor)
    entries.sort(key=lambda entry: entry.identity)
    provisional = CatalogSnapshot(
        entries=tuple(entries),
        export_bytes=b"",
        snapshot_digest="",
        rejected_count=rejected,
    )
    export_bytes = canonical_json_bytes(
        catalog_export_payload(provisional)
    )
    if len(export_bytes) > runtime.catalog_export_max_bytes:
        raise CatalogAdapterError("catalog_export_too_large")
    digest_payload = [
        {
            "identity": entry.identity,
            "path": str(entry.skill_dir),
            "skill_sha256": entry.skill_sha256,
        }
        for entry in entries
    ]
    return CatalogSnapshot(
        entries=tuple(entries),
        export_bytes=export_bytes,
        snapshot_digest=sha256_json(digest_payload),
        rejected_count=rejected,
    )


def resolve_catalog_target(
    snapshot: CatalogSnapshot,
    target_identity: str,
) -> CatalogEntry:
    for entry in snapshot.entries:
        if entry.identity == target_identity:
            return entry
    raise CatalogAdapterError("catalog_target_unknown")


def inspect_catalog_target(
    runtime: ReviewRuntime,
    snapshot: CatalogSnapshot,
    target_identity: str,
) -> bytes:
    expected = resolve_catalog_target(snapshot, target_identity)
    if len(runtime.mutable_skill_roots) != 1:
        raise CatalogAdapterError("catalog_root_invalid")
    root = runtime.mutable_skill_roots[0]
    if expected.skill_dir.parent != root:
        raise CatalogAdapterError("catalog_target_changed")
    descriptor = _catalog_root_descriptor(root)
    try:
        try:
            current, raw = _read_catalog_entry(
                runtime, root, descriptor, expected.skill_dir.name
            )
        except CatalogAdapterError:
            raise
        except (OSError, UnicodeError, ValueError):
            raise CatalogAdapterError("catalog_target_changed") from None
    finally:
        os.close(descriptor)
    if current != expected:
        raise CatalogAdapterError("catalog_target_changed")
    return raw


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
    for key, maximum in HARD_LIMITS.items():
        if int(merged[key]) > maximum:
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
    for key, maximum in HARD_LIMITS.items():
        if values[key] > maximum:
            raise ValueError(f"invalid_config_{key}")
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
        else:
            connection.execute(
                """
                UPDATE review_items
                SET first_stop_at=MIN(first_stop_at,?),
                    dedupe_expires_at=MIN(dedupe_expires_at,?),
                    raw_metadata_expires_at=CASE
                      WHEN raw_redacted_at IS NULL
                        AND raw_metadata_expires_at IS NOT NULL
                      THEN MIN(raw_metadata_expires_at,?)
                      ELSE raw_metadata_expires_at
                    END,
                    pending_since=CASE
                      WHEN generation=1 AND status='pending'
                      THEN CASE
                        WHEN pending_since IS NULL THEN ?
                        ELSE MIN(pending_since,?)
                      END
                      ELSE pending_since
                    END
                WHERE session_key=?
                """,
                (
                    now_text,
                    iso_utc(
                        now + config.session_dedupe_days * 86_400
                    ),
                    iso_utc(
                        now + config.raw_metadata_ttl_days * 86_400
                    ),
                    now_text,
                    now_text,
                    key,
                ),
            )
            row = connection.execute(
                "SELECT * FROM review_items WHERE session_key=?",
                (key,),
            ).fetchone()
        if (
            row is not None
            and row["status"] != "expired"
            and row["raw_redacted_at"] is None
        ):
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
                excluded_reason = row["excluded_reason"]
                if status not in {"pending", "reviewing"} and new_work:
                    reserve_pending_session(connection, config, now)
                    status = "pending"
                    generation += 1
                    pending_since = now_text
                    excluded_reason = None
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
                        pending_since=?,excluded_reason=?,error_code=?
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
                        excluded_reason,
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


def spool_payload_hmac(
    installation: Installation, payload: dict[str, object]
) -> str:
    return hmac.new(
        installation.identity_key.read_bytes(),
        b"spool\0" + canonical_json_bytes(payload),
        "sha256",
    ).hexdigest()


def spooled_stop_payload(
    installation: Installation,
    event: CapturedSessionStop,
    key: str,
    config: Config,
) -> dict[str, object]:
    created_at = event.observed_at_ns / 1_000_000_000
    body: dict[str, object] = {
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
        "created_at": iso_utc(created_at),
        "created_at_ns": event.observed_at_ns,
        "expires_at": iso_utc(
            created_at + config.pending_retention_days * 86_400
        ),
    }
    return {
        **body,
        "payload_hmac": spool_payload_hmac(installation, body),
    }


OVERFLOW_EVENT = b"1\n"
MAX_OVERFLOW_EVENT_BYTES = 65_536
MAX_SPOOL_SCAN_ENTRIES = 203
MAX_SPOOL_FUTURE_SKEW_SECONDS = 300


def record_spool_overflow(installation: Installation) -> None:
    path = installation.spool / "overflow.events"
    try:
        descriptor = os.open(
            str(path),
            os.O_WRONLY
            | os.O_APPEND
            | os.O_CREAT
            | os.O_NONBLOCK
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as error:
        if error.errno == errno.ENXIO:
            raise ValueError("spool_overflow_permissions") from None
        raise
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise ValueError("spool_overflow_permissions")
        if not acquire_spool_lock(descriptor, timeout_seconds=0.005):
            return
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise ValueError("spool_overflow_permissions")
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
    try:
        lock = open_locked_spool(installation)
    except BlockingIOError:
        record_spool_overflow(installation)
        return False
    with lock:
        file_limit = min(
            config.spool_limit_files, HARD_LIMITS["spool_limit_files"]
        )
        byte_limit = min(
            config.spool_limit_bytes, HARD_LIMITS["spool_limit_bytes"]
        )
        files: list[Path] = []
        scanned_total = 0
        with os.scandir(installation.spool) as entries:
            for scanned, entry in enumerate(entries, start=1):
                scanned_total = scanned
                # Admit 200 payloads plus the lock/overflow sidecars; the next
                # entry proves attacker-inflated inventory and ends the scan.
                if scanned >= MAX_SPOOL_SCAN_ENTRIES:
                    record_spool_overflow(installation)
                    return False
                if not entry.name.endswith(".json"):
                    continue
                files.append(Path(entry.path))
                if len(files) >= file_limit:
                    record_spool_overflow(installation)
                    return False
        if scanned_total + 1 >= MAX_SPOOL_SCAN_ENTRIES:
            record_spool_overflow(installation)
            return False
        total = 0
        for path in files:
            if path.is_symlink():
                raise ValueError("spool_payload_symlink")
            total += private_file(path).stat().st_size
        encoded = (
            canonical_json_bytes(
                spooled_stop_payload(installation, event, key, config)
            )
            + b"\n"
        )
        if (
            len(files) >= file_limit
            or total + len(encoded) > byte_limit
        ):
            record_spool_overflow(installation)
            return False
        destination = installation.spool / (
            f"{time.time_ns()}-{os.getpid()}-{secrets.token_hex(4)}.json"
        )
        atomic_write_bytes(destination, encoded)
        return True


def parse_iso_utc(value: str) -> float:
    try:
        parsed = float(
            calendar.timegm(
                time.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
            )
        )
    except (OSError, OverflowError, TypeError, ValueError):
        raise ValueError("invalid_iso_utc") from None
    if iso_utc(parsed) != value:
        raise ValueError("invalid_iso_utc")
    return parsed


def event_from_spool(
    payload: object,
    installation: Installation,
    config: Config,
    now: float,
) -> tuple[CapturedSessionStop, str, float, float]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("invalid_spool_schema")
    required = {
        "schema_version",
        "session_key",
        "raw_session_id",
        "diagnostic_turn_id",
        "cwd",
        "transcript_path",
        "transcript_size",
        "transcript_mtime_ns",
        "transcript_device",
        "transcript_inode",
        "created_at",
        "created_at_ns",
        "expires_at",
        "payload_hmac",
    }
    if set(payload) != required:
        raise ValueError("invalid_spool_fields")
    supplied_hmac = payload["payload_hmac"]
    if not isinstance(supplied_hmac, str) or len(supplied_hmac) != 64:
        raise ValueError("invalid_spool_hmac")
    body = {
        name: value
        for name, value in payload.items()
        if name != "payload_hmac"
    }
    if not hmac.compare_digest(
        supplied_hmac, spool_payload_hmac(installation, body)
    ):
        raise ValueError("invalid_spool_hmac")

    raw_session_id = bounded_string(body, "raw_session_id", 512)
    assert raw_session_id is not None
    diagnostic = bounded_string(
        body, "diagnostic_turn_id", 512, required=False
    )
    cwd_value = bounded_string(body, "cwd", 4_096)
    transcript_value = bounded_string(body, "transcript_path", 4_096)
    assert cwd_value is not None and transcript_value is not None
    cwd = Path(cwd_value)
    transcript = Path(transcript_value)
    if (
        not cwd.is_absolute()
        or not transcript.is_absolute()
        or Path(os.path.normpath(cwd_value)) != cwd
        or Path(os.path.normpath(transcript_value)) != transcript
    ):
        raise ValueError("invalid_spool_path")
    if not within(transcript, installation.transcript_roots):
        raise ValueError("invalid_spool_transcript_root")
    integers = [
        body["transcript_size"],
        body["transcript_mtime_ns"],
        body["transcript_device"],
        body["transcript_inode"],
    ]
    if any(
        type(value) is not int
        or value < 0
        or value > SQLITE_INTEGER_MAX
        for value in integers
    ):
        raise ValueError("invalid_spool_stat")
    created_at_ns = body["created_at_ns"]
    created_at_text = body["created_at"]
    expires_at_text = body["expires_at"]
    if (
        type(created_at_ns) is not int
        or created_at_ns < 0
        or not isinstance(created_at_text, str)
        or not isinstance(expires_at_text, str)
    ):
        raise ValueError("invalid_spool_time")
    if created_at_ns > int(
        (now + MAX_SPOOL_FUTURE_SKEW_SECONDS) * 1_000_000_000
    ):
        raise ValueError("spool_capture_in_future")
    created_at = created_at_ns / 1_000_000_000
    if iso_utc(created_at) != created_at_text:
        raise ValueError("inconsistent_spool_created_at")
    signed_expires_at = parse_iso_utc(expires_at_text)
    if signed_expires_at < created_at:
        raise ValueError("inconsistent_spool_expiry")
    expires_at = min(
        signed_expires_at,
        created_at + config.pending_retention_days * 86_400,
    )
    key = body["session_key"]
    if not isinstance(key, str) or not hmac.compare_digest(
        key, session_key(installation, raw_session_id)
    ):
        raise ValueError("invalid_spool_session_key")
    return (
        CapturedSessionStop(
            session_id=raw_session_id,
            diagnostic_turn_id=diagnostic,
            cwd=cwd,
            transcript_path=transcript,
            transcript_size=int(body["transcript_size"]),
            transcript_mtime_ns=int(body["transcript_mtime_ns"]),
            transcript_device=int(body["transcript_device"]),
            transcript_inode=int(body["transcript_inode"]),
            observed_at_ns=created_at_ns,
        ),
        key,
        min(created_at, now),
        expires_at,
    )


def open_locked_spool(installation: Installation):
    lock_path = installation.spool / ".lock"
    descriptor = os.open(
        str(lock_path),
        os.O_RDWR
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
            or info.st_nlink != 1
        ):
            raise ValueError("spool_lock_permissions")
        if not acquire_spool_lock(descriptor):
            raise BlockingIOError(
                errno.EWOULDBLOCK, "spool_lock_busy"
            )
        return os.fdopen(descriptor, "r+b", buffering=0)
    except BaseException:
        os.close(descriptor)
        raise


def bounded_spool_paths(
    installation: Installation,
    maximum_payloads: int,
) -> tuple[list[Path], bool]:
    paths: list[Path] = []
    with os.scandir(installation.spool) as entries:
        for scanned, entry in enumerate(entries, start=1):
            if entry.name.endswith(".json"):
                if len(paths) >= maximum_payloads:
                    return paths, True
                paths.append(Path(entry.path))
            if scanned >= MAX_SPOOL_SCAN_ENTRIES:
                return paths, True
    return paths, False


def read_spool_snapshot(
    path: Path,
) -> tuple[str, Optional[bytes], Optional[tuple[int, int]]]:
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return "missing", None, None
    identity = (before.st_dev, before.st_ino)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.getuid()
        or stat.S_IMODE(before.st_mode) != 0o600
        or before.st_nlink != 1
        or before.st_size > MAX_HOOK_BYTES
    ):
        return "invalid", None, identity
    try:
        descriptor = os.open(
            str(path),
            os.O_RDONLY
            | os.O_NONBLOCK
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except FileNotFoundError:
        return "missing", None, None
    except OSError as error:
        if error.errno == errno.ELOOP:
            return "preserved", None, identity
        raise
    try:
        info = os.fstat(descriptor)
        if (
            (info.st_dev, info.st_ino) != identity
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or info.st_size > MAX_HOOK_BYTES
        ):
            return (
                "preserved"
                if (info.st_dev, info.st_ino) != identity
                else "invalid"
            ), None, identity
        chunks: list[bytes] = []
        remaining = MAX_HOOK_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        encoded = b"".join(chunks)
        if len(encoded) > MAX_HOOK_BYTES:
            return "invalid", None, identity
        return "verified", encoded, identity
    finally:
        os.close(descriptor)


def delete_spool_identity(
    installation: Installation,
    path: Path,
    identity: tuple[int, int],
) -> bool:
    with open_locked_spool(installation):
        try:
            current = os.lstat(path)
        except FileNotFoundError:
            return False
        if (current.st_dev, current.st_ino) != identity:
            return False
        try:
            if stat.S_ISDIR(current.st_mode):
                os.rmdir(path)
            else:
                os.unlink(path)
        except OSError as error:
            if error.errno in {errno.ENOTEMPTY, errno.EEXIST}:
                return False
            raise
        return True


def import_spool(
    connection: sqlite3.Connection,
    installation: Installation,
    config: Config,
    now: float,
) -> dict[str, int]:
    if connection.in_transaction:
        raise ValueError("active_transaction")
    imported = duplicates = invalid = expired = preserved = 0
    deleted_any = False

    def remove_snapshot(path: Path, identity: tuple[int, int]) -> bool:
        nonlocal deleted_any
        removed = delete_spool_identity(installation, path, identity)
        deleted_any = deleted_any or removed
        return removed

    with open_locked_spool(installation):
        paths, saturated = bounded_spool_paths(
            installation, HARD_LIMITS["spool_limit_files"]
        )
    verified: list[
        tuple[
            int,
            str,
            Path,
            tuple[int, int],
            CapturedSessionStop,
            str,
            float,
        ]
    ] = []
    for path in paths:
        with open_locked_spool(installation):
            state, encoded, identity = read_spool_snapshot(path)
        if state == "missing":
            continue
        assert identity is not None
        if state == "preserved":
            preserved += 1
            continue
        if state == "invalid":
            if remove_snapshot(path, identity):
                invalid += 1
            else:
                preserved += 1
            continue
        assert encoded is not None
        try:
            payload = json.loads(encoded.decode("utf-8"))
            event, key, created_at, expires_at = event_from_spool(
                payload, installation, config, now
            )
            if now >= expires_at:
                if remove_snapshot(path, identity):
                    expired += 1
                else:
                    preserved += 1
                continue
            verified.append(
                (
                    event.observed_at_ns,
                    path.name,
                    path,
                    identity,
                    event,
                    key,
                    created_at,
                )
            )
        except (
            KeyError,
            OverflowError,
            RecursionError,
            TypeError,
            UnicodeError,
            ValueError,
            json.JSONDecodeError,
        ):
            if remove_snapshot(path, identity):
                invalid += 1
            else:
                preserved += 1
    verified.sort(key=lambda item: (item[0], item[1]))
    for _, _, path, identity, event, key, created_at in verified:
        outcome = upsert_session(
            connection,
            event,
            key,
            config,
            created_at,
        )
        imported += int(outcome == "inserted")
        duplicates += int(outcome != "inserted")
        if not remove_snapshot(path, identity):
            preserved += 1
    if deleted_any:
        fsync_directory(installation.spool)
    return {
        "spool_imported": imported,
        "spool_duplicates": duplicates,
        "spool_invalid_deleted": invalid,
        "spool_expired": expired,
        "spool_preserved": preserved,
        "spool_scan_saturated": int(saturated),
    }


def run_maintenance(
    connection: sqlite3.Connection,
    installation: Installation,
    config: Config,
    now: float,
) -> dict[str, int]:
    if connection.in_transaction:
        raise ValueError("active_transaction")
    counts = import_spool(connection, installation, config, now)
    leases_recovered = recover_expired_review_leases(connection, now)
    connection.execute("BEGIN IMMEDIATE")
    try:
        retention_ids = [
            int(row["id"])
            for row in connection.execute(
                """
                SELECT id FROM review_items
                WHERE status='pending' AND pending_since <= ?
                ORDER BY pending_since,id
                """,
                (
                    iso_utc(
                        now - config.pending_retention_days * 86_400
                    ),
                ),
            )
        ]
        pending_expired = expire_session_ids(
            connection, retention_ids, "retention", now
        )
        pending_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM review_items WHERE status='pending'"
            ).fetchone()[0]
        )
        capacity_ids = [
            int(row["id"])
            for row in connection.execute(
                """
                SELECT id FROM review_items
                WHERE status='pending'
                ORDER BY pending_since,id
                LIMIT ?
                """,
                (max(pending_count - config.pending_limit_sessions, 0),),
            )
        ]
        capacity_expired = expire_session_ids(
            connection, capacity_ids, "capacity", now
        )
        raw_redacted = connection.execute(
            f"""
            UPDATE review_items
            SET status=CASE
                  WHEN status IN ('pending','reviewing') THEN 'expired'
                  ELSE status
                END,
                excluded_reason=CASE
                  WHEN status IN ('pending','reviewing')
                  THEN COALESCE(excluded_reason,'raw_metadata_ttl')
                  ELSE excluded_reason
                END,
                reviewed_at=CASE
                  WHEN status IN ('pending','reviewing') THEN ?
                  ELSE reviewed_at
                END,
                batch_id=NULL,review_started_at=NULL,frozen_epoch=NULL,
                frozen_from=NULL,frozen_to=NULL,frozen_locator_json=NULL,
                lease_owner=NULL,lease_expires_at=NULL,raw_redacted_at=?,
                {RAW_CLEAR_ASSIGNMENTS}
            WHERE raw_redacted_at IS NULL
              AND raw_metadata_expires_at <= ?
            """,
            (iso_utc(now), iso_utc(now), iso_utc(now)),
        ).rowcount
        dedupe_cutoff = iso_utc(now)
        connection.execute(
            """
            DELETE FROM candidate_evidence
            WHERE session_key IN (
              SELECT session_key FROM review_items
              WHERE dedupe_expires_at <= ?
                AND status NOT IN ('pending','reviewing')
            )
            """,
            (dedupe_cutoff,),
        )
        dedupe_deleted = connection.execute(
            """
            DELETE FROM review_items
            WHERE dedupe_expires_at <= ?
              AND status NOT IN ('pending','reviewing')
            """,
            (dedupe_cutoff,),
        ).rowcount
        connection.execute(
            """
            INSERT INTO metadata(key,value) VALUES('last_maintenance_at',?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (iso_utc(now),),
        )
        if capacity_expired:
            connection.execute(
                """
                INSERT INTO metadata(key,value)
                VALUES('capacity_expired_count',?)
                ON CONFLICT(key) DO UPDATE SET
                  value=CAST(CAST(value AS INTEGER)+excluded.value AS TEXT)
                """,
                (str(capacity_expired),),
            )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return {
        **counts,
        "leases_recovered": leases_recovered,
        "pending_expired": pending_expired,
        "capacity_expired": capacity_expired,
        "raw_redacted": raw_redacted,
        "dedupe_deleted": dedupe_deleted,
    }


def spool_inventory(installation: Installation) -> tuple[int, int, bool]:
    count = total = 0
    paths, saturated = bounded_spool_paths(
        installation, DEFAULTS["spool_limit_files"]
    )
    for path in paths:
        try:
            info = os.lstat(path)
        except OSError:
            continue
        if (
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600
        ):
            count += 1
            total += info.st_size
    return count, total, saturated


def queue_status(
    connection: sqlite3.Connection,
    installation: Installation,
    now: float,
) -> dict[str, object]:
    pending = connection.execute(
        """
        SELECT COUNT(*) AS sessions,MIN(pending_since) AS oldest
        FROM review_items WHERE status='pending'
        """
    ).fetchone()
    generations = int(
        connection.execute(
            "SELECT COALESCE(SUM(generation),0) FROM review_items"
        ).fetchone()[0]
    )
    status_counts = {
        str(row["status"]): int(row["count"])
        for row in connection.execute(
            """
            SELECT status,COUNT(*) AS count
            FROM review_items GROUP BY status
            """
        )
    }
    leases = connection.execute(
        """
        SELECT
          COALESCE(SUM(CASE WHEN lease_expires_at>=? THEN 1 ELSE 0 END),0)
            AS active,
          COALESCE(SUM(CASE WHEN lease_expires_at<? THEN 1 ELSE 0 END),0)
            AS expired
        FROM review_items WHERE status='reviewing'
        """,
        (iso_utc(now), iso_utc(now)),
    ).fetchone()
    binding_failures = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM review_items
            WHERE binding_status='pending_epoch'
               OR error_code IN (
                 'transcript_rebind_required',
                 'session_binding_unavailable'
               )
            """
        ).fetchone()[0]
    )
    overdue_raw = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM review_items
            WHERE raw_redacted_at IS NULL
              AND raw_metadata_expires_at <= ?
            """,
            (iso_utc(now),),
        ).fetchone()[0]
    )
    metadata = {
        str(row["key"]): str(row["value"])
        for row in connection.execute(
            """
            SELECT key,value FROM metadata
            WHERE key IN (
              'last_hook_success_at',
              'last_maintenance_at',
              'capacity_expired_count'
            )
            """
        )
    }
    spool_files, spool_bytes, spool_saturated = spool_inventory(
        installation
    )
    overflow_path = installation.spool / "overflow.events"
    try:
        overflow_info = os.lstat(overflow_path)
        overflow_bytes = (
            overflow_info.st_size
            if stat.S_ISREG(overflow_info.st_mode)
            and overflow_info.st_uid == os.getuid()
            and stat.S_IMODE(overflow_info.st_mode) == 0o600
            else 0
        )
    except FileNotFoundError:
        overflow_bytes = 0
    oldest = pending["oldest"]
    return {
        "schema_version": SCHEMA_VERSION,
        "pending_sessions": int(pending["sessions"]),
        "pending_generations": int(pending["sessions"]),
        "generation_count_total": generations,
        "oldest_pending_age_seconds": (
            max(0, int(now - parse_iso_utc(str(oldest))))
            if oldest is not None
            else None
        ),
        "sessions_by_status": status_counts,
        "leases": {
            "active": int(leases["active"]),
            "expired": int(leases["expired"]),
        },
        "binding_failures": binding_failures,
        "spool": {
            "files": spool_files,
            "bytes": spool_bytes,
            "scan_saturated": spool_saturated,
            "overflow_total": overflow_bytes // len(OVERFLOW_EVENT),
            "overflow_counter_saturated": (
                overflow_bytes + len(OVERFLOW_EVENT)
                > MAX_OVERFLOW_EVENT_BYTES
            ),
        },
        "last_hook_success_at": metadata.get("last_hook_success_at"),
        "raw_metadata_cleanup": {
            "overdue_sessions": overdue_raw,
            "last_maintenance_at": metadata.get("last_maintenance_at"),
        },
        "capacity_expired_total": int(
            metadata.get("capacity_expired_count", "0")
        ),
        "checked_at": iso_utc(now),
    }


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


def cmd_maintain(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    config = load_config(installation)
    connection = open_database(installation)
    try:
        result = run_maintenance(
            connection, installation, config, time.time()
        )
    finally:
        connection.close()
    write_json_stdout(result)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    connection = open_database(installation, read_only=True)
    try:
        result = queue_status(connection, installation, time.time())
    finally:
        connection.close()
    write_json_stdout(result)
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
    maintain = commands.add_parser("maintain")
    maintain.add_argument("--installation", required=True)
    maintain.set_defaults(handler=cmd_maintain)
    status = commands.add_parser("status")
    status.add_argument("--installation", required=True)
    status.set_defaults(handler=cmd_status)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
