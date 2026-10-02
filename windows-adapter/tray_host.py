"""Windows notification-area UI for the existing loopback adapter."""
import ctypes as C
from ctypes import wintypes as W
import json
from pathlib import Path
import sys
import threading
import time
import urllib.request
import webbrowser


def run(root, open_window, exit_app, quitting=lambda:False):
    config = json.loads((root / 'config.json').read_text(encoding='utf-8'))
    url = f"http://127.0.0.1:{config['port']}"
    user32 = C.WinDLL('user32', use_last_error=True)
    shell32 = C.WinDLL('shell32', use_last_error=True)
    kernel32 = C.WinDLL('kernel32', use_last_error=True)
    LRESULT = C.c_ssize_t
    CALLBACK = C.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)

    class WNDCLASS(C.Structure):
        _fields_ = [('style', W.UINT), ('lpfnWndProc', CALLBACK), ('cbClsExtra', C.c_int),
                    ('cbWndExtra', C.c_int), ('hInstance', W.HINSTANCE), ('hIcon', W.HICON),
                    ('hCursor', W.HANDLE), ('hbrBackground', W.HBRUSH), ('lpszMenuName', W.LPCWSTR),
                    ('lpszClassName', W.LPCWSTR)]

    class GUID(C.Structure):
        _fields_ = [('Data1', W.DWORD), ('Data2', W.WORD), ('Data3', W.WORD), ('Data4', C.c_byte * 8)]

    class NOTIFYICON(C.Structure):
        _fields_ = [('cbSize', W.DWORD), ('hWnd', W.HWND), ('uID', W.UINT), ('uFlags', W.UINT),
                    ('uCallbackMessage', W.UINT), ('hIcon', W.HICON), ('szTip', W.WCHAR * 128),
                    ('dwState', W.DWORD), ('dwStateMask', W.DWORD), ('szInfo', W.WCHAR * 256),
                    ('uVersion', W.UINT), ('szInfoTitle', W.WCHAR * 64), ('dwInfoFlags', W.DWORD),
                    ('guidItem', GUID), ('hBalloonIcon', W.HICON)]

    kernel32.GetModuleHandleW.restype = W.HMODULE
    kernel32.CreateMutexW.argtypes = [W.LPVOID, W.BOOL, W.LPCWSTR]
    kernel32.CreateMutexW.restype = W.HANDLE
    user32.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.RegisterClassW.argtypes = [C.POINTER(WNDCLASS)]
    user32.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, C.c_int, C.c_int, C.c_int, C.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]
    user32.CreateWindowExW.restype = W.HWND
    user32.LoadImageW.argtypes = [W.HINSTANCE, W.LPCWSTR, W.UINT, C.c_int, C.c_int, W.UINT]
    user32.LoadImageW.restype = W.HANDLE
    user32.CreatePopupMenu.restype = W.HMENU
    user32.AppendMenuW.argtypes = [W.HMENU, W.UINT, C.c_size_t, W.LPCWSTR]
    user32.TrackPopupMenu.argtypes = [W.HMENU, W.UINT, C.c_int, C.c_int, C.c_int, W.HWND, W.LPVOID]
    user32.TrackPopupMenu.restype = W.UINT
    user32.DestroyMenu.argtypes = [W.HMENU]
    user32.SetForegroundWindow.argtypes = [W.HWND]
    user32.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    user32.DestroyWindow.argtypes = [W.HWND]
    shell32.Shell_NotifyIconW.argtypes = [W.DWORD, C.POINTER(NOTIFYICON)]
    shell32.Shell_NotifyIconW.restype = W.BOOL

    mutex = kernel32.CreateMutexW(None, False, 'Local\\TokenMonitorExeTray'+str(config['port']))
    if C.get_last_error() == 183:
        sys.exit(0)
    icons = {name: user32.LoadImageW(None, str(root / ('icon-' + name + '.ico')), 1, 32, 32, 0x10)
             for name in ('green', 'amber', 'red')}
    if not all(icons.values()):
        raise RuntimeError('Tray icon files unavailable')
    icon = NOTIFYICON()
    icon.cbSize = C.sizeof(icon)
    icon.uID = 1
    icon.uFlags = 1 | 2 | 4
    icon.uCallbackMessage = 0x8001
    icon.hIcon = icons['amber']
    icon.szTip = 'Token Monitor 热点同步：正在读取状态'
    status = {}
    stop = threading.Event()
    taskbar_created = user32.RegisterWindowMessageW('TaskbarCreated')

    def refresh_remote():
        try:
            request = urllib.request.Request(url + '/adapter/refresh', method='POST',
                                             headers={'Origin': url, 'X-Adapter-Action': 'refresh'})
            with urllib.request.urlopen(request, timeout=18) as response:
                response.read()
        except Exception:
            pass
        poll_once()

    def poll_once():
        nonlocal status
        try:
            with urllib.request.urlopen(url + '/adapter/status', timeout=4) as response:
                status = json.load(response)
        except Exception:
            status = {'state': 'adapter_unavailable'}
        user32.PostMessageW(icon.hWnd, 0x8002, 0, 0)

    def poll():
        while not stop.is_set():
            if quitting():
                user32.PostMessageW(icon.hWnd,0x10,0,0);return
            poll_once()
            for _ in range(40):
                if stop.wait(.5) or quitting(): break

    @CALLBACK
    def wndproc(hwnd, message, wparam, lparam):
        if message == taskbar_created:
            shell32.Shell_NotifyIconW(0, C.byref(icon))
            return 0
        if message == 0x8002:
            state = status.get('state', 'waiting')
            color = 'red' if state == 'adapter_unavailable' else 'green' if status.get('health_level') == 'ok' else 'amber'
            label = '适配器未运行' if state == 'adapter_unavailable' else status.get('health_label', '等待首次同步')
            if status.get('pending_upload'):
                label += '，' + {'queued': '等待定时上报', 'uploading': '正在上报', 'retry': '等待重试'}.get(status.get('upload_phase'), '有待上报数据')
            at = status.get('last_success_at')
            tip = 'Hub 中转：' + label
            if at:
                tip += '\n最近下载 ' + time.strftime('%H:%M:%S', time.localtime(at)) + ' · 周期 '+str(status.get('interval_seconds',600))+'秒'
            icon.szTip, icon.hIcon = tip[:127], icons[color]
            visible = bool(shell32.Shell_NotifyIconW(1, C.byref(icon)))
            # Sanitized diagnostic receipt; never includes credentials or Hub payloads.
            from os import getpid
            (root / 'tray-status.json').write_text(json.dumps({'pid': getpid(), 'icon_registered': visible,
                'color': color, 'state': state, 'tooltip': tip, 'checked_at': time.time()}, ensure_ascii=False), encoding='utf-8')
            return 0
        if message == 0x8001:
            if lparam == 0x203:  # Double-click.
                open_window()
            elif lparam == 0x205:  # Right-click.
                menu = user32.CreatePopupMenu()
                for command, label in [(1, '打开窗口'), (2, '立即同步远端'), (3, '退出适配器')]:
                    user32.AppendMenuW(menu, 0, command, label)
                point = W.POINT()
                user32.GetCursorPos(C.byref(point))
                user32.SetForegroundWindow(hwnd)
                command = user32.TrackPopupMenu(menu, 0x100 | 0x2, point.x, point.y, 0, hwnd, None)
                user32.DestroyMenu(menu)
                user32.PostMessageW(hwnd, 0, 0, 0)
                if command == 1:
                    open_window()
                elif command == 2:
                    threading.Thread(target=refresh_remote, daemon=True).start()
                elif command == 3:
                    exit_app()
                    user32.DestroyWindow(hwnd)
            return 0
        if message == 2:
            stop.set()
            shell32.Shell_NotifyIconW(2, C.byref(icon))
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    instance = kernel32.GetModuleHandleW(None)
    wc = WNDCLASS()
    wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = wndproc, instance, 'TokenMonitorHotspotTrayWindow'
    if not user32.RegisterClassW(C.byref(wc)):
        raise C.WinError(C.get_last_error())
    icon.hWnd = user32.CreateWindowExW(0, wc.lpszClassName, 'Token Monitor 热点同步', 0, 0, 0, 0, 0, None, None, instance, None)
    if not icon.hWnd or not shell32.Shell_NotifyIconW(0, C.byref(icon)):
        raise C.WinError(C.get_last_error())
    threading.Thread(target=poll, daemon=True).start()
    message = W.MSG()
    while user32.GetMessageW(C.byref(message), None, 0, 0) > 0:
        user32.TranslateMessage(C.byref(message))
        user32.DispatchMessageW(C.byref(message))
