"""Offline tests; importing the live smoke helper never sends a request."""
import contextlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request

import test_oracle_https as smoke


def report():
    sha = 'a' * 40
    return {'schema_version': '1.1', 'analysis': {'tier': 'deep'},
            'repository': {'name': smoke.REPOSITORY, 'url': smoke.REPO_URL,
                           'pinned_url': smoke.REPO_URL + '/tree/' + sha},
            'snapshot': {'commit_sha': sha}, 'nodes': [{'id': 'a'}, {'id': 'b'}],
            'edges': [{'source': 'a', 'target': 'b'}]}


def runtime():
    return {'id': 'b' * 64, 'image': smoke.EXPECTED_IMAGE, 'pid': 123,
            'running': True, 'oom': False, 'started': '2026-09-03T00:00:00Z',
            'restarts': 0, 'memory': smoke.MEMORY_LIMIT, 'swap': smoke.MEMORY_LIMIT,
            'cpus': 1_000_000_000, 'pids': 64, 'readonly': True,
            'user': '10001:10001', 'caps': ['ALL'], 'security': ['no-new-privileges'],
            'current_bytes': 32 * 1024**2, 'peak_bytes': 64 * 1024**2}


class RuntimeChecks(unittest.TestCase):
    def test_approved_runtime(self):
        smoke.validate_runtime(runtime())
        smoke.validate_runtime({**runtime(), 'security': ['no-new-privileges:true']})

    def test_changed_controls_fail_closed(self):
        for key, value in [('image', 'different'), ('running', False), ('oom', True),
                           ('memory', 0), ('swap', -1), ('cpus', 0), ('pids', -1),
                           ('readonly', False), ('user', 'root'), ('caps', []),
                           ('security', []), ('pid', 0), ('id', '../other'), ('restarts', -1)]:
            with self.subTest(key=key), self.assertRaises(smoke.SmokeFailure):
                smoke.validate_runtime({**runtime(), key: value})

    def snapshot(self, **changes):
        contents = {'memory.max': str(smoke.MEMORY_LIMIT),
                    'memory.current': str(32 * 1024**2), 'memory.peak': str(64 * 1024**2),
                    'memory.events': 'low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n'}
        contents.update(changes)
        with patch.object(smoke.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(runtime()))) as run, \
             patch.object(smoke, 'cgroup_directory', return_value=Path('/fake-cgroup')), \
             patch.object(Path, 'read_text', autospec=True, side_effect=lambda path: contents[path.name]):
            result = smoke.runtime_snapshot()
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ['/usr/bin/docker', 'inspect', '--type=container'])
        self.assertEqual(command[-1], 'codebase-archaeologist')
        self.assertNotIn('.Config.Env', command[4])
        self.assertFalse(run.call_args.kwargs.get('shell', False))
        self.assertEqual(run.call_args.kwargs['timeout'], 10)
        return result

    def test_snapshot_only_reads_selected_fields(self):
        self.assertEqual(self.snapshot()['current_bytes'], 32 * 1024**2)

    def test_oom_or_memory_drift_stops_check(self):
        for change in [{'memory.max': '0'}, {'memory.peak': str(smoke.MEMORY_LIMIT + 1)},
                       {'memory.events': 'oom 1\noom_kill 0\n'},
                       {'memory.events': 'oom 0\noom_kill 1\n'}]:
            with self.subTest(change=change), self.assertRaises(smoke.SmokeFailure):
                self.snapshot(**change)

    def test_missing_container_does_not_launch_one_or_echo_stderr(self):
        with patch.object(smoke.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='', stderr='private-value')) as run:
            with self.assertRaises(smoke.SmokeFailure) as caught:
                smoke.runtime_snapshot()
        self.assertNotIn('private-value', str(caught.exception))
        self.assertEqual(run.call_count, 1)

    def test_cgroup_path_must_stay_beneath_controller(self):
        for membership in ['0::/../../etc', '0::/', '2:memory:/container', '0::relative']:
            with self.subTest(membership=membership), patch.object(Path, 'read_text', return_value=membership), \
                 patch.object(Path, 'resolve', autospec=True, side_effect=lambda path, **_: Path(os.path.abspath(path))), \
                 self.assertRaises(smoke.SmokeFailure):
                smoke.cgroup_directory(123)

    def test_restart_or_replacement_fails(self):
        for key, value in [('id', 'c' * 64), ('image', 'different'), ('pid', 124),
                           ('started', 'later'), ('restarts', 1)]:
            with self.subTest(key=key), self.assertRaises(smoke.SmokeFailure):
                smoke.compare_runtime(runtime(), {**runtime(), key: value})

    def test_summary_is_small_and_has_no_identity_or_secret(self):
        result = smoke.compare_runtime(runtime(), runtime())
        self.assertEqual(result['memory_limit_mib'], 384)
        self.assertEqual(result['container_lifetime_peak_mib'], 64)
        for key in ['id', 'image', 'pid', 'token', 'security']:
            self.assertNotIn(key, result)

    def test_main_runs_one_smoke_between_two_snapshots(self):
        with patch.object(smoke.sys, 'platform', 'linux'), \
             patch.object(smoke.os, 'geteuid', return_value=0, create=True), \
             patch.object(smoke.signal, 'SIGALRM', 14, create=True), \
             patch.object(smoke.signal, 'signal'), patch.object(smoke.signal, 'alarm', create=True), \
             patch.object(smoke, 'read_token', return_value='private-test-value'), \
             patch.object(smoke, 'runtime_snapshot', side_effect=[runtime(), runtime()]) as snapshot, \
             patch.object(smoke, 'smoke', return_value={'result': 'PASS'}) as run, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(smoke.main(['--check-runtime']), 0)
        self.assertEqual(snapshot.call_count, 2)
        run.assert_called_once_with('private-test-value')
        self.assertNotIn('private-test-value', output.getvalue())

    def test_preflight_failure_never_reads_token_or_starts_analysis(self):
        with patch.object(smoke.sys, 'platform', 'linux'), \
             patch.object(smoke.os, 'geteuid', return_value=0, create=True), \
             patch.object(smoke.signal, 'SIGALRM', 14, create=True), \
             patch.object(smoke.signal, 'signal'), patch.object(smoke.signal, 'alarm', create=True), \
             patch.object(smoke, 'read_token') as token, patch.object(smoke, 'smoke') as run, \
             patch.object(smoke, 'runtime_snapshot', side_effect=smoke.SmokeFailure('preflight failed')), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(smoke.main(['--check-runtime']), 1)
        token.assert_not_called()
        run.assert_not_called()


class HttpsSmokeTests(unittest.TestCase):
    def test_valid_report(self):
        result = smoke.validate_report(json.dumps(report()))
        self.assertEqual((result['nodes'], result['edges']), (2, 1))

    def test_invalid_reports(self):
        variants = [None, {}, {**report(), 'schema_version': '0'},
                    {**report(), 'analysis': {'tier': 'inventory'}},
                    {**report(), 'repository': {'name': 'wrong'}},
                    {**report(), 'snapshot': {'commit_sha': 'invalid'}},
                    {**report(), 'nodes': []},
                    {**report(), 'nodes': [{'id': 'a'}, {'id': 'a'}]},
                    {**report(), 'edges': [{'source': 'a', 'target': 'missing'}]}]
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(smoke.SmokeFailure):
                smoke.validate_report(json.dumps(variant))

    def test_token_strict_and_no_secret_in_error(self):
        self.assertEqual(smoke.parse_token(b'ARCHAEOLOGIST_SERVICE_TOKEN=' + b'a' * 64 + b'\n'), 'a' * 64)
        for value in (b'secret', b'ARCHAEOLOGIST_SERVICE_TOKEN=short\n', b'KEY=x\n'):
            with self.assertRaises(smoke.SmokeFailure) as caught:
                smoke.parse_token(value)
            self.assertNotIn(value.decode().strip(), str(caught.exception))

    def test_redirects_disabled(self):
        req = urllib.request.Request(smoke.BASE, headers={'Authorization': 'Bearer secret'})
        self.assertIsNone(smoke.NoRedirect().redirect_request(req, None, 302, '', {}, 'https://example.com'))
        self.assertTrue(any(isinstance(handler, smoke.NoRedirect) for handler in smoke.HTTP.handlers))

    def test_success_one_analysis_no_secret_output(self):
        responses = [(200, b'{"status":"ok"}'), (401, b''),
                     (200, json.dumps(report()).encode()), (200, b'{"status":"ok"}')]
        with patch.object(smoke, 'request', side_effect=responses) as request, contextlib.redirect_stdout(io.StringIO()) as output:
            result = smoke.smoke('secret-test-token')
        self.assertEqual(result['result'], 'PASS')
        self.assertEqual(request.call_count, 4)
        self.assertEqual(request.call_args_list[2].args[2], 'secret-test-token')
        self.assertNotIn('secret-test-token', output.getvalue() + json.dumps(result))

    def test_analysis_failure_not_retried(self):
        responses = [(200, b'{"status":"ok"}'), (401, b''), (429, b'')]
        with patch.object(smoke, 'request', side_effect=responses) as request, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(smoke.SmokeFailure, 'HTTP 429'):
                smoke.smoke('secret')
            self.assertEqual(request.call_count, 3)

    def test_unauthorized_failure_stops_before_token_sent(self):
        with patch.object(smoke, 'request', side_effect=[(200, b'{"status":"ok"}'), (200, b'')]) as request:
            with self.assertRaises(smoke.SmokeFailure):
                smoke.smoke('secret')
            self.assertEqual(request.call_count, 2)

    def test_request_bounds_size_and_uses_verified_hostname(self):
        with patch.object(smoke.HTTP, 'open') as opened:
            response = opened.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = b'{}'
            self.assertEqual(smoke.request('/api/analyze', {'repositoryUrl': smoke.REPO_URL}, 'secret'), (200, b'{}'))
            req = opened.call_args.args[0]
            self.assertEqual(req.full_url, smoke.BASE + '/api/analyze')
            self.assertEqual(opened.call_args.kwargs['timeout'], 75)
            response.read.assert_called_once_with(smoke.MAX_BYTES + 1)
            response.read.return_value = b'x' * (smoke.MAX_BYTES + 1)
            with self.assertRaises(smoke.SmokeFailure):
                smoke.request('/health')


class RequestLifecycleTests(unittest.TestCase):
    def run_check(self, codes, advance_on_recovery=0):
        clock = [0.0]
        calls = []
        def request(path, payload=None, token=None, timeout=75):
            calls.append((path, payload, token, timeout))
            if len(calls) >= 6:
                clock[0] += advance_on_recovery
            return next(codes), b'{ "status": "ok" }'
        def sleep(seconds):
            clock[0] += seconds
        with patch.object(smoke, 'request', side_effect=request), \
             patch.object(smoke.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(smoke.time, 'sleep', side_effect=sleep), \
             patch.object(smoke.http.client, 'HTTPSConnection') as factory:
            self.connection = factory.return_value
            try:
                result = smoke.request_lifecycle('private-test-token')
            finally:
                self.calls = calls
            self.assertEqual(factory.call_args.args, ('codebase-archaeologist.duckdns.org',))
            context = factory.call_args.kwargs['context']
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, smoke.ssl.CERT_REQUIRED)
            self.connection.endheaders.assert_called_once_with(b'{')
            self.connection.putheader.assert_any_call('Content-Length', '100')
            self.connection.close.assert_called_once()
            return result

    def test_busy_then_disconnect_recovers_without_analysis(self):
        result = self.run_check(iter([200, 401, 400, 429, 200, 429, 400]))
        self.assertEqual(result['result'], 'PASS')
        self.assertEqual(result['analysis_jobs_submitted'], 0)
        self.assertIn('not active-job', result['scope'])
        self.assertNotIn('private-test-token', json.dumps(result))
        for path, payload, _, _ in self.calls:
            if path == '/api/analyze':
                self.assertEqual(payload, {})

    def test_preexisting_busy_stops_before_opening_held_request(self):
        with self.assertRaises(smoke.SmokeFailure):
            self.run_check(iter([200, 401, 429]))
        self.connection.connect.assert_not_called()

    def test_buffered_or_unobserved_hold_is_inconclusive(self):
        with self.assertRaisesRegex(smoke.SmokeFailure, 'INCONCLUSIVE'):
            self.run_check(iter([200, 401, 400, 400]))
        self.connection.close.assert_called_once()

    def test_health_remains_required_while_busy(self):
        with self.assertRaisesRegex(smoke.SmokeFailure, 'Health endpoint'):
            self.run_check(iter([200, 401, 400, 429, 503]))
        self.connection.close.assert_called_once()

    def test_recovery_probes_are_bounded(self):
        with self.assertRaisesRegex(smoke.SmokeFailure, 'INCONCLUSIVE'):
            self.run_check(iter([200, 401, 400, 429, 200] + [429] * 6))
        self.assertEqual(len(self.calls), 11)
        self.connection.close.assert_called_once()

    def test_natural_body_timeout_cannot_count_as_disconnect_success(self):
        with self.assertRaisesRegex(smoke.SmokeFailure, 'INCONCLUSIVE'):
            self.run_check(iter([200, 401, 400, 429, 200, 400]), advance_on_recovery=5)
        self.connection.close.assert_called_once()

    def test_unexpected_recovery_response_is_not_success(self):
        with self.assertRaisesRegex(smoke.SmokeFailure, 'HTTP 502'):
            self.run_check(iter([200, 401, 400, 429, 200, 502]))
        self.connection.close.assert_called_once()

    def test_connection_error_still_closes_held_socket(self):
        with patch.object(smoke, 'request', side_effect=[(200, b'{"status":"ok"}'), (401, b''), (400, b'')]), \
             patch.object(smoke.http.client, 'HTTPSConnection') as factory:
            factory.return_value.endheaders.side_effect = OSError('private-test-token')
            with self.assertRaises(OSError):
                smoke.request_lifecycle('private-test-token')
            factory.return_value.close.assert_called_once()

    def test_main_lifecycle_mode_never_runs_repository_smoke(self):
        with patch.object(smoke.sys, 'platform', 'linux'), \
             patch.object(smoke.os, 'geteuid', return_value=0, create=True), \
             patch.object(smoke.signal, 'SIGALRM', 14, create=True), \
             patch.object(smoke.signal, 'signal'), patch.object(smoke.signal, 'alarm', create=True), \
             patch.object(smoke, 'read_token', return_value='private-test-token'), \
             patch.object(smoke, 'runtime_snapshot', side_effect=[runtime(), runtime()]), \
             patch.object(smoke, 'request_lifecycle', return_value={'result': 'PASS'}) as lifecycle, \
             patch.object(smoke, 'smoke') as repository_smoke, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(smoke.main(['--check-request-lifecycle']), 0)
        lifecycle.assert_called_once_with('private-test-token')
        repository_smoke.assert_not_called()
        self.assertNotIn('private-test-token', output.getvalue())


def process(pid, parent=10, group=None, start=100, state='S', name='python'):
    return {'pid': pid, 'ppid': parent, 'group': pid if group is None else group,
            'session': pid if group is None else group, 'start': start,
            'state': state, 'name': name}


class ActiveCancellationTests(unittest.TestCase):
    def test_process_inspection_rejects_invalid_ids(self):
        for pid in ('../etc', 0, -1, True):
            with self.subTest(pid=pid), self.assertRaises(smoke.SmokeFailure):
                smoke.read_process(pid)
        with patch.object(Path, 'read_text', side_effect=FileNotFoundError):
            self.assertIsNone(smoke.read_process(123))

    def test_main_active_mode_uses_snapshot_and_not_regular_smoke(self):
        with patch.object(smoke.sys, 'platform', 'linux'), \
             patch.object(smoke.os, 'geteuid', return_value=0, create=True), \
             patch.object(smoke.signal, 'SIGALRM', 14, create=True), \
             patch.object(smoke.signal, 'signal'), patch.object(smoke.signal, 'alarm', create=True), \
             patch.object(smoke, 'read_token', return_value='private-test-token'), \
             patch.object(smoke, 'runtime_snapshot', side_effect=[runtime(), runtime()]), \
             patch.object(smoke, 'active_job_disconnect', return_value={'result': 'PASS'}) as active, \
             patch.object(smoke, 'smoke') as regular, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(smoke.main(['--check-active-cancellation']), 0)
        active.assert_called_once_with('private-test-token', runtime())
        regular.assert_not_called()
        self.assertNotIn('private-test-token', output.getvalue())

    def test_proc_stat_fields_and_parenthesized_name(self):
        fields = ['S', '10', '20', '20'] + ['0'] * 15 + ['12345']
        info = smoke.parse_process_stat('20 (python) ' + ' '.join(fields))
        self.assertEqual(info, process(20, start=12345))
        self.assertEqual(smoke.parse_process_stat('20 (name ) with spaces) ' + ' '.join(fields))['name'], 'name ) with spaces')

    def test_only_new_isolated_python_child_is_selected(self):
        baseline = {10: process(10, name='uvicorn')}
        self.assertEqual(smoke.isolated_jobs({20: process(20)}, baseline), [process(20)])
        for candidate in [process(10), process(20, parent=99), process(20, group=10),
                          process(20, state='Z'), process(20, name='git')]:
            self.assertEqual(smoke.isolated_jobs({candidate['pid']: candidate}, baseline), [])

    def scenario(self, listings=None, codes=None, remaining=None):
        base = {10: process(10, name='uvicorn')}
        running = {**base, 20: process(20), 21: process(21, parent=20, group=20, name='git')}
        clock = [0.0]
        if listings is None:
            listings = [base, running, running, base]
        if codes is None:
            codes = [200, 401, 400, 429, 400, 200]
        def sleep(seconds):
            clock[0] += seconds
        with patch.object(smoke, 'container_processes', side_effect=listings) as processes, \
             patch.object(smoke, 'read_process', return_value=remaining), \
             patch.object(smoke, 'request', side_effect=[(code, b'') for code in codes]) as request, \
             patch.object(smoke.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(smoke.time, 'sleep', side_effect=sleep), \
             patch.object(smoke.http.client, 'HTTPSConnection') as connection:
            self.connection = connection.return_value
            self.requests = request
            self.processes = processes
            return smoke.active_job_disconnect('private-test-token', {'pid': 10})

    def test_active_processes_exit_and_slot_recovers(self):
        result = self.scenario()
        self.assertEqual(result['observed_processes_reaped'], 2)
        self.assertEqual(result['analysis_jobs_submitted'], 1)
        self.assertTrue(result['process_group_empty'])
        self.assertIn('normal completion can race', result['scope'])
        self.connection.close.assert_called_once()
        self.connection.request.assert_called_once()
        args = self.connection.request.call_args.args
        self.assertEqual(args[:2], ('POST', '/api/analyze'))
        self.assertEqual(json.loads(args[2]), {'repositoryUrl': smoke.REPO_URL})
        self.assertNotIn('private-test-token', json.dumps(result))

    def test_busy_preflight_does_not_launch_job(self):
        with self.assertRaisesRegex(smoke.SmokeFailure, 'not idle'):
            self.scenario(codes=[200, 401, 429])
        self.connection.request.assert_not_called()

    def test_missing_active_process_is_inconclusive_without_retry(self):
        base = {10: process(10)}
        with self.assertRaisesRegex(smoke.SmokeFailure, 'INCONCLUSIVE'):
            self.scenario(listings=[base] * 102, codes=[200, 401, 400])
        self.connection.request.assert_called_once()
        self.connection.close.assert_called_once()
        self.assertLessEqual(self.processes.call_count, 101)

    def test_completion_before_disconnect_is_inconclusive(self):
        base = {10: process(10)}
        with self.assertRaisesRegex(smoke.SmokeFailure, 'INCONCLUSIVE'):
            self.scenario(listings=[base, {**base, 20: process(20)}, base], codes=[200, 401, 400, 429])
        self.connection.close.assert_called_once()

    def test_running_descendant_prevents_success(self):
        base = {10: process(10)}
        running = {**base, 20: process(20), 21: process(21, parent=20, group=20)}
        with self.assertRaisesRegex(smoke.SmokeFailure, 'cleanup was not observed'):
            self.scenario(listings=[base, running, running] + [{**base, 21: running[21]}] * 100,
                          codes=[200, 401, 400, 429], remaining=running[21])
        self.connection.close.assert_called_once()

    def test_zombie_not_in_cgroup_still_requires_reaping(self):
        base = {10: process(10)}
        running = {**base, 20: process(20)}
        with self.assertRaisesRegex(smoke.SmokeFailure, 'cleanup was not observed'):
            self.scenario(listings=[base, running, running] + [base] * 100,
                          codes=[200, 401, 400, 429], remaining=process(20, state='Z'))

    def test_reused_pid_is_not_mistaken_for_original_process(self):
        result = self.scenario(remaining=process(20, start=200))
        self.assertEqual(result['result'], 'PASS')

    def test_recovery_http_probes_are_bounded(self):
        base = {10: process(10)}
        running = {**base, 20: process(20)}
        with self.assertRaises(smoke.SmokeFailure):
            self.scenario(listings=[base, running, running] + [base] * 10,
                          codes=[200, 401, 400, 429] + [429] * 6)
        self.assertEqual(self.requests.call_count, 10)


if __name__ == '__main__':
    unittest.main()
