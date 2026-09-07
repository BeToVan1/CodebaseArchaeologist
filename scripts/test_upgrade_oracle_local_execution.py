"""Offline release pin and inherited rollback tests."""
import hashlib
import json
from pathlib import Path
import tarfile
from unittest.mock import patch

import test_upgrade_oracle_secret_screening as previous_tests
import upgrade_oracle_local_execution as release


class LocalExecutionUpgradeTests(previous_tests.SecretScreeningUpgradeTests):
    def setUp(self):
        configuration = patch.multiple(release.updater, **release.SETTINGS)
        configuration.start()
        self.addCleanup(configuration.stop)

    def test_unit_changes_only_image_and_preserves_private_configuration(self):
        self.assertEqual(release.OLD_IMAGE, release.previous.NEW_IMAGE)
        self.assertEqual(release.OLD_UNIT, release.previous.NEW_UNIT)
        self.assertEqual(release.NEW_UNIT.replace(release.NEW_IMAGE, release.OLD_IMAGE), release.OLD_UNIT)
        self.assertEqual(release.NEW_UNIT.count(release.updater.common.MOUNT), 1)
        self.assertNotIn('ARCHAEOLOGIST_CF_AI_TOKEN=', release.NEW_UNIT)
        self.assertEqual(release.BACKUP.name, 'pre-local-execution-v1.service')

    def test_image_pin_matches_owner_validated_archive(self):
        archive = Path(__file__).resolve().parent.parent / 'artifacts/oracle-d6776fa807fd4fbd8a32e6cc728750cf/deep-service.tar'
        with archive.open('rb') as source:
            self.assertEqual(hashlib.file_digest(source, 'sha256').hexdigest(),
                             'f482d9bef41b80250c28e36ac7f7d696cf4e521765de557495e8ff71b21e3765')
        with tarfile.open(archive) as bundle:
            index = json.load(bundle.extractfile('index.json'))
            images = [item for item in index['manifests'] if item.get('annotations', {}).get('io.containerd.image.name') ==
                      'docker.io/library/codebase-archaeologist-deep:oracle-d6776fa807fd4fbd8a32e6cc728750cf']
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0]['digest'], release.NEW_IMAGE)

    def test_entrypoint_restores_settings_and_uses_non_inference_checks(self):
        saved = {name: getattr(release.updater, name) for name in release.SETTINGS}
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
        self.assertEqual({name: getattr(release.updater, name) for name in saved}, saved)
