import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

import interpretation_eval_runner as runner
from workers_ai_client import WorkersAIConfig, WorkersAIError


def config():
    return WorkersAIConfig('a' * 32, 'x' * 40)


def test_dry_run_never_reads_credentials_or_calls_model(capsys):
    with patch.object(runner.os, 'environ', {}), patch.object(runner, 'execute') as execute:
        assert runner.main(['--case', 'direct-transform']) == 0
        execute.assert_not_called()
    assert json.loads(capsys.readouterr().out)['modelRequests'] == 0


def test_six_case_dry_plan_is_reproducible_and_does_not_write(tmp_path, capsys):
    ids = [case['caseId'] for case in runner.build_cases()]
    args = [item for cid in ids for item in ('--case', cid)]
    output = tmp_path / 'must-not-exist'
    with patch.object(runner.WorkersAIConfig, 'optional', side_effect=AssertionError('credentials accessed')), \
            patch.object(runner, 'execute', side_effect=AssertionError('execution attempted')):
        assert runner.main(args + ['--max-requests', '6', '--output', str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['modelRequests'] == 0
    assert not output.exists()
    plan = result['plan']
    assert plan == runner.build_plan(runner.select_cases(ids, 6), 6)
    assert len(plan['cases']) == 6
    assert all(len(case['inputSha256']) == 64 for case in plan['cases'])
    assert set(plan['cases'][0]) == {'caseId', 'inputSha256'}
    assert 'rubric' not in json.dumps(plan)
    with patch.object(runner, 'SYSTEM_PROMPT', runner.SYSTEM_PROMPT + '\nChanged'):
        assert runner.build_plan(runner.select_cases(ids, 6), 6)['systemPromptSha256'] != plan['systemPromptSha256']


@pytest.mark.parametrize('ids,cap', [([], 1), (['unknown'], 1),
    (['direct-transform'] * 2, 2), (['direct-transform'], 0),
    (['direct-transform'], 7), (['direct-transform', 'conditional-execution'], 1)])
def test_invalid_selection_rejected(ids, cap):
    with pytest.raises(ValueError):
        runner.select_cases(ids, cap)


def test_failure_is_retained_and_stops_without_retry(tmp_path):
    cases = runner.select_cases(['direct-transform', 'conditional-execution'], 2)
    provider = AsyncMock(side_effect=WorkersAIError('sensitive-input'))
    destination = tmp_path / 'run'
    assert asyncio.run(runner.execute(cases, 2, destination, config(), provider=provider)) == 1
    assert provider.await_count == 1
    assert json.loads((destination / '00-outcome.json').read_text())['category'] == 'sensitive-input'
    assert json.loads((destination / 'candidates.json').read_text()) == []
    assert not (destination / '01-started.json').exists()
    with pytest.raises(FileExistsError):
        asyncio.run(runner.execute(cases, 2, destination, config(), provider=provider))
    assert provider.await_count == 1


def test_success_records_assessable_output_without_answer_key(tmp_path):
    cases = runner.select_cases(['direct-transform'], 1)
    async def provider(packet, source, cfg):
        assert cfg == config()
        assert isinstance(source, str)
        section = {'text': 'Synthetic mocked response.', 'confidence': 0.5,
                   'evidence_refs': [packet.node_id]}
        return {**{name: section for name in ('what_it_does', 'execution_role', 'structural_rationale')},
                'uncertainties': []}
    destination = tmp_path / 'run'
    assert asyncio.run(runner.execute(cases, 1, destination, config(), provider=provider)) == 0
    samples = json.loads((destination / 'candidates.json').read_text())
    assert len(samples) == 1
    plan = json.loads((destination / 'plan.json').read_text())
    assert plan == runner.build_plan(cases, 1)
    assert len(plan['systemPromptSha256']) == 64
    assert len(plan['providerAdapterSha256']) == 64
    assert samples[0]['inputSha256'] == cases[0]['inputSha256']
    assert 'rubric' not in json.loads((destination / '00-started.json').read_text())['input']


def test_unexpected_errors_do_not_leak_details(tmp_path):
    provider = AsyncMock(side_effect=RuntimeError('sensitive-error-text'))
    destination = tmp_path / 'run'
    cases = runner.select_cases(['direct-transform'], 1)
    assert asyncio.run(runner.execute(cases, 1, destination, config(), provider=provider)) == 1
    assert 'sensitive-error-text' not in (destination / '00-outcome.json').read_text()


def test_execute_without_explicit_confirmation_rejected(tmp_path):
    with patch.object(runner, 'execute') as execute:
        assert runner.main(['--case', 'direct-transform', '--execute', '--output', str(tmp_path / 'run')]) == 2
        execute.assert_not_called()
