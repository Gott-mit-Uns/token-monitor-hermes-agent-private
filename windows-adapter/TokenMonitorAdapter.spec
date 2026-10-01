from pathlib import Path
root = Path(SPECPATH)
a = Analysis([str(root / 'desktop.py')], pathex=[str(root)],
    binaries=[], datas=[(str(root/'dashboard.html'),'.'),(str(root/'assets'),'assets')],
    hiddenimports=['webview.platforms.edgechromium','webview.platforms.winforms'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PyQt5','PyQt6','PySide2','PySide6','tkinter','cefpython3'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,a.binaries,a.datas,[],name='TokenMonitorAdapter',
    debug=False,bootloader_ignore_signals=False,strip=False,upx=False,
    console=False,disable_windowed_traceback=True,icon=str(root/'assets'/'icon-green.ico'))
