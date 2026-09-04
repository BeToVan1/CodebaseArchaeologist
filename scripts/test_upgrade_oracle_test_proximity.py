"""Offline owner-upgrade tests; no Docker, SSH or production mutations."""
import hashlib
import json
from pathlib import Path
import tarfile
from unittest.mock import patch

import test_upgrade_oracle_patterns as previous_tests
import upgrade_oracle_test_proximity as release


class TestProximityUpgradeTests(previous_tests.PatternUpgradeTests):
    def setUp(self):
        configuration = patch.multiple(release.updater, **release.SETTINGS)
        configuration.start()
        self.addCleanup(configuration.stop)

    def test_unit_changes_only_image_and_uses_separate_backup(self):
        self.assertEqual(release.NEW_UNIT.replace(release.NEW_IMAGE, release.OLD_IMAGE), release.OLD_UNIT)
        self.assertEqual(release.OLD_UNIT, release.previous.NEW_UNIT)
        self.assertEqual(release.NEW_UNIT.count(release.updater.common.MOUNT), 1)
        self.assertEqual(release.NEW_UNIT.count(release.updater.common.QUOTA_ENV), 1)
        self.assertEqual(release.BACKUP.name, 'pre-test-proximity-v1.service')

    def test_image_pin_matches_owner_validated_archive(self):
        archive = Path(__file__).resolve().parent.parent / 'artifacts/oracle-cdeab4828b3442c3ba258e40df8d2cba/deep-service.tar'
        if not archive.is_file():
            self.skipTest('Owner archive unavailable')
        with archive.open('rb') as source:
            self.assertEqual(hashlib.file_digest(source, 'sha256').hexdigest(),
                '57c46927ce852d19485d72b8380a2f11841c880c36be8c044068669292653f86')
        with tarfile.open(archive) as bundle:
            index = json.load(bundle.extractfile('index.json'))
            images = [item for item in index['manifests'] if item.get('annotations', {}).get('io.containerd.image.name') ==
                'docker.io/library/codebase-archaeologist-deep:oracle-cdeab4828b3442c3ba258e40df8d2cba']
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0]['digest'], release.NEW_IMAGE)

    def test_entrypoint_restores_settings_and_installs_feature_check(self):
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

    def test_one_analysis_reuses_response_and_invalid_feature_fails_closed(self):
        graph = {'test_proximity': 'placeholder'}
        with patch.object(release.previous, 'real_analysis', return_value=graph) as analysis, patch.object(release, 'check_test_proximity') as check:
            release.real_analysis('private-token')
            analysis.assert_called_once_with('private-token')
            check.assert_called_once_with(graph)
        with patch.object(release.previous, 'real_analysis', return_value={}) as analysis:
            with self.assertRaisesRegex(release.updater.common.UpgradeError, 'HTTPS test proximity'):
                release.real_analysis('private-token')
            analysis.assert_called_once()
