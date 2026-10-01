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
from adapter import Adapter,Server,Handler,atomic_json
import settings

VERSION='0.1.5'
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
        K.WaitForSingleObject(stop,0xffffffff); a.stop.set(); server.shutdown()
    threading.Thread(target=watch,daemon=True).start()
    scheduler=threading.Thread(target=a.scheduler,daemon=True) if cfg['upstream'] else None
    if scheduler: scheduler.start()
    atomic_json(Path(root)/'process.json',{'pid':os.getpid(),'version':VERSION,'started_at':time.time()})
    try: server.serve_forever(poll_interval=.3)
    finally:
        a.stop.set()
        if scheduler: scheduler.join(timeout=15)
        a.save(); server.server_close(); K.CloseHandle(stop)

class Host:
    def __init__(self,root):
        self.root=root; self.child=None; self.window=None; self.quitting=False; self.lock=threading.RLock()
        self.worker_stop=event(root,'worker-stop',True); self.open_event=event(root,'show')
        self.quit_event=event(root,'quit')
    def spawn(self):
        K.ResetEvent(self.worker_stop)
        self.child=subprocess.Popen(command()+['--worker','--root',str(self.root)],creationflags=subprocess.CREATE_NO_WINDOW)
    def restart(self):
        with self.lock:
            if self.child and self.child.poll() is None:
                K.SetEvent(self.worker_stop)
                try: self.child.wait(timeout=18)
                except subprocess.TimeoutExpired: raise ValueError('后台仍有请求，请稍后重试。')
            self.spawn()
    def watch(self):
        while not self.quitting:
            if K.WaitForSingleObject(self.quit_event,0)==0:
                self.quit(); return
            if K.WaitForSingleObject(self.open_event,500)==0: self.show()
            with self.lock:
                if not self.quitting and self.child and self.child.poll() is not None:
                    time.sleep(2)
                    if not self.quitting: self.spawn()
    def show(self):
        if self.window: self.window.show(); self.window.restore()
    def quit(self):
        self.quitting=True; K.SetEvent(self.worker_stop)
        if self.child:
            try: self.child.wait(timeout=18)
            except subprocess.TimeoutExpired: pass
        if self.window: self.window.destroy()
    def closing(self):
        if self.quitting: return True
        self.window.hide(); return False

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
    atomic_json(Path(root)/'self-test-result.json',{'ok':True,'version':VERSION,'frozen':bool(getattr(sys,'frozen',False))})

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=settings.data_root());p.add_argument('--background',action='store_true');p.add_argument('--worker',action='store_true');p.add_argument('--self-test',action='store_true');p.add_argument('--quit',action='store_true');args=p.parse_args()
    root=args.root;root.mkdir(parents=True,exist_ok=True)
    if args.quit: K.SetEvent(event(root,'quit')); return
    if args.self_test: return self_test(root)
    if args.worker: return worker(root)
    mutex,existing=instance_mutex(root)
    if existing:
        if not args.background: K.SetEvent(event(root,'show'))
        K.CloseHandle(mutex)
        return
    cfg=settings.load(root)
    try:
        with socket.socket() as s: s.bind(('127.0.0.1',cfg['port']))
    except OSError:
        message('本机端口 '+str(cfg['port'])+' 已被占用。请先退出旧适配器或占用该端口的程序。');return
    for name in ('dashboard.html',): shutil.copy2(assets()/name,root/name)
    for name in ('green','amber','red'): shutil.copy2(assets()/'assets'/('icon-'+name+'.ico'),root/('icon-'+name+'.ico'))
    atomic_json(root/'config.json',cfg)
    host=Host(root);host.spawn()
    for _ in range(100):
        try: status(root);break
        except Exception: time.sleep(.1)
    else: message('后台无法启动，请检查本机端口和数据目录。');host.quit();return
    import webview
    import tray_host
    window=webview.create_window('Token Monitor Adapter','http://127.0.0.1:'+str(cfg['port'])+'/adapter',js_api=Bridge(host),width=560,height=680,min_size=(440,520),hidden=args.background)
    host.window=window;window.events.closing+=host.closing
    threading.Thread(target=host.watch,daemon=True).start()
    threading.Thread(target=tray_host.run,args=(root,host.show,host.quit),daemon=True).start()
    try: webview.start(gui='edgechromium',private_mode=True)
    except Exception:
        message('窗口无法启动，请安装 Microsoft Edge WebView2 Runtime 后重试。下载说明：https://developer.microsoft.com/microsoft-edge/webview2/');host.quit()
    finally:
        if not host.quitting: host.quit()
        K.CloseHandle(mutex)

if __name__=='__main__': main()
