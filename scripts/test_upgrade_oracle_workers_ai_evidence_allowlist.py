"""Offline tests for the pinned Workers AI evidence-allowlist updater."""
import hashlib
import json
from pathlib import Path
import tarfile
from unittest.mock import patch

import test_upgrade_oracle_workers_ai_diagnostics as previous_tests
import upgrade_oracle_workers_ai_evidence_allowlist as release


class WorkersAIEvidenceAllowlistUpgradeTests(previous_tests.WorkersAIDiagnosticsUpgradeTests):
    def setUp(self):
        configuration = patch.multiple(release.updater, **release.SETTINGS)
        configuration.start()
        self.addCleanup(configuration.stop)

    def test_unit_changes_only_image_and_preserves_private_configuration(self):
        self.assertEqual(release.OLD_IMAGE, release.previous.NEW_IMAGE)
        self.assertEqual(release.OLD_UNIT, release.previous.NEW_UNIT)
        self.assertEqual(release.NEW_UNIT.replace(release.NEW_IMAGE, release.OLD_IMAGE), release.OLD_UNIT)
        self.assertEqual(release.NEW_UNIT.count(release.previous.activation.AI_ENV_ARGUMENT), 1)
        self.assertEqual(release.NEW_UNIT.count(release.updater.common.MOUNT), 1)
        self.assertNotIn('ARCHAEOLOGIST_CF_AI_TOKEN=', release.NEW_UNIT)
        self.assertEqual(release.BACKUP.name, 'pre-workers-ai-evidence-allowlist-v1.service')

    def test_image_pin_matches_owner_validated_archive(self):
        archive = Path(__file__).resolve().parent.parent / \
            'artifacts/oracle-df76d39afeea490aae6641e095179e0d/deep-service.tar'
        self.assertTrue(archive.is_file(), 'Validated evidence-allowlist archive is required.')
        with archive.open('rb') as source:
            self.assertEqual(hashlib.file_digest(source, 'sha256').hexdigest(),
                '747035a9c8e2e185f001be5f7532861f0a19ff50e01f86a889c02c4e8e98e457')
        with tarfile.open(archive) as bundle:
            index = json.load(bundle.extractfile('index.json'))
            images = [item for item in index['manifests']
                if item.get('annotations', {}).get('io.containerd.image.name') ==
                'docker.io/library/codebase-archaeologist-deep:oracle-df76d39afeea490aae6641e095179e0d']
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0]['digest'], release.NEW_IMAGE)

    def test_entrypoint_restores_settings_and_uses_non_inference_checks(self):
        old_analysis = release.updater.common.real_analysis

        def check(argv):
            self.assertEqual(argv, ['--verify'])
            self.assertIs(release.updater.common.real_analysis, release.previous.real_analysis)
            for name, value in release.SETTINGS.items():
                self.assertEqual(getattr(release.updater, name), value)
            return 0

        with patch.object(release.updater, 'main', side_effect=check):
            self.assertEqual(release.main(['--verify']), 0)
        self.assertIs(release.updater.common.real_analysis, old_analysis)
