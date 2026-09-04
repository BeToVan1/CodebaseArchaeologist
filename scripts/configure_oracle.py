"""Owner-run Oracle Micro setup. Stdlib only; never prints the service token.

Run with sudo on the tested Ubuntu VM. No package installation, remote commands,
image pulls, Sites changes, firewall flushing, or existing-file overwrites.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request

CONFIG = Path('/etc/codebase-archaeologist')
TOKEN_PATH = CONFIG / 'service.env'
QUOTA_DIRECTORY = Path('/var/lib/archaeologist-quota')
QUOTA_PATH = QUOTA_DIRECTORY / 'quota.sqlite3'
CADDY_PATH = Path('/etc/caddy/archaeologist.Caddyfile')
UNIT_ROOT = Path('/etc/systemd/system')
DROPIN = UNIT_ROOT / 'caddy.service.d/archaeologist.conf'
SERVICE = 'codebase-archaeologist.service'
FIREWALL_SERVICE = 'archaeologist-web-firewall.service'
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the service credential to a redirect target.
        return None


HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def run(*args, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=90)
    if check and result.returncode:
        # Do not dump arbitrary command output: installed services can contain secrets.
        raise RuntimeError(f'{args[0]} {args[1]} failed (exit {result.returncode}).')
    return result


def validate_inputs(hostname, expected_ip, image):
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.duckdns\.org', hostname):
        raise ValueError('Use a single lowercase DuckDNS hostname, without https:// or a path.')
    address = ipaddress.IPv4Address(expected_ip)
    if not address.is_global:
        raise ValueError('Expected IP must be a public IPv4 address.')
    if not re.fullmatch(r'codebase-archaeologist-deep:oracle-[a-f0-9]{32}', image):
        raise ValueError('Use the runtime image tag from the validated Oracle bundle.')


def validate_image(image):
    info = json.loads(run('/usr/bin/docker', 'image', 'inspect', image).stdout)[0]
    if (info.get('Os') != 'linux' or info.get('Architecture') != 'amd64'
            or info.get('Config', {}).get('User') != '10001:10001'
            or not re.fullmatch(r'sha256:[a-f0-9]{64}', info.get('Id', ''))):
        raise ValueError('Expected a Linux amd64 runtime image with non-root user 10001:10001.')
    expected_cmd = ['uvicorn', 'deep_service:create_app', '--factory', '--host', '0.0.0.0',
                    '--port', '8000', '--workers', '1', '--no-access-log']
    if info['Config'].get('Cmd') != expected_cmd or info['Config'].get('Entrypoint'):
        raise ValueError('Image command differs from the tested one-worker service.')
    if info['Config'].get('Volumes'):
        raise ValueError('Unexpected image volumes.')
    return info['Id']


def check_path(path):
    for item in (path, *path.parents):
        if item.is_symlink():
            raise RuntimeError(f'Refusing symlink: {item}')
        if item.exists() and sys.platform.startswith('linux'):
            metadata = item.stat()
            if metadata.st_uid != 0 or metadata.st_mode & 0o022:
                raise RuntimeError(f'Expected root-owned path without group/other write access: {item}')


def check_existing(path, content):
    check_path(path)
    if path.exists() and (not path.is_file() or path.read_text() != content):
        raise RuntimeError(f'Existing configuration differs; not overwriting: {path}')


def write_new_or_identical(path, content, mode=0o644):
    check_existing(path, content)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    check_path(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), mode)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as output:
        output.write(content)


def load_or_create_token():
    check_path(CONFIG)
    CONFIG.mkdir(mode=0o700, exist_ok=True)
    if sys.platform.startswith('linux') and stat.S_IMODE(CONFIG.stat().st_mode) != 0o700:
        raise RuntimeError('Service configuration directory must have permissions 0700.')
    check_path(TOKEN_PATH)
    if not TOKEN_PATH.exists():
        write_new_or_identical(TOKEN_PATH, 'ARCHAEOLOGIST_SERVICE_TOKEN=' + secrets.token_hex(32) + '\n', 0o600)
    if sys.platform.startswith('linux') and stat.S_IMODE(TOKEN_PATH.stat().st_mode) != 0o600:
        raise RuntimeError('Service token file must have permissions 0600.')
    match = re.fullmatch(r'ARCHAEOLOGIST_SERVICE_TOKEN=([a-f0-9]{64})\n', TOKEN_PATH.read_text())
    if not match:
        raise RuntimeError('Existing token file is invalid; refusing to replace it.')
    return match[1]


def prepare_quota_directory():
    # Dedicated state only; never chown an existing tree or follow symlinks.
    check_path(QUOTA_DIRECTORY.parent)
    if not QUOTA_DIRECTORY.exists() and not QUOTA_DIRECTORY.is_symlink():
        QUOTA_DIRECTORY.mkdir(mode=0o700)
        os.chown(QUOTA_DIRECTORY, 10001, 10001)
    metadata = QUOTA_DIRECTORY.lstat()
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 10001
            or metadata.st_gid != 10001 or stat.S_IMODE(metadata.st_mode) != 0o700):
        raise RuntimeError('Quota directory must be a private directory owned by 10001:10001; not changing existing state.')


def initialize_quota(image_id):
    # Owner-run setup only, never on service restart or at request time.
    prepare_quota_directory()
    run('/usr/bin/docker', 'run', '--rm', '--pull=never', '--network=none',
        '--read-only', '--user=10001:10001', '--cap-drop=ALL',
        '--security-opt=no-new-privileges', '--memory=64m', '--memory-swap=64m',
        '--cpus=1', '--pids-limit=16',
        '--mount=type=bind,src=/var/lib/archaeologist-quota,dst=/var/lib/archaeologist-quota',
        image_id, 'python', '-m', 'deep_quota', 'init', str(QUOTA_PATH))


def render_files(hostname, image_id):
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', image_id):
        raise ValueError('Invalid immutable Docker image ID.')
    firewall = '''import subprocess
rule = ['INPUT', '-p', 'tcp', '-m', 'multiport', '--dports', '80,443', '-j', 'ACCEPT']
result = subprocess.run(['/usr/sbin/iptables', '-w', '5', '-C', *rule])
if result.returncode == 1:
    subprocess.run(['/usr/sbin/iptables', '-w', '5', '-I', 'INPUT', '1', *rule[1:]], check=True)
elif result.returncode != 0:
    raise SystemExit(result.returncode)
'''
    return {
        CONFIG / 'ensure_web_firewall.py': firewall,
        UNIT_ROOT / FIREWALL_SERVICE: '''[Unit]
Description=Allow Archaeologist HTTP and HTTPS without replacing Oracle firewall rules
After=netfilter-persistent.service
Before=caddy.service

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /etc/codebase-archaeologist/ensure_web_firewall.py
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
''',
        UNIT_ROOT / SERVICE: f'''[Unit]
Description=Codebase Archaeologist bounded analysis service
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target
StartLimitIntervalSec=120
StartLimitBurst=3

[Service]
Type=simple
ExecStart=/usr/bin/docker run --rm --pull=never --name codebase-archaeologist --init --read-only --user=10001:10001 --cap-drop=ALL --security-opt=no-new-privileges --memory=384m --memory-swap=384m --cpus=1 --pids-limit=64 --tmpfs=/tmp:rw,noexec,nosuid,nodev,size=128m,mode=1777 --log-driver=json-file --log-opt=max-size=5m --log-opt=max-file=2 --publish=127.0.0.1:8000:8000 --env-file=/etc/codebase-archaeologist/service.env --env=ARCHAEOLOGIST_QUOTA_PATH=/var/lib/archaeologist-quota/quota.sqlite3 --mount=type=bind,src=/var/lib/archaeologist-quota,dst=/var/lib/archaeologist-quota {image_id}
ExecStop=/usr/bin/docker stop --time=10 codebase-archaeologist
Restart=on-failure
RestartSec=10
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
''',
        CADDY_PATH: f'''# Managed by configure_oracle.py; no credentials in this file.
{hostname} {{
    bind 0.0.0.0
    reverse_proxy 127.0.0.1:8000
}}
''',
        DROPIN: '''[Unit]
Requires=archaeologist-web-firewall.service
After=archaeologist-web-firewall.service codebase-archaeologist.service
StartLimitIntervalSec=120
StartLimitBurst=3

[Service]
ExecStart=
ExecStart=/usr/bin/caddy run --config /etc/caddy/archaeologist.Caddyfile --adapter caddyfile
ExecReload=
ExecReload=/usr/bin/caddy reload --config /etc/caddy/archaeologist.Caddyfile --adapter caddyfile --force
Environment=GOMEMLIMIT=48MiB
MemoryHigh=48M
MemoryMax=64M
MemorySwapMax=0
Restart=on-failure
RestartSec=10
''',
    }


def request_status(base, path, payload=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = urllib.request.Request(base + path, headers=headers,
        data=None if payload is None else json.dumps(payload).encode())
    try:
        with HTTP.open(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def wait_health(base, attempts):
    for _ in range(attempts):
        try:
            if request_status(base, '/health') == 200:
                return
        except (urllib.error.URLError, TimeoutError, ConnectionResetError):
            pass
        time.sleep(2)
    raise RuntimeError('Health check did not succeed. Check the service logs; do not disable TLS verification.')


def verify_auth(base, token):
    payload = {'repositoryUrl': 'https://example.com/a/b'}
    if request_status(base, '/api/analyze', payload) != 401:
        raise RuntimeError('Unauthenticated requests were not rejected; stop rollout.')
    if request_status(base, '/api/analyze', payload, token) != 400:
        raise RuntimeError('Authenticated invalid input did not return HTTP 400; stop rollout.')


def configure(args):
    if not sys.platform.startswith('linux') or os.geteuid() != 0:
        raise RuntimeError('Run with sudo python3 on the Oracle Ubuntu VM, not on Windows.')
    validate_inputs(args.hostname, args.expected_ip, args.image)
    os.umask(0o022)
    for executable in ('/usr/bin/docker', '/usr/bin/caddy', '/usr/bin/python3', '/usr/sbin/iptables'):
        if not os.access(executable, os.X_OK):
            raise RuntimeError(f'Missing prerequisite: {executable}')
    addresses = {item[4][0] for item in socket.getaddrinfo(args.hostname, 443, socket.AF_INET)}
    if addresses != {args.expected_ip}:
        raise RuntimeError('DNS does not point exclusively at the expected Oracle IPv4 address.')
    try:
        ipv6 = socket.getaddrinfo(args.hostname, 443, socket.AF_INET6)
    except socket.gaierror as error:
        if error.errno not in (socket.EAI_NONAME, getattr(socket, 'EAI_NODATA', socket.EAI_NONAME)):
            raise RuntimeError('Could not verify IPv6 DNS; retry after DNS is working.') from error
        ipv6 = []
    if ipv6:
        raise RuntimeError('Remove the DuckDNS IPv6 record before this IPv4-only setup.')
    image_id = validate_image(args.image)
    files = render_files(args.hostname, image_id)
    for path, content in files.items():
        check_existing(path, content)
    dropins = run('systemctl', 'show', 'caddy', '--property=DropInPaths', '--value').stdout.split()
    if any(item != str(DROPIN) for item in dropins):
        raise RuntimeError('Unrecognized Caddy overrides; review them before setup.')
    if not DROPIN.exists() and run('systemctl', 'is-active', '--quiet', 'caddy', check=False).returncode == 0:
        raise RuntimeError('Caddy is already serving another configuration; stop and review it first.')
    # Do not take over any pre-existing listener/container on first setup.
    if not (UNIT_ROOT / SERVICE).exists():
        container = run('/usr/bin/docker', 'ps', '-aq', '--filter', 'name=^/codebase-archaeologist$').stdout.strip()
        if container:
            raise RuntimeError('A container with the service name already exists; refusing to replace it.')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 8000))
    for path, content in files.items():
        if path.parent == CONFIG:
            CONFIG.mkdir(mode=0o700, exist_ok=True)
        write_new_or_identical(path, content)
    token = load_or_create_token()
    initialize_quota(image_id)
    run('runuser', '-u', 'caddy', '--', '/usr/bin/caddy', 'validate', '--config', str(CADDY_PATH), '--adapter', 'caddyfile')
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', SERVICE)
    wait_health('http://127.0.0.1:8000', 15)
    verify_auth('http://127.0.0.1:8000', token)
    print('PASS: loopback health and authorization; token stays in a root-only file.', flush=True)
    run('systemctl', 'enable', '--now', FIREWALL_SERVICE)
    run('systemctl', 'enable', '--now', 'caddy')
    print('Waiting for HTTPS certificate and health check (up to about 2 minutes).', flush=True)
    wait_health('https://' + args.hostname, 18)
    try:
        verify_auth('https://' + args.hostname, token)
    except Exception:
        run('systemctl', 'stop', 'caddy', check=False)
        raise
    print('PASS: verified HTTPS, health, unauthorized rejection and authenticated input validation.')
    print('Backend configured. Public website integration and end-to-end repository analysis over HTTPS are still pending.')
    print('If needed, stop the backend with: sudo systemctl stop caddy codebase-archaeologist')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hostname', required=True)
    parser.add_argument('--expected-ip', required=True)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    try:
        configure(args)
    except Exception as error:
        print(f'STOP: {error}', file=sys.stderr)
        print('Configuration may be partially applied. Keep SSH open; report this error before further changes.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
