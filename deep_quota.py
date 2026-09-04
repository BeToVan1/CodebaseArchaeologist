"""Bounded, persistent admission ledger. No network, source, tokens or raw IPs.

Initialize explicitly with: python -m deep_quota init /absolute/path/quota.sqlite3
The private parent directory must already exist. Existing ledgers are checked,
never reset. Request handling opens an existing database only and fails closed.
"""
from contextlib import closing
import os
from pathlib import Path
import re
import sqlite3
import stat

MAX_BYTES = 1024 * 1024
CLIENT_KEY = re.compile(r"[a-f0-9]{64}")
SCHEMA = (
    "CREATE TABLE deep_admissions (id INTEGER PRIMARY KEY, client_key TEXT NOT NULL CHECK(length(client_key)=64), created_at INTEGER NOT NULL)",
    "CREATE INDEX idx_admissions_time ON deep_admissions(created_at)",
    "CREATE INDEX idx_admissions_client_time ON deep_admissions(client_key, created_at)",
)


class QuotaUnavailable(Exception):
    """Safe public error; never include database paths or underlying errors."""


def checked_path(value, *, missing=False):
    path = Path(value)
    if not path.is_absolute() or any(item.is_symlink() for item in (path, *path.parents)):
        raise QuotaUnavailable("Usage storage unavailable.")
    parent = path.parent.stat()
    if not stat.S_ISDIR(parent.st_mode):
        raise QuotaUnavailable("Usage storage unavailable.")
    if os.name == "posix" and (parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700):
        raise QuotaUnavailable("Usage storage unavailable.")
    if not missing or path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
            raise QuotaUnavailable("Usage storage unavailable.")
        if os.name == "posix" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
            raise QuotaUnavailable("Usage storage unavailable.")
    return path


def connect(path):
    # mode=rw is essential: a missing mount/database must never reset allowances.
    db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=0.15, isolation_level=None)
    try:
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("PRAGMA synchronous=FULL")
        if db.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
            raise QuotaUnavailable("Usage storage unavailable.")
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
        maximum = MAX_BYTES // page_size
        if db.execute(f"PRAGMA max_page_count={maximum}").fetchone()[0] > maximum:
            raise QuotaUnavailable("Usage storage unavailable.")
        return db
    except Exception:
        db.close()
        raise


def verify(db):
    if db.execute("PRAGMA user_version").fetchone()[0] != 1:
        raise QuotaUnavailable("Usage storage unavailable.")
    if db.execute("SELECT count(*) FROM deep_admissions").fetchone()[0] > 30:
        raise QuotaUnavailable("Usage storage unavailable.")


def initialize(value):
    """Offline schema initialization; preserve and validate any existing ledger."""
    try:
        path = checked_path(value, missing=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except FileExistsError:
            with closing(connect(checked_path(value))) as db:
                verify(db)
            return
        os.close(fd)
        with closing(connect(path)) as db:
            db.execute("BEGIN IMMEDIATE")
            for sql in SCHEMA:
                db.execute(sql)
            db.execute("PRAGMA user_version=1")
            db.execute("COMMIT")
            db.execute("PRAGMA optimize")
    except (OSError, ValueError, sqlite3.Error) as error:
        raise QuotaUnavailable("Usage storage unavailable.") from error


def reserve(value, client_key):
    """3/network/600s and 30/global/3600s; atomic across connections/processes.

    SQLite's clock is authoritative. Denials prune expired records but add none.
    Admitted failures/cancellations count; busy and invalid requests never enter.
    The short lock timeout bounds event-loop blocking; any error denies work.
    """
    if not isinstance(client_key, str) or not CLIENT_KEY.fullmatch(client_key):
        raise QuotaUnavailable("Usage storage unavailable.")
    try:
        with closing(connect(checked_path(value))) as db:
            db.execute("BEGIN IMMEDIATE")
            verify(db)
            now = db.execute("SELECT unixepoch()").fetchone()[0]
            db.execute("DELETE FROM deep_admissions WHERE created_at <= ?", (now - 3600,))
            cursor = db.execute(
                "INSERT INTO deep_admissions(client_key, created_at) SELECT ?, ? "
                "WHERE (SELECT count(*) FROM deep_admissions) < 30 "
                "AND (SELECT count(*) FROM deep_admissions WHERE client_key=? AND created_at>?) < 3",
                (client_key, now, client_key, now - 600),
            )
            allowed = cursor.rowcount == 1
            db.execute("COMMIT")
            return allowed
    except (OSError, ValueError, sqlite3.Error) as error:
        raise QuotaUnavailable("Usage storage unavailable.") from error


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init"])
    parser.add_argument("path")
    args = parser.parse_args()
    try:
        initialize(args.path)
    except QuotaUnavailable:
        parser.exit(1, "STOP: quota storage initialization failed; existing data was not reset.\n")
    print("PASS: quota ledger initialized or existing ledger verified.")
