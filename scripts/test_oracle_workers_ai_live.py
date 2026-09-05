"""One-shot owner-run Workers AI validation over verified Oracle HTTPS.

This script submits one known-small repository analysis and exactly one AI
interpretation request. It never retries, prints generated prose, displays
credentials, or changes service/site configuration.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import signal
import ssl
import stat
import sys
import urllib.error
import urllib.request

BASE = 'https://codebase-archaeologist.duckdns.org'
REPOSITORY = 'pallets/itsdangerous'
REPOSITORY_URL = 'https://github.com/' + REPOSITORY
MODEL = '@cf/meta/llama-3.3-70b-instruct-fp8-fast'
PROVENANCE = f'Cloudflare Workers AI {MODEL} interpretation of server-retained evidence'
TOKEN_PATH = Path('/etc/codebase-archaeologist/service.env')
MAX_REPORT = 10 * 1024 * 1024
MAX_INTERPRETATION = 64 * 1024
HTTP = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPSHandler(context=ssl.create_default_context()),
)


class ValidationFailure(Exception):
    pass


def require(condition, message):
    if not condition:
        raise ValidationFailure(message)


def read_service_token():
    require(stat.S_IMODE(TOKEN_PATH.parent.stat().st_mode) == 0o700,
        'Service configuration directory is not root-only.')
    descriptor = os.open(TOKEN_PATH, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as source:
        metadata = os.fstat(source.fileno())
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid == 0
            and metadata.st_nlink == 1 and stat.S_IMODE(metadata.st_mode) == 0o600,
            'Service token file permissions differ.')
        content = source.read(257)
    match = re.fullmatch(rb'ARCHAEOLOGIST_SERVICE_TOKEN=([a-f0-9]{64})\n', content)
    require(match is not None, 'Service token file format differs.')
    return match[1].decode('ascii')


def post(path, payload, token, owner_key, limit, timeout):
    request = urllib.request.Request(
        BASE + path,
        headers={
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + token,
            'X-Archaeologist-Client-Key': owner_key,
        },
        data=json.dumps(payload).encode(),
    )
    try:
        with HTTP.open(request, timeout=timeout) as response:
            return response.status, response.read(limit + 1), response.headers
    except urllib.error.HTTPError as error:
        status = error.code
        body = error.read(limit + 1)
        headers = error.headers
        error.close()
        return status, body, headers


def known_refs(packet):
    refs = {packet.get('node_id')}
    for field in ('related_edge_ids', 'flow_ids', 'finding_ids', 'pattern_ids'):
        values = packet.get(field, [])
        require(isinstance(values, list), 'Evidence packet reference collection differs.')
        refs.update(value for value in values if isinstance(value, str))
    claims = packet.get('claims')
    require(isinstance(claims, list), 'Evidence packet claims differ.')
    for claim in claims:
        require(isinstance(claim, dict) and isinstance(claim.get('evidence_refs'), list),
            'Evidence claim format differs.')
        if isinstance(claim.get('id'), str):
            refs.add(claim['id'])
        refs.update(value for value in claim['evidence_refs'] if isinstance(value, str))
    refs.discard(None)
    return refs


def validate_section(section, allowed):
    require(isinstance(section, dict) and set(section) == {
        'text', 'confidence', 'evidence_refs', 'classification', 'provenance'},
        'Interpretation section shape differs.')
    require(isinstance(section['text'], str) and 1 <= len(section['text']) <= 1200,
        'Interpretation text bounds differ.')
    require(type(section['confidence']) in (int, float)
        and 0 <= section['confidence'] <= 0.85,
        'Interpretation confidence exceeds policy.')
    require(section['classification'] == 'interpretation'
        and section['provenance'] == PROVENANCE,
        'Interpretation trust metadata differs.')
    refs = section['evidence_refs']
    require(isinstance(refs, list) and 1 <= len(refs) <= 10
        and all(isinstance(ref, str) and ref in allowed for ref in refs),
        'Interpretation cites evidence outside the retained packet.')
    return {'confidence': section['confidence'], 'citation_count': len(refs)}


def validate_interpretation(value, packet, commit, node_id):
    require(isinstance(value, dict) and set(value) == {
        'model', 'classification', 'commitSha', 'nodeId', 'what_it_does',
        'execution_role', 'structural_rationale', 'uncertainties'},
        'Interpretation response shape differs.')
    require(value['model'] == MODEL and value['classification'] == 'interpretation',
        'Interpretation model or classification differs.')
    require(value['commitSha'] == commit and value['nodeId'] == node_id,
        'Interpretation identity differs from the retained snapshot.')
    allowed = known_refs(packet)
    sections = {
        name: validate_section(value[name], allowed)
        for name in ('what_it_does', 'execution_role', 'structural_rationale')
    }
    uncertainties = value['uncertainties']
    require(isinstance(uncertainties, list) and len(uncertainties) <= 5
        and all(isinstance(item, str) and 1 <= len(item) <= 500 for item in uncertainties),
        'Interpretation uncertainty bounds differ.')
    return sections, len(uncertainties)


def main():
    require(sys.platform.startswith('linux') and os.geteuid() == 0,
        'Run with sudo python3 on Oracle Ubuntu.')
    token = read_service_token()
    owner_key = hmac.new(token.encode(), b'oracle-workers-ai-one-shot-v1', hashlib.sha256).hexdigest()

    analysis_status, analysis_body, analysis_headers = post(
        '/api/analyze', {'repositoryUrl': REPOSITORY_URL}, token, owner_key, MAX_REPORT, 75)
    require(len(analysis_body) <= MAX_REPORT, 'Analysis report exceeded its output limit.')
    require(analysis_status == 200, 'The single repository analysis failed; no inference was attempted.')
    try:
        report = json.loads(analysis_body)
    except (ValueError, UnicodeError):
        raise ValidationFailure('Analysis returned invalid JSON.') from None
    commit = report.get('snapshot', {}).get('commit_sha')
    require(report.get('repository', {}).get('url') == REPOSITORY_URL
        and report.get('analysis', {}).get('tier') == 'deep'
        and isinstance(commit, str) and re.fullmatch(r'[a-f0-9]{40}', commit),
        'Analysis report identity differs.')
    candidates = sorted(
        (node for node in report.get('nodes', [])
         if isinstance(node, dict) and isinstance(node.get('id'), str)
         and isinstance(node.get('evidence_packet'), dict)),
        key=lambda node: node['id'],
    )
    require(bool(candidates), 'Analysis returned no interpretable evidence packet.')
    node = candidates[0]
    packet = node['evidence_packet']
    report_id = analysis_headers.get('X-Archaeologist-Report-Id', '')
    require(re.fullmatch(r'[A-Za-z0-9_-]{43}', report_id) is not None,
        'Analysis did not return an opaque evidence reference.')

    # Exactly one inference request. There is intentionally no retry path.
    ai_status, ai_body, _ = post('/api/interpret/quota-v1',
        {'reportId': report_id, 'nodeId': node['id']},
        token, owner_key, MAX_INTERPRETATION, 30)
    require(len(ai_body) <= MAX_INTERPRETATION, 'Interpretation exceeded its output limit.')
    require(ai_status == 200,
        f'The one AI request failed safely with HTTP {ai_status}; it was not retried.')
    try:
        interpretation = json.loads(ai_body)
    except (ValueError, UnicodeError):
        raise ValidationFailure('Interpretation returned invalid JSON.') from None
    sections, uncertainty_count = validate_interpretation(
        interpretation, packet, commit, node['id'])
    print(json.dumps({
        'result': 'PASS',
        'transport': 'verified HTTPS',
        'repository': REPOSITORY,
        'commit': commit,
        'node_id': node['id'],
        'model': MODEL,
        'model_requests': 1,
        'sections': sections,
        'uncertainty_count': uncertainty_count,
        'generated_text_printed': False,
        'public_site_changed': False,
    }, sort_keys=True))
    return 0


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    if sys.platform.startswith('linux'):
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGHUP, interrupted)
    try:
        raise SystemExit(main())
    except (ValidationFailure, KeyboardInterrupt) as error:
        message = str(error) if isinstance(error, ValidationFailure) else 'Interrupted; no retry was attempted.'
        print('STOP: ' + message)
        raise SystemExit(1)
