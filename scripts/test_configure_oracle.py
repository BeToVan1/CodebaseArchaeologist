"""Offline provisioning checks: no Docker, network, root access or deployment."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import configure_oracle as setup

HOST = 'codebase-archaeologist.duckdns.org'
IP = '159.54.182.161'
TAG = 'codebase-archaeologist-deep:oracle-' + 'a' * 32
IMAGE_ID = 'sha256:' + 'b' * 64


class ConfigurationTests(unittest.TestCase):
    def test_readiness_retries_connection_reset(self):
        with patch.object(setup, 'request_status', side_effect=[ConnectionResetError(104, 'reset'), 200]) as request, patch.object(setup.time, 'sleep'):
            setup.wait_health('http://127.0.0.1:8000', 2)
            self.assertEqual(request.call_count, 2)

    def test_readiness_resets_remain_bounded(self):
        with patch.object(setup, 'request_status', side_effect=ConnectionResetError(104, 'reset')) as request, patch.object(setup.time, 'sleep'):
            with self.assertRaises(RuntimeError):
                setup.wait_health('http://127.0.0.1:8000', 2)
            self.assertEqual(request.call_count, 2)

    def test_valid_inputs(self):
        setup.validate_inputs(HOST, IP, TAG)

    def test_reject_invalid_inputs(self):
        for host in ('https://' + HOST, HOST + '/x', HOST + '\n', 'a.b.duckdns.org'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                setup.validate_inputs(host, IP, TAG)
        for ip in ('127.0.0.1', '10.0.0.21', '::1'):
            with self.subTest(ip=ip), self.assertRaises(ValueError):
                setup.validate_inputs(HOST, ip, TAG)
        for tag in ('latest', TAG + '\n', 'codebase-archaeologist-deep-test:' + 'a' * 32):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                setup.validate_inputs(HOST, IP, tag)

    def test_immutable_image_and_runtime_controls(self):
        files = setup.render_files(HOST, IMAGE_ID)
        unit = files[setup.UNIT_ROOT / setup.SERVICE]
        for setting in ('--pull=never', '--read-only', '--cap-drop=ALL',
                        '--user=10001:10001', '--security-opt=no-new-privileges',
                        '--memory=384m', '--memory-swap=384m', '--pids-limit=64',
                        '--cpus=1', '--publish=127.0.0.1:8000:8000',
                        'size=128m', '--log-opt=max-size=5m',
                        '--mount=type=bind,src=/var/lib/archaeologist-quota,dst=/var/lib/archaeologist-quota',
                        '--env=ARCHAEOLOGIST_QUOTA_PATH=/var/lib/archaeologist-quota/quota.sqlite3', IMAGE_ID):
            self.assertIn(setting, unit)
        self.assertNotIn('--volume', unit)
        self.assertEqual(unit.count('--mount='), 1)
        self.assertNotIn('ExecStartPre=', unit)  # Never initialize/reset on restart.
        self.assertNotIn('--privileged', unit)
        self.assertNotIn('/var/run/docker.sock', unit)
        with self.assertRaises(ValueError):
            setup.render_files(HOST, 'latest')

    def test_quota_initialization_is_offline_without_secret_or_ports(self):
        with patch.object(setup, 'prepare_quota_directory') as prepare, patch.object(setup, 'run') as run:
            setup.initialize_quota(IMAGE_ID)
            prepare.assert_called_once()
            args = run.call_args.args
            self.assertIn('--network=none', args)
            self.assertIn('--read-only', args)
            self.assertIn('--memory=64m', args)
            self.assertNotIn('--env-file', ' '.join(args))
            self.assertNotIn('--publish', ' '.join(args))
            self.assertEqual(args[-5:], ('python', '-m', 'deep_quota', 'init', str(setup.QUOTA_PATH)))

    def test_caddy_preserves_default_and_limits_memory(self):
        files = setup.render_files(HOST, IMAGE_ID)
        self.assertNotIn(Path('/etc/caddy/Caddyfile'), files)
        self.assertIn('reverse_proxy 127.0.0.1:8000', files[setup.CADDY_PATH])
        self.assertNotIn('SERVICE_TOKEN', files[setup.CADDY_PATH])
        self.assertIn('MemoryMax=64M', files[setup.DROPIN])
        self.assertIn('MemorySwapMax=0', files[setup.DROPIN])

    def test_firewall_only_adds_missing_web_rule(self):
        code = setup.render_files(HOST, IMAGE_ID)[setup.CONFIG / 'ensure_web_firewall.py']
        for result_code, expected_calls in ((0, 1), (1, 2)):
            with patch.object(subprocess, 'run') as run:
                run.return_value.returncode = result_code
                exec(compile(code, '<firewall>', 'exec'), {})
                self.assertEqual(run.call_count, expected_calls)
                self.assertEqual(run.call_args_list[0].args[0][3:5], ['-C', 'INPUT'])
                if result_code:
                    self.assertEqual(run.call_args.args[0][3:6], ['-I', 'INPUT', '1'])
        with patch.object(subprocess, 'run') as run:
            run.return_value.returncode = 2
            with self.assertRaises(SystemExit):
                exec(compile(code, '<firewall>', 'exec'), {})
            self.assertEqual(run.call_count, 1)

    def test_identical_files_reused_conflicts_preserved(self):
        # Root ownership is exercised on Ubuntu, not assumed on this test host.
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'check_path'):
            path = Path(directory) / 'configuration'
            setup.write_new_or_identical(path, 'original\n')
            setup.write_new_or_identical(path, 'original\n')
            with self.assertRaises(RuntimeError):
                setup.write_new_or_identical(path, 'replacement\n')
            self.assertEqual(path.read_text(), 'original\n')

    def test_token_created_reused_and_not_printed(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'private'
            with patch.object(setup, 'CONFIG', config), \
                    patch.object(setup, 'TOKEN_PATH', config / 'service.env'), \
                    patch.object(setup, 'check_path'), contextlib.redirect_stdout(io.StringIO()) as output:
                first = setup.load_or_create_token()
                self.assertRegex(first, r'^[a-f0-9]{64}$')
                self.assertEqual(first, setup.load_or_create_token())
                self.assertEqual(output.getvalue(), '')

    def test_image_inspection_rejects_untested_runtime(self):
        info = {'Os': 'linux', 'Architecture': 'amd64', 'Id': IMAGE_ID,
                'Config': {'User': '10001:10001', 'Cmd': [
                    'uvicorn', 'deep_service:create_app', '--factory', '--host',
                    '0.0.0.0', '--port', '8000', '--workers', '1', '--no-access-log']}}
        with patch.object(setup, 'run') as run:
            run.return_value.stdout = json.dumps([info])
            self.assertEqual(setup.validate_image(TAG), IMAGE_ID)
            for key, bad_value in (('User', 'root'), ('Cmd', ['pytest']),
                                   ('Entrypoint', ['sh']), ('Volumes', {'/data': {}})):
                bad = json.loads(json.dumps(info))
                bad['Config'][key] = bad_value
                run.return_value.stdout = json.dumps([bad])
                with self.subTest(key=key), self.assertRaises(ValueError):
                    setup.validate_image(TAG)

    def test_no_redirect_with_credentials(self):
        request = urllib.request.Request('https://' + HOST, headers={'Authorization': 'Bearer test'})
        self.assertIsNone(setup.NoRedirect().redirect_request(
            request, None, 302, 'Found', {}, 'https://example.com'))
        self.assertTrue(any(isinstance(handler, setup.NoRedirect) for handler in setup.HTTP.handlers))

    def test_authorization_checks_fail_closed(self):
        with patch.object(setup, 'request_status', side_effect=[401, 400]):
            setup.verify_auth('http://127.0.0.1:8000', 'test')
        for statuses in ([200], [401, 200]):
            with patch.object(setup, 'request_status', side_effect=statuses), self.assertRaises(RuntimeError):
                setup.verify_auth('http://127.0.0.1:8000', 'test')


if __name__ == '__main__':
    unittest.main()
