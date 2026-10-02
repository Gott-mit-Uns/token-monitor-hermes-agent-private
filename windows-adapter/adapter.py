"""Loopback-only Token Monitor cache adapter. No third-party dependencies."""
import argparse
import collections
import copy
import gzip
import hmac
import http.client
import json
import os
from pathlib import Path
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote

MAX_BODY = 16 * 1024 * 1024
READ_PATHS = {'/api/stats', '/api/history', '/api/devices', '/api/subscriptions', '/api/health'}

def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')

def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_bytes(encode(value))
    os.replace(temporary, path)

class UpstreamError(Exception):
    def __init__(self, status, body=None):
        self.status = status
        self.body = body or {'error': 'upstream_unavailable'}

class StorageError(Exception):
    pass

class Adapter:
    def __init__(self, config, root, transport=None, secret_provider=None, local_secret_provider=None, version='development'):
        self.config, self.root = config, Path(root)
        self.version = version
        self.upload_in_progress = False
        self.active_requests = {}
        self.cache_dirty = False
        self.pending_dirty = False
        self.storage_failures = set()
        self.scheduler_health = {'stage': 'starting', 'heartbeat_at': time.time(), 'consecutive_errors': 0,
                                 'error_category': None, 'message': None, 'restarts': 0}
        self.heartbeat_monotonic = time.monotonic()
        self.scheduler_thread = None
        self.scheduler_guard = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)
        self.interval = config.get('interval_seconds', 600)
        self.transport = transport or self.http_request
        self.secret_provider = secret_provider or self.read_secret
        self.local_secret_provider = local_secret_provider or self.secret_provider
        self.lock = threading.RLock()
        self.read_locks = {p: threading.Lock() for p in READ_PATHS}
        self.upload_lock = threading.Lock()
        self.cache = {}
        self.failures, self.next_retry = {}, {}
        self.clients = {}
        self.stop = threading.Event()
        self.metrics = {'started_at': time.time(), 'upstream': {}, 'local': {}, 'last_upload_at': None,
                        'daily_started_at': time.time(), 'daily': {}, 'history_started_at': time.time(), 'sync_history': {}, 'local_accepted_uploads': 0}
        self.pending = None
        self.pending_generation = 0
        self.upload_failures = 0
        self.upload_retry_at = 0
        self.last_error = None
        self.local_port = config.get('port', 17322)
        self._load()
        self.upload_interval = config.get('upload_interval_ms', 1800000) / 1000
        schedule = self.metrics.setdefault('upload_schedule', {'started_at': time.time()})
        if schedule.get('interval') != self.upload_interval or 'next_at' not in schedule:
            schedule['next_at'] = schedule.get('last_attempt_at', schedule['started_at']) + self.upload_interval
        schedule['interval'] = self.upload_interval
        self.upload_retry_at = schedule.get('retry_at', 0)
        self.upload_failures = schedule.get('failures', 0)
        self.deadlines = {}
        self.deadline_due('upload', schedule['next_at'])
        if self.upload_retry_at: self.deadline_due('upload-retry', self.upload_retry_at)
        for path,item in self.cache.items():
            self.deadline_due('read:'+path+':normal', item['at']+self.interval)
            self.deadline_due('read:'+path+':manual', item['at']+60)
        try: self.save()
        except StorageError: pass

    def deadline_due(self, key, target):
        # Persist wall dates; use monotonic deadlines during this process lifetime.
        current = self.deadlines.get(key)
        if current is None or current[0] != target:
            current = (target, time.monotonic() + max(0, target - time.time()))
            self.deadlines[key] = current
        return time.monotonic() >= current[1]

    def _load(self):
        try:
            metrics = json.loads((self.root / 'metrics.json').read_text(encoding='utf-8'))
            if isinstance(metrics.get('upstream'), dict) and isinstance(metrics.get('local'), dict):
                self.metrics.update(metrics)
                self.metrics['session_started_at'] = time.time()
        except (OSError, ValueError, TypeError):
            pass
        try:
            document = json.loads((self.root / 'cache.json').read_text(encoding='utf-8'))
            if document.get('upstream') == self.config['upstream']:
                for path, item in document.get('entries', {}).items():
                    if path in READ_PATHS and isinstance(item, dict) and self.valid(path, item.get('data')) and isinstance(item.get('at'), (int, float)):
                        self.cache[path] = item
        except (OSError, ValueError, TypeError):
            pass
        try:
            pending = json.loads((self.root / 'pending.json').read_text(encoding='utf-8'))
            if pending and str(pending.get('deviceId', pending.get('id', ''))) == self.config['device_id']:
                self.pending = pending
        except (OSError, ValueError, TypeError):
            pass

    def read_secret(self):
        document = json.loads(Path(self.config['credentials_file']).read_text(encoding='utf-8'))
        secret = document.get('credentials', {}).get('hub', {}).get('clientSecret', '')
        if not isinstance(secret, str) or not secret:
            raise UpstreamError(503, {'error': 'credential_unavailable'})
        return secret

    def authorized(self, headers):
        supplied = headers.get('Authorization', '')
        supplied = supplied[7:] if supplied.lower().startswith('bearer ') else headers.get('X-Token-Monitor-Secret', '')
        try:
            return hmac.compare_digest(supplied.encode(), self.local_secret_provider().encode())
        except Exception:
            return False

    def record_daily(self, upload=0, download=0):
        # Call under self.lock, at each direction's accounting instant (local time).
        day = time.strftime('%Y-%m-%d', time.localtime())
        row = self.metrics['daily'].setdefault(day, {'upload_body_bytes': 0, 'download_body_bytes': 0})
        row['upload_body_bytes'] += upload
        row['download_body_bytes'] += download

    def http_request(self, method, path, body=None):
        base = urlsplit(self.config['upstream'])
        if base.scheme != 'https' or base.username or base.password or base.query or base.fragment:
            raise UpstreamError(503, {'error': 'invalid_upstream'})
        if path not in READ_PATHS and not (method == 'POST' and path == '/api/ingest') and not (method == 'PUT' and path == '/api/subscriptions') and not (method == 'DELETE' and path.startswith('/api/devices/')):
            raise UpstreamError(405, {'error': 'unsupported_upstream_route'})
        headers = {'Authorization': 'Bearer ' + self.secret_provider(), 'Accept-Encoding': 'gzip'}
        data = encode(body) if body is not None else None
        if data is not None:
            headers['Content-Type'] = 'application/json'
        if path == '/api/ingest':
            headers['x-token-monitor-response'] = 'minimal'
        connection = http.client.HTTPSConnection(base.hostname, base.port or 443, timeout=12)
        key = method + ' ' + path
        with self.lock:
            meter = self.metrics['upstream'].setdefault(key, {'requests': 0, 'upload_body_bytes': 0, 'download_body_bytes': 0, 'failures': 0, 'gzip_responses': 0})
            meter['requests'] += 1
            meter['upload_body_bytes'] += len(data or b'')
            self.record_daily(upload=len(data or b''))
        request_id=threading.get_ident()
        with self.lock:self.active_requests[request_id]={'monotonic':time.monotonic()}
        try:
            connection.request(method, base.path.rstrip('/') + path, data, headers)
            response = connection.getresponse()
            with self.lock:
                meter['last_status'] = response.status
            raw = response.read(MAX_BODY + 1)
            with self.lock:
                meter['download_body_bytes'] += len(raw)
                self.record_daily(download=len(raw))
                meter['gzip_responses'] += int(response.getheader('Content-Encoding') == 'gzip')
            if len(raw) > MAX_BODY:
                raise UpstreamError(502, {'error': 'response_too_large'})
            decoded = gzip.decompress(raw) if response.getheader('Content-Encoding') == 'gzip' else raw
            if len(decoded) > 64 * 1024 * 1024:
                raise UpstreamError(502, {'error': 'decoded_response_too_large'})
            parsed = json.loads(decoded) if decoded else {}
            if not 200 <= response.status < 300:
                # Do not echo arbitrary upstream errors (which could contain secrets).
                error = {'error': 'upstream_rejected'}
                if response.status == 409 and path == '/api/subscriptions':
                    error = parsed
                raise UpstreamError(response.status, error)
            return parsed
        except UpstreamError:
            with self.lock:
                meter['failures'] += 1
            raise
        except Exception as error:
            with self.lock:
                meter['failures'] += 1
                meter['last_exception_type'] = type(error).__name__
            raise UpstreamError(502, {'error': 'upstream_connection_failed'}) from None
        finally:
            connection.close()
            with self.lock:self.active_requests.pop(request_id,None)

    @staticmethod
    def valid(path, data):
        if not isinstance(data, dict):
            return False
        if path == '/api/stats':
            return isinstance(data.get('devices'), list) and isinstance(data.get('periods'), dict)
        if path == '/api/devices':
            return isinstance(data.get('devices'), list)
        if path == '/api/health':
            return data.get('role') == 'hub'
        return True

    def _persist(self, name, value):
        try:
            atomic_json(self.root / name, value)
        except OSError:
            self.storage_failures.add(name)
            raise StorageError('local_save_failed') from None
        self.storage_failures.discard(name)

    def save_cache(self):
        with self.lock:
            if self.cache_dirty:
                self._persist('cache.json', {'upstream': self.config['upstream'], 'entries': self.cache})
                self.cache_dirty = False

    def save(self):
        # Compatibility entry point: only dirty cache/pending and current runtime state.
        with self.lock:
            if self.pending_dirty:
                self._persist('pending.json', self.pending)
                self.pending_dirty = False
            self.save_cache()
            self._persist('metrics.json', self.metrics)

    def status(self):
        with self.lock:
            at = self.cache.get('/api/stats', {}).get('at')
            age = time.time() - at if at else None
            state = 'waiting' if at is None else ('offline_cached' if self.failures.get('/api/stats') else ('stale' if age > self.interval + 60 else 'cached'))
            upload_failed = self.pending is not None and bool(self.upload_retry_at)
            labels = {'waiting': '等待首次同步', 'offline_cached': '下载失败，使用缓存', 'stale': '缓存已过期'}
            label = labels.get(state, '上报失败，等待重试' if upload_failed else '同步正常')
            request_age=max((time.monotonic()-r['monotonic'] for r in self.active_requests.values()),default=0)
            stalled = (time.monotonic() - self.heartbeat_monotonic > 60 and self.scheduler_thread is not None) or request_age>60
            scheduler_bad = stalled or bool(self.scheduler_health['error_category'])
            if self.storage_failures: label = '本地保存失败'
            elif scheduler_bad: label = '调度停滞' if stalled else self.scheduler_health['message']
            metrics = copy.deepcopy(self.metrics)
            metrics.pop('token_baseline', None)
            try: recovery = json.loads((self.root/'recovery.json').read_text(encoding='utf-8'))
            except (OSError,ValueError): recovery = {}
            return {'recovery': recovery, 'adapter': 'Token Monitor Hotspot Cache', 'interval_seconds': self.interval,
                    'last_success_at': at, 'cache_age_seconds': round(age, 1) if age is not None else None,
                    'state': state, 'health_level': 'ok' if state == 'cached' and not upload_failed and not self.storage_failures and not scheduler_bad else 'warning', 'health_label': label,
                    'next_attempt_at': self.next_retry.get('/api/stats'), 'last_error': self.last_error,
                    'device_count': len(self.cache.get('/api/stats', {}).get('data', {}).get('devices', [])),
                    'pending_upload': self.pending is not None, 'metrics': metrics,
                    'pending_tokens': self.pending_token_summary(),
                    'storage': {'ok': not self.storage_failures, 'message': '本地保存失败' if self.storage_failures else None},
                    'scheduler': {**copy.deepcopy(self.scheduler_health), 'stalled': stalled, 'request_max_age_seconds': round(request_age,1), 'heartbeat_age_seconds': round(time.monotonic() - self.heartbeat_monotonic, 1)},
                    'upload_progress_started_at': self.metrics['upload_schedule'].get('last_attempt_at', self.metrics['upload_schedule']['next_at'] - self.upload_interval),
                    'version': self.version, 'upload_retry_at': self.upload_retry_at,
                    'upload_phase': ('uploading' if self.upload_in_progress else ('retry' if self.upload_retry_at else 'queued') if self.pending is not None
                                     else 'idle' if self.metrics.get('last_upload_at') else 'waiting'),
                    'upload_interval_seconds': self.upload_interval, 'next_upload_at': self.metrics['upload_schedule']['next_at'],
                    'sync_history_started_at': self.metrics['history_started_at'],
                    'sync_history': [{'date': day, **copy.deepcopy(row)} for day, row in sorted(self.metrics['sync_history'].items(), reverse=True)],
                    'manual_result': copy.deepcopy(self.metrics.get('manual_result')),
                    'daily_started_at': self.metrics['daily_started_at'],
                    'daily_traffic': [{'date': day, **row, 'total_body_bytes': row['upload_body_bytes'] + row['download_body_bytes']}
                                      for day, row in sorted(self.metrics['daily'].items(), reverse=True)]}

    def token_summary(self, payload):
        if not isinstance(payload, dict): return None
        periods = payload.get('periods') if isinstance(payload.get('periods'), dict) else payload
        period = periods.get('allTime')
        total = period.get('totalTokens') if isinstance(period, dict) else None
        clients = payload.get('trackedClients')
        if type(total) is not int or total < 0 or not isinstance(clients, list) or not all(isinstance(c, str) for c in clients):
            return None
        return {'deviceId': self.config['device_id'], 'upstream': self.config['upstream'],
                'tracked_clients': sorted(set(clients)), 'total_tokens': total}

    def pending_token_summary(self):
        def unavailable(reason): return {'value': None, 'calculable': False, 'reason': reason}
        if self.pending is None: return unavailable('no_pending')
        current = self.token_summary(self.pending)
        if current is None: return unavailable('missing_fields')
        baseline = self.metrics.get('token_baseline')
        if not isinstance(baseline, dict): return unavailable('no_baseline')
        if any(current[k] != baseline.get(k) for k in ('deviceId', 'upstream')): return unavailable('identity_changed')
        if current['tracked_clients'] != baseline.get('tracked_clients'): return unavailable('scope_changed')
        previous = baseline.get('total_tokens')
        if type(previous) is not int or previous < 0: return unavailable('no_baseline')
        delta = current['total_tokens'] - previous
        if delta < 0: return unavailable('counter_decreased')
        return {'value': delta, 'calculable': True, 'reason': 'snapshot_difference'}

    def refresh(self, path='/api/stats', manual=False):
        if path not in READ_PATHS:
            raise UpstreamError(404)
        with self.read_locks[path]:
            now = time.time()
            with self.lock:
                item = self.cache.get(path)
                minimum_age = 60 if manual else self.interval
                due = not item or self.deadline_due('read:'+path+(':'+ 'manual' if manual else ':normal'), item['at']+minimum_age)
                retry = self.next_retry.get(path, 0)
                if not due or (retry and not self.deadline_due('read-retry:'+path,retry)):
                    if item:
                        self.metrics['local']['cache_hits'] = self.metrics['local'].get('cache_hits', 0) + 1
                        return copy.deepcopy(item['data'])
                    raise UpstreamError(503, {'error': 'waiting_for_upstream'})
            try:
                data = self.request_remote('GET', path)
                if not self.valid(path, data):
                    raise UpstreamError(502, {'error': 'invalid_upstream_schema'})
                with self.lock:
                    self.cache[path] = {'at': time.time(), 'data': data}
                    self.cache_dirty = True
                    self.failures[path] = 0
                    self.next_retry[path] = 0
                    if path == '/api/stats':
                        self.last_error = None
                    self.save()
                if path == '/api/stats':
                    self.broadcast(data)
                return copy.deepcopy(data)
            except UpstreamError as error:
                with self.lock:
                    failures = self.failures.get(path, 0) + 1
                    self.failures[path] = failures
                    self.next_retry[path] = time.time() + min(600, 60 * 2 ** min(failures - 1, 4))
                    if path == '/api/stats':
                        self.last_error = error.body.get('error', 'upstream_unavailable')
                    self.save()
                    if item:
                        return copy.deepcopy(item['data'])
                raise

    def broadcast(self, stats):
        message = b'event: stats\ndata: ' + encode({'type': 'stats', 'reason': 'cached_remote_refresh', 'stats': stats, 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}) + b'\n\n'
        with self.lock:
            clients = list(self.clients.values())
        for client in clients:
            client.append(message)

    def request_remote(self, method, path, body=None):
        direction = 'upload' if method == 'POST' and path == '/api/ingest' else 'download' if method == 'GET' else None
        day = time.strftime('%Y-%m-%d', time.localtime())
        if direction:
            with self.lock:
                record = self.metrics['sync_history'].setdefault(day, {})
                counts = record.setdefault(direction, {'requests': 0, 'successes': 0, 'failures': 0})
                counts['requests'] += 1
        succeeded = False
        try:
            result = self.transport(method, path, body)
            if direction == 'download' and not self.valid(path, result):
                raise UpstreamError(502, {'error': 'invalid_upstream_schema'})
            if direction == 'upload' and result.get('ok') is not True:
                raise UpstreamError(502, {'error': 'invalid_ingest_ack'})
            succeeded = True
            return result
        finally:
            if direction:
                with self.lock:
                    counts['successes' if succeeded else 'failures'] += 1

    def ingest(self, payload):
        if not isinstance(payload, dict) or str(payload.get('deviceId', payload.get('id', ''))) != self.config['device_id']:
            raise UpstreamError(400, {'error': 'unexpected_device_id'})
        with self.lock:
            self.pending_generation += 1
            self.pending = copy.deepcopy(payload)
            self.pending_dirty = True
            self.metrics['local_accepted_uploads'] += 1
            try: self.save()
            except StorageError:
                self.metrics['local_accepted_uploads'] -= 1
                raise
        return {'ok': True, 'deviceId': self.config['device_id'], 'queued': True}

    def upload_pending(self, manual=False):
        with self.upload_lock:
            with self.lock:
                now = time.time()
                schedule = self.metrics['upload_schedule']
                if self.pending is None: return 'no_data'
                if self.upload_retry_at and not self.deadline_due('upload-retry',self.upload_retry_at): return 'backoff'
                if not manual and not self.upload_retry_at and not self.deadline_due('upload',schedule['next_at']): return 'waiting'
                payload = copy.deepcopy(self.pending)
                generation = self.pending_generation
                if self.storage_failures or self.pending_dirty or self.cache_dirty: self.save()
                previous_schedule = copy.deepcopy(schedule)
                schedule['last_attempt_at'] = now
                schedule['next_at'] = now + self.upload_interval
                try: self.save()
                except StorageError:
                    schedule.clear();schedule.update(previous_schedule)
                    raise
                self.upload_in_progress = True
            acknowledged = False
            try:
                self.request_remote('POST', '/api/ingest', payload)
                acknowledged = True
                with self.lock:
                    self.metrics['token_baseline'] = self.token_summary(payload)
                    self.metrics['last_upload_at'] = time.time()
                    self.metrics['last_upload_device_id'] = self.config['device_id']
                    self.upload_failures = 0
                    self.upload_retry_at = 0
                    self.metrics['last_upload_error'] = None
                return 'success'
            except UpstreamError as error:
                with self.lock:
                    self.upload_failures += 1
                    safe_errors = {'invalid_ingest_ack', 'upstream_connection_failed', 'credential_unavailable', 'upstream_unavailable'}
                    reason = error.body.get('error')
                    self.metrics['last_upload_error'] = {'status': error.status, 'error': reason if reason in safe_errors else 'upstream_rejected'}
                    self.upload_retry_at = time.time() + min(600, 60 * 2 ** min(self.upload_failures - 1, 4))
                return 'failed'
            finally:
                with self.lock:
                    self.upload_in_progress = False
                    schedule['retry_at'] = self.upload_retry_at
                    schedule['failures'] = self.upload_failures
                    self.save()
                    if acknowledged and self.pending_generation == generation:
                        self.pending_dirty = True
                        self._persist('pending.json', None)
                        self.pending = None
                        self.pending_dirty = False

    def retry_upload(self):
        return self.upload_pending()

    def manual_sync(self):
        with self.upload_lock:
            with self.lock:
                now = time.time()
                target = self.metrics.get('last_manual_at', 0) + 60
                if not self.deadline_due('manual', target): return
                self.metrics['last_manual_at'] = now
                self.save()
        upload = self.upload_pending(manual=True)
        try:
            self.refresh(manual=True)
            download = 'failed' if self.failures.get('/api/stats') else 'success_or_cached'
        except UpstreamError:
            download = 'failed'
        with self.lock:
            self.metrics['manual_result'] = {'upload': upload, 'download': download}
            self.save()

    def heartbeat(self, stage):
        with self.lock:
            self.heartbeat_monotonic = time.monotonic()
            self.scheduler_health.update(stage=stage, heartbeat_at=time.time())

    def scheduler_cycle(self):
        self.heartbeat('storage')
        if self.storage_failures or self.pending_dirty or self.cache_dirty: self.save()
        self.heartbeat('upload')
        result = self.upload_pending()
        if self.stop.is_set(): return
        self.heartbeat('download')
        self.refresh()
        self.heartbeat('idle')
        network = result == 'failed' or bool(self.failures.get('/api/stats'))
        with self.lock:
            self.scheduler_health.update(consecutive_errors=0, error_category='network' if network else None,
                                         message='远端请求失败，等待重试' if network else None)

    def scheduler(self):
        while not self.stop.is_set():
            delay = 5
            try:
                self.scheduler_cycle()
            except Exception as error:
                category = 'storage' if isinstance(error, StorageError) else 'network' if isinstance(error, UpstreamError) else 'internal'
                with self.lock:
                    n = self.scheduler_health['consecutive_errors'] + 1 if self.scheduler_health['error_category'] == category else 1
                    self.scheduler_health.update(consecutive_errors=n, error_category=category,
                        message={'storage':'本地保存失败', 'network':'远端请求失败，等待重试', 'internal':'调度内部异常，正在恢复'}[category])
                if category == 'internal': delay = min(60, 5 * 2 ** min(n - 1, 4))
            self.heartbeat('waiting')
            self.stop.wait(delay)
        self.heartbeat('stopped')

    def ensure_scheduler(self):
        with self.scheduler_guard:
            if self.stop.is_set(): return
            if self.scheduler_thread is None or not self.scheduler_thread.is_alive():
                if self.scheduler_thread is not None: self.scheduler_health['restarts'] += 1
                self.heartbeat('starting')
                self.scheduler_thread = threading.Thread(target=self.scheduler, daemon=True)
                self.scheduler_thread.start()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass  # Never log authorization headers, query strings or payloads.

    def reply(self, status, data, content_type='application/json; charset=utf-8'):
        body = encode(data) if not isinstance(data, bytes) else data
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise UpstreamError(400, {'error': 'invalid_length'})
        if length <= 0 or length > 1024 * 1024:
            self.close_connection = True
            raise UpstreamError(413, {'error': 'invalid_body_size'})
        try:
            return json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError):
            raise UpstreamError(400, {'error': 'invalid_json'})

    def route(self, method):
        adapter = self.server.adapter
        parsed = urlsplit(self.path)
        path = parsed.path
        if self.headers.get('Host') not in {f'127.0.0.1:{adapter.local_port}', f'localhost:{adapter.local_port}'} or parsed.query:
            return self.reply(400, {'error': 'invalid_local_request'})
        with adapter.lock:
            key = method + ' ' + path
            adapter.metrics['local'][key] = adapter.metrics['local'].get(key, 0) + 1
        try:
            if method == 'GET' and path == '/adapter':
                page = adapter.root / 'dashboard.html'
                return self.reply(200, page.read_bytes() if page.is_file() else DASHBOARD.encode('utf-8'), 'text/html; charset=utf-8')
            if method == 'GET' and path == '/adapter/status':
                return self.reply(200, adapter.status())
            if method == 'POST' and path == '/adapter/refresh':
                # Explicit same-origin action: no credential exposed to a browser.
                origin = self.headers.get('Origin')
                allowed = {f'http://127.0.0.1:{adapter.local_port}', f'http://localhost:{adapter.local_port}'}
                if origin not in allowed or self.headers.get('X-Adapter-Action') != 'refresh':
                    return self.reply(403, {'error': 'same_origin_required'})
                adapter.manual_sync()
                return self.reply(200, adapter.status())
            if method == 'GET' and path == '/api/health':
                health = adapter.refresh('/api/health')
                return self.reply(200, {'ok': True, 'role': 'hub', 'runtime': 'local-cache-adapter', 'version': 1,
                                        'deviceCount': adapter.status()['device_count'], 'secretRequired': True,
                                        'adapter': adapter.status(), 'hubBuild': health.get('hubBuild'),
                                        'upstreamHubBuild': health.get('hubBuild')})
            if not adapter.authorized(self.headers):
                return self.reply(401, {'error': 'unauthorized'})
            if method == 'GET' and path == '/api/stats/stream':
                return self.stream()
            if method == 'GET' and path in READ_PATHS:
                return self.reply(200, adapter.refresh(path))
            if method == 'POST' and path == '/api/ingest':
                return self.reply(200, adapter.ingest(self.read_body()))
            if method == 'PUT' and path == '/api/subscriptions':
                payload = self.read_body()
                if not isinstance(payload, dict) or not isinstance(payload.get('subscriptions'), list) or not isinstance(payload.get('baseUpdatedAt', ''), str):
                    raise UpstreamError(400, {'error': 'invalid_subscriptions'})
                with adapter.read_locks['/api/subscriptions']:
                    try: data = adapter.transport('PUT', path, payload)
                    except UpstreamError:
                        adapter.save()
                        raise
                    with adapter.lock:
                        adapter.cache[path] = {'at': time.time(), 'data': data}
                        adapter.cache_dirty = True
                        adapter.save()
                return self.reply(200, data)
            if method == 'DELETE' and path.startswith('/api/devices/'):
                # Only the original client's own cleanup route is supported.
                if unquote(path[len('/api/devices/'):]) != adapter.config['device_id']:
                    return self.reply(403, {'error': 'other_device_delete_disabled'})
                try: data = adapter.transport('DELETE', path)
                finally: adapter.save()
                return self.reply(200, data)
            return self.reply(405, {'error': 'unsupported_route'})
        except StorageError:
            return self.reply(503, {'error': 'local_save_failed'})
        except UpstreamError as error:
            return self.reply(error.status, error.body)
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            self.close_connection = True
        except Exception:
            return self.reply(500, {'error': 'adapter_internal_error'})

    def stream(self):
        adapter = self.server.adapter
        stats = adapter.refresh()
        queue = collections.deque(maxlen=2)
        with adapter.lock:
            adapter.clients[id(queue)] = queue
            stats = copy.deepcopy(adapter.cache['/api/stats']['data'])
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache, no-transform')
            self.send_header('Connection', 'close')
            self.send_header('X-Accel-Buffering', 'no')
            self.end_headers()
            self.wfile.write(b'event: snapshot\ndata: ' + encode({'type': 'stats', 'reason': 'local_cache', 'stats': stats}) + b'\n\n')
            self.wfile.flush()
            heartbeat = time.monotonic()
            while not adapter.stop.wait(0.5):
                if queue:
                    self.wfile.write(queue.popleft())
                    self.wfile.flush()
                if time.monotonic() - heartbeat > 15:
                    self.wfile.write(b': local-cache heartbeat; remote freshness: /adapter\n\n')
                    self.wfile.flush()
                    heartbeat = time.monotonic()
        finally:
            with adapter.lock:
                adapter.clients.pop(id(queue), None)
            self.close_connection = True

    def do_GET(self):
        self.route('GET')
    def do_POST(self):
        self.route('POST')
    def do_PUT(self):
        self.route('PUT')
    def do_DELETE(self):
        self.route('DELETE')

DASHBOARD = '''<!doctype html><meta charset="utf-8"><title>Token Monitor 热点同步</title>
<style>body{font:16px system-ui;background:#f3f6fa;color:#172c43;max-width:850px;margin:40px auto;padding:24px}h1{font-size:26px}button{padding:12px;border:0;border-radius:8px;background:#245c9a;color:white}pre{background:white;padding:20px;white-space:pre-wrap;border-radius:12px}</style>
<h1>Token Monitor 热点同步</h1><p>远端每 10 分钟压缩下载；本机界面连接不代表远端刚同步成功。</p>
<button onclick="refreshRemote()">立即同步远端（60 秒内合并重复请求）</button><p id="summary"></p><pre id="detail"></pre>
<script>async function show(){let s=await(await fetch('/adapter/status')).json();let t=s.last_success_at?new Date(s.last_success_at*1000).toLocaleString():'尚未成功同步';document.querySelector('#summary').textContent='状态：'+s.state+' ｜最后成功：'+t+' ｜缓存年龄：'+s.cache_age_seconds+' 秒 ｜待上报：'+s.pending_upload;document.querySelector('#detail').textContent=JSON.stringify(s,null,2)}async function refreshRemote(){await fetch('/adapter/refresh',{method:'POST',headers:{'X-Adapter-Action':'refresh'}});show()}show();setInterval(show,5000)</script>'''

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    root = Path(args.root)
    config = json.loads((root / 'config.json').read_text(encoding='utf-8'))
    adapter = Adapter(config, root)
    server = Server(('127.0.0.1', config.get('port', 17322)), Handler)
    server.adapter = adapter
    atomic_json(root / 'process.json', {'pid': os.getpid(), 'started_at': time.time()})
    threading.Thread(target=adapter.scheduler, daemon=True).start()
    try:
        server.serve_forever(poll_interval=1)
    finally:
        adapter.stop.set()
        adapter.save()
        server.server_close()

if __name__ == '__main__':
    main()
