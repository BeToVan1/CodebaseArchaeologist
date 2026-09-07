"""Opt-in synthetic evaluation runner; defaults to no network or credentials.

Uses the Oracle provider adapter, not the public reference-only HTTP API.
Never accepts arbitrary source, model, endpoint, or automatic retries.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

from interpretation import EvidencePacket
from interpretation_evaluation import build_cases
from workers_ai_client import SYSTEM_PROMPT, WORKERS_AI_MODEL, WorkersAIConfig, WorkersAIError, generate_workers_ai


def select_cases(case_ids, max_requests):
    cases = build_cases()
    known = {case['caseId']: case for case in cases}
    if not case_ids or len(set(case_ids)) != len(case_ids) or any(i not in known for i in case_ids):
        raise ValueError('Choose unique case IDs from the checked-in corpus.')
    if type(max_requests) is not int or not 1 <= max_requests <= 6 or len(case_ids) > max_requests:
        raise ValueError('Request cap must be 1–6 and cover all selected cases.')
    return [known[i] for i in case_ids]


def write_record(directory, name, value):
    # Exclusive creation prevents a rerun overwriting earlier observations.
    fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write('\n')


def build_plan(cases, max_requests):
    """Describe the exact proposed run without credentials, writes, or inference."""
    if not cases or len(cases) > max_requests or not 1 <= max_requests <= 6:
        raise ValueError('Invalid request cap.')
    return {
        'model': WORKERS_AI_MODEL, 'maxRequests': max_requests,
        'systemPromptSha256': hashlib.sha256(SYSTEM_PROMPT.encode('utf-8')).hexdigest(),
        'providerAdapterSha256': hashlib.sha256(
            Path(__file__).with_name('workers_ai_client.py').read_bytes()).hexdigest(),
        'scope': 'checked-in synthetic corpus; not a live repository report',
        'settings': {'max_tokens': 1024, 'temperature': 0, 'stream': False},
        'cases': [{'caseId': c['caseId'], 'inputSha256': c['inputSha256']} for c in cases],
    }


async def execute(cases, max_requests, directory, config, *, provider=generate_workers_ai):
    plan = build_plan(cases, max_requests)
    # Require a new directory. Interrupted runs cannot silently resume/retry.
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    write_record(directory, 'plan.json', plan)
    samples = []
    for index, case in enumerate(cases):
        model_input = case['input']
        packet = EvidencePacket.model_validate(model_input['evidence_packet'])
        # No rubric/answer key is passed to the provider.
        write_record(directory, f'{index:02d}-started.json', {
            'caseId': case['caseId'], 'inputSha256': case['inputSha256'],
            'input': model_input, 'status': 'attempt-started',
        })
        try:
            result = await provider(packet, model_input['source_excerpt'], config)
        except WorkersAIError as error:
            write_record(directory, f'{index:02d}-outcome.json', {
                'caseId': case['caseId'], 'status': 'failed',
                'category': error.category, 'providerStatus': error.provider_status,
                'structuredReason': error.structured_reason,
            })
            write_record(directory, 'candidates.json', samples)
            return 1
        except Exception:
            # A timeout/unknown failure may already have consumed a request.
            write_record(directory, f'{index:02d}-outcome.json', {
                'caseId': case['caseId'], 'status': 'failed', 'category': 'unexpected',
            })
            write_record(directory, 'candidates.json', samples)
            return 1
        output = {name: {k: result[name][k] for k in ('text', 'confidence', 'evidence_refs')}
                  for name in ('what_it_does', 'execution_role', 'structural_rationale')}
        output['uncertainties'] = result['uncertainties']
        sample = {'caseId': case['caseId'], 'inputSha256': case['inputSha256'],
                  'model': WORKERS_AI_MODEL, 'origin': 'provider', 'output': output}
        write_record(directory, f'{index:02d}-outcome.json', {'status': 'received', 'sample': sample})
        samples.append(sample)
    write_record(directory, 'candidates.json', samples)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', action='append', dest='case_ids', required=True)
    parser.add_argument('--max-requests', type=int, default=1)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--confirm-free-only', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        cases = select_cases(args.case_ids, args.max_requests)
        if not args.execute:
            print(json.dumps({'mode': 'dry-run', 'modelRequests': 0,
                              'maxRequests': args.max_requests, 'model': WORKERS_AI_MODEL,
                              'cases': [c['caseId'] for c in cases],
                              'plan': build_plan(cases, args.max_requests)}))
            return 0
        if os.name != 'posix' or not args.confirm_free_only or args.output is None:
            raise ValueError('Execution requires Linux, free-only confirmation, and a new output directory.')
        config = WorkersAIConfig.optional(os.environ.get('ARCHAEOLOGIST_CF_ACCOUNT_ID', ''),
                                          os.environ.get('ARCHAEOLOGIST_CF_AI_TOKEN', ''))
        if config is None:
            raise ValueError('Valid server-side provider configuration is required.')
        status = asyncio.run(execute(cases, args.max_requests, args.output, config))
        print(json.dumps({'status': 'recorded' if status == 0 else 'stopped',
                          'semanticReview': 'pending', 'generatedTextPrinted': False}))
        return status
    except (ValueError, OSError):
        print('STOP: invalid configuration or unavailable output storage; no retry attempted.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
