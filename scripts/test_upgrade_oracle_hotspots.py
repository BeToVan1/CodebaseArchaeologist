"""Run the image-only safety suite with the new release's actual pins."""
import hashlib
import json
from pathlib import Path
import tarfile
from unittest.mock import patch

import test_upgrade_oracle_patterns as previous_tests
import upgrade_oracle_hotspots as release


class HotspotScoringUpgradeTests(previous_tests.PatternUpgradeTests):
    def setUp(self):
        self.configuration = patch.multiple(release.updater, **release.SETTINGS)
        self.configuration.start()
        self.addCleanup(self.configuration.stop)

    def test_unit_changes_only_image_and_uses_separate_backup(self):
        self.assertEqual(release.NEW_UNIT.replace(release.NEW_IMAGE, release.OLD_IMAGE), release.OLD_UNIT)
        self.assertEqual(release.NEW_UNIT.count(release.updater.common.MOUNT), 1)
        self.assertEqual(release.NEW_UNIT.count(release.updater.common.QUOTA_ENV), 1)
        self.assertEqual(release.BACKUP.name, 'pre-hotspot-scoring-v1.service')
        self.assertNotEqual(release.BACKUP, release.updater.common.BACKUP)

    def test_image_pin_matches_owner_validated_archive(self):
        archive = Path(__file__).resolve().parent.parent / 'artifacts/oracle-ffcf743f3651412092fa68d8a822851b/deep-service.tar'
        if not archive.is_file():
            self.skipTest('Owner archive unavailable')
        with archive.open('rb') as source:
            self.assertEqual(hashlib.file_digest(source, 'sha256').hexdigest(),
                '43ec153ee23471930e845f6e325160dc86eff4563434f8fd703a07705ff3198a')
        with tarfile.open(archive) as bundle:
            index = json.load(bundle.extractfile('index.json'))
            images = [item for item in index['manifests'] if item.get('annotations', {}).get('io.containerd.image.name') ==
                'docker.io/library/codebase-archaeologist-deep:oracle-ffcf743f3651412092fa68d8a822851b']
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0]['digest'], release.NEW_IMAGE)
            def blob(digest):
                data = bundle.extractfile('blobs/sha256/' + digest.split(':')[1]).read()
                self.assertEqual('sha256:' + hashlib.sha256(data).hexdigest(), digest)
                return json.loads(data)
            platforms = [item for item in blob(release.NEW_IMAGE)['manifests']
                if item.get('platform') == {'architecture': 'amd64', 'os': 'linux'}]
            self.assertEqual(len(platforms), 1)
            config = blob(blob(platforms[0]['digest'])['config']['digest'])
            self.assertEqual(config['config']['User'], '10001:10001')

    def test_entrypoint_supplies_fixed_release_settings(self):
        def check(argv):
            self.assertEqual(argv, ['--verify'])
            for name, value in release.SETTINGS.items():
                self.assertEqual(getattr(release.updater, name), value)
            return 0
        with patch.object(release.updater, 'main', side_effect=check):
            self.assertEqual(release.main(['--verify']), 0)
