"""Offline owner-updater tests; no SSH, Docker, systemd or production writes."""
from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tarfile
import unittest
from unittest.mock import patch

import upgrade_oracle_patterns as upgrade

c = upgrade.common


class PatternUpgradeTests(unittest.TestCase):
    def test_image_pin_matches_owner_validated_archive(self):
        archive = Path(__file__).resolve().parent.parent / 'artifacts/oracle-d795c0f209b842c8978a676e419ae111/deep-service.tar'
        if not archive.is_file():
            self.skipTest('Owner archive unavailable')
        with archive.open('rb') as source:
            self.assertEqual(hashlib.file_digest(source, 'sha256').hexdigest(),
                '32fe7e84e012dcd59b865cc6b50e6694bdf3629e6b86ec0ee0ff0b081268aa65')
        with tarfile.open(archive) as bundle:
            index = json.load(bundle.extractfile('index.json'))
            images = [item for item in index['manifests'] if item.get('annotations', {}).get('io.containerd.image.name') ==
                'docker.io/library/codebase-archaeologist-deep:oracle-d795c0f209b842c8978a676e419ae111']
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0]['digest'], upgrade.NEW_IMAGE)
            def blob(digest):
                data = bundle.extractfile('blobs/sha256/' + digest.split(':')[1]).read()
                self.assertEqual('sha256:' + hashlib.sha256(data).hexdigest(), digest)
                return json.loads(data)
            platforms = [item for item in blob(upgrade.NEW_IMAGE)['manifests']
                if item.get('platform') == {'architecture': 'amd64', 'os': 'linux'}]
            self.assertEqual(len(platforms), 1)
            config = blob(blob(platforms[0]['digest'])['config']['digest'])
            self.assertEqual(config['config']['User'], '10001:10001')

    def test_unit_changes_only_image_and_uses_separate_backup(self):
        self.assertEqual(upgrade.OLD_UNIT, c.NEW_UNIT)
        self.assertEqual(upgrade.NEW_UNIT.replace(upgrade.NEW_IMAGE, upgrade.OLD_IMAGE), upgrade.OLD_UNIT)
        self.assertEqual(upgrade.NEW_UNIT.count(c.MOUNT), 1)
        self.assertEqual(upgrade.NEW_UNIT.count(c.QUOTA_ENV), 1)
        self.assertNotEqual(upgrade.BACKUP, c.BACKUP)

    def mock_apply(self, stack):
        mocks = {}
        mocks['preflight'] = stack.enter_context(patch.object(upgrade, 'preflight', return_value=('private-token', upgrade.OLD_UNIT)))
        mocks['backup'] = stack.enter_context(patch.object(upgrade, 'backup_unit'))
        mocks['fingerprint'] = stack.enter_context(patch.object(upgrade, 'ledger_fingerprint', side_effect=['old', 'old', 'admitted']))
        mocks['restore'] = stack.enter_context(patch.object(upgrade, 'restore'))
        for name in ('run', 'write_atomic', 'wait_health', 'runtime', 'auth_checks', 'real_analysis', 'initialize_ledger'):
            mocks[name] = stack.enter_context(patch.object(c, name))
        stack.enter_context(patch.object(c, 'read_root', return_value=upgrade.OLD_UNIT))
        stack.enter_context(patch.object(c, 'read_token', return_value='private-token'))
        mocks['output'] = stack.enter_context(redirect_stdout(io.StringIO()))
        return mocks

    def test_success_preserves_quota_and_submits_exactly_one_analysis(self):
        with ExitStack() as stack:
            m = self.mock_apply(stack)
            upgrade.apply()
            m['initialize_ledger'].assert_not_called()
            m['restore'].assert_not_called()
            m['real_analysis'].assert_called_once_with('private-token')
            m['write_atomic'].assert_called_once_with(c.UNIT, upgrade.NEW_UNIT)
            self.assertEqual([call.args for call in m['run'].call_args_list], [
                ('systemctl', 'stop', c.SERVICE), ('systemctl', 'daemon-reload'), ('systemctl', 'start', c.SERVICE)])
            self.assertNotIn('private-token', m['output'].getvalue())

    def test_ledger_loss_stops_before_analysis_and_rolls_back(self):
        with ExitStack() as stack:
            m = self.mock_apply(stack)
            m['fingerprint'].side_effect = ['old', 'lost']
            with self.assertRaisesRegex(c.UpgradeError, 'previous backend restored'):
                upgrade.apply()
            m['real_analysis'].assert_not_called()
            m['restore'].assert_called_once_with('private-token')

    def test_post_stop_errors_and_interrupts_trigger_rollback(self):
        for stage in ('run', 'write_atomic', 'wait_health', 'runtime', 'auth_checks', 'real_analysis'):
            for error in (RuntimeError('private-detail'), KeyboardInterrupt()):
                with self.subTest(stage=stage, error=type(error).__name__), ExitStack() as stack:
                    m = self.mock_apply(stack)
                    m[stage].side_effect = error
                    with self.assertRaisesRegex(c.UpgradeError, 'previous backend restored'):
                        upgrade.apply()
                    m['restore'].assert_called_once()
                    self.assertNotIn('PASS: analyzer image upgraded', m['output'].getvalue())

    def test_rollback_failure_is_not_success(self):
        with ExitStack() as stack:
            m = self.mock_apply(stack)
            m['real_analysis'].side_effect = RuntimeError('private-detail')
            m['restore'].side_effect = RuntimeError('another-private-detail')
            with self.assertRaisesRegex(c.UpgradeError, 'Rollback could not be verified'):
                upgrade.apply()
            self.assertNotIn('PASS: analyzer image upgraded', m['output'].getvalue())

    def test_repeated_apply_and_backup_failure_do_not_stop_service(self):
        for repeated in (True, False):
            with self.subTest(repeated=repeated), ExitStack() as stack:
                m = self.mock_apply(stack)
                if repeated:
                    m['preflight'].return_value = ('private-token', upgrade.NEW_UNIT)
                else:
                    m['backup'].side_effect = c.UpgradeError('Backup differs')
                with self.assertRaises(c.UpgradeError):
                    upgrade.apply()
                m['run'].assert_not_called()
                m['real_analysis'].assert_not_called()

    def test_runtime_override_still_requires_existing_quota_mount(self):
        info = {'image': upgrade.OLD_IMAGE, 'running': True, 'oom': False,
            'memory': 384*1024*1024, 'swap': 384*1024*1024, 'cpus': 1000000000,
            'pids': 64, 'readonly': True, 'user': '10001:10001', 'caps': ['ALL'],
            'security': ['no-new-privileges:true'], 'restarts': 0,
            'ports': {'8000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8000'}]},
            'mounts': [{'Type': 'bind', 'Source': str(c.DATA), 'Destination': str(c.DATA), 'RW': True}]}
        with patch.object(c, 'run', return_value=json.dumps(info)):
            c.runtime(True, image=upgrade.OLD_IMAGE)
        info['mounts'] = []
        with patch.object(c, 'run', return_value=json.dumps(info)), self.assertRaises(c.UpgradeError):
            c.runtime(True, image=upgrade.OLD_IMAGE)

    def test_restore_keeps_old_quota_mount_and_never_initializes_storage(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(c, 'read_root', side_effect=[upgrade.OLD_UNIT, upgrade.NEW_UNIT]))
            stack.enter_context(patch.object(c, 'read_token', return_value='private-token'))
            stack.enter_context(patch.object(upgrade, 'ledger_fingerprint', return_value='unchanged'))
            run = stack.enter_context(patch.object(c, 'run'))
            write = stack.enter_context(patch.object(c, 'write_atomic'))
            runtime = stack.enter_context(patch.object(c, 'runtime'))
            init = stack.enter_context(patch.object(c, 'initialize_ledger'))
            for name in ('wait_health', 'auth_checks'):
                stack.enter_context(patch.object(c, name))
            stack.enter_context(redirect_stdout(io.StringIO()))
            upgrade.restore('private-token')
            init.assert_not_called()
            write.assert_called_once_with(c.UNIT, upgrade.OLD_UNIT)
            runtime.assert_called_once_with(True, image=upgrade.OLD_IMAGE)
            self.assertTrue(all(call.args[0] == 'systemctl' for call in run.call_args_list))

    def test_default_is_preflight_only_and_errors_are_sanitized(self):
        with patch.object(upgrade, 'preflight', return_value=('private-token', upgrade.OLD_UNIT)), \
                patch.object(upgrade, 'apply') as apply, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(upgrade.main([]), 0)
            apply.assert_not_called()
            self.assertNotIn('private-token', output.getvalue())
        with patch.object(upgrade, 'preflight', side_effect=RuntimeError('private-token')), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(upgrade.main([]), 1)
            self.assertNotIn('private-token', output.getvalue())


if __name__ == '__main__':
    unittest.main()
