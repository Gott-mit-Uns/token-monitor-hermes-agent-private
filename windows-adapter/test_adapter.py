import copy
import gzip
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from adapter import Adapter, Handler, Server, UpstreamError, encode

STATS = {'devices': [{'deviceId': 'Synthetic Desktop'}, {'deviceId': 'Mac'}, {'deviceId': 'NAS1'}, {'deviceId': 'NAS2'}], 'periods': {'today': {'totalTokens': 42}}}

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.calls = []
        self.down = False
        self.config = {'upstream': 'https://example.invalid:13245', 'device_id': 'Synthetic Desktop', 'interval_seconds': 600}
        self.a = Adapter(self.config, self.temp.name, transport=self.remote, secret_provider=lambda: 'synthetic-test-secret')
    def tearDown(self):
        self.temp.cleanup()
    def remote(self, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        if self.down:
            raise UpstreamError(502)
        if path == '/api/stats': return copy.deepcopy(STATS)
        if path == '/api/health': return {'role': 'hub', 'hubBuild': {'runtime': 'node-hub'}}
        if path == '/api/ingest': return {'ok': True, 'deviceId': body['deviceId']}
        if path == '/api/devices': return {'devices': copy.deepcopy(STATS['devices'])}
        if path == '/api/history': return {'schemaVersion': 1, 'days': {}}
        if path == '/api/subscriptions': return {'subscriptions': [], 'updatedAt': 'test'}
        raise UpstreamError(404)
    def start_http(self):
        self.server = Server(('127.0.0.1', 0), Handler)
        self.server.adapter = self.a
        self.a.local_port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.a.stop.set)
    def request(self, path, method='GET', body=None, auth=True, extra=None):
        c = http.client.HTTPConnection('127.0.0.1', self.a.local_port, timeout=3)
        h = {'Authorization': 'Bearer synthetic-test-secret'} if auth else {}
        h.update(extra or {})
        c.request(method, path, encode(body) if body is not None else None, h)
        r = c.getresponse(); value = json.loads(r.read()); status = r.status; c.close()
        return status, value
    def test_many_refreshes_one_download(self):
        for _ in range(30): self.assertEqual(self.a.refresh(), STATS)
        self.assertEqual(len(self.calls), 1)
    def test_parallel_singleflight(self):
        threads = [threading.Thread(target=self.a.refresh) for _ in range(12)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(self.calls), 1)
    def test_interval_and_manual_coalescing(self):
        self.a.refresh()
        self.a.refresh(manual=True)
        self.assertEqual(len(self.calls), 1)
        self.a.cache['/api/stats']['at'] -= 601
        self.a.refresh()
        self.assertEqual(len(self.calls), 2)
    def test_offline_backoff_old_cache(self):
        self.a.refresh(); self.a.cache['/api/stats']['at'] -= 601; self.down = True
        self.assertEqual(self.a.refresh(), STATS)
        for _ in range(20): self.a.refresh()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.a.status()['state'], 'offline_cached')
        self.a.next_retry['/api/stats'] = 0; self.down = False; self.a.refresh()
        self.assertEqual(self.a.status()['state'], 'cached')
    def test_cold_offline_no_fake_zero(self):
        self.down = True
        with self.assertRaises(UpstreamError): self.a.refresh()
        for _ in range(5):
            with self.assertRaises(UpstreamError): self.a.refresh()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.a.status()['state'], 'waiting')
    def test_restart_and_corrupt_cache(self):
        self.a.refresh()
        a2 = Adapter(self.config, self.temp.name, transport=self.remote, secret_provider=lambda: 'x')
        self.assertEqual(a2.refresh(), STATS); self.assertEqual(len(self.calls), 1)
        Path(self.temp.name, 'cache.json').write_text('bad cache')
        a3 = Adapter(self.config, self.temp.name, transport=self.remote, secret_provider=lambda: 'x')
        self.assertEqual(a3.refresh(), STATS); self.assertEqual(len(self.calls), 2)
    def test_stale_restart_and_resume_fetch_once(self):
        self.a.refresh()
        self.a.cache['/api/stats']['at'] -= 3600
        self.a.save()
        resumed = Adapter(self.config, self.temp.name, transport=self.remote, secret_provider=lambda: 'x')
        for _ in range(10): resumed.refresh()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(resumed.status()['state'], 'cached')
    def test_invalid_stats_never_replaces_good_cache(self):
        self.a.refresh(); self.a.cache['/api/stats']['at'] -= 601
        self.a.transport = lambda *args: {'devices': 'invalid', 'periods': {}}
        self.assertEqual(self.a.refresh(), STATS)
        self.assertEqual(self.a.status()['state'], 'offline_cached')
    def test_metrics_survive_restart(self):
        self.a.metrics['upstream']['GET /api/stats'] = {'requests': 8, 'download_body_bytes': 123456}
        self.a.save()
        a2 = Adapter(self.config, self.temp.name, transport=self.remote, secret_provider=lambda: 'x')
        self.assertEqual(a2.metrics['upstream']['GET /api/stats']['download_body_bytes'], 123456)
    def test_upload_failure_queue_latest_recovery(self):
        self.down = True
        for tokens in [1, 2]:
            with self.assertRaises(UpstreamError): self.a.ingest({'deviceId': 'Synthetic Desktop', 'today': {'totalTokens': tokens}})
        self.assertEqual(self.a.pending['today']['totalTokens'], 2)
        self.down = False; self.a.retry_upload()
        self.assertIsNone(self.a.pending)
        self.assertEqual(self.calls[-1][2]['today']['totalTokens'], 2)
        self.assertEqual(self.a.metrics['last_upload_device_id'], 'Synthetic Desktop')
    def test_no_other_device_ingest(self):
        with self.assertRaises(UpstreamError): self.a.ingest({'deviceId': 'NAS1'})
        self.assertEqual(self.calls, [])
    def test_local_and_remote_credentials_are_separate(self):
        a = Adapter(self.config, self.temp.name, transport=self.remote,
                    secret_provider=lambda: 'synthetic-remote-key',
                    local_secret_provider=lambda: 'synthetic-client-key')
        self.assertTrue(a.authorized({'Authorization': 'Bearer synthetic-client-key'}))
        self.assertFalse(a.authorized({'Authorization': 'Bearer synthetic-remote-key'}))
    def test_old_success_cannot_clear_new_failed_upload(self):
        entered, release = threading.Event(), threading.Event()
        errors = []
        def transport(method, path, body):
            self.calls.append(copy.deepcopy(body))
            if body['sequence'] == 1:
                entered.set()
                self.assertTrue(release.wait(3))
                return {'ok': True}
            raise UpstreamError(502, {'error': 'synthetic_failure'})
        self.a.transport = transport
        def submit(n):
            try: self.a.ingest({'deviceId': 'Synthetic Desktop', 'sequence': n})
            except UpstreamError: errors.append(n)
        old = threading.Thread(target=submit, args=(1,))
        new = threading.Thread(target=submit, args=(2,))
        old.start(); self.assertTrue(entered.wait(3)); new.start()
        time.sleep(.1)
        self.assertEqual(self.a.pending['sequence'], 1)
        release.set(); old.join(3); new.join(3)
        self.assertEqual(errors, [2])
        self.assertEqual(self.a.pending['sequence'], 2)
        persisted = json.loads(Path(self.temp.name, 'pending.json').read_text())
        self.assertEqual(persisted['sequence'], 2)
        resumed = Adapter(self.config, self.temp.name, transport=lambda *args: {'ok': True}, secret_provider=lambda: 'x')
        self.assertEqual(resumed.pending['sequence'], 2)
        resumed.retry_upload()
        self.assertIsNone(resumed.pending)
    def test_http_auth_routes_and_details(self):
        self.start_http()
        self.assertEqual(self.request('/api/stats', auth=False)[0], 401)
        self.assertEqual(self.request('/api/stats')[1], STATS)
        for path in ['/api/history', '/api/devices', '/api/subscriptions']:
            for _ in range(3): self.assertEqual(self.request(path)[0], 200)
        self.assertEqual(self.request('/unknown')[0], 405)
        self.assertEqual(self.request('/api/devices/NAS1', method='DELETE')[0], 403)
        self.assertEqual(self.request('/api/stats?secret=bad')[0], 400)
        health = self.request('/api/health', auth=False)[1]
        self.assertEqual(health['hubBuild'], {'runtime': 'node-hub'})
        self.assertEqual(health['hubBuild'], health['upstreamHubBuild'])
        self.assertEqual(health['runtime'], 'local-cache-adapter')
        self.assertEqual(len([r for r in self.calls if r[1] == '/api/history']), 1)
    def test_health_build_passthrough_and_cache(self):
        build = {'runtime': 'node-hub', 'schemaVersion': 1, 'coreRevision': 2,
                 'runtimeRevision': 3, 'coreBuildId': 'sha256:' + 'a'*64,
                 'runtimeBuildId': 'sha256:' + 'b'*64}
        self.a.transport = lambda *args: {'role': 'hub', 'hubBuild': build}
        self.start_http()
        for _ in range(3):
            self.assertEqual(self.request('/api/health', auth=False)[1]['hubBuild'], build)
        self.a.cache['/api/health']['at'] -= 601
        self.a.transport = lambda *args: {'role': 'hub'}
        health = self.request('/api/health', auth=False)[1]
        self.assertIn('hubBuild', health)
        self.assertIsNone(health['hubBuild'])
    def test_sse_local_only_and_reconnection(self):
        self.start_http()
        for _ in range(3):
            c = http.client.HTTPConnection('127.0.0.1', self.a.local_port, timeout=3)
            c.request('GET', '/api/stats/stream', headers={'Authorization': 'Bearer synthetic-test-secret'})
            r = c.getresponse(); self.assertEqual(r.status, 200)
            self.assertEqual(r.readline(), b'event: snapshot\n')
            self.assertIn(b'Synthetic Desktop', r.readline()); c.close()
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(any(p == '/api/stats/stream' for _,p,_ in self.calls))
    def test_manual_action_origin_protection(self):
        self.start_http()
        self.assertEqual(self.request('/adapter/refresh', method='POST', auth=False)[0], 403)
        self.assertEqual(self.request('/adapter/refresh', method='POST', auth=False, extra={'Origin': f'http://127.0.0.1:{self.a.local_port}', 'X-Adapter-Action': 'refresh'})[0], 200)
    def test_wire_gzip_count_and_minimal_header(self):
        class Response:
            status = 200
            def getheader(self, name): return 'gzip' if name == 'Content-Encoding' else None
            def read(self, _): return gzip.compress(encode(STATS))
        class Connection:
            def __init__(self, *args, **kwargs): pass
            def request(self, method, path, data, headers):
                self.headers = headers
                Tests.last_headers = headers
            def getresponse(self): return Response()
            def close(self): pass
        with patch('adapter.http.client.HTTPSConnection', Connection):
            value = self.a.http_request('GET', '/api/stats')
        self.assertEqual(value, STATS)
        m = self.a.metrics['upstream']['GET /api/stats']
        self.assertEqual(m['download_body_bytes'], len(gzip.compress(encode(STATS))))
        self.assertEqual(m['gzip_responses'], 1)
        self.assertEqual(Tests.last_headers['Accept-Encoding'], 'gzip')
        with patch('adapter.http.client.HTTPSConnection', Connection): self.a.http_request('POST', '/api/ingest', {'deviceId': 'Synthetic Desktop'})
        self.assertEqual(Tests.last_headers['x-token-monitor-response'], 'minimal')

    def test_daily_midnight_persistence_and_legacy(self):
        atomic = {'started_at': 100, 'upstream': {'GET /api/stats': {'download_body_bytes': 999}}, 'local': {}}
        Path(self.temp.name, 'metrics.json').write_text(json.dumps(atomic))
        resumed = Adapter(self.config, self.temp.name, version='synthetic-version')
        self.assertEqual(resumed.status()['daily_traffic'], [])
        with resumed.lock, patch('adapter.time.strftime', return_value='2026-10-01'):
            resumed.record_daily(upload=10)
        with resumed.lock, patch('adapter.time.strftime', return_value='2026-10-02'):
            resumed.record_daily(download=20)
        resumed.save()
        loaded = Adapter(self.config, self.temp.name, version='synthetic-version')
        rows = loaded.status()['daily_traffic']
        self.assertEqual([r['date'] for r in rows], ['2026-10-02', '2026-10-01'])
        self.assertEqual([r['total_body_bytes'] for r in rows], [20, 10])
        self.assertEqual(loaded.metrics['upstream'], atomic['upstream'])
        self.assertEqual(loaded.status()['version'], 'synthetic-version')
        self.assertEqual(loaded.metrics['daily_started_at'], resumed.metrics['daily_started_at'])

    def test_upload_phase_and_restored_pending(self):
        self.assertEqual(self.a.status()['upload_phase'], 'waiting')
        def transport(*args):
            self.assertEqual(self.a.status()['upload_phase'], 'uploading')
            raise UpstreamError(502, {'error': 'synthetic_failure'})
        self.a.transport = transport
        with self.assertRaises(UpstreamError):
            self.a.ingest({'deviceId': 'Synthetic Desktop'})
        self.assertEqual(self.a.status()['upload_phase'], 'retry')
        self.assertGreater(self.a.status()['upload_retry_at'], time.time())
        loaded = Adapter(self.config, self.temp.name, transport=lambda *args: {'ok': True})
        self.assertEqual(loaded.status()['upload_phase'], 'retry')
        loaded.retry_upload()
        self.assertEqual(loaded.status()['upload_phase'], 'idle')
        self.assertEqual(loaded.status()['upload_retry_at'], 0)

    def test_daily_wire_failure_and_local_status_read(self):
        raw = gzip.compress(encode({'error': 'synthetic_failure'}))
        class Response:
            status = 503
            def getheader(self, name): return 'gzip' if name == 'Content-Encoding' else None
            def read(self, _): return raw
        class Connection:
            def __init__(self, *args, **kwargs): pass
            def request(self, *args): pass
            def getresponse(self): return Response()
            def close(self): pass
        payload = {'deviceId': 'Synthetic Desktop'}
        with patch('adapter.http.client.HTTPSConnection', Connection):
            with self.assertRaises(UpstreamError): self.a.http_request('POST', '/api/ingest', payload)
        row = self.a.status()['daily_traffic'][0]
        self.assertEqual(row['upload_body_bytes'], len(encode(payload)))
        self.assertEqual(row['download_body_bytes'], len(raw))
        self.assertEqual(row['total_body_bytes'], len(raw) + len(encode(payload)))
        self.start_http()
        for _ in range(4): self.assertEqual(self.request('/adapter/status', auth=False)[1]['daily_traffic'], [row])
        self.assertEqual(self.calls, [])

if __name__ == '__main__': unittest.main(verbosity=2)
