"""Internal preparation from server-owned analysis snapshots, not HTTP input.

The caller must obtain the graph and source mapping from the same trusted,
commit-pinned analysis job. These consistency checks do not authenticate an
uploaded report. No network, filesystem access, or model calls happen here.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from interpretation import EvidencePacket, MAX_SOURCE_EXCERPT_CHARACTERS

MAX_SOURCE_FILE_BYTES = 1024 * 1024


class EvidencePreparationError(ValueError):
    """The selected symbol cannot be grounded in the supplied snapshot."""


@dataclass(frozen=True)
class PreparedSymbolEvidence:
    commit_sha: str
    packet_json: str
    source_excerpt: str

    @property
    def packet(self) -> EvidencePacket:
        # Return a new model so caller mutations cannot change the retained copy.
        return EvidencePacket.model_validate_json(self.packet_json)


def prepare_symbol_evidence(
    graph: Mapping[str, Any],
    source_files: Mapping[str, bytes],
    node_id: str,
    *,
    commit_sha: str,
) -> PreparedSymbolEvidence:
    """Select server evidence by node ID; never accept a client packet or range.

source_files contains bytes captured by the analysis job, keyed by its exact
repository-relative paths. The commit must be the server-resolved Git SHA.
The caller owns snapshot isolation, access control, and capture integrity.
"""
    if not re.fullmatch(r"[0-9a-f]{40}", commit_sha) or graph.get("snapshot", {}).get("commit_sha") != commit_sha:
        raise EvidencePreparationError("Analysis and source snapshot commit must match.")
    if graph.get("analysis", {}).get("tier") != "deep":
        raise EvidencePreparationError("Interpretation requires a deep analysis snapshot.")
    matches = [node for node in graph.get("nodes", []) if node.get("id") == node_id]
    if len(matches) != 1 or matches[0].get("kind") not in {"class", "function", "method"}:
        raise EvidencePreparationError("Select one unique analyzed symbol.")
    node = matches[0]
    try:
        packet = EvidencePacket.model_validate(node.get("evidence_packet"))
    except ValueError as exc:
        raise EvidencePreparationError("Symbol evidence packet is invalid or too large.") from exc
    location = packet.source_range
    if packet.node_id != node_id or location.model_dump() != {
        "path": node.get("path"), "start_line": node.get("start_line"), "end_line": node.get("end_line")
    }:
        raise EvidencePreparationError("Symbol and packet source identity must match.")
    path = location.path
    if not path or "\\" in path or ":" in path or any(part in {"", ".", ".."} for part in path.split("/")):
        raise EvidencePreparationError("Source path must be repository-relative and canonical.")

    # Check each reference category against this report, rather than treating
    # packet-provided IDs as proof that evidence exists.
    ids_by_group = {
        group: {item["id"] for item in graph.get(group, [])}
        for group in ("nodes", "edges", "flows", "findings", "patterns")
    }
    for refs, group in (
        (packet.related_edge_ids, "edges"), (packet.flow_ids, "flows"),
        (packet.finding_ids, "findings"), (packet.pattern_ids, "patterns"),
    ):
        if not set(refs) <= ids_by_group[group]:
            raise EvidencePreparationError("Evidence references are missing from the analysis snapshot.")
    all_ids = set().union(*ids_by_group.values())
    if any(not set(claim.evidence_refs) <= all_ids for claim in packet.claims):
        raise EvidencePreparationError("Claim references are missing from the analysis snapshot.")

    source = source_files.get(path)
    if not isinstance(source, bytes) or len(source) > MAX_SOURCE_FILE_BYTES:
        raise EvidencePreparationError("Captured source is missing or exceeds the file limit.")
    try:
        # Match Python physical line numbering, not str.splitlines() which also
        # treats Unicode separators and form feeds as new source lines.
        text = source.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as exc:
        raise EvidencePreparationError("Captured source is not UTF-8.") from exc
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    if location.end_line > len(lines):
        raise EvidencePreparationError("Symbol range extends beyond captured source.")
    excerpt = "\n".join(lines[location.start_line - 1:location.end_line])
    if len(excerpt) > MAX_SOURCE_EXCERPT_CHARACTERS:
        raise EvidencePreparationError("Selected symbol exceeds the source excerpt limit.")
    return PreparedSymbolEvidence(commit_sha, packet.model_dump_json(), excerpt)
