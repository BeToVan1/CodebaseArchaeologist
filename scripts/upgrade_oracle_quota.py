"""Owner-run, pinned Oracle quota update. Default: read-only preflight.

Use --apply only for the approved backend update. No image pulls, package
installation, token rotation, Caddy/firewall changes, or Sites deployment.
"""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

OLD_IMAGE = 'sha256:320de275261a0a2e1255df7d971ee26322dbb3837e579521ff25eadc1f8efa07'
# Oracle identifies this export by its OCI index, not the nested config digest.
# The complete index -> platform manifest -> config chain is archive-verified.
NEW_IMAGE = 'sha256:b0fa42aa6579e01c8d823abe90d073328de9ffad5869bb0179c7361573478786'
SERVICE = 'codebase-archaeologist.service'
CONTAINER = 'codebase-archaeologist'
UNIT = Path('/etc/systemd/system') / SERVICE
CONFIG = Path('/etc/codebase-archaeologist')
BACKUP = CONFIG / 'pre-quota-v1.service'
TOKEN = CONFIG / 'service.env'
DATA = Path('/var/lib/archaeologist-quota')
LEDGER = DATA / 'quota.sqlite3'
BASE = 'https://codebase-archaeologist.duckdns.org'
ROUTE = '/api/analyze/quota-v1'
REPO = 'https://github.com/pallets/itsdangerous'
MOUNT = '--mount=type=bind,src=/var/lib/archaeologist-quota,dst=/var/lib/archaeologist-quota'
QUOTA_ENV = '--env=ARCHAEOLOGIST_QUOTA_PATH=/var/lib/archaeologist-quota/quota.sqlite3'
RUN_LINE = 'ExecStart=/usr/bin/docker run --rm --pull=never --name codebase-archaeologist --init --read-only --user=10001:10001 --cap-drop=ALL --security-opt=no-new-privileges --memory=384m --memory-swap=384m --cpus=1 --pids-limit=64 --tmpfs=/tmp:rw,noexec,nosuid,nodev,size=128m,mode=1777 --log-driver=json-file --log-opt=max-size=5m --log-opt=max-file=2 --publish=127.0.0.1:8000:8000 --env-file=/etc/codebase-archaeologist/service.env '
UNIT_TEMPLATE = '''[Unit]
Description=Codebase Archaeologist bounded analysis service
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target
StartLimitIntervalSec=120
StartLimitBurst=3

[Service]
Type=simple
{run}
ExecStop=/usr/bin/docker stop --time=10 codebase-archaeologist
Restart=on-failure
RestartSec=10
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
'''
OLD_UNIT = UNIT_TEMPLATE.format(run=RUN_LINE + OLD_IMAGE)
NEW_UNIT = UNIT_TEMPLATE.format(run=RUN_LINE + QUOTA_ENV + ' ' + MOUNT + ' ' + NEW_IMAGE)


class UpgradeError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise UpgradeError(message)


def command_label(args):
    # Fixed allowlisted labels only. Never echo arbitrary argv, stdout or stderr.
    if args[:2] == ('systemctl', 'show'):
        for flag, label in (('--property=FragmentPath', 'check service-unit location'),
                            ('--property=DropInPaths', 'check backend overrides'),
                            ('--property=NeedDaemonReload', 'check pending systemd changes')):
            if flag in args:
                return label
    if args and args[0] == 'systemctl' and len(args) > 1:
        return {'stop': 'stop backend', 'start': 'start backend',
                'restart': 'restart backend', 'daemon-reload': 'reload systemd'}.get(args[1], 'systemd command')
    if args[:3] == ('/usr/bin/docker', 'image', 'inspect'):
        return 'inspect previous pinned image' if args[-1] == OLD_IMAGE else 'inspect replacement pinned image'
    if args[:2] == ('/usr/bin/docker', 'inspect'):
        return 'inspect running backend'
    if args[:2] == ('/usr/bin/docker', 'run'):
        return 'initialize quota ledger'
    if args[:2] == ('/usr/bin/docker', 'exec'):
        return 'read quota ledger'
    return 'system command'


def run(*args, timeout=40):
    label = command_label(args)
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise UpgradeError(f'Could not {label}: timed out; private output withheld.') from None
    except OSError:
        raise UpgradeError(f'Could not {label}: command could not be started; private details withheld.') from None
    require(result.returncode == 0, f'Could not {label}: exit {result.returncode}; private output withheld.')
    require(len(result.stdout) <= 32768, 'Unexpectedly large command response.')
    return result.stdout.strip()


def root_path(path):
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            meta = part.lstat()
            require(not stat.S_ISLNK(meta.st_mode) and meta.st_uid == 0
                    and not meta.st_mode & 0o022, 'Unexpected path ownership, link, or write permissions.')


def read_root(path, limit=8192):
    root_path(path)
    with path.open('rb') as source:
        meta = os.fstat(source.fileno())
        require(stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1, 'Expected a root-owned regular file.')
        data = source.read(limit + 1)
    require(len(data) <= limit, 'Configuration file exceeds expected size.')
    return data.decode('ascii')


def read_token():
    require(stat.S_IMODE(CONFIG.stat().st_mode) == 0o700, 'Token directory must be 0700.')
    require(stat.S_IMODE(TOKEN.stat().st_mode) == 0o600, 'Token file must be 0600.')
    content = read_root(TOKEN, 256)
    match = re.fullmatch(r'ARCHAEOLOGIST_SERVICE_TOKEN=([a-f0-9]{64})\n', content)
    require(match is not None, 'Unexpected token file format; refusing to replace it.')
    return match[1]


def write_atomic(path, content):
    root_path(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.archaeologist-update-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='ascii', newline='\n') as target:
            os.fchmod(target.fileno(), 0o644)
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)  # Only this call's exact temporary file.


def backup_unit():
    if BACKUP.exists() or BACKUP.is_symlink():
        require(read_root(BACKUP) == OLD_UNIT, 'Existing backup differs; refusing to overwrite it.')
        return
    root_path(BACKUP)
    fd = os.open(BACKUP, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='ascii', newline='\n') as target:
        target.write(OLD_UNIT)
        target.flush()
        os.fsync(target.fileno())


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def request(base, path, token=None, payload=None, key=None, timeout=3):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if key:
        headers['X-Archaeologist-Client-Key'] = key
    req = urllib.request.Request(base + path, headers=headers,
        data=None if payload is None else json.dumps(payload).encode())
    try:
        with HTTP.open(req, timeout=timeout) as response:
            body = response.read(10 * 1024 * 1024 + 1)
            require(len(body) <= 10 * 1024 * 1024, 'Report exceeded the output limit.')
            return response.status, body
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        return code, b''


def wait_health():
    for _ in range(15):
        try:
            if request('http://127.0.0.1:8000', '/health')[0] == 200:
                return
        except (urllib.error.URLError, TimeoutError, ConnectionResetError):
            pass
        time.sleep(1)
    raise UpgradeError('Backend did not become healthy.')


def auth_checks(token, new=True):
    route = ROUTE if new else '/api/analyze'
    for base in ('http://127.0.0.1:8000', BASE):
        require(request(base, '/health')[0] == 200, 'Health check failed.')
        require(request(base, route, payload={})[0] == 401, 'Unauthorized request was not rejected.')
        require(request(base, route, token, {})[0] == 400, 'Service is busy or input validation differs.')


def inspect_image(image):
    # Select only non-secret metadata; never dump image/container environment.
    value = run('/usr/bin/docker', 'image', 'inspect', '--format',
        '{{.Id}} {{.Os}}/{{.Architecture}} {{.Config.User}}', image)
    require(value == image + ' linux/amd64 10001:10001', 'Expected pinned non-root amd64 image is not loaded.')


INSPECT = '''{"image":{{json .Image}},"running":{{json .State.Running}},
"oom":{{json .State.OOMKilled}},"memory":{{json .HostConfig.Memory}},
"swap":{{json .HostConfig.MemorySwap}},"cpus":{{json .HostConfig.NanoCpus}},
"pids":{{json .HostConfig.PidsLimit}},"readonly":{{json .HostConfig.ReadonlyRootfs}},
"user":{{json .Config.User}},"mounts":{{json .Mounts}},
"ports":{{json .HostConfig.PortBindings}},"caps":{{json .HostConfig.CapDrop}},
"security":{{json .HostConfig.SecurityOpt}},"restarts":{{json .RestartCount}}}'''


def runtime(new):
    info = json.loads(run('/usr/bin/docker', 'inspect', '--type=container', '--format', INSPECT, CONTAINER))
    expected = {'image': NEW_IMAGE if new else OLD_IMAGE, 'running': True, 'oom': False,
        'memory': 384 * 1024 * 1024, 'swap': 384 * 1024 * 1024, 'cpus': 1000000000,
        'pids': 64, 'readonly': True, 'user': '10001:10001', 'caps': ['ALL'], 'restarts': 0}
    require(all(info.get(k) == v for k, v in expected.items()), 'Runtime identity/state or limits differ.')
    require(any(item in ('no-new-privileges', 'no-new-privileges:true') for item in info.get('security', [])), 'Missing no-new-privileges protection.')
    require(info.get('ports') == {'8000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8000'}]}, 'Unexpected published ports.')
    mounts = info.get('mounts', [])
    # Docker versions may also expose the --tmpfs entry in Mounts.
    require(all(item.get('Destination') == '/tmp' for item in mounts if item.get('Type') == 'tmpfs'),
            'Unexpected tmpfs mount destination.')
    mounts = [item for item in mounts if item.get('Type') != 'tmpfs']
    require(len(mounts) == int(new), 'Unexpected runtime mount count.')
    if new:
        mount = mounts[0]
        require(mount.get('Type') == 'bind' and mount.get('Source') == str(DATA)
                and mount.get('Destination') == str(DATA) and mount.get('RW') is True,
                'Quota bind mount differs from the approved path.')


def preflight(rollback=False):
    require(sys.platform.startswith('linux') and os.geteuid() == 0, 'Run with sudo python3 on Oracle Ubuntu, not Windows.')
    token = read_token()
    current = read_root(UNIT)
    require(current in (OLD_UNIT, NEW_UNIT), 'Service unit differs from the approved versions; no changes made.')
    require(run('systemctl', 'show', SERVICE, '--property=FragmentPath', '--value') == str(UNIT), 'Unexpected systemd unit path.')
    require(not run('systemctl', 'show', SERVICE, '--property=DropInPaths', '--value'), 'Unreviewed backend overrides exist.')
    if not rollback:
        require(run('systemctl', 'show', SERVICE, '--property=NeedDaemonReload', '--value') == 'no', 'Pending systemd edits must be reviewed first.')
    inspect_image(OLD_IMAGE)
    if not rollback:
        inspect_image(NEW_IMAGE)
        runtime(current == NEW_UNIT)
        auth_checks(token, current == NEW_UNIT)
    return token, current


def initialize_ledger():
    root_path(DATA.parent)
    if not DATA.exists() and not DATA.is_symlink():
        DATA.mkdir(mode=0o700)
        os.chown(DATA, 10001, 10001)
    meta = DATA.lstat()
    require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == 10001 and meta.st_gid == 10001
            and stat.S_IMODE(meta.st_mode) == 0o700, 'Unexpected quota directory ownership or permissions.')
    run('/usr/bin/docker', 'run', '--rm', '--pull=never', '--network=none', '--read-only',
        '--user=10001:10001', '--cap-drop=ALL', '--security-opt=no-new-privileges',
        '--memory=64m', '--memory-swap=64m', '--cpus=1', '--pids-limit=16', MOUNT,
        NEW_IMAGE, 'python', '-m', 'deep_quota', 'init', str(LEDGER))


def ledger_fingerprint():
    # Inside the service, verify actual mount permissions/schema and hash rows.
    # No admissions, keys or tokens are printed or returned to the user.
    code = "from deep_quota import checked_path,connect,verify; import hashlib,json; from contextlib import closing; "
    code += "p=checked_path('/var/lib/archaeologist-quota/quota.sqlite3'); "
    code += "db=connect(p); verify(db); rows=db.execute('SELECT id,client_key,created_at FROM deep_admissions ORDER BY id').fetchall(); db.close(); print(hashlib.sha256(json.dumps(rows).encode()).hexdigest())"
    value = run('/usr/bin/docker', 'exec', CONTAINER, 'python', '-c', code)
    require(re.fullmatch(r'[a-f0-9]{64}', value) is not None, 'Could not verify the quota ledger.')
    return value


def real_analysis(token):
    key = hmac.new(token.encode(), b'oracle-quota-deployment-validation', hashlib.sha256).hexdigest()
    code, body = request(BASE, ROUTE, token, {'repositoryUrl': REPO}, key, timeout=75)
    require(code == 200, 'The one HTTPS analysis failed; no automatic analysis retry.')
    graph = json.loads(body)
    require(graph.get('schema_version') == '1.1' and graph.get('analysis', {}).get('tier') == 'deep'
        and graph.get('repository', {}).get('url') == REPO
        and re.fullmatch(r'[a-f0-9]{40}', graph.get('snapshot', {}).get('commit_sha', '')) is not None
        and bool(graph.get('nodes')) and bool(graph.get('edges')), 'HTTPS analysis report identity or content differs.')


def restore(token):
    require(read_root(BACKUP) == OLD_UNIT, 'Rollback backup differs; refusing restore.')
    require(read_root(UNIT) in (OLD_UNIT, NEW_UNIT), 'Service changed since update; refusing overwrite.')
    run('systemctl', 'stop', SERVICE)
    write_atomic(UNIT, OLD_UNIT)
    run('systemctl', 'daemon-reload')
    run('systemctl', 'start', SERVICE)
    wait_health()
    runtime(False)
    auth_checks(token, False)
    print('ROLLBACK PASS: previous backend restored; quota ledger preserved; public website unchanged.', flush=True)


def apply():
    token, current = preflight()
    require(current == OLD_UNIT, 'Quota backend already installed; use --verify instead of repeating analysis.')
    backup_unit()
    initialize_ledger()  # Before downtime; never reset existing state.
    # Recheck just before replacing any live configuration.
    require(read_root(UNIT) == OLD_UNIT and read_token() == token, 'Configuration changed during preflight.')
    print('Preflight and backup complete. Updating only the backend service.', flush=True)
    try:
        run('systemctl', 'stop', SERVICE)
        write_atomic(UNIT, NEW_UNIT)
        run('systemctl', 'daemon-reload')
        run('systemctl', 'start', SERVICE)
        wait_health()
        runtime(True)
        auth_checks(token)
        before = ledger_fingerprint()
        real_analysis(token)
        after = ledger_fingerprint()
        require(before != after, 'Analysis did not record a persistent admission.')
        runtime(True)
        print('HTTPS analysis passed. Checking the real quota mount across one service restart.', flush=True)
        run('systemctl', 'restart', SERVICE)
        wait_health()
        runtime(True)
        auth_checks(token)
        require(ledger_fingerprint() == after, 'Quota ledger changed across restart.')
        require(read_token() == token, 'Token changed during the update.')
    except BaseException:
        print('Update verification failed; attempting to restore the previous backend.', flush=True)
        try:
            restore(token)
        except BaseException:
            raise UpgradeError('Rollback could not be verified. Keep SSH open; run --rollback after reviewing the error. Do not publish the website.') from None
        raise UpgradeError('Update did not complete; the previous backend was restored.') from None
    print('PASS: quota backend installed; verified HTTPS analysis and quota persistence across service restart. Public website unchanged.')
    print('Backup: /etc/codebase-archaeologist/pre-quota-v1.service. Keep it and the old image for rollback.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--apply', action='store_true')
    modes.add_argument('--rollback', action='store_true')
    modes.add_argument('--verify', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.apply:
            apply()
        elif args.rollback:
            token, _ = preflight(rollback=True)
            restore(token)
        else:
            _, current = preflight()
            if args.verify:
                require(current == NEW_UNIT, 'Quota backend is not installed.')
                ledger_fingerprint()
            print('PASS: read-only backend checks complete. No changes or analysis jobs submitted.')
        return 0
    except (Exception, KeyboardInterrupt) as error:
        print('STOP: ' + (str(error) if isinstance(error, UpgradeError) else type(error).__name__ + '; no private error details displayed.'))
        return 1


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    if sys.platform.startswith('linux'):
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGHUP, interrupted)
    raise SystemExit(main())
