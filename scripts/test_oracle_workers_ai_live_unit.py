"""Offline tests for the one-shot live validator."""
import json
import unittest
from unittest.mock import patch

import test_oracle_workers_ai_live as live


def packet():
    return {
        'node_id': 'symbol:example.py:run',
        'related_edge_ids': ['edge:1'],
        'flow_ids': ['flow:1'],
        'finding_ids': [],
        'pattern_ids': ['pattern:1'],
        'claims': [{'id': 'claim:1', 'evidence_refs': ['edge:1']}],
    }


def response():
    section = {
        'text': 'Grounded text.', 'confidence': 0.7,
        'evidence_refs': ['claim:1'], 'classification': 'interpretation',
        'provenance': live.PROVENANCE,
    }
    return {
        'model': live.MODEL, 'classification': 'interpretation',
        'commitSha': 'a' * 40, 'nodeId': 'symbol:example.py:run',
        'what_it_does': dict(section), 'execution_role': dict(section),
        'structural_rationale': dict(section), 'uncertainties': ['Static evidence only.'],
    }


class LiveWorkersAIValidatorTests(unittest.TestCase):
    def test_structured_grounded_response_passes_without_returning_prose(self):
        sections, uncertainties = live.validate_interpretation(
            response(), packet(), 'a' * 40, 'symbol:example.py:run')
        self.assertEqual(uncertainties, 1)
        self.assertEqual(sections['what_it_does'], {'confidence': 0.7, 'citation_count': 1})
        self.assertNotIn('text', sections['what_it_does'])

    def test_unknown_evidence_and_excess_confidence_fail(self):
        unknown = response()
        unknown['execution_role']['evidence_refs'] = ['invented']
        with self.assertRaisesRegex(live.ValidationFailure, 'outside the retained packet'):
            live.validate_interpretation(unknown, packet(), 'a' * 40, 'symbol:example.py:run')
        confident = response()
        confident['what_it_does']['confidence'] = 0.86
        with self.assertRaisesRegex(live.ValidationFailure, 'exceeds policy'):
            live.validate_interpretation(confident, packet(), 'a' * 40, 'symbol:example.py:run')

    def test_main_submits_exactly_one_analysis_and_one_inference(self):
        graph = {
            'repository': {'url': live.REPOSITORY_URL},
            'analysis': {'tier': 'deep'}, 'snapshot': {'commit_sha': 'a' * 40},
            'nodes': [{'id': 'symbol:example.py:run', 'evidence_packet': packet()}],
        }
        headers = {'X-Archaeologist-Report-Id': 'R' * 43}
        calls = [
            (200, json.dumps(graph).encode(), headers),
            (200, json.dumps(response()).encode(), {}),
        ]
        with patch.object(live.sys, 'platform', 'linux'), \
             patch.object(live.os, 'geteuid', return_value=0, create=True), \
             patch.object(live, 'read_service_token', return_value='s' * 64), \
             patch.object(live, 'post', side_effect=calls) as post, \
             patch('builtins.print') as output:
            self.assertEqual(live.main(), 0)
        self.assertEqual(post.call_count, 2)
        self.assertEqual([item.args[0] for item in post.call_args_list],
            ['/api/analyze', '/api/interpret/quota-v1'])
        summary = json.loads(output.call_args.args[0])
        self.assertEqual(summary['model_requests'], 1)
        self.assertFalse(summary['generated_text_printed'])


if __name__ == '__main__':
    unittest.main()
