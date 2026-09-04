"""Offline tests for the pinned evidence-reference upgrade; no host changes."""
import hashlib
import json
from pathlib import Path
import tarfile
from unittest.mock import patch

import test_upgrade_oracle_test_proximity as previous_tests
import upgrade_oracle_evidence_reference as release


class EvidenceReferenceUpgradeTests(previous_tests.TestProximityUpgradeTests):
    def setUp(self):
        configuration = patch.multiple(release.updater, **release.SETTINGS)
        configuration.start()
        self.addCleanup(configuration.stop)

    def test_unit_changes_only_image_and_uses_separate_backup(self):
        self.assertEqual(release.NEW_UNIT.replace(release.NEW_IMAGE, release.OLD_IMAGE), release.OLD_UNIT)
        self.assertEqual(release.OLD_UNIT, release.previous.NEW_UNIT)
        self.assertEqual(release.NEW_UNIT.count(release.updater.common.MOUNT), 1)
        self.assertEqual(release.NEW_UNIT.count(release.updater.common.QUOTA_ENV), 1)
        self.assertEqual(release.BACKUP.name, 'pre-evidence-reference-v1.service')

    def test_image_pin_matches_owner_validated_archive(self):
        archive = Path(__file__).resolve().parent.parent / 'artifacts/oracle-6361ed3b211346bfa30ea12c4752675f/deep-service.tar'
        self.assertTrue(archive.is_file(), 'Validated evidence-reference archive is required.')
        with archive.open('rb') as source:
            self.assertEqual(hashlib.file_digest(source, 'sha256').hexdigest(),
                'f97c2611d59a4784ba95a9eb5b84089b9f2625e390b8447e5d80ec5e283871b3')
        with tarfile.open(archive) as bundle:
            index = json.load(bundle.extractfile('index.json'))
            images = [item for item in index['manifests'] if item.get('annotations', {}).get('io.containerd.image.name') ==
                'docker.io/library/codebase-archaeologist-deep:oracle-6361ed3b211346bfa30ea12c4752675f']
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

    def test_one_analysis_then_owner_isolation_and_exact_prepare(self):
        token = 'private-token'
        packet = {'version': '1', 'node_id': 'symbol:example.py:run'}
        graph = {
            'schema_version': '1.1', 'analysis': {'tier': 'deep'},
            'repository': {'url': release.updater.common.REPO},
            'snapshot': {'commit_sha': 'a' * 40},
            'nodes': [{'id': packet['node_id'], 'evidence_packet': packet}],
            'edges': [{'id': 'edge:1'}],
        }
        headers = {
            'X-Archaeologist-Report-Id': 'R' * 43,
            'X-Archaeologist-Report-TTL': '900', 'Cache-Control': 'no-store',
        }
        prepared = {'commitSha': 'a' * 40, 'evidencePacket': packet,
                    'sourceExcerpt': 'def run():\n    pass'}
        with patch.object(release, 'analyze_with_headers', return_value=(200, json.dumps(graph).encode(), headers)) as analysis, \
             patch.object(release.updater.common, 'request', side_effect=[(404, b''), (200, json.dumps(prepared).encode())]) as request:
            self.assertEqual(release.real_analysis(token), graph)
        analysis.assert_called_once()
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].args[1], '/api/evidence/prepare')
        self.assertEqual(request.call_args_list[1].args[1], '/api/evidence/prepare')

    def test_missing_reference_header_fails_without_prepare(self):
        graph = {
            'schema_version': '1.1', 'analysis': {'tier': 'deep'},
            'repository': {'url': release.updater.common.REPO},
            'snapshot': {'commit_sha': 'a' * 40},
            'nodes': [{'id': 'symbol:x', 'evidence_packet': {'node_id': 'symbol:x'}}],
            'edges': [{'id': 'edge:1'}],
        }
        with patch.object(release, 'analyze_with_headers', return_value=(200, json.dumps(graph).encode(), {})), \
             patch.object(release.updater.common, 'request') as request:
            with self.assertRaisesRegex(release.updater.common.UpgradeError, 'opaque evidence reference'):
                release.real_analysis('private-token')
        request.assert_not_called()
