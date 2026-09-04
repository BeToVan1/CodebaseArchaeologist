from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3
import subprocess
import sys
from unittest.mock import patch

import pytest

import deep_quota
import interpretation_budget as budget
from repository_loader import public_git_environment


@pytest.fixture
def ledger(tmp_path):
    directory = tmp_path / "private-budget"
    directory.mkdir(mode=0o700)
    return directory / "budget.sqlite3"


def test_reserve_reopen_and_reinitialize_never_reset(ledger):
    budget.initialize(ledger, limit_units=10)
    assert budget.reserve(ledger, units=6).remaining_units == 4
    assert budget.initialize(ledger, limit_units=10).reserved_units == 6
    with pytest.raises(budget.BudgetExceeded): budget.reserve(ledger, units=5)
    assert budget.status(ledger).reservations == 1
    assert budget.reserve(ledger, units=4).remaining_units == 0
    with pytest.raises(budget.BudgetExceeded): budget.reserve(ledger, units=1)


def test_policy_change_is_not_an_implicit_top_up(ledger):
    budget.initialize(ledger, limit_units=10)
    budget.reserve(ledger, units=10)
    with pytest.raises(budget.BudgetUnavailable): budget.initialize(ledger, limit_units=100)
    assert budget.status(ledger).remaining_units == 0


@pytest.mark.parametrize("operation", ["status", "reserve"])
def test_missing_storage_fails_closed_without_creating_file(ledger, operation):
    with pytest.raises(budget.BudgetUnavailable):
        budget.status(ledger) if operation == "status" else budget.reserve(ledger, units=1)
    assert not ledger.exists()


@pytest.mark.parametrize("units", [0, -1, True, 1.5, "1", 10**12 + 1])
def test_invalid_charge_cannot_change_budget(ledger, units):
    budget.initialize(ledger, limit_units=10)
    with pytest.raises(ValueError): budget.reserve(ledger, units=units)
    assert budget.status(ledger).reserved_units == 0


def test_foreign_quota_ledger_is_not_modified(ledger):
    deep_quota.initialize(ledger)
    original = ledger.read_bytes()
    with pytest.raises(budget.BudgetUnavailable): budget.initialize(ledger, limit_units=10)
    with pytest.raises(budget.BudgetUnavailable): budget.reserve(ledger, units=1)
    assert ledger.read_bytes() == original


def test_corrupt_ledger_is_not_repaired(ledger):
    ledger.write_bytes(b"not a database")
    ledger.chmod(0o600)
    with pytest.raises(budget.BudgetUnavailable): budget.initialize(ledger, limit_units=10)
    assert ledger.read_bytes() == b"not a database"


def test_concurrent_connections_never_overspend(ledger):
    budget.initialize(ledger, limit_units=5)
    def attempt(_):
        try:
            budget.reserve(ledger, units=1)
            return True
        except (budget.BudgetExceeded, budget.BudgetUnavailable):
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        admitted = sum(pool.map(attempt, range(20)))
    state = budget.status(ledger)
    assert 1 <= admitted <= 5
    assert state.reserved_units == state.reservations == admitted


def test_locked_ledger_denies_work(ledger):
    budget.initialize(ledger, limit_units=10)
    with closing(sqlite3.connect(ledger)) as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(budget.BudgetUnavailable): budget.reserve(ledger, units=1)
    assert budget.status(ledger).reserved_units == 0


def test_failure_after_commit_keeps_charge_without_approval(ledger):
    budget.initialize(ledger, limit_units=10)
    original_connect = budget.connect
    class AmbiguousCommit:
        def __init__(self, connection): self.connection = connection
        def close(self): self.connection.close()
        def execute(self, sql, *args):
            result = self.connection.execute(sql, *args)
            if sql == "COMMIT": raise sqlite3.OperationalError("synthetic lost acknowledgement")
            return result
    with patch.object(budget, "connect", side_effect=lambda path: AmbiguousCommit(original_connect(path))):
        with pytest.raises(budget.BudgetUnavailable): budget.reserve(ledger, units=6)
    assert budget.status(ledger).reserved_units == 6


def test_reservation_survives_a_separate_process(ledger):
    budget.initialize(ledger, limit_units=10)
    code = "import sys; from interpretation_budget import reserve; reserve(sys.argv[1], units=7)"
    result = subprocess.run([sys.executable, "-c", code, str(ledger)],
        env=public_git_environment(), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert budget.status(ledger).reserved_units == 7


@pytest.mark.parametrize("damage", ["version", "identity", "empty", "trigger"])
def test_unexpected_schema_or_state_denies_reservation(ledger, damage):
    budget.initialize(ledger, limit_units=10)
    with sqlite3.connect(ledger) as db:
        if damage == "version": db.execute("PRAGMA user_version=999")
        if damage == "identity": db.execute("PRAGMA application_id=0")
        if damage == "empty": db.execute("DELETE FROM budget_state")
        if damage == "trigger": db.execute("CREATE TRIGGER unexpected AFTER UPDATE ON budget_state BEGIN SELECT 1; END")
    with pytest.raises(budget.BudgetUnavailable): budget.reserve(ledger, units=1)
