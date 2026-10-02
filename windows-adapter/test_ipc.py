import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
import uuid
import local_ipc as ipc

class IpcTests(unittest.TestCase):
    def setUp(self):
        self.allowed=set();self.calls=[]
        self.address=r'\\.\pipe\TokenMonitorAdapter-test-'+uuid.uuid4().hex
        def dispatch(method,value):self.calls.append(method);return {'ok':True,'version':'synthetic'}
        self.server=ipc.PipeServer(self.address,lambda pid:pid in self.allowed,dispatch)
        self.server.start();self.addCleanup(self.server.close)
    def test_registered_child_json_only(self):
        script="import local_ipc,sys; r=local_ipc.call(sys.argv[1],int(sys.argv[2]),'get_settings'); assert r['ok']; print('ok')"
        child=subprocess.Popen([getattr(sys,'_base_executable',sys.executable),'-c',script,self.address,str(os.getpid())],stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=Path(__file__).parent)
        self.allowed.add(child.pid)
        output,error=child.communicate(timeout=10)
        self.assertEqual(child.returncode,0,error.decode(errors='replace'));self.assertEqual(output.strip(),b'ok')
        self.assertEqual(self.calls,['get_settings'])
    def test_unregistered_same_user_rejected(self):
        with ipc.PipeClient(self.address) as c:
            with self.assertRaises((EOFError,OSError)):
                c.send_bytes(b'{"method":"get_settings"}');c.recv_bytes(65536)
        self.assertEqual(self.calls,[])
    def test_unknown_method_rejected(self):
        self.allowed.add(os.getpid())
        with ipc.PipeClient(self.address) as c:
            c.send_bytes(b'{"method":"execute"}')
            with self.assertRaises((EOFError,OSError)):c.recv_bytes(65536)
        self.assertEqual(self.calls,[])
    def test_wrong_server_pid_rejected(self):
        self.allowed.add(os.getpid())
        with self.assertRaisesRegex(RuntimeError,'settings_peer_rejected'):ipc.call(self.address,os.getpid()+1,'get_settings')
    def test_user_sid_and_ui_job_cleanup(self):
        self.assertTrue(ipc.user_sid().startswith('S-1-'))
        job=ipc.UiJob();self.addCleanup(job.close)
        child=subprocess.Popen([getattr(sys,'_base_executable',sys.executable),'-c','import time;time.sleep(60)'],creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            job.assign(child);job.close();child.wait(timeout=3)
            self.assertIsNotNone(child.poll())
        finally:
            if child.poll() is None:child.terminate();child.wait(3)
    def test_tray_module_does_not_import_webview(self):
        child=subprocess.run([getattr(sys,'_base_executable',sys.executable),'-c',"import desktop,sys;assert 'webview' not in sys.modules;assert 'clr' not in sys.modules"],cwd=Path(__file__).parent,capture_output=True,timeout=5)
        self.assertEqual(child.returncode,0)

if __name__=='__main__':unittest.main()

