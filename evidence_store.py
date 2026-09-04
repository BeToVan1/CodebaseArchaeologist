"""Bounded, process-local store for trusted analysis snapshots.

INTERNAL ONLY: register only from the server analysis job, never from uploaded
JSON. owner_key must come from server-authenticated identity, not a request
field. This module supplies ownership checks, not authentication or source
capture. Restart loses references; it is not a durable quota/spending ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import secrets
from threading import Lock
import time
from typing import Any, Callable, Mapping

from interpretation_evidence import MAX_SOURCE_FILE_BYTES, PreparedSymbolEvidence, prepare_symbol_evidence


class SnapshotCapacityError(ValueError):
    """A snapshot cannot be admitted within the configured limits."""


class SnapshotUnavailable(LookupError):
    """A reference is absent, expired, or not owned by this caller."""


@dataclass(frozen=True)
class SnapshotReference:
    report_id: str
    expires_in_seconds: int


@dataclass(frozen=True)
class _Snapshot:
    owner_key: str
    commit_sha: str
    expires_at: float
    graph_json: bytes
    source_files: dict[str, bytes]
    retained_bytes: int


class EvidenceSnapshotStore:
    def __init__(self, *, max_snapshots: int = 8, max_per_owner: int = 2,
                 max_snapshot_bytes: int = 4 * 1024 * 1024,
                 max_total_bytes: int = 16 * 1024 * 1024,
                 ttl_seconds: int = 900, clock: Callable[[], float] = time.monotonic):
        for value in (max_snapshots, max_per_owner, max_snapshot_bytes, max_total_bytes, ttl_seconds):
            if type(value) is not int or value < 1:
                raise ValueError("Snapshot limits must be positive integers.")
        self._max_snapshots = max_snapshots
        self._max_per_owner = max_per_owner
        self._max_snapshot_bytes = min(max_snapshot_bytes, max_total_bytes)
        self._max_total_bytes = max_total_bytes
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = Lock()
        self._snapshots: dict[str, _Snapshot] = {}
        self._retained_bytes = 0

    def _purge_expired(self, now: float) -> None:
        for report_id in [key for key, value in self._snapshots.items() if value.expires_at <= now]:
            self._retained_bytes -= self._snapshots.pop(report_id).retained_bytes

    def register_trusted_snapshot(self, *, owner_key: str, graph: Mapping[str, Any],
                                  source_files: Mapping[str, bytes], commit_sha: str) -> SnapshotReference:
        """Copy one completed trusted job; caller must prevent mutation during capture.

Limits count retained serialized graph/source payload, not Python heap overhead
or the analysis job's existing input objects. Per-file/count limits also bound
source-map overhead. No HTTP route should expose this registration method.
"""
        if not isinstance(owner_key, str) or not owner_key.strip() or len(owner_key) > 256:
            raise ValueError("A server-established owner identity is required.")
        if not isinstance(commit_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
            raise ValueError("A server-resolved commit SHA is required.")
        if graph.get("snapshot", {}).get("commit_sha") != commit_sha or graph.get("analysis", {}).get("tier") != "deep":
            raise ValueError("A matching deep analysis snapshot is required.")
        if len(source_files) > 3000:
            raise SnapshotCapacityError("Snapshot source file count exceeds the limit.")

        # Serializing under the lock bounds concurrent retained/copy operations.
        # This is deliberately not an admission queue or raw HTTP body limiter.
        with self._lock:
            self._purge_expired(self._clock())
            if len(self._snapshots) >= self._max_snapshots or sum(
                item.owner_key == owner_key for item in self._snapshots.values()
            ) >= self._max_per_owner:
                raise SnapshotCapacityError("Snapshot capacity is currently unavailable.")
            remaining = min(self._max_snapshot_bytes, self._max_total_bytes - self._retained_bytes)
            graph_bytes = bytearray()
            encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            for chunk in encoder.iterencode(graph):
                encoded = chunk.encode("utf-8")
                if len(graph_bytes) + len(encoded) > remaining:
                    raise SnapshotCapacityError("Snapshot exceeds the byte budget.")
                graph_bytes.extend(encoded)
            # Use only the detached graph to establish allowed source paths.
            copied_graph = json.loads(graph_bytes)
            allowed_paths = {node.get("path") for node in copied_graph.get("nodes", [])}
            copied_sources = {}
            size = len(graph_bytes) + len(owner_key.encode("utf-8")) + len(commit_sha) + 43
            for path, source in source_files.items():
                if (not isinstance(path, str) or not path or len(path) > 500 or path not in allowed_paths
                        or "\\" in path or ":" in path
                        or any(part in {"", ".", ".."} for part in path.split("/"))):
                    raise ValueError("Source paths must belong to this analysis snapshot.")
                if not isinstance(source, bytes) or len(source) > MAX_SOURCE_FILE_BYTES:
                    raise SnapshotCapacityError("Captured source exceeds the file limit or is invalid.")
                size += len(path.encode("utf-8")) + len(source)
                if size > remaining:
                    raise SnapshotCapacityError("Snapshot exceeds the byte budget.")
                copied_sources[path] = source  # bytes are immutable; copy the mapping only.
            if size > remaining:
                raise SnapshotCapacityError("Snapshot exceeds the byte budget.")
            report_id = secrets.token_urlsafe(32)
            while report_id in self._snapshots:
                report_id = secrets.token_urlsafe(32)
            self._snapshots[report_id] = _Snapshot(
                owner_key, commit_sha, self._clock() + self._ttl,
                bytes(graph_bytes), copied_sources, size,
            )
            self._retained_bytes += size
            return SnapshotReference(report_id, self._ttl)

    def _owned_snapshot(self, owner_key: str, report_id: str) -> _Snapshot:
        self._purge_expired(self._clock())
        item = self._snapshots.get(report_id)
        if item is None or item.owner_key != owner_key:
            # Do not reveal whether another owner has this report reference.
            raise SnapshotUnavailable("Analysis snapshot is unavailable; run a new analysis.")
        return item

    def prepare(self, *, owner_key: str, report_id: str, node_id: str) -> PreparedSymbolEvidence:
        with self._lock:
            item = self._owned_snapshot(owner_key, report_id)
            return prepare_symbol_evidence(json.loads(item.graph_json), item.source_files,
                                           node_id, commit_sha=item.commit_sha)

    def discard(self, *, owner_key: str, report_id: str) -> None:
        with self._lock:
            item = self._owned_snapshot(owner_key, report_id)
            del self._snapshots[report_id]
            self._retained_bytes -= item.retained_bytes

    def usage(self) -> dict[str, int]:
        """Internal aggregate counters, never report IDs, identities, or source."""
        with self._lock:
            self._purge_expired(self._clock())
            return {"snapshots": len(self._snapshots), "retained_bytes": self._retained_bytes}
