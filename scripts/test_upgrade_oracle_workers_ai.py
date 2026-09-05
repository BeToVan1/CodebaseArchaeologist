"""Offline tests for the pinned Workers AI bridge release; no host changes."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.error
from unittest.mock import patch

import test_upgrade_oracle_evidence_reference as previous_tests
import upgrade_oracle_workers_ai as release


class WorkersAIBridgeUpgradeTests(previous_tests.EvidenceReferenceUpgradeTests):
    def setUp(self):
        configuration = patch.multiple(release.updater, **release.SETTINGS)
        configuration.start()
        self.addCleanup(configuration.stop)

    def test_unit_changes_only_image_and_uses_separate_backup(self):
        self.assertEqual(release.NEW_UNIT.replace(release.NEW_IMAGE, release.OLD_IMAGE), release.OLD_UNIT)
        self.assertEqual(release.OLD_UNIT, release.previous.NEW_UNIT)
        self.assertEqual(release.NEW_UNIT.count(release.updater.common.MOUNT), 1)
        self.assertEqual(release.NEW_UNIT.count(release.updater.common.QUOTA_ENV), 1)
        self.assertNotIn('ARCHAEOLOGIST_CF_', release.NEW_UNIT)
        self.assertNotIn('ARCHAEOLOGIST_INTERPRETATION_ENABLED', release.NEW_UNIT)
        self.assertEqual(release.BACKUP.name, 'pre-workers-ai-bridge-v1.service')

    def test_image_pin_matches_owner_validated_archive(self):
        archive = Path(__file__).resolve().parent.parent / \
            'artifacts/oracle-2b6c6608ea3947e8b7833ae58e3df835/deep-service.tar'
        self.assertTrue(archive.is_file(), 'Validated Workers AI bridge archive is required.')
        with archive.open('rb') as source:
            self.assertEqual(hashlib.file_digest(source, 'sha256').hexdigest(),
                'd910271bac951ceb77ae884df4991e283171e686b8beb47884fb30c4a0edfc12')
        with tarfile.open(archive) as bundle:
            index = json.load(bundle.extractfile('index.json'))
            images = [item for item in index['manifests']
                if item.get('annotations', {}).get('io.containerd.image.name') ==
                'docker.io/library/codebase-archaeologist-deep:oracle-2b6c6608ea3947e8b7833ae58e3df835']
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0]['digest'], release.NEW_IMAGE)

    def test_entrypoint_restores_settings_and_installs_fail_closed_check(self):
        old_analysis = release.updater.common.real_analysis
        def check(argv):
            self.assertEqual(argv, ['--verify'])
            self.assertIs(release.updater.common.real_analysis, release.real_analysis)
            for name, value in release.SETTINGS.items():
                self.assertEqual(getattr(release.updater, name), value)
            return 0
        with patch.object(release.updater, 'main', side_effect=check):
            self.assertEqual(release.main(['--verify']), 0)
        self.assertIs(release.updater.common.real_analysis, old_analysis)

    def test_one_analysis_then_disabled_route_check(self):
        graph = {'schema_version': '1.1'}
        response = json.dumps({'detail': 'AI interpretation is not configured.'}).encode()
        with patch.object(release.previous, 'real_analysis', return_value=graph) as analysis, \
             patch.object(release, 'disabled_interpretation_response', return_value=(503, response)) as request:
            self.assertIs(release.real_analysis('private-token'), graph)
        analysis.assert_called_once_with('private-token')
        request.assert_called_once_with('private-token')

    def test_disabled_check_retains_only_bounded_http_error_body(self):
        body = json.dumps({'detail': 'AI interpretation is not configured.'}).encode()
        error = urllib.error.HTTPError(
            'https://fixed.invalid', 503, 'unavailable', {}, io.BytesIO(body))
        with patch.object(release.updater.common.HTTP, 'open', side_effect=error) as opened:
            self.assertEqual(release.disabled_interpretation_response('private-token'), (503, body))
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url,
            release.updater.common.BASE + '/api/interpret/quota-v1')
        self.assertEqual(request.get_header('Authorization'), 'Bearer private-token')

        oversized = urllib.error.HTTPError(
            'https://fixed.invalid', 503, 'unavailable', {}, io.BytesIO(b'x' * 2049))
        with patch.object(release.updater.common.HTTP, 'open', side_effect=oversized):
            with self.assertRaisesRegex(release.updater.common.UpgradeError, 'output limit'):
                release.disabled_interpretation_response('private-token')

    def test_enabled_or_malformed_route_response_stops_upgrade(self):
        with patch.object(release.previous, 'real_analysis', return_value={}), \
             patch.object(release, 'disabled_interpretation_response', return_value=(200, b'not-json')):
            with self.assertRaisesRegex(release.updater.common.UpgradeError, 'invalid JSON'):
                release.real_analysis('private-token')

        enabled = json.dumps({'model': 'unexpected-live-call'}).encode()
        with patch.object(release.previous, 'real_analysis', return_value={}), \
             patch.object(release, 'disabled_interpretation_response', return_value=(200, enabled)):
            with self.assertRaisesRegex(release.updater.common.UpgradeError, 'fail-closed'):
                release.real_analysis('private-token')
