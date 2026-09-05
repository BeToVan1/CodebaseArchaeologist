"""Offline credential-activation tests; no secrets, systemd, Docker, or network."""
import unittest
from unittest.mock import call, patch

import configure_oracle_workers_ai as activation


class OracleWorkersAIActivationTests(unittest.TestCase):
    def test_active_unit_adds_only_root_env_file(self):
        self.assertEqual(
            activation.ACTIVE_UNIT.replace(' ' + activation.AI_ENV_ARGUMENT, ''),
            activation.CURRENT_UNIT,
        )
        self.assertEqual(activation.ACTIVE_UNIT.count(activation.AI_ENV_ARGUMENT), 1)
        self.assertNotIn('ARCHAEOLOGIST_CF_ACCOUNT_ID=', activation.ACTIVE_UNIT)
        self.assertNotIn('ARCHAEOLOGIST_CF_AI_TOKEN=', activation.ACTIVE_UNIT)
        self.assertEqual(activation.BACKUP.name, 'pre-workers-ai-credentials-v1.service')

    def test_route_checks_stop_before_inference_on_loopback_and_https(self):
        responses = [(401, b''), (400, b''), (404, b'')] * 2
        with patch.object(activation.common, 'request', side_effect=responses) as request:
            activation.route_checks('private-service-token')
        self.assertEqual(request.call_count, 6)
        for base, offset in (
                ('http://127.0.0.1:8000', 0), (activation.common.BASE, 3)):
            self.assertEqual(request.call_args_list[offset].args[:2],
                (base, '/api/interpret/quota-v1'))
            self.assertEqual(request.call_args_list[offset + 2].args[3],
                {'reportId': 'R' * 43, 'nodeId': 'symbol:missing'})

    def test_configuration_probe_returns_only_boolean_validity(self):
        with patch.object(activation.common, 'run', return_value='{"valid": true}') as run:
            activation.configuration_probe()
        command = run.call_args.args
        self.assertEqual(command[:3],
            ('/usr/bin/docker', 'exec', activation.common.CONTAINER))
        self.assertNotIn('private-service-token', ' '.join(command))

        with patch.object(activation.common, 'run', return_value='{"valid": false}'):
            with self.assertRaisesRegex(activation.common.UpgradeError, 'valid private AI configuration'):
                activation.configuration_probe()

    def test_apply_activates_then_removes_only_staging_file(self):
        staged = ('a' * 32, 'provider-token-' + 'x' * 32)
        with patch.object(activation, 'preflight', return_value=('service-token', staged)), \
             patch.object(activation, 'backup_unit') as backup, \
             patch.object(activation.common, 'read_root', return_value=activation.CURRENT_UNIT), \
             patch.object(activation.common, 'read_token', return_value='service-token'), \
             patch.object(activation, 'write_credentials') as write_credentials, \
             patch.object(activation.common, 'run') as run, \
             patch.object(activation.common, 'write_atomic') as write_atomic, \
             patch.object(activation.common, 'wait_health') as health, \
             patch.object(activation.common, 'runtime') as runtime, \
             patch.object(activation.common, 'auth_checks') as auth, \
             patch.object(activation, 'configuration_probe') as probe, \
             patch.object(activation, 'route_checks') as routes, \
             patch('pathlib.Path.unlink') as unlink:
            activation.apply()
        backup.assert_called_once_with()
        write_credentials.assert_called_once_with(*staged)
        write_atomic.assert_called_once_with(activation.UNIT, activation.ACTIVE_UNIT)
        self.assertIn(call('systemctl', 'stop', activation.SERVICE), run.call_args_list)
        self.assertIn(call('systemctl', 'start', activation.SERVICE), run.call_args_list)
        health.assert_called_once_with()
        runtime.assert_called_once_with(True, image=activation.image_release.NEW_IMAGE)
        auth.assert_called_once_with('service-token')
        probe.assert_called_once_with()
        routes.assert_called_once_with('service-token')
        unlink.assert_called_once_with()

    def test_failed_activation_restores_disabled_service_and_keeps_staging(self):
        staged = ('a' * 32, 'provider-token-' + 'x' * 32)
        with patch.object(activation, 'preflight', return_value=('service-token', staged)), \
             patch.object(activation, 'backup_unit'), \
             patch.object(activation.common, 'read_root', return_value=activation.CURRENT_UNIT), \
             patch.object(activation.common, 'read_token', return_value='service-token'), \
             patch.object(activation, 'write_credentials'), \
             patch.object(activation.common, 'run'), \
             patch.object(activation.common, 'write_atomic'), \
             patch.object(activation.common, 'wait_health', side_effect=RuntimeError('private')), \
             patch.object(activation, 'restore') as restore, \
             patch('pathlib.Path.unlink') as unlink:
            with self.assertRaisesRegex(activation.common.UpgradeError, 'disabled backend was restored'):
                activation.apply()
        restore.assert_called_once_with('service-token', remove_credentials=True)
        unlink.assert_not_called()


if __name__ == '__main__':
    unittest.main()
