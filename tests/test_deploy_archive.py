import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import types
import zipfile

class DeployArchiveTests(unittest.TestCase):
    def setUp(self):
        source=Path(__file__).parents[1]/'bin/nexus-deploy.py'
        spec=importlib.util.spec_from_file_location('deploy_helper',source)
        self.module=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'fcntl':types.SimpleNamespace()}):spec.loader.exec_module(self.module)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
    def payload(self,extra=None):
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as archive:
            for name in ['server.py','worker.py','publisher.py','common.py']:archive.writestr('app/'+name,'x=1\n')
            archive.writestr('app/static/index.html','hello')
            for name in extra or []:archive.writestr(name,'x=1\n')
        return buffer.getvalue()
    def test_valid_sources(self):
        self.module.unpack(self.payload(),Path(self.tmp.name))
        self.assertTrue((Path(self.tmp.name)/'app/server.py').is_file())
    def test_reject_control_files_traversal_and_unrelated_sources(self):
        for name in ['Dockerfile','compose.yaml','.env','app/../bad.py','app/subdir/x.py','app/bad.exe']:
            with self.assertRaises(ValueError):self.module.unpack(self.payload([name]),Path(self.tmp.name))
