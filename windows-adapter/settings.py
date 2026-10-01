"""User-local configuration and Windows DPAPI; never returns stored secrets to UI."""
import base64
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import shutil
import time
from urllib.parse import urlsplit
from adapter import atomic_json, UpstreamError

class Blob(C.Structure):
    _fields_=[('length',W.DWORD),('data',C.POINTER(C.c_ubyte))]

def protect(data, decrypt=False):
    if os.name != 'nt': raise RuntimeError('Windows required')
    buf=(C.c_ubyte*len(data)).from_buffer_copy(data)
    source=Blob(len(data),buf); result=Blob()
    crypt=C.WinDLL('crypt32',use_last_error=True)
    name='CryptUnprotectData' if decrypt else 'CryptProtectData'
    fn=getattr(crypt,name)
    fn.argtypes=[C.POINTER(Blob),C.c_void_p,C.c_void_p,C.c_void_p,C.c_void_p,W.DWORD,C.POINTER(Blob)]
    fn.restype=W.BOOL
    if not fn(C.byref(source),None,None,None,None,1,C.byref(result)): raise RuntimeError('Windows credential encryption failed')
    try: return C.string_at(result.data,result.length)
    finally:
        kernel=C.WinDLL('kernel32'); kernel.LocalFree.argtypes=[C.c_void_p]; kernel.LocalFree(C.cast(result.data,C.c_void_p))

def client_root(): return Path(os.environ['APPDATA'])/'Token Monitor'
def data_root(): return Path(os.environ['LOCALAPPDATA'])/'TokenMonitorHotspotAdapter'
def load(root):
    try: value=json.loads((Path(root)/'config.json').read_text(encoding='utf-8-sig'))
    except (OSError,ValueError): value={}
    original={}
    try: original=json.loads((client_root()/'settings.json').read_text(encoding='utf-8-sig'))
    except (OSError,ValueError): pass
    return {'upstream':'','port':17322,'device_id':original.get('deviceId','Desktop'),
            'credentials_file':str(client_root()/'credentials.json'),'interval_seconds':600,
            'upload_interval_ms':1800000,'theme':'system',**value}

def local_secret(config):
    try:
        d=json.loads(Path(config['credentials_file']).read_text(encoding='utf-8-sig'))
        v=d['credentials']['hub']['clientSecret']
        if not isinstance(v,str) or not v: raise ValueError()
        return v
    except (OSError,KeyError,ValueError): raise UpstreamError(503,{'error':'local_credential_unavailable'}) from None

def remote_secret(root,config):
    p=Path(root)/'remote-secret.bin'
    if p.exists():
        try: return protect(p.read_bytes(),True).decode('utf-8')
        except Exception: raise UpstreamError(503,{'error':'remote_credential_unavailable'}) from None
    # Existing installations remain compatible until the user supplies a new key.
    return local_secret(config)

def validate(value):
    u=urlsplit(value.get('upstream','').strip())
    if u.scheme!='https' or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError('请输入 HTTPS 服务器地址，不要在地址中包含密钥、查询参数或账号。')
    try: u.port
    except ValueError: raise ValueError('服务器端口无效。') from None
    download=int(value.get('interval_seconds',600)); upload=int(value.get('upload_interval_ms',1800000))
    if download not in (60,300,600,900,1800,3600) or upload not in (60000,300000,600000,900000,1800000,3600000):
        raise ValueError('请选择提供的同步周期。')
    theme=value.get('theme','system')
    if theme not in ('system','light','dark'): raise ValueError('主题无效。')
    return {'upstream':value['upstream'].strip().rstrip('/'),'interval_seconds':download,'upload_interval_ms':upload,'theme':theme}

def save(root,value,key=''):
    root=Path(root); root.mkdir(parents=True,exist_ok=True); old=load(root); new={**old,**validate(value)}
    for name, legacy in [('interval_seconds', 3600), ('upload_interval_ms', 3600000)]:
        if new[name] == legacy and old.get(name) != legacy:
            raise ValueError('60 分钟仅用于保留原配置，请选择新的同步周期。')
    if new['upstream']!=old['upstream']:
        try: pending=json.loads((root/'pending.json').read_text(encoding='utf-8'))
        except FileNotFoundError: pending=None
        if pending: raise ValueError('仍有待上报数据，请完成旧服务器上报后再切换地址。')
    if key:
        if not isinstance(key,str) or len(key)>8192: raise ValueError('密钥长度无效。')
        temp=root/'remote-secret.bin.tmp'; temp.write_bytes(protect(key.encode())); os.replace(temp,root/'remote-secret.bin')
    elif not (root/'remote-secret.bin').exists():
        # Encrypt the existing client's key locally; never add it to config.json.
        (root/'remote-secret.bin').write_bytes(protect(local_secret(old).encode()))
    atomic_json(root/'config.json',new)
    return new

def connect_client(root,config):
    path=client_root()/'settings.json'
    if not path.exists(): raise ValueError('请先运行并配置 Token Monitor 客户端。')
    local_secret(config)
    d=json.loads(path.read_text(encoding='utf-8-sig'))
    backup=Path(root)/'backups'; backup.mkdir(parents=True,exist_ok=True)
    shutil.copy2(path,backup/('settings-before-exe-'+str(time.time_ns())+'.json'))
    d['hubUrl']='http://127.0.0.1:'+str(config['port']); d['syncUploadIntervalMs']=0
    atomic_json(path,d)
    return '已备份并接入 Token Monitor；请重启 Token Monitor 客户端使设置生效。'

def autostart(enabled,executable):
    import winreg
    target=Path(os.environ['LOCALAPPDATA'])/'Programs'/'TokenMonitorAdapter'/'TokenMonitorAdapter.exe'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
        if enabled:
            try:
                existing=winreg.QueryValueEx(key,'TokenMonitorHotspotAdapter')[0]
                if existing.startswith('"'+str(Path(executable))+'" '): target=Path(executable)
            except FileNotFoundError: pass
            target.parent.mkdir(parents=True,exist_ok=True)
            if Path(executable).resolve()!=target.resolve(): shutil.copy2(executable,target)
            winreg.SetValueEx(key,'TokenMonitorHotspotAdapter',0,winreg.REG_SZ,'"'+str(target)+'" --background')
        else:
            try: winreg.DeleteValue(key,'TokenMonitorHotspotAdapter')
            except FileNotFoundError: pass
    return target

def startup_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run') as k:
            return 'TokenMonitorAdapter.exe' in winreg.QueryValueEx(k,'TokenMonitorHotspotAdapter')[0]
    except FileNotFoundError: return False
