"""Single-file Windows host, supervised worker, local-only settings bridge."""
import argparse
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from adapter import Adapter,Server,Handler,atomic_json,StorageError
import settings

VERSION='0.1.7'
K=C.WinDLL('kernel32',use_last_error=True)
K.CreateEventW.argtypes=[W.LPVOID,W.BOOL,W.BOOL,W.LPCWSTR]; K.CreateEventW.restype=W.HANDLE
K.CreateMutexW.argtypes=[W.LPVOID,W.BOOL,W.LPCWSTR]; K.CreateMutexW.restype=W.HANDLE
K.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD]; K.WaitForSingleObject.restype=W.DWORD
K.SetEvent.argtypes=[W.HANDLE]; K.ResetEvent.argtypes=[W.HANDLE]
K.CloseHandle.argtypes=[W.HANDLE]

def event(root,name,manual=False):
    import hashlib
    suffix=hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:20]
    return K.CreateEventW(None,manual,False,'Local\\TokenMonitorAdapter-'+suffix+'-'+name)
def assets(): return Path(getattr(sys,'_MEIPASS',Path(__file__).parent))
def command(): return [sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).resolve())]
def status(root):
    port=settings.load(root)['port']
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/adapter/status',timeout=3) as r: return json.load(r)
def message(text): C.windll.user32.MessageBoxW(None,text,'Token Monitor Adapter',0x10)

def instance_mutex(root):
    import hashlib
    C.set_last_error(0)
    handle=K.CreateMutexW(None,False,'Local\\TokenMonitorAdapter-'+hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:20])
    error=C.get_last_error()
    if not handle: raise OSError(error,'Unable to create adapter instance mutex')
    return handle,error==183

def worker(root):
    cfg=settings.load(root)
    a=Adapter(cfg,root,secret_provider=lambda:settings.remote_secret(root,cfg),local_secret_provider=lambda:settings.local_secret(cfg),version=VERSION)
    server=Server(('127.0.0.1',cfg['port']),Handler); server.adapter=a
    stop=event(root,'worker-stop',True)
    def watch():
        while K.WaitForSingleObject(stop,1000)!=0 and not a.stop.is_set():
            if cfg['upstream']:
                a.ensure_scheduler()
                request_age=max((time.monotonic()-r['monotonic'] for r in list(a.active_requests.values())),default=0)
                if max(time.monotonic()-a.heartbeat_monotonic,request_age)>120: break
        a.stop.set(); server.shutdown()
    if cfg['upstream']: a.ensure_scheduler()
    threading.Thread(target=watch,daemon=True).start()
    atomic_json(Path(root)/'process.json',{'pid':os.getpid(),'version':VERSION,'started_at':time.time()})
    try: server.serve_forever(poll_interval=.3)
    finally:
        a.stop.set()
        if a.scheduler_thread: a.scheduler_thread.join(timeout=15)
        try: a.save()
        except StorageError: pass
        server.server_close(); K.CloseHandle(stop)

class Host:
    def __init__(self,root):
        from local_ipc import PipeServer
        import uuid
        self.root=root; self.child=None; self.ui=None; self.ui_job=None; self.quitting=False; self.lock=threading.RLock()
        self.worker_stop=event(root,'worker-stop',True); self.open_event=event(root,'show'); self.quit_event=event(root,'quit')
        self.ui_stop=event(root,'ui-stop',True);self.ui_show=event(root,'ui-show')
        self.restart_index=0;self.restart_at=0;self.child_started=0;self.stopping_at=None;self.probe_failed_since=None
        self.recovery={'restarts':0,'phase':'running','message':None}
        self.pipe_name=r'\\.\pipe\TokenMonitorAdapter-'+uuid.uuid4().hex
        self.bridge=Bridge(self)
        self.pipe=PipeServer(self.pipe_name,self.authorized_ui,self.dispatch);self.pipe.start()
    def authorized_ui(self,pid):
        with self.lock: return self.ui is not None and self.ui.poll() is None and self.ui.pid==pid
    def dispatch(self,method,value):
        if method=='get_settings': return self.bridge.get_settings()
        if method=='save_settings': return self.bridge.save_settings(value)
        return self.bridge.exit_app()
    def record_recovery(self,**values):
        self.recovery.update(values)
        try: atomic_json(self.root/'recovery.json',self.recovery)
        except OSError: pass  # Worker exposes storage failures independently.
    def spawn(self):
        K.ResetEvent(self.worker_stop)
        self.child=subprocess.Popen(command()+['--worker','--root',str(self.root)],creationflags=subprocess.CREATE_NO_WINDOW)
        self.child_started=time.monotonic(); self.stopping_at=None; self.probe_failed_since=None
        self.record_recovery(phase='running',message=None)
    def stop_worker(self):
        if self.child and self.child.poll() is None:
            K.SetEvent(self.worker_stop)
            try: self.child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.child.terminate(); self.child.wait(timeout=5)
    def watch(self):
        while not self.quitting:
            try: self.monitor()
            except Exception:
                self.record_recovery(phase='error',message='后台监视异常，正在恢复')
                time.sleep(5)
    def monitor(self):
        last_probe=0
        while not self.quitting:
            if K.WaitForSingleObject(self.quit_event,0)==0: self.quit(); return
            if K.WaitForSingleObject(self.open_event,500)==0: self.show()
            with self.lock:
                if self.ui and self.ui.poll() is not None:
                    if self.ui_job: self.ui_job.close(); self.ui_job=None
                    self.ui=None
                now=time.monotonic()
                if self.child and self.child.poll() is None:
                    if now-self.child_started>=300: self.restart_index=0
                    if self.stopping_at is not None and now-self.stopping_at>=20:
                        self.child.terminate(); self.child.wait(timeout=5)
                    if now-last_probe>=5:
                        last_probe=now
                        try:
                            health=status(self.root).get('scheduler',{})
                            self.check_worker_health(now,health)
                        except Exception: self.check_worker_health(now,None)
                elif self.child:
                    if not self.restart_at:
                        delay=(2,5,10,30,60)[min(self.restart_index,4)]
                        self.restart_index+=1;self.restart_at=now+delay
                        self.record_recovery(phase='backoff',message='后台已退出，等待恢复',restarts=self.recovery['restarts']+1)
                    if now>=self.restart_at and not self.quitting:
                        self.restart_at=0;self.spawn()
    def check_worker_health(self,now,health):
        if health is None:
            if self.probe_failed_since is None:self.probe_failed_since=now
            stalled=now-self.probe_failed_since>120
        else:
            self.probe_failed_since=None
            stalled=health.get('stalled') and max(health.get('heartbeat_age_seconds',0),health.get('request_max_age_seconds',0))>120
        if stalled and self.stopping_at is None:
            self.stopping_at=now;K.SetEvent(self.worker_stop)
            self.record_recovery(phase='stopping',message='后台无进展，正在停止并恢复')
    def show(self):
        from local_ipc import UiJob
        with self.lock:
            if self.quitting: return
            if self.ui and self.ui.poll() is None: K.SetEvent(self.ui_show); return
            if self.ui_job: self.ui_job.close();self.ui_job=None
            K.ResetEvent(self.ui_stop)
            job=UiJob()
            try:
                child=subprocess.Popen(command()+['--ui','--root',str(self.root),'--pipe',self.pipe_name,'--parent',str(os.getpid())],creationflags=subprocess.CREATE_NO_WINDOW)
                job.assign(child);self.ui_job=job;self.ui=child
            except Exception:
                job.close()
                if 'child' in locals() and child.poll() is None: child.terminate();child.wait(timeout=5)
                message('窗口进程无法启动，请稍后重试。')
    def quit(self):
        with self.lock:
            if self.quitting: return
            self.quitting=True;K.SetEvent(self.ui_stop)
            self.stop_worker()
            if self.ui:
                try: self.ui.wait(timeout=8)
                except subprocess.TimeoutExpired: pass
            if self.ui_job: self.ui_job.close(); self.ui_job=None
        self.pipe.close()


class Bridge:
    def __init__(self,host): self._host=host
    def get_settings(self):
        c=settings.load(self._host.root)
        return {k:c[k] for k in ('upstream','interval_seconds','upload_interval_ms','theme')}|{'key_saved':(self._host.root/'remote-secret.bin').exists(),'autostart':settings.startup_enabled(),'version':VERSION}
    def exit_app(self):
        threading.Thread(target=self._host.quit,daemon=True).start()
        return {'ok':True}
    def save_settings(self,value):
        h=self._host
        try:
            # Drain in-flight work before checking pending or changing credentials.
            with h.lock:
                K.SetEvent(h.worker_stop)
                if h.child: h.child.wait(timeout=18)
                h.restart_at=0
                cfg=settings.save(h.root,value,value.get('secret',''))
                if value.get('autostart') and not getattr(sys,'frozen',False): raise ValueError('开机启动请使用 EXE 版本。')
                settings.autostart(bool(value.get('autostart')),sys.executable)
                note=settings.connect_client(h.root,cfg) if value.get('connect_client') else '设置已保存。'
                h.spawn()
            return {'ok':True,'message':note,'settings':self.get_settings()}
        except Exception as e:
            with h.lock:
                if h.child is None or h.child.poll() is not None: h.spawn()
            return {'ok':False,'message':str(e) if isinstance(e,ValueError) else '设置未完成，请检查本机客户端配置或稍后重试。'}

def self_test(root):
    cfg={'upstream':'https://example.invalid','device_id':'Synthetic Desktop','port':0,'interval_seconds':600}
    calls=[]
    def remote(method,path,body=None):
        calls.append(path)
        if path=='/api/stats': return {'devices':[],'periods':{}}
        return {'ok':True}
    with tempfile.TemporaryDirectory() as tmp:
        a=Adapter(cfg,tmp,transport=remote,secret_provider=lambda:'synthetic')
        a.refresh(); a.refresh(); assert calls==['/api/stats']
        blob=settings.protect(b'synthetic'); assert settings.protect(blob,True)==b'synthetic'
        a.ingest({'deviceId':'Synthetic Desktop','today':{'totalTokens':42}}); assert a.pending is not None
        a.upload_pending(manual=True); assert a.pending is None
        assert (assets()/'dashboard.html').exists()
    atomic_json(Path(root)/'self-test-result.json',{'ok':True,'version':VERSION,'frozen':bool(getattr(sys,'frozen',False)), 'background_imports_clean': 'webview' not in sys.modules and 'clr' not in sys.modules})

def ui_main(root,pipe,parent):
    # Imported only in the disposable UI process.
    import local_ipc
    try: local_ipc.call(pipe,parent,'get_settings')
    except Exception: message('无法连接托盘主进程，请重新打开窗口。');return
    import webview
    class UiBridge:
        def get_settings(self): return local_ipc.call(pipe,parent,'get_settings')
        def save_settings(self,value): return local_ipc.call(pipe,parent,'save_settings',value)
        def exit_app(self): return local_ipc.call(pipe,parent,'exit_app')
    cfg=settings.load(root)
    window=webview.create_window('Token Monitor Adapter','http://127.0.0.1:'+str(cfg['port'])+'/adapter',js_api=UiBridge(),width=560,height=680,min_size=(440,520))
    stop=event(root,'ui-stop',True);show=event(root,'ui-show');force=threading.Event();closed=threading.Event()
    close_requested=threading.Event()
    def closing():
        if force.is_set(): closed.set();return True
        if close_requested.is_set(): return False
        close_requested.set()
        def confirm():
            try:
                # FormClosing runs on the WinForms UI thread. Cancel it first,
                # then inspect JS off-thread so Invoke cannot deadlock the form.
                dirty=False
                if window.events.loaded.is_set():
                    try: dirty=window.evaluate_js('Boolean(window.adapterSettingsDirty)')
                    except Exception: dirty=True
                if dirty and C.windll.user32.MessageBoxW(None,'设置尚未保存。关闭窗口并丢弃修改吗？','Token Monitor Adapter',0x24)!=6: return
                force.set();window.destroy()
            finally: close_requested.clear()
        threading.Thread(target=confirm,daemon=True).start()
        return False
    window.events.closing+=closing
    def watch():
        while not closed.is_set():
            if K.WaitForSingleObject(stop,250)==0:
                force.set();window.destroy();return
            if K.WaitForSingleObject(show,0)==0: window.show();window.restore()
    threading.Thread(target=watch,daemon=True).start()
    try: webview.start(gui='edgechromium',private_mode=True)
    except Exception: message('窗口无法启动，请安装 Microsoft Edge WebView2 Runtime 后重试。')
    finally:
        closed.set();K.CloseHandle(stop);K.CloseHandle(show)
    # .NET finalizers must not keep a closed UI host alive; its private Job
    # is closed by the tray supervisor and cleans up any browser descendants.
    os._exit(0)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',type=Path,default=settings.data_root())
    for flag in ('background','worker','ui','self-test','quit'): p.add_argument('--'+flag,action='store_true')
    p.add_argument('--pipe');p.add_argument('--parent',type=int);args=p.parse_args()
    root=args.root;root.mkdir(parents=True,exist_ok=True)
    if args.quit: K.SetEvent(event(root,'quit'));return
    if args.self_test: return self_test(root)
    if args.worker: return worker(root)
    if args.ui: return ui_main(root,args.pipe,args.parent)
    mutex,existing=instance_mutex(root)
    if existing:
        if not args.background: K.SetEvent(event(root,'show'))
        K.CloseHandle(mutex);return
    cfg=settings.load(root)
    try:
        with socket.socket() as s: s.bind(('127.0.0.1',cfg['port']))
    except OSError:
        message('本机端口 '+str(cfg['port'])+' 已被占用。请先退出旧适配器或占用该端口的程序。');K.CloseHandle(mutex);return
    for name in ('dashboard.html',): shutil.copy2(assets()/name,root/name)
    for name in ('green','amber','red'): shutil.copy2(assets()/'assets'/('icon-'+name+'.ico'),root/('icon-'+name+'.ico'))
    atomic_json(root/'config.json',cfg)
    host=Host(root);host.spawn()
    for _ in range(100):
        try: status(root);break
        except Exception: time.sleep(.1)
    else: message('后台无法启动，请检查本机端口和数据目录。');host.quit();K.CloseHandle(mutex);return
    import tray_host
    threading.Thread(target=host.watch,daemon=True).start()
    if not args.background: host.show()
    try: tray_host.run(root,host.show,host.quit,lambda:host.quitting)
    finally: host.quit();K.CloseHandle(mutex)

if __name__=='__main__': main()
