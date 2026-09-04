from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from evidence_store import EvidenceSnapshotStore, SnapshotCapacityError, SnapshotUnavailable
from test_interpretation_evidence import NODE, PIN, report

SOURCES = {"example.py": b"def run():\n    pass\n"}


def register(store, owner="owner-a", graph=None, sources=None):
    return store.register_trusted_snapshot(
        owner_key=owner, graph=report() if graph is None else graph,
        source_files=SOURCES if sources is None else sources, commit_sha=PIN,
    )


def test_store_returns_only_reference_and_resolves_isolated_evidence():
    store = EvidenceSnapshotStore()
    graph, sources = report(), dict(SOURCES)
    ref = register(store, graph=graph, sources=sources)
    assert len(ref.report_id) == 43
    assert ref.expires_in_seconds == 900
    graph["nodes"][0]["evidence_packet"]["summary"]["text"] = "edited in browser"
    sources["example.py"] = b"not the captured source"
    result = store.prepare(owner_key="owner-a", report_id=ref.report_id, node_id=NODE)
    assert result.packet.summary.text == "Defines run."
    assert result.source_excerpt == "def run():\n    pass"
    with pytest.raises(FrozenInstanceError):
        ref.report_id = "replacement"


def test_wrong_owner_missing_and_expired_have_same_error():
    now = [100.0]
    store = EvidenceSnapshotStore(clock=lambda: now[0], ttl_seconds=5)
    ref = register(store)
    errors = []
    for owner, report_id in [("owner-b", ref.report_id), ("owner-a", "missing")]:
        with pytest.raises(SnapshotUnavailable) as exc:
            store.prepare(owner_key=owner, report_id=report_id, node_id=NODE)
        errors.append(str(exc.value))
    now[0] = 105.0
    with pytest.raises(SnapshotUnavailable) as exc:
        store.prepare(owner_key="owner-a", report_id=ref.report_id, node_id=NODE)
    assert errors == [str(exc.value)] * 2
    assert store.usage() == {"snapshots": 0, "retained_bytes": 0}


def test_capacity_rejection_does_not_evict_existing_report():
    store = EvidenceSnapshotStore(max_snapshots=1)
    ref = register(store)
    with pytest.raises(SnapshotCapacityError): register(store, "owner-b")
    assert store.prepare(owner_key="owner-a", report_id=ref.report_id, node_id=NODE)


def test_per_owner_limit_does_not_block_other_owner():
    store = EvidenceSnapshotStore(max_per_owner=1)
    register(store)
    with pytest.raises(SnapshotCapacityError): register(store)
    register(store, "owner-b")
    assert store.usage()["snapshots"] == 2


def test_reads_do_not_extend_expiration_and_expiry_frees_capacity():
    now = [0.0]
    store = EvidenceSnapshotStore(clock=lambda: now[0], max_snapshots=1, ttl_seconds=5)
    ref = register(store)
    now[0] = 4.9
    store.prepare(owner_key="owner-a", report_id=ref.report_id, node_id=NODE)
    now[0] = 5.0
    replacement = register(store)
    assert replacement.report_id != ref.report_id
    assert store.usage()["snapshots"] == 1


def test_explicit_discard_requires_owner_and_releases_bytes():
    store = EvidenceSnapshotStore()
    ref = register(store)
    with pytest.raises(SnapshotUnavailable): store.discard(owner_key="owner-b", report_id=ref.report_id)
    store.discard(owner_key="owner-a", report_id=ref.report_id)
    assert store.usage() == {"snapshots": 0, "retained_bytes": 0}


def test_restart_loses_references_instead_of_falling_back_to_client_data():
    ref = register(EvidenceSnapshotStore())
    with pytest.raises(SnapshotUnavailable):
        EvidenceSnapshotStore().prepare(owner_key="owner-a", report_id=ref.report_id, node_id=NODE)


def test_total_byte_budget_rejects_without_partial_registration():
    probe = EvidenceSnapshotStore()
    register(probe)
    size = probe.usage()["retained_bytes"]
    store = EvidenceSnapshotStore(max_total_bytes=size, max_snapshot_bytes=size)
    register(store)
    with pytest.raises(SnapshotCapacityError): register(store, "owner-b")
    assert store.usage() == {"snapshots": 1, "retained_bytes": size}


@pytest.mark.parametrize("case", ["graph-limit", "source-limit", "unknown-source", "invalid-pin", "wrong-tier", "empty-owner"])
def test_invalid_registration_retains_nothing(case):
    store = EvidenceSnapshotStore(max_snapshot_bytes=4096)
    graph, sources, owner = report(), dict(SOURCES), "owner-a"
    if case == "graph-limit": graph["padding"] = "x" * 5000
    if case == "source-limit": sources["example.py"] = b"x" * (1024 * 1024 + 1)
    if case == "unknown-source": sources["secret.txt"] = b"secret"
    if case == "invalid-pin": graph["snapshot"]["commit_sha"] = "b" * 40
    if case == "wrong-tier": graph["analysis"]["tier"] = "basic"
    if case == "empty-owner": owner = ""
    with pytest.raises(ValueError): register(store, owner, graph, sources)
    assert store.usage() == {"snapshots": 0, "retained_bytes": 0}


def test_concurrent_registrations_cannot_exceed_capacity():
    store = EvidenceSnapshotStore(max_snapshots=2, max_per_owner=2)
    def attempt(_):
        try: return register(store).report_id
        except SnapshotCapacityError: return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        admitted = [ref for ref in pool.map(attempt, range(16)) if ref]
    assert len(admitted) == len(set(admitted)) == 2
    assert store.usage()["snapshots"] == 2


@pytest.mark.parametrize("options", [{"max_snapshots": 0}, {"ttl_seconds": -1},
    {"max_total_bytes": True}, {"max_snapshot_bytes": 1.5}, {"max_per_owner": 0}])
def test_invalid_store_limits_are_rejected(options):
    with pytest.raises(ValueError): EvidenceSnapshotStore(**options)


def test_unicode_is_charged_by_bytes():
    graph = report()
    graph["note"] = "界" * 20
    ascii_store, unicode_store = EvidenceSnapshotStore(), EvidenceSnapshotStore()
    graph["note"] = "x" * 20
    register(ascii_store, graph=graph)
    graph["note"] = "界" * 20
    register(unicode_store, graph=graph)
    assert unicode_store.usage()["retained_bytes"] - ascii_store.usage()["retained_bytes"] == 40


def test_reference_cannot_select_symbol_from_another_report():
    store = EvidenceSnapshotStore()
    first = register(store)
    second_graph = report()
    second_graph["nodes"][0]["id"] = "symbol:example.py:other"
    register(store, graph=second_graph)
    with pytest.raises(ValueError, match="unique analyzed symbol"):
        store.prepare(owner_key="owner-a", report_id=first.report_id, node_id="symbol:example.py:other")


def test_real_analyzer_report_survives_store_and_evidence_preparation():
    from analyzer import analyze_repository

    root = Path(__file__).parent / "tests" / "fixtures" / "portable-report"
    graph = analyze_repository(root)
    graph["snapshot"] = {"commit_sha": PIN}  # Local fixture, not a real Git pin.
    sources = {node["path"]: (root / node["path"]).read_bytes()
               for node in graph["nodes"] if node["kind"] == "file"}
    store = EvidenceSnapshotStore()
    ref = register(store, graph=graph, sources=sources)
    symbols = [node for node in graph["nodes"] if "evidence_packet" in node]
    assert symbols
    for node in symbols:
        prepared = store.prepare(owner_key="owner-a", report_id=ref.report_id, node_id=node["id"])
        assert prepared.packet.node_id == node["id"]
        assert prepared.commit_sha == PIN
