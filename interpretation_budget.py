"""Persistent conservative reservation counter for future interpretation calls.

Internal only; not wired to a provider or HTTP route. Units are abstract, not
dollars. The caller must establish a worst-case request charge before this can
bound spending. Reservations are permanent: no refunds, rollover or auto-reset.
"""
from contextlib import closing
from dataclasses import dataclass
import os
import sqlite3

# Reuse only existing filesystem/SQLite safety helpers, never the quota schema
# or admissions operations. A separate application ID prevents ledger mix-ups.
from deep_quota import QuotaUnavailable, checked_path, connect

MAX_UNITS = 10**12
APPLICATION_ID = 0x43414231
SCHEMA = (
    "CREATE TABLE budget_state (id INTEGER PRIMARY KEY CHECK(id=1), "
    "limit_units INTEGER NOT NULL CHECK(typeof(limit_units)='integer' AND limit_units BETWEEN 1 AND 1000000000000), "
    "reserved_units INTEGER NOT NULL CHECK(typeof(reserved_units)='integer' AND reserved_units BETWEEN 0 AND limit_units), "
    "reservations INTEGER NOT NULL CHECK(typeof(reservations)='integer' AND reservations BETWEEN 0 AND reserved_units))"
)


class BudgetUnavailable(RuntimeError):
    """Storage/configuration failure; do not dispatch a model request."""


class BudgetExceeded(RuntimeError):
    """The lifetime reservation allowance is exhausted."""


@dataclass(frozen=True)
class BudgetStatus:
    limit_units: int
    reserved_units: int
    reservations: int

    @property
    def remaining_units(self):
        return self.limit_units - self.reserved_units


def _positive_units(value):
    if type(value) is not int or not 1 <= value <= MAX_UNITS:
        raise ValueError("Budget units must be a positive bounded integer.")


def _verify(db):
    if (db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
            or db.execute("PRAGMA user_version").fetchone()[0] != 1):
        raise BudgetUnavailable("Interpretation budget storage unavailable.")
    objects = db.execute("SELECT name, type, sql FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*'").fetchall()
    if objects != [("budget_state", "table", SCHEMA)]:
        raise BudgetUnavailable("Interpretation budget storage unavailable.")
    rows = db.execute("SELECT id, limit_units, reserved_units, reservations FROM budget_state").fetchall()
    if len(rows) != 1:
        raise BudgetUnavailable("Interpretation budget storage unavailable.")
    row_id, limit, reserved, count = rows[0]
    if (any(type(value) is not int for value in rows[0]) or row_id != 1
            or not 1 <= limit <= MAX_UNITS or not 0 <= count <= reserved <= limit
            or (count == 0) != (reserved == 0)):
        raise BudgetUnavailable("Interpretation budget storage unavailable.")
    return BudgetStatus(limit, reserved, count)


def initialize(path_value, *, limit_units: int):
    """Explicit operator setup only; never overwrite, repair or increase a ledger.

Parent must already exist. POSIX permissions are checked by the shared helper;
Windows ACL provisioning remains the operator's responsibility.
"""
    _positive_units(limit_units)
    try:
        path = checked_path(path_value, missing=True)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except FileExistsError:
            current = status(path_value)
            if current.limit_units != limit_units:
                raise BudgetUnavailable("Existing interpretation budget policy does not match.")
            return current
        os.close(descriptor)
        with closing(connect(path)) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(SCHEMA)
            db.execute("INSERT INTO budget_state VALUES (1, ?, 0, 0)", (limit_units,))
            db.execute(f"PRAGMA application_id={APPLICATION_ID}")
            db.execute("PRAGMA user_version=1")
            db.execute("COMMIT")
            return _verify(db)
    except (OSError, ValueError, sqlite3.Error, QuotaUnavailable) as exc:
        raise BudgetUnavailable("Interpretation budget storage unavailable.") from exc


def status(path_value) -> BudgetStatus:
    """Read an existing ledger. Missing storage never creates fresh allowance."""
    try:
        with closing(connect(checked_path(path_value))) as db:
            return _verify(db)
    except (OSError, ValueError, sqlite3.Error, QuotaUnavailable) as exc:
        raise BudgetUnavailable("Interpretation budget storage unavailable.") from exc


def reserve(path_value, *, units: int) -> BudgetStatus:
    """Commit a charge before dispatch. Any exception forbids provider execution.

Each call consumes a new reservation; never replay a returned approval. Timeout,
cancellation, crash, refusal, or an ambiguous commit must not refund capacity.
This synchronous operation uses the shared short SQLite lock timeout.
"""
    _positive_units(units)
    try:
        with closing(connect(checked_path(path_value))) as db:
            db.execute("BEGIN IMMEDIATE")
            current = _verify(db)
            if units > current.remaining_units:
                raise BudgetExceeded("Interpretation budget is exhausted.")
            db.execute("UPDATE budget_state SET reserved_units=reserved_units+?, reservations=reservations+1 WHERE id=1", (units,))
            updated = _verify(db)
            db.execute("COMMIT")
            return updated
    except (OSError, ValueError, sqlite3.Error, QuotaUnavailable) as exc:
        raise BudgetUnavailable("Interpretation budget storage unavailable.") from exc
