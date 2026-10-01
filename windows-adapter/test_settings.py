import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import settings
from adapter import atomic_json

class SettingsTests(unittest.TestCase):
    def test_duplicate_instance_uses_ctypes_last_error(self):
        import desktop
        with tempfile.TemporaryDirectory() as name:
            first,existing=desktop.instance_mutex(name)
            second=None
            try:
                self.assertFalse(existing)
                second,existing=desktop.instance_mutex(name)
                self.assertTrue(existing)
            finally:
                if second: desktop.K.CloseHandle(second)
                desktop.K.CloseHandle(first)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.value={'upstream':'https://example.invalid','interval_seconds':600,'upload_interval_ms':1800000,'theme':'dark'}
    def tearDown(self): self.temp.cleanup()
    def test_dpapi_roundtrip_and_no_plaintext(self):
        key=b'synthetic-secret-for-tests'; blob=settings.protect(key)
        self.assertNotIn(key,blob);self.assertEqual(settings.protect(blob,True),key)
    def test_save_config_contains_no_key(self):
        c=settings.save(self.root,self.value,'synthetic-secret')
        self.assertNotIn('secret',(self.root/'config.json').read_text())
        self.assertEqual(settings.remote_secret(self.root,c),'synthetic-secret')
    def test_save_preserves_registered_executable_location(self):
        exe=self.root/'chosen-location'/'TokenMonitorAdapter.exe'
        exe.parent.mkdir();exe.write_bytes(b'synthetic executable')
        with patch('winreg.CreateKey'),patch('winreg.QueryValueEx',return_value=('"'+str(exe)+'" --background',1)),patch('winreg.SetValueEx') as save,patch('settings.shutil.copy2') as copy:
            self.assertEqual(settings.autostart(True,exe),exe)
            copy.assert_not_called()
            self.assertEqual(save.call_args.args[-1],'"'+str(exe)+'" --background')
    def test_pending_blocks_server_change_without_touching_key(self):
        settings.save(self.root,self.value,'old-synthetic-key')
        old=(self.root/'remote-secret.bin').read_bytes()
        atomic_json(self.root/'pending.json',{'deviceId':'Synthetic Desktop'})
        with self.assertRaises(ValueError): settings.save(self.root,{**self.value,'upstream':'https://other.invalid'},'new-synthetic-key')
        self.assertEqual((self.root/'remote-secret.bin').read_bytes(),old)
    def test_reject_credentials_in_address_and_bad_periods(self):
        for url in ['http://example.invalid','https://u:p@example.invalid','https://example.invalid?key=synthetic']:
            with self.assertRaises(ValueError): settings.validate({**self.value,'upstream':url})
        with self.assertRaises(ValueError): settings.validate({**self.value,'interval_seconds':0})
    def test_connect_client_backup_and_local_route(self):
        client=self.root/'client';client.mkdir()
        atomic_json(client/'settings.json',{'hubUrl':'https://example.invalid','deviceId':'Synthetic Desktop'})
        with patch('settings.client_root',return_value=client),patch('settings.local_secret',return_value='synthetic'):
            settings.connect_client(self.root,{'port':17322,'upload_interval_ms':1800000})
        d=json.loads((client/'settings.json').read_text());self.assertEqual(d['syncUploadIntervalMs'],1800000)
        self.assertEqual(d['hubUrl'],'http://127.0.0.1:17322');self.assertEqual(len(list((self.root/'backups').glob('*.json'))),1)

if __name__=='__main__':unittest.main(verbosity=2)
