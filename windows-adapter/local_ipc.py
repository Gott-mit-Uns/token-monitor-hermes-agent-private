"""Current-user-only named pipes; JSON bytes, never pickle or command-line secrets."""
import ctypes as C
from ctypes import wintypes as W
import json
import os
import threading
import time
import _winapi
from multiprocessing.connection import PipeListener, PipeClient

K=C.WinDLL('kernel32',use_last_error=True)
A=C.WinDLL('advapi32',use_last_error=True)
K.GetCurrentProcess.restype=W.HANDLE
K.CloseHandle.argtypes=[W.HANDLE]
K.LocalFree.argtypes=[W.LPVOID]
A.OpenProcessToken.argtypes=[W.HANDLE,W.DWORD,C.POINTER(W.HANDLE)]
A.GetTokenInformation.argtypes=[W.HANDLE,C.c_int,W.LPVOID,W.DWORD,C.POINTER(W.DWORD)]
A.ConvertSidToStringSidW.argtypes=[W.LPVOID,C.POINTER(W.LPWSTR)]
A.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[W.LPCWSTR,W.DWORD,C.POINTER(W.LPVOID),W.LPVOID]
K.CreateNamedPipeW.argtypes=[W.LPCWSTR,W.DWORD,W.DWORD,W.DWORD,W.DWORD,W.DWORD,W.DWORD,W.LPVOID]
K.CreateNamedPipeW.restype=W.HANDLE
K.GetNamedPipeClientProcessId.argtypes=[W.HANDLE,C.POINTER(W.ULONG)]
K.GetNamedPipeServerProcessId.argtypes=[W.HANDLE,C.POINTER(W.ULONG)]

class SecurityAttributes(C.Structure):
    _fields_=[('length',W.DWORD),('descriptor',W.LPVOID),('inherit',W.BOOL)]

def user_sid():
    token=W.HANDLE();size=W.DWORD();text=W.LPWSTR()
    if not A.OpenProcessToken(K.GetCurrentProcess(),8,C.byref(token)): raise C.WinError(C.get_last_error())
    try:
        A.GetTokenInformation(token,1,None,0,C.byref(size))
        buffer=C.create_string_buffer(size.value)
        if not A.GetTokenInformation(token,1,buffer,size,C.byref(size)): raise C.WinError(C.get_last_error())
        sid=C.cast(buffer,C.POINTER(W.LPVOID))[0]
        if not A.ConvertSidToStringSidW(sid,C.byref(text)): raise C.WinError(C.get_last_error())
        try: return text.value
        finally: K.LocalFree(C.cast(text,W.LPVOID))
    finally: K.CloseHandle(token)

class SecureListener(PipeListener):
    def _new_handle(self,first=False):
        descriptor=W.LPVOID()
        if not A.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:P(A;;GA;;;'+user_sid()+')',1,C.byref(descriptor),None):
            raise C.WinError(C.get_last_error())
        try:
            sa=SecurityAttributes(C.sizeof(SecurityAttributes),descriptor,False)
            flags=3|0x40000000|(0x80000 if first else 0)
            # Reject remote clients even if they authenticate as the same user.
            handle=K.CreateNamedPipeW(self._address,flags,4|2|8,255,65536,65536,0,C.byref(sa))
            if handle==C.c_void_p(-1).value: raise C.WinError(C.get_last_error())
            return handle
        finally: K.LocalFree(descriptor)

class PipeServer:
    def __init__(self,address,authorized,dispatch):
        self.address=address;self.authorized=authorized;self.dispatch=dispatch
        self.stop=threading.Event();self.listener=SecureListener(address)
        self.thread=threading.Thread(target=self.run,daemon=True)
    def start(self): self.thread.start()
    def run(self):
        while not self.stop.is_set():
            try:
                connection=self.listener.accept()
                with connection:
                    pid=W.ULONG()
                    if not K.GetNamedPipeClientProcessId(connection.fileno(),C.byref(pid)) or not self.authorized(pid.value): continue
                    if not connection.poll(10): continue
                    request=json.loads(connection.recv_bytes(65536))
                    if not isinstance(request,dict) or request.get('method') not in ('get_settings','save_settings','exit_app'): continue
                    value=request.get('value',{})
                    if not isinstance(value,dict): continue
                    result=self.dispatch(request['method'],value)
                    connection.send_bytes(json.dumps(result,ensure_ascii=False).encode('utf-8'))
            except (OSError,EOFError,ValueError,TypeError): pass
        self.listener.close()
    def close(self):
        self.stop.set()
        try: PipeClient(self.address).close()  # Wake blocked accept; PID is rejected.
        except OSError: pass
        self.thread.join(timeout=2)

def call(address,parent,method,value=None):
    for attempt in range(20):
        try:
            with PipeClient(address) as connection:
                pid=W.ULONG()
                if not K.GetNamedPipeServerProcessId(connection.fileno(),C.byref(pid)) or pid.value!=parent:
                    raise RuntimeError('settings_peer_rejected')
                connection.send_bytes(json.dumps({'method':method,'value':value or {}},ensure_ascii=False).encode('utf-8'))
                if not connection.poll(40): raise RuntimeError('settings_timeout')
                return json.loads(connection.recv_bytes(65536))
        except (OSError,EOFError):
            if attempt==19: raise RuntimeError('settings_unavailable') from None
            time.sleep(.1)

# A private job owns the UI and all WebView2 descendants. Closing it cannot kill
# the tray, sync worker, or another application's browser processes.
class BasicLimits(C.Structure):
    _fields_=[('process_time',C.c_longlong),('job_time',C.c_longlong),('flags',W.DWORD),
              ('minimum',C.c_size_t),('maximum',C.c_size_t),('active',W.DWORD),
              ('affinity',C.c_size_t),('priority',W.DWORD),('scheduling',W.DWORD)]
class IoCounters(C.Structure):
    _fields_=[(n,C.c_ulonglong) for n in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
class ExtendedLimits(C.Structure):
    _fields_=[('basic',BasicLimits),('io',IoCounters),('process_memory',C.c_size_t),('job_memory',C.c_size_t),
              ('peak_process',C.c_size_t),('peak_job',C.c_size_t)]
K.CreateJobObjectW.argtypes=[W.LPVOID,W.LPCWSTR];K.CreateJobObjectW.restype=W.HANDLE
K.SetInformationJobObject.argtypes=[W.HANDLE,C.c_int,W.LPVOID,W.DWORD]
K.AssignProcessToJobObject.argtypes=[W.HANDLE,W.HANDLE]
class UiJob:
    def __init__(self):
        self.handle=K.CreateJobObjectW(None,None)
        limits=ExtendedLimits();limits.basic.flags=0x2000
        if not self.handle or not K.SetInformationJobObject(self.handle,9,C.byref(limits),C.sizeof(limits)):
            self.close();raise RuntimeError('ui_job_unavailable')
    def assign(self,child):
        if not K.AssignProcessToJobObject(self.handle,int(child._handle)): raise RuntimeError('ui_job_assignment_failed')
    def close(self):
        if self.handle: K.CloseHandle(self.handle);self.handle=None
