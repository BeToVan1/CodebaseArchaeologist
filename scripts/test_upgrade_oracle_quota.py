"""Offline updater verification. No Docker, SSH, systemd, listeners or /etc writes."""
from contextlib import ExitStack, redirect_stdout
import io
import hashlib
import json
from pathlib import Path
import tempfile
import subprocess
import tarfile
import unittest
from unittest.mock import MagicMock, patch

import upgrade_oracle_quota as upgrade
import configure_oracle as setup


class UpgradeTests(unittest.TestCase):
    def test_image_pin_matches_verified_archive_index_not_configuration(self):
        archive = Path(__file__).resolve().parent.parent / 'artifacts/oracle-6dc73a48a744469f8f28b581eea42ee7/deep-service.tar'
        if not archive.is_file():
            self.skipTest('Owner-built image archive not present on this machine')
        with archive.open('rb') as source:
            self.assertEqual(hashlib.file_digest(source, 'sha256').hexdigest(),
                '6e06d52cd54c6070afd0989652837f63ccb21407bc4b2de44cb39e7bb41e9ec6')
        with tarfile.open(archive) as bundle:
            index = json.load(bundle.extractfile('index.json'))
            images = [item for item in index['manifests'] if item.get('annotations', {}).get('io.containerd.image.name') ==
                      'docker.io/library/codebase-archaeologist-deep:oracle-6dc73a48a744469f8f28b581eea42ee7']
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0]['digest'], upgrade.NEW_IMAGE)
            def read_blob(digest):
                blob = bundle.extractfile('blobs/sha256/' + digest.split(':')[1]).read()
                self.assertEqual('sha256:' + hashlib.sha256(blob).hexdigest(), digest)
                return json.loads(blob)
            image_index = read_blob(upgrade.NEW_IMAGE)
            platforms = [item for item in image_index['manifests'] if item.get('platform') == {'architecture': 'amd64', 'os': 'linux'}]
            self.assertEqual(len(platforms), 1)
            manifest = read_blob(platforms[0]['digest'])
            self.assertEqual(manifest['config']['digest'], 'sha256:1f4b0df20bd150c91ac00b67bd4d4cfc8042a3f67aa53a1fb5b51b4df3733468')
            self.assertNotEqual(upgrade.NEW_IMAGE, manifest['config']['digest'])
            config = read_blob(manifest['config']['digest'])
            self.assertEqual(config['config']['User'], '10001:10001')

    def test_only_image_and_quota_flags_change_in_unit(self):
        expected = upgrade.OLD_UNIT.replace(upgrade.OLD_IMAGE,
            upgrade.QUOTA_ENV + ' ' + upgrade.MOUNT + ' ' + upgrade.NEW_IMAGE)
        self.assertEqual(upgrade.NEW_UNIT, expected)
        self.assertNotIn('ExecStartPre', expected)
        self.assertEqual(expected.count('--mount='), 1)
        self.assertIn('--publish=127.0.0.1:8000:8000', expected)
        self.assertEqual(upgrade.NEW_UNIT, setup.render_files('codebase-archaeologist.duckdns.org', upgrade.NEW_IMAGE)[setup.UNIT_ROOT / setup.SERVICE])

    def test_changed_persistence_or_rollback_failure_never_reports_success(self):
        for restore_fails in (False, True):
            with ExitStack() as stack:
                stack.enter_context(patch.object(upgrade, 'preflight', return_value=('secret', upgrade.OLD_UNIT)))
                for name in ('backup_unit', 'initialize_ledger', 'write_atomic', 'wait_health', 'runtime', 'auth_checks', 'real_analysis', 'run'):
                    stack.enter_context(patch.object(upgrade, name))
                stack.enter_context(patch.object(upgrade, 'read_root', return_value=upgrade.OLD_UNIT))
                stack.enter_context(patch.object(upgrade, 'read_token', return_value='secret'))
                stack.enter_context(patch.object(upgrade, 'ledger_fingerprint', side_effect=['before', 'after', 'lost']))
                restore = stack.enter_context(patch.object(upgrade, 'restore', side_effect=RuntimeError() if restore_fails else None))
                output = stack.enter_context(redirect_stdout(io.StringIO()))
                with self.assertRaisesRegex(upgrade.UpgradeError, 'Rollback could not be verified' if restore_fails else 'previous backend was restored'):
                    upgrade.apply()
                restore.assert_called_once()
                self.assertNotIn('PASS: quota backend installed', output.getvalue())

    def test_update_success_order_and_persistence(self):
        calls = []
        with ExitStack() as stack:
            stack.enter_context(patch.object(upgrade, 'preflight', return_value=('secret', upgrade.OLD_UNIT)))
            for name in ('backup_unit', 'initialize_ledger', 'write_atomic', 'wait_health', 'runtime', 'auth_checks', 'real_analysis'):
                stack.enter_context(patch.object(upgrade, name, side_effect=lambda *args, n=name: calls.append(n)))
            stack.enter_context(patch.object(upgrade, 'run', side_effect=lambda *args, **kw: calls.append(' '.join(args))))
            stack.enter_context(patch.object(upgrade, 'read_root', return_value=upgrade.OLD_UNIT))
            stack.enter_context(patch.object(upgrade, 'read_token', return_value='secret'))
            fingerprint = stack.enter_context(patch.object(upgrade, 'ledger_fingerprint', side_effect=['empty', 'admission', 'admission']))
            rollback = stack.enter_context(patch.object(upgrade, 'restore'))
            stack.enter_context(redirect_stdout(io.StringIO()))
            upgrade.apply()
            rollback.assert_not_called()
            self.assertEqual(fingerprint.call_count, 3)
        self.assertLess(calls.index('backup_unit'), calls.index('systemctl stop ' + upgrade.SERVICE))
        self.assertLess(calls.index('initialize_ledger'), calls.index('systemctl stop ' + upgrade.SERVICE))
        self.assertLess(calls.index('real_analysis'), calls.index('systemctl restart ' + upgrade.SERVICE))
        self.assertEqual(calls.count('real_analysis'), 1)
        self.assertFalse(any('caddy' in call or 'iptables' in call or 'image rm' in call for call in calls))

    def test_post_stop_failures_and_interrupts_trigger_rollback(self):
        for failing in ('write_atomic', 'wait_health', 'runtime', 'auth_checks', 'real_analysis', 'ledger_fingerprint'):
            for exception in (RuntimeError('private'), KeyboardInterrupt()):
                with self.subTest(failing=failing, exception=type(exception).__name__), ExitStack() as stack:
                    stack.enter_context(patch.object(upgrade, 'preflight', return_value=('secret', upgrade.OLD_UNIT)))
                    for name in ('backup_unit', 'initialize_ledger', 'write_atomic', 'wait_health', 'runtime', 'auth_checks', 'real_analysis', 'run', 'ledger_fingerprint'):
                        stack.enter_context(patch.object(upgrade, name, side_effect=exception if name == failing else None))
                    stack.enter_context(patch.object(upgrade, 'read_root', return_value=upgrade.OLD_UNIT))
                    stack.enter_context(patch.object(upgrade, 'read_token', return_value='secret'))
                    rollback = stack.enter_context(patch.object(upgrade, 'restore'))
                    stack.enter_context(redirect_stdout(io.StringIO()))
                    with self.assertRaisesRegex(upgrade.UpgradeError, 'previous backend was restored'):
                        upgrade.apply()
                    rollback.assert_called_once_with('secret')

    def test_initialization_failure_does_not_stop_production(self):
        with patch.object(upgrade, 'preflight', return_value=('secret', upgrade.OLD_UNIT)), \
                patch.object(upgrade, 'backup_unit'), patch.object(upgrade, 'initialize_ledger', side_effect=RuntimeError()), \
                patch.object(upgrade, 'run') as run, patch.object(upgrade, 'restore') as restore:
            with self.assertRaises(RuntimeError):
                upgrade.apply()
            run.assert_not_called()
            restore.assert_not_called()

    def test_repeated_apply_refuses_additional_analysis(self):
        with patch.object(upgrade, 'preflight', return_value=('secret', upgrade.NEW_UNIT)), patch.object(upgrade, 'backup_unit') as backup:
            with self.assertRaisesRegex(upgrade.UpgradeError, 'already installed'):
                upgrade.apply()
            backup.assert_not_called()

    def test_restore_preserves_ledger_and_uses_verified_backup(self):
        with patch.object(upgrade, 'read_root', side_effect=[upgrade.OLD_UNIT, upgrade.NEW_UNIT]), \
                patch.object(upgrade, 'run') as run, patch.object(upgrade, 'write_atomic') as write, \
                patch.object(upgrade, 'wait_health'), patch.object(upgrade, 'runtime'), \
                patch.object(upgrade, 'auth_checks'), redirect_stdout(io.StringIO()):
            upgrade.restore('secret')
            write.assert_called_once_with(upgrade.UNIT, upgrade.OLD_UNIT)
            self.assertTrue(all(call.args[0] == 'systemctl' for call in run.call_args_list))
        with patch.object(upgrade, 'read_root', return_value='other'), patch.object(upgrade, 'run') as run:
            with self.assertRaises(upgrade.UpgradeError):
                upgrade.restore('secret')
            run.assert_not_called()

    def test_backup_never_overwrites_conflicting_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'backup'
            path.write_text('existing')
            with patch.object(upgrade, 'BACKUP', path), patch.object(upgrade, 'read_root', return_value='existing'):
                with self.assertRaises(upgrade.UpgradeError):
                    upgrade.backup_unit()
            self.assertEqual(path.read_text(), 'existing')

    def runtime_info(self, new=True):
        return {'image': upgrade.NEW_IMAGE if new else upgrade.OLD_IMAGE, 'running': True, 'oom': False,
            'memory': 384*1024*1024, 'swap': 384*1024*1024, 'cpus': 1000000000, 'pids': 64,
            'readonly': True, 'user': '10001:10001', 'caps': ['ALL'], 'security': ['no-new-privileges:true'],
            'restarts': 0, 'ports': {'8000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8000'}]},
            'mounts': [{'Type': 'bind', 'Source': str(upgrade.DATA), 'Destination': str(upgrade.DATA), 'RW': True}] if new else []}

    def test_runtime_checks_exact_mount_image_and_limits(self):
        for new in (True, False):
            value = self.runtime_info(new)
            with patch.object(upgrade, 'run', return_value=json.dumps(value)):
                upgrade.runtime(new)
            value['mounts'].append({'Type': 'tmpfs', 'Destination': '/tmp'})
            with patch.object(upgrade, 'run', return_value=json.dumps(value)):
                upgrade.runtime(new)
        for key, value in (('image', upgrade.OLD_IMAGE), ('memory', 0), ('oom', True), ('ports', {}), ('mounts', []), ('caps', [])):
            info = self.runtime_info()
            info[key] = value
            with self.subTest(key=key), patch.object(upgrade, 'run', return_value=json.dumps(info)), self.assertRaises(upgrade.UpgradeError):
                upgrade.runtime(True)

    def test_default_does_not_mutate_or_submit_analysis(self):
        with patch.object(upgrade, 'preflight', return_value=('secret', upgrade.OLD_UNIT)), \
                patch.object(upgrade, 'apply') as apply, patch.object(upgrade, 'real_analysis') as job, redirect_stdout(io.StringIO()):
            self.assertEqual(upgrade.main([]), 0)
            apply.assert_not_called()
            job.assert_not_called()

    def test_external_error_details_are_not_printed(self):
        with patch.object(upgrade, 'preflight', side_effect=RuntimeError('do-not-print-token')), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(upgrade.main([]), 1)
            self.assertNotIn('do-not-print-token', output.getvalue())

    def test_redirects_cannot_receive_token(self):
        self.assertIsNone(upgrade.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.test'))

    def test_command_failures_identify_stage_without_leaking_output(self):
        cases = [
            (('systemctl', 'show', upgrade.SERVICE, '--property=FragmentPath', '--value'), 'service-unit location'),
            (('systemctl', 'show', upgrade.SERVICE, '--property=DropInPaths', '--value'), 'backend overrides'),
            (('systemctl', 'show', upgrade.SERVICE, '--property=NeedDaemonReload', '--value'), 'pending systemd'),
            (('/usr/bin/docker', 'image', 'inspect', upgrade.OLD_IMAGE), 'previous pinned image'),
            (('/usr/bin/docker', 'image', 'inspect', upgrade.NEW_IMAGE), 'replacement pinned image'),
            (('/usr/bin/docker', 'inspect', 'private-argument'), 'running backend'),
            (('/usr/bin/docker', 'run', 'private-argument'), 'initialize quota ledger'),
            (('/usr/bin/docker', 'exec', 'private-argument'), 'read quota ledger'),
            (('private-command', 'private-argument'), 'system command'),
        ]
        for args, label in cases:
            with self.subTest(label=label), patch.object(upgrade.subprocess, 'run', return_value=subprocess.CompletedProcess(args, 1, 'private-stdout', 'private-stderr')):
                with self.assertRaises(upgrade.UpgradeError) as error:
                    upgrade.run(*args)
                self.assertIn(label, str(error.exception))
                self.assertIn('exit 1', str(error.exception))
                for secret in ('private-argument', 'private-command', 'private-stdout', 'private-stderr'):
                    self.assertNotIn(secret, str(error.exception))

    def test_timeout_and_spawn_errors_are_sanitized(self):
        for error in (subprocess.TimeoutExpired('private-command', 40, output='private-output'), OSError('private-details')):
            with patch.object(upgrade.subprocess, 'run', side_effect=error):
                with self.assertRaises(upgrade.UpgradeError) as caught:
                    upgrade.run('/usr/bin/docker', 'run', 'private-argument')
                self.assertIn('initialize quota ledger', str(caught.exception))
                self.assertNotIn('private-argument', str(caught.exception))
                self.assertNotIn('private-output', str(caught.exception))
                self.assertNotIn('private-details', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
