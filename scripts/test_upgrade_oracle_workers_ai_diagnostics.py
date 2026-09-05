"""Offline tests for the pinned structured-diagnostics image updater."""
import hashlib
import json
from pathlib import Path
import tarfile
from unittest.mock import patch

import test_upgrade_oracle_workers_ai as previous_tests
import upgrade_oracle_workers_ai_diagnostics as release


class WorkersAIDiagnosticsUpgradeTests(previous_tests.WorkersAIBridgeUpgradeTests):
    def setUp(self):
        configuration = patch.multiple(release.updater, **release.SETTINGS)
        configuration.start()
        self.addCleanup(configuration.stop)

    def test_unit_changes_only_image_and_preserves_private_configuration(self):
        self.assertEqual(release.NEW_UNIT.replace(release.NEW_IMAGE, release.OLD_IMAGE), release.OLD_UNIT)
        self.assertEqual(release.OLD_UNIT, release.activation.ACTIVE_UNIT)
        self.assertEqual(release.NEW_UNIT.count(release.activation.AI_ENV_ARGUMENT), 1)
        self.assertEqual(release.NEW_UNIT.count(release.updater.common.MOUNT), 1)
        self.assertNotIn('ARCHAEOLOGIST_CF_AI_TOKEN=', release.NEW_UNIT)
        self.assertEqual(release.BACKUP.name, 'pre-workers-ai-structured-diagnostics-v1.service')

    def test_image_pin_matches_owner_validated_archive(self):
        archive = Path(__file__).resolve().parent.parent / \
            'artifacts/oracle-15b11f8f092d4f55a475a8d4e3404a83/deep-service.tar'
        self.assertTrue(archive.is_file(), 'Validated diagnostics archive is required.')
        with archive.open('rb') as source:
            self.assertEqual(hashlib.file_digest(source, 'sha256').hexdigest(),
                '429dccd7a8b63b80c33814b16c028fc4b413d123b4d56375a80ab80fc71f4db9')
        with tarfile.open(archive) as bundle:
            index = json.load(bundle.extractfile('index.json'))
            images = [item for item in index['manifests']
                if item.get('annotations', {}).get('io.containerd.image.name') ==
                'docker.io/library/codebase-archaeologist-deep:oracle-15b11f8f092d4f55a475a8d4e3404a83']
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0]['digest'], release.NEW_IMAGE)

    def test_entrypoint_restores_settings_and_feature_check(self):
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

    def test_one_analysis_then_private_non_inference_checks(self):
        graph = {'schema_version': '1.1'}
        with patch.object(release.evidence_release, 'real_analysis', return_value=graph) as analysis, \
             patch.object(release.activation, 'configuration_probe') as configuration, \
             patch.object(release.activation, 'route_checks') as routes:
            self.assertIs(release.real_analysis('private-token'), graph)
        analysis.assert_called_once_with('private-token')
        configuration.assert_called_once_with()
        routes.assert_called_once_with('private-token')
