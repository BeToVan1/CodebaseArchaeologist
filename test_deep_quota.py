"""Offline real SQLite checks; run with stdlib unittest or pytest."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

import deep_quota as quota


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name).resolve() / "quota.sqlite3"
        quota.initialize(self.path)
        self.key = "a" * 64

    def sql(self, statement, values=()):
        with closing(sqlite3.connect(self.path)) as db:
            result = db.execute(statement, values).fetchall()
            db.commit()
            return result

    def test_network_limit_and_reinitialization_preserve_admissions(self):
        for _ in range(3):
            self.assertTrue(quota.reserve(self.path, self.key))
            quota.initialize(self.path)
        self.assertFalse(quota.reserve(self.path, self.key))
        self.assertEqual(self.sql("SELECT count(*) FROM deep_admissions"), [(3,)])

    def test_separate_process_reopening_does_not_reset_allowance(self):
        for _ in range(3):
            quota.reserve(self.path, self.key)
        result = subprocess.run([sys.executable, "-c",
            "import sys; from deep_quota import reserve; print(reserve(sys.argv[1], 'a'*64))", str(self.path)],
            cwd=Path(__file__).parent, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "False")

    def test_competing_connections_cannot_over_admit(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: quota.reserve(self.path, self.key), range(16)))
        self.assertEqual(results.count(True), 3)

    def test_global_limit_and_expiry(self):
        for i in range(30):
            self.assertTrue(quota.reserve(self.path, f"{i:064x}"))
        self.assertFalse(quota.reserve(self.path, self.key))
        self.sql("UPDATE deep_admissions SET created_at = unixepoch() - 3600")
        self.assertTrue(quota.reserve(self.path, self.key))
        self.assertEqual(self.sql("SELECT count(*) FROM deep_admissions"), [(1,)])

    def test_network_expiry_preserves_global_history(self):
        for _ in range(3):
            quota.reserve(self.path, self.key)
        self.sql("UPDATE deep_admissions SET created_at = unixepoch() - 600")
        self.assertTrue(quota.reserve(self.path, self.key))
        self.assertEqual(self.sql("SELECT count(*) FROM deep_admissions"), [(4,)])

    def test_missing_ledger_fails_without_creating_file(self):
        path = self.path.with_name("missing.sqlite3")
        with self.assertRaises(quota.QuotaUnavailable):
            quota.reserve(path, self.key)
        self.assertFalse(path.exists())

    def test_invalid_keys_and_paths_fail_closed(self):
        for key in (None, "", "192.0.2.1", "A" * 64, "a" * 63, "a" * 64 + "\n"):
            with self.subTest(key=key), self.assertRaises(quota.QuotaUnavailable):
                quota.reserve(self.path, key)
        for path in ("", "relative.sqlite3", self.path.parent):
            with self.subTest(path=path), self.assertRaises(quota.QuotaUnavailable):
                quota.reserve(path, self.key)

    def test_lock_contention_is_bounded_and_does_not_admit(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("BEGIN IMMEDIATE")
            started = time.monotonic()
            with self.assertRaises(quota.QuotaUnavailable):
                quota.reserve(self.path, self.key)
            self.assertLess(time.monotonic() - started, 1)
            db.rollback()
        self.assertEqual(self.sql("SELECT count(*) FROM deep_admissions"), [(0,)])

    def test_wrong_schema_is_not_reset(self):
        self.sql("PRAGMA user_version=999")
        with self.assertRaises(quota.QuotaUnavailable):
            quota.reserve(self.path, self.key)
        with self.assertRaises(quota.QuotaUnavailable):
            quota.initialize(self.path)
        self.assertEqual(self.sql("PRAGMA user_version"), [(999,)])

    def test_corrupt_and_oversized_ledgers_fail_closed(self):
        for data in (b"not a database", b"x" * (quota.MAX_BYTES + 1)):
            self.path.write_bytes(data)
            with self.assertRaises(quota.QuotaUnavailable):
                quota.reserve(self.path, self.key)
            with self.assertRaises(quota.QuotaUnavailable):
                quota.initialize(self.path)
            self.assertEqual(self.path.read_bytes(), data)

    def test_full_database_error_rolls_back(self):
        # Simulate a disk-full statement failure, not a quota rejection.
        self.sql("CREATE TRIGGER simulate_full BEFORE INSERT ON deep_admissions BEGIN SELECT RAISE(ABORT, 'disk full'); END")
        with self.assertRaises(quota.QuotaUnavailable):
            quota.reserve(self.path, self.key)
        self.assertEqual(self.sql("SELECT count(*) FROM deep_admissions"), [(0,)])

    def test_ledger_size_cap_and_indexed_network_query(self):
        with closing(quota.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA max_page_count").fetchone()[0] * db.execute("PRAGMA page_size").fetchone()[0], quota.MAX_BYTES)
            plan = db.execute("EXPLAIN QUERY PLAN SELECT count(*) FROM deep_admissions WHERE client_key=? AND created_at>?", (self.key, 0)).fetchall()
            self.assertIn("idx_admissions_client_time", str(plan))
        self.assertLess(self.path.stat().st_size, quota.MAX_BYTES)

    @unittest.skipUnless(os.name == "posix", "POSIX ownership/mode checks")
    def test_open_permissions_rejected(self):
        self.path.chmod(0o644)
        with self.assertRaises(quota.QuotaUnavailable):
            quota.reserve(self.path, self.key)
        self.path.chmod(0o600)
        self.path.parent.chmod(0o755)
        with self.assertRaises(quota.QuotaUnavailable):
            quota.reserve(self.path, self.key)


if __name__ == "__main__":
    unittest.main()
