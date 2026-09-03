"""Owner-run live HTTPS smoke test. Run with sudo python3 on Oracle.

Only submits the known small public repository to the approved hostname. Does
not alter configuration, print credentials/reports, follow redirects, or retry
analysis jobs. TLS certificate verification stays enabled.
"""
import argparse
import http.client
import json
import os
from pathlib import Path
import re
import signal
import ssl
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = 'https://codebase-archaeologist.duckdns.org'
REPOSITORY = 'pallets/itsdangerous'
REPO_URL = 'https://github.com/' + REPOSITORY
TOKEN_PATH = Path('/etc/codebase-archaeologist/service.env')
MAX_BYTES = 10 * 1024 * 1024
MEMORY_LIMIT = 384 * 1024 * 1024
EXPECTED_IMAGE = 'sha256:320de275261a0a2e1255df7d971ee26322dbb3837e579521ff25eadc1f8efa07'
CGROUP_ROOT = Path('/sys/fs/cgroup')
# Request specific fields only: never inspect or display Config.Env (the token).
INSPECT_FORMAT = '''{"id":{{json .Id}},"image":{{json .Image}},
"pid":{{json .State.Pid}},"running":{{json .State.Running}},
"oom":{{json .State.OOMKilled}},"started":{{json .State.StartedAt}},
"restarts":{{json .RestartCount}},"memory":{{json .HostConfig.Memory}},
"swap":{{json .HostConfig.MemorySwap}},"cpus":{{json .HostConfig.NanoCpus}},
"pids":{{json .HostConfig.PidsLimit}},"readonly":{{json .HostConfig.ReadonlyRootfs}},
"user":{{json .Config.User}},"caps":{{json .HostConfig.CapDrop}},
"security":{{json .HostConfig.SecurityOpt}}}'''


class SmokeFailure(Exception):
    pass


def validate_runtime(info):
    expected = {'image': EXPECTED_IMAGE, 'running': True, 'oom': False,
                'memory': MEMORY_LIMIT, 'swap': MEMORY_LIMIT,
                'cpus': 1_000_000_000, 'pids': 64, 'readonly': True,
                'user': '10001:10001'}
    if any(info.get(key) != value for key, value in expected.items()):
        raise SmokeFailure('Runtime image, state or resource limits differ from the approved configuration.')
    if (not isinstance(info.get('id'), str) or not re.fullmatch(r'[a-f0-9]{64}', info['id'])
            or type(info.get('pid')) is not int or info['pid'] <= 0
            or not isinstance(info.get('started'), str) or not info['started']
            or type(info.get('restarts')) is not int or info['restarts'] < 0
            or info.get('caps') != ['ALL']
            or not any(item in ('no-new-privileges', 'no-new-privileges:true')
                       for item in (info.get('security') or []))):
        raise SmokeFailure('Runtime identity or isolation controls could not be verified.')


def cgroup_directory(pid):
    membership = Path(f'/proc/{pid}/cgroup').read_text()
    unified = [line[3:] for line in membership.splitlines() if line.startswith('0::')]
    if len(unified) != 1 or not unified[0].startswith('/'):
        raise SmokeFailure('Expected a cgroup-v2 memory controller; no configuration changed.')
    root = CGROUP_ROOT.resolve(strict=True)
    directory = (root / unified[0].lstrip('/')).resolve(strict=True)
    if directory == root or not directory.is_relative_to(root):
        raise SmokeFailure('Unexpected runtime cgroup path.')
    return directory


def runtime_snapshot():
    result = subprocess.run(['/usr/bin/docker', 'inspect', '--type=container',
                             '--format', INSPECT_FORMAT, 'codebase-archaeologist'],
                            capture_output=True, text=True, timeout=10, check=False)
    if result.returncode != 0 or len(result.stdout) > 16384:
        raise SmokeFailure('Could not inspect the running analyzer; no container was started or changed.')
    info = json.loads(result.stdout)
    validate_runtime(info)
    directory = cgroup_directory(info['pid'])
    info['current_bytes'] = int((directory / 'memory.current').read_text())
    info['peak_bytes'] = int((directory / 'memory.peak').read_text())
    maximum = int((directory / 'memory.max').read_text())
    events = dict(line.split() for line in (directory / 'memory.events').read_text().splitlines())
    if maximum != MEMORY_LIMIT or any(int(events[key]) != 0 for key in ('oom', 'oom_kill')):
        raise SmokeFailure('Runtime memory limit differs or cgroup records an out-of-memory event.')
    if not 0 <= info['current_bytes'] <= info['peak_bytes'] <= MEMORY_LIMIT:
        raise SmokeFailure('Runtime memory readings are inconsistent with the configured limit.')
    return info


def compare_runtime(before, after):
    if any(before[key] != after[key] for key in ('id', 'image', 'pid', 'started', 'restarts')):
        raise SmokeFailure('Analyzer restarted or was replaced during validation.')
    return {'runtime': 'same container, no observed OOM event',
            'memory_limit_mib': 384,
            'memory_before_mib': round(before['current_bytes'] / 1024**2, 2),
            'memory_after_mib': round(after['current_bytes'] / 1024**2, 2),
            'container_lifetime_peak_mib': round(after['peak_bytes'] / 1024**2, 2)}


def parse_process_stat(data):
    # Kernel /proc/PID/stat: parse the parenthesized comm separately. Never read
    # process command lines, environments, or unrelated host process listings.
    prefix, fields = data.rsplit(') ', 1)
    pid, name = prefix.split(' (', 1)
    values = fields.split()
    return {'pid': int(pid), 'name': name, 'state': values[0],
            'ppid': int(values[1]), 'group': int(values[2]),
            'session': int(values[3]), 'start': int(values[19])}


def read_process(pid):
    if type(pid) is not int or pid <= 0:
        raise SmokeFailure('Invalid process identity.')
    try:
        return parse_process_stat(Path(f'/proc/{pid}/stat').read_text())
    except FileNotFoundError:
        return None


def container_processes(container_pid):
    values = (cgroup_directory(container_pid) / 'cgroup.procs').read_text().split()
    if len(values) > 64 or any(not value.isdecimal() or int(value) <= 0 for value in values):
        raise SmokeFailure('Unexpected container process listing.')
    return {int(value): info for value in values if (info := read_process(int(value))) is not None}


def isolated_jobs(processes, baseline):
    return [info for pid, info in processes.items()
            if pid not in baseline and info['ppid'] in baseline
            and info['group'] == pid == info['session']
            and info['state'] not in ('Z', 'X')
            and re.fullmatch(r'python(?:[0-9]+(?:\.[0-9]+)*)?', info['name'])]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


HTTP = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), NoRedirect(),
    urllib.request.HTTPSHandler(context=ssl.create_default_context()))


def parse_token(data):
    match = re.fullmatch(rb'ARCHAEOLOGIST_SERVICE_TOKEN=([a-f0-9]{64})\n', data)
    if not match:
        raise SmokeFailure('Token file has an unexpected format; no changes made.')
    return match[1].decode('ascii')


def read_token():
    for path in (TOKEN_PATH.parent, *TOKEN_PATH.parent.parents):
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise SmokeFailure('Unsafe token directory ownership or permissions.')
    if stat.S_IMODE(TOKEN_PATH.parent.stat().st_mode) != 0o700:
        raise SmokeFailure('Token directory must be root-only (0700).')
    fd = os.open(TOKEN_PATH, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise SmokeFailure('Token must be a root-owned regular file with permissions 0600.')
        return parse_token(source.read(257))


def request(path, payload=None, token=None, timeout=75):
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(BASE + path, headers=headers,
        data=None if payload is None else json.dumps(payload).encode())
    try:
        with HTTP.open(req, timeout=timeout) as response:
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise SmokeFailure('Response exceeded the 10 MiB report limit.')
            return response.status, body
    except urllib.error.HTTPError as error:
        # Never display error bodies or headers that might echo credentials.
        code = error.code
        error.close()
        return code, b''


def validate_report(data):
    graph = json.loads(data)
    if not isinstance(graph, dict) or graph.get('schema_version') != '1.1':
        raise SmokeFailure('Unexpected report schema.')
    if not isinstance(graph.get('analysis'), dict) or graph['analysis'].get('tier') != 'deep':
        raise SmokeFailure('Report is not deep analysis.')
    if not isinstance(graph.get('repository'), dict) or graph['repository'].get('name') != REPOSITORY or graph['repository'].get('url') != REPO_URL:
        raise SmokeFailure('Report repository does not match the requested repository.')
    snapshot = graph.get('snapshot')
    sha = snapshot.get('commit_sha') if isinstance(snapshot, dict) else None
    if not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{40}', sha):
        raise SmokeFailure('Report is missing a valid commit snapshot.')
    if graph['repository'].get('pinned_url') != REPO_URL + '/tree/' + sha:
        raise SmokeFailure('Pinned source URL does not match the commit snapshot.')
    nodes, edges = graph.get('nodes'), graph.get('edges')
    if not isinstance(nodes, list) or not nodes or not isinstance(edges, list) or not edges:
        raise SmokeFailure('Expected nonempty nodes and edges.')
    ids = [node.get('id') if isinstance(node, dict) else None for node in nodes]
    if not all(isinstance(item, str) and item for item in ids) or len(set(ids)) != len(ids):
        raise SmokeFailure('Graph node IDs are invalid or duplicated.')
    known = set(ids)
    for edge in edges:
        if not isinstance(edge, dict) or not isinstance(edge.get('source'), str) or not isinstance(edge.get('target'), str) or edge['source'] not in known or edge['target'] not in known:
            raise SmokeFailure('A graph relationship references a missing node.')
    return {'repository': REPOSITORY, 'commit': sha, 'tier': 'deep', 'nodes': len(nodes), 'edges': len(edges)}


def smoke(token):
    code, body = request('/health')
    if code != 200 or json.loads(body) != {'status': 'ok'}:
        raise SmokeFailure(f'HTTPS health check failed (HTTP {code}).')
    if request('/api/analyze', {'repositoryUrl': REPO_URL})[0] != 401:
        raise SmokeFailure('Unauthenticated analysis was not rejected with 401.')
    print('PASS: HTTPS health and unauthenticated rejection. Starting one repository analysis.', flush=True)
    start = time.monotonic()
    code, body = request('/api/analyze', {'repositoryUrl': REPO_URL}, token)
    if code != 200:
        raise SmokeFailure(f'Authenticated analysis returned HTTP {code}; no automatic retry.')
    result = validate_report(body)
    if request('/health')[0] != 200:
        raise SmokeFailure('Post-analysis health check failed.')
    return {'result': 'PASS', 'transport': 'verified HTTPS', **result,
            'elapsed_seconds': round(time.monotonic() - start, 2)}


def request_lifecycle(token):
    """One incomplete body, no GitHub jobs. Run only when the service is idle.

    Recovery must precede the service's five-second body timeout, otherwise
    timeout recovery could be mistaken for disconnect cleanup. Timing races or
    proxy buffering produce an inconclusive STOP, not a false cancellation PASS.
    """
    code, body = request('/health', timeout=3)
    if code != 200 or json.loads(body) != {'status': 'ok'}:
        raise SmokeFailure('Lifecycle preflight health check failed.')
    if request('/api/analyze', {}, timeout=3)[0] != 401:
        raise SmokeFailure('Lifecycle preflight authorization check failed.')
    if request('/api/analyze', {}, token, timeout=3)[0] != 400:
        raise SmokeFailure('Service is not idle or input validation changed; lifecycle test not started.')
    held = http.client.HTTPSConnection('codebase-archaeologist.duckdns.org',
                                      timeout=2, context=ssl.create_default_context())
    try:
        held.connect()
        held.putrequest('POST', '/api/analyze')
        held.putheader('Authorization', 'Bearer ' + token)
        held.putheader('Content-Type', 'application/json')
        held.putheader('Content-Length', '100')
        held.putheader('Connection', 'close')
        started = time.monotonic()
        held.endheaders(b'{')  # Deliberately incomplete and never a repository URL.
        time.sleep(0.25)
        code = request('/api/analyze', {}, token, timeout=1)[0]
        if code != 429:
            raise SmokeFailure('INCONCLUSIVE: held request did not produce HTTP 429; check proxy buffering or concurrent traffic.')
        if request('/health', timeout=1)[0] != 200:
            raise SmokeFailure('Health endpoint was unavailable while the request slot was busy.')
        if time.monotonic() - started >= 2:
            raise SmokeFailure('INCONCLUSIVE: timing window too slow to distinguish disconnect from body timeout.')
    finally:
        held.close()
    closed = time.monotonic()
    # At most six invalid-input probes. Never retry or start analysis work.
    for _ in range(6):
        if time.monotonic() - started >= 4:
            break
        code = request('/api/analyze', {}, token, timeout=0.7)[0]
        elapsed = time.monotonic() - started
        if code == 400 and elapsed < 4:
            return {'result': 'PASS', 'transport': 'verified HTTPS',
                    'scope': 'request-body disconnect only; not active-job cancellation',
                    'busy_status': 429, 'recovered_status': 400,
                    'recovery_seconds': round(time.monotonic() - closed, 3),
                    'analysis_jobs_submitted': 0}
        if code not in (400, 429):
            raise SmokeFailure(f'Unexpected recovery status HTTP {code}; no automatic analysis retry.')
        time.sleep(0.25)
    raise SmokeFailure('INCONCLUSIVE: slot did not recover before the body-timeout exclusion window.')


def active_job_disconnect(token, before):
    """Observe one real job around disconnect; never signal or restart processes.

    This is a live observation, not deterministic proof of cause: normal job
    completion can race a disconnect. The Linux lifecycle tests cover forced
    cancellation deterministically. Never expand the repository to prolong work.
    """
    if request('/health', timeout=3)[0] != 200:
        raise SmokeFailure('Active-job preflight health check failed.')
    if request('/api/analyze', {}, timeout=3)[0] != 401:
        raise SmokeFailure('Active-job preflight authorization check failed.')
    if request('/api/analyze', {}, token, timeout=3)[0] != 400:
        raise SmokeFailure('Service is not idle; active-job test not started.')
    baseline = container_processes(before['pid'])
    held = http.client.HTTPSConnection('codebase-archaeologist.duckdns.org',
                                      timeout=3, context=ssl.create_default_context())
    observed = {}
    try:
        held.connect()
        started = time.monotonic()
        held.request('POST', '/api/analyze', json.dumps({'repositoryUrl': REPO_URL}),
                     {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                      'Connection': 'close'})
        job = None
        for _ in range(100):
            if time.monotonic() - started >= 2:
                break
            current = container_processes(before['pid'])
            candidates = isolated_jobs(current, baseline)
            if len(candidates) > 1:
                raise SmokeFailure('INCONCLUSIVE: multiple isolated jobs observed; stop other tests.')
            if candidates:
                job = candidates[0]
                observed.update({pid: info['start'] for pid, info in current.items() if info['group'] == job['pid']})
                break
            time.sleep(0.02)
        if job is None:
            raise SmokeFailure('INCONCLUSIVE: no active analyzer process observed; no automatic retry.')
        if request('/api/analyze', {}, token, timeout=1)[0] != 429:
            raise SmokeFailure('INCONCLUSIVE: job was not still busy before disconnect.')
        current = container_processes(before['pid'])
        still_running = current.get(job['pid'])
        if (not still_running or still_running['start'] != job['start']
                or still_running['state'] in ('Z', 'X')
                or time.monotonic() - started >= 4):
            raise SmokeFailure('INCONCLUSIVE: job finished or timing window elapsed before disconnect.')
        observed.update({pid: info['start'] for pid, info in current.items() if info['group'] == job['pid']})
    finally:
        held.close()
    closed = time.monotonic()
    recovery_probes = 0
    for _ in range(100):
        if time.monotonic() - closed >= 5:
            break
        current = container_processes(before['pid'])
        members = {pid: info for pid, info in current.items() if info['group'] == job['pid']}
        observed.update({pid: info['start'] for pid, info in members.items()})
        remaining = [info for pid, start in observed.items()
                     if (info := read_process(pid)) is not None and info['start'] == start]
        if not members and not remaining:
            if recovery_probes >= 6:
                break
            recovery_probes += 1
            code = request('/api/analyze', {}, token, timeout=0.7)[0]
            if code == 400 and time.monotonic() - closed < 5:
                if request('/health', timeout=1)[0] != 200:
                    raise SmokeFailure('Post-disconnect health check failed.')
                return {'result': 'PASS', 'transport': 'verified HTTPS',
                        'scope': 'active-job disconnect observation; normal completion can race cancellation',
                        'analysis_jobs_submitted': 1, 'active_job_observed': True,
                        'busy_status': 429, 'observed_processes_reaped': len(observed),
                        'process_group_empty': True, 'recovered_status': 400,
                        'recovery_seconds': round(time.monotonic() - closed, 3)}
            if code != 429:
                raise SmokeFailure('Active-job recovery did not meet the status/timing checks.')
        time.sleep(0.05)
    raise SmokeFailure('Active-job cleanup was not observed within five seconds; no processes were manually killed.')


def deadline(signum, frame):
    raise SmokeFailure('Test exceeded its 120-second total deadline; no automatic retry.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--check-runtime', action='store_true',
                        help='Read Docker/cgroup limits and OOM counters before and after the one analysis.')
    modes.add_argument('--check-request-lifecycle', action='store_true',
                       help='Check busy rejection and incomplete-body disconnect cleanup; no analysis jobs.')
    modes.add_argument('--check-active-cancellation', action='store_true',
                       help='Observe process cleanup around disconnect of one small active analysis job.')
    args = parser.parse_args(argv)
    if not sys.platform.startswith('linux') or os.geteuid() != 0:
        print('STOP: Run with sudo python3 on the Oracle Ubuntu VM.')
        return 1
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(120)
    try:
        before = runtime_snapshot() if args.check_runtime or args.check_request_lifecycle or args.check_active_cancellation else None
        token = read_token()
        if args.check_active_cancellation:
            result = active_job_disconnect(token, before)
        else:
            result = request_lifecycle(token) if args.check_request_lifecycle else smoke(token)
        if before is not None:
            result.update(compare_runtime(before, runtime_snapshot()))
        print(json.dumps(result))
        return 0
    except SmokeFailure as error:
        print('STOP: ' + str(error))
    except Exception as error:
        # Print type only: raw exception messages can contain remote content.
        print('STOP: ' + type(error).__name__ + '; keep TLS verification enabled and report this output.')
    finally:
        signal.alarm(0)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
