"""Offline checks only: no production connections or mutations."""
import hashlib
import json
from pathlib import Path
import tarfile
from unittest.mock import patch

import test_upgrade_oracle_patterns as previous_tests
import upgrade_oracle_project_discovery as release


class ProjectDiscoveryUpgradeTests(previous_tests.PatternUpgradeTests):
    def setUp(self):
        configuration = patch.multiple(release.updater, **release.SETTINGS)
        configuration.start()
        self.addCleanup(configuration.stop)

    def test_unit_changes_only_image_and_uses_separate_backup(self):
        self.assertEqual(release.NEW_UNIT.replace(release.NEW_IMAGE, release.OLD_IMAGE), release.OLD_UNIT)
        self.assertEqual(release.NEW_UNIT.count(release.updater.common.MOUNT), 1)
        self.assertEqual(release.NEW_UNIT.count(release.updater.common.QUOTA_ENV), 1)
        self.assertEqual(release.BACKUP.name, 'pre-project-discovery-v1.service')

    def test_image_pin_matches_owner_validated_archive(self):
        archive = Path(__file__).resolve().parent.parent / 'artifacts/oracle-0cbfe602abbd4826b0a344c5b0bc1f3b/deep-service.tar'
        if not archive.is_file():
            self.skipTest('Owner archive unavailable')
        with archive.open('rb') as source:
            self.assertEqual(hashlib.file_digest(source, 'sha256').hexdigest(),
                'f8fe8981d018937f3c5ae2eea45cec782d3c7693b3647fa4df328a38c412c137')
        with tarfile.open(archive) as bundle:
            index = json.load(bundle.extractfile('index.json'))
            images = [item for item in index['manifests'] if item.get('annotations', {}).get('io.containerd.image.name') ==
                'docker.io/library/codebase-archaeologist-deep:oracle-0cbfe602abbd4826b0a344c5b0bc1f3b']
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

    def test_https_check_requires_feature_and_never_retries(self):
        common = release.updater.common
        graph = {'schema_version': '1.1', 'analysis': {'tier': 'deep'},
            'repository': {'url': common.REPO}, 'snapshot': {'commit_sha': 'a'*40},
            'nodes': [1], 'edges': [1], 'project_discovery': {'version': '1', 'status': 'parsed',
                'scope': 'root-pyproject-only', 'path': 'pyproject.toml', 'sha256': 'b'*64,
                'limitations': ['Literal declarations only'], 'declarations': [
                    {'key': ['project', 'name'], 'value': 'itsdangerous', 'classification': 'fact', 'confidence': 1}]}}
        with patch.object(common, 'request', return_value=(200, json.dumps(graph))) as request:
            release.real_analysis('private-token')
            request.assert_called_once()
        for metadata in (None, {**graph['project_discovery'], 'status': 'missing'},
                         {**graph['project_discovery'], 'sha256': 'bad'},
                         {**graph['project_discovery'], 'declarations': []}):
            with patch.object(common, 'request', return_value=(200, json.dumps({**graph, 'project_discovery': metadata}))) as request:
                with self.assertRaises(common.UpgradeError):
                    release.real_analysis('private-token')
                request.assert_called_once()
