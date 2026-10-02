import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import adapter
from adapter import Adapter, StorageError, UpstreamError, encode

class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.cfg={'upstream':'https://example.invalid','device_id':'Synthetic','interval_seconds':600}
        self.a=Adapter(self.cfg,self.temp.name,transport=lambda *args:{'devices':[],'periods':{}})
    def test_100_ingests_never_rewrite_cache_and_each_is_durable(self):
        self.a.refresh();before=(self.a.root/'cache.json').read_bytes();writes=[]
        real=adapter.atomic_json
        def record(path,value):
            writes.append((Path(path).name,len(encode(value))));real(path,value)
        with patch('adapter.atomic_json',side_effect=record):
            for n in range(100):
                self.a.ingest({'deviceId':'Synthetic','sequence':n})
                self.assertEqual(json.loads((self.a.root/'pending.json').read_bytes())['sequence'],n)
        self.assertEqual([n for n,_ in writes].count('cache.json'),0)
        self.assertEqual([n for n,_ in writes].count('pending.json'),100)
        self.assertEqual((self.a.root/'cache.json').read_bytes(),before)
    def test_disk_failure_keeps_latest_and_never_acknowledges(self):
        self.a.ingest({'deviceId':'Synthetic','sequence':1})
        with patch('adapter.atomic_json',side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(StorageError): self.a.ingest({'deviceId':'Synthetic','sequence':2})
        self.assertEqual(self.a.pending['sequence'],2)
        self.assertTrue(self.a.pending_dirty)
        self.assertFalse(self.a.status()['storage']['ok'])
        self.assertEqual(self.a.metrics['local_accepted_uploads'],1)
        self.a.save();self.assertTrue(self.a.status()['storage']['ok'])
        restored=Adapter(self.cfg,self.temp.name)
        self.assertEqual(restored.pending['sequence'],2)
    def test_metrics_failure_does_not_rewrite_cache(self):
        self.a.refresh();real=adapter.atomic_json
        def fail(path,value):
            if Path(path).name=='metrics.json': raise OSError('synthetic')
            real(path,value)
        with patch('adapter.atomic_json',side_effect=fail):
            with self.assertRaises(StorageError):self.a.ingest({'deviceId':'Synthetic'})
        self.a.save();self.assertTrue(self.a.status()['storage']['ok'])
    def test_cache_failure_retains_updated_memory(self):
        real=adapter.atomic_json
        def fail(path,value):
            if Path(path).name=='cache.json':raise OSError('synthetic')
            real(path,value)
        with patch('adapter.atomic_json',side_effect=fail):
            with self.assertRaises(StorageError): self.a.refresh()
        self.assertTrue(self.a.cache_dirty);self.assertIn('/api/stats',self.a.cache)
        self.a.save();self.assertFalse(self.a.cache_dirty)
    def test_request_registers_result_before_final_save(self):
        writes=[];real=adapter.atomic_json
        with patch('adapter.atomic_json',side_effect=lambda path,value:(writes.append(Path(path).name),real(path,value))[1]):self.a.refresh()
        self.assertEqual(writes,['cache.json','metrics.json'])
        self.assertEqual(next(iter(self.a.metrics['sync_history'].values()))['download']['successes'],1)
    def test_monotonic_deadline_ignores_wall_clock_jump(self):
        target=time.time()+600
        self.assertFalse(self.a.deadline_due('test',target))
        with patch('adapter.time.time',return_value=target+9999):
            self.assertFalse(self.a.deadline_due('test',target))
        with patch('adapter.time.monotonic',return_value=self.a.deadlines['test'][1]+1):
            self.assertTrue(self.a.deadline_due('test',target))
    def test_scheduler_internal_backoff_and_reset(self):
        waits=[];n=[0]
        def cycle():
            n[0]+=1
            if n[0]<=6: raise RuntimeError('do not expose this text')
            self.a.scheduler_health.update(consecutive_errors=0,error_category=None,message=None)
            self.a.stop.set()
        with patch.object(self.a,'scheduler_cycle',side_effect=cycle),patch.object(self.a.stop,'wait',side_effect=lambda seconds:waits.append(seconds)):
            self.a.scheduler()
        self.assertEqual(waits,[5,10,20,40,60,60,5])
        self.assertEqual(self.a.scheduler_health['consecutive_errors'],0)
    def test_storage_is_not_network_failure(self):
        def cycle():self.a.stop.set();raise StorageError('private path must not leak')
        with patch.object(self.a,'scheduler_cycle',side_effect=cycle):self.a.scheduler()
        self.assertEqual(self.a.scheduler_health['error_category'],'storage')
        self.assertEqual(self.a.scheduler_health['message'],'本地保存失败')
    def test_dead_thread_restart_without_duplicate(self):
        runs=[];started=threading.Event()
        def loop():runs.append(threading.get_ident());started.set();self.a.stop.wait(2)
        with patch.object(self.a,'scheduler',side_effect=loop):
            for _ in range(20):self.a.ensure_scheduler()
            self.assertTrue(started.wait(1));self.assertEqual(len(runs),1)
            old=self.a.scheduler_thread;self.a.stop.set();old.join(2)
            self.a.stop.clear();started.clear();self.a.ensure_scheduler();self.assertTrue(started.wait(1))
            self.assertEqual(len(runs),2);self.assertEqual(self.a.scheduler_health['restarts'],1)
            self.a.stop.set();self.a.scheduler_thread.join(2)
    def test_pre_attempt_save_failure_preserves_target_and_does_not_send(self):
        self.a.ingest({'deviceId':'Synthetic'})
        target=self.a.status()['next_upload_at'];calls=[]
        self.a.transport=lambda *args:calls.append(args) or {'ok':True}
        with patch('adapter.atomic_json',side_effect=OSError('synthetic')):
            with self.assertRaises(StorageError):self.a.upload_pending(manual=True)
        self.assertFalse(self.a.upload_in_progress);self.assertEqual(calls,[])
        self.assertEqual(self.a.status()['next_upload_at'],target)
        self.a.save();self.assertEqual(self.a.upload_pending(manual=True),'success')
    def test_success_baseline_saved_before_failed_pending_clear(self):
        self.a.ingest({'deviceId':'Synthetic','trackedClients':['synthetic'],'allTime':{'totalTokens':17}})
        self.a.transport=lambda *args:{'ok':True};real=adapter.atomic_json
        def fail(path,value):
            if Path(path).name=='pending.json' and value is None:raise OSError('synthetic')
            real(path,value)
        with patch('adapter.atomic_json',side_effect=fail):
            with self.assertRaises(StorageError):self.a.upload_pending(manual=True)
        self.assertIsNotNone(self.a.pending);self.assertEqual(self.a.upload_failures,0)
        self.a.save();restored=Adapter(self.cfg,self.temp.name)
        self.assertEqual(restored.pending_token_summary()['value'],0)
        self.assertEqual(restored.metrics['token_baseline']['total_tokens'],17)
    def test_local_http_failure_is_503(self):
        import http.client
        server=adapter.Server(('127.0.0.1',0),adapter.Handler);server.adapter=self.a;self.a.local_port=server.server_port
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        self.a.local_secret_provider=lambda:'synthetic'
        with patch('adapter.atomic_json',side_effect=OSError('private disk error')):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port)
            c.request('POST','/api/ingest',encode({'deviceId':'Synthetic'}),{'Authorization':'Bearer synthetic'})
            r=c.getresponse();self.assertEqual(r.status,503);self.assertEqual(json.loads(r.read()),{'error':'local_save_failed'});c.close()

if __name__=='__main__':unittest.main()
