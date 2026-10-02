"""Inspect the source release allowlist without printing any matched data."""
from pathlib import Path
import re
import sys

ALLOWED={'adapter.py','desktop.py','settings.py','tray_host.py','local_ipc.py','dashboard.html',
         'test_adapter.py','test_settings.py','test_runtime.py','test_ipc.py','requirements-build.txt','TokenMonitorAdapter.spec',
         '.gitignore','config.example.json','README.md','RELEASE.md','check_release.py'}
PATTERN=re.compile(rb'github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]{0,16}PRIVATE KEY-----')
def check(root):
    findings=[]
    for name in ALLOWED:
        p=root/name
        if not p.is_file(): findings.append(name+': missing release source');continue
        if PATTERN.search(p.read_bytes()): findings.append(name+': credential-like content')
    assets=list((root/'assets').glob('*'))
    if sorted(p.name for p in assets)!=['icon-amber.ico','icon-green.ico','icon-red.ico']: findings.append('Unexpected asset files')
    for line in findings: print(line)
    return bool(findings)
if __name__=='__main__':sys.exit(check(Path(__file__).parent))
