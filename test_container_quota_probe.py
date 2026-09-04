"""Offline probe checks: actual SQLite, mocked HTTP/processes, no listeners."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

from scripts import container_quota_check as probe


class ProbeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name).resolve() / 'quota.sqlite3'
        self.ledger_patch = patch.object(probe, 'LEDGER', self.path)
        self.ledger_patch.start()
        self.addCleanup(self.ledger_patch.stop)

    def test_seed_is_bounded_and_refuses_existing_state(self):
        probe.seed()
        self.assertFalse(probe.reserve(self.path, probe.KEY))
        with self.assertRaises(RuntimeError):
            probe.seed()

    def run_probe(self, mode, statuses):
        calls = []
        def request(req, timeout):
            calls.append(req)
            status = statuses.pop(0)
            if status != 200:
                raise urllib.error.HTTPError(req.full_url, status, 'fixed',
                    {'X-Archaeologist-Limit': 'quota'} if status == 429 else {}, None)
            response = MagicMock()
            response.__enter__.return_value.status = 200
            return response
        server = MagicMock()
        server.poll.return_value = None
        with patch.object(probe.subprocess, 'Popen', return_value=server) as spawn, \
                patch.object(probe.urllib.request, 'build_opener') as http:
            http.return_value.open.side_effect = request
            try:
                probe.probe(mode)
            finally:
                server.terminate.assert_called_once()
                server.wait.assert_called_once_with(timeout=5)
        self.assertEqual(spawn.call_args.kwargs['env']['ARCHAEOLOGIST_QUOTA_PATH'], str(self.path))
        self.assertEqual([req.get_method() for req in calls], ['GET', 'POST', 'POST', 'POST', 'POST'])

    def test_replacement_verifies_both_http_routes(self):
        probe.seed()
        self.run_probe('persisted', [200, 401, 429, 401, 429])

    def test_missing_storage_fails_closed_and_is_not_created(self):
        self.run_probe('missing', [200, 401, 503, 401, 503])
        self.assertFalse(self.path.exists())

    def test_wrong_status_fails_and_stops_service(self):
        probe.seed()
        with self.assertRaises(RuntimeError):
            self.run_probe('persisted', [200, 401, 200])

    def test_persisted_mode_never_creates_missing_ledger(self):
        with patch.object(probe.subprocess, 'Popen') as spawn:
            with self.assertRaises(probe.QuotaUnavailable):
                probe.probe('persisted')
            spawn.assert_not_called()
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
