import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('native_sources',Path(__file__).resolve().parents[1]/'tools/native_sources.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SourceCacheTests(unittest.TestCase):
    def test_download_checks_hash_before_installing(self):
        payload=b'SYNTHETIC_SOURCE_DOWNLOAD_FIXTURE'
        with tempfile.TemporaryDirectory() as folder:
            archive=Path(folder)/'source.tar.xz'
            with patch.object(module,'SOURCE_SHA256',hashlib.sha256(payload).hexdigest()), patch.object(module.urllib.request,'urlopen',return_value=io.BytesIO(payload)):
                module.ensure_archive(archive)
            self.assertEqual(archive.read_bytes(),payload)

    def test_bad_download_does_not_install(self):
        with tempfile.TemporaryDirectory() as folder:
            archive=Path(folder)/'source.tar.xz'
            with patch.object(module.urllib.request,'urlopen',return_value=io.BytesIO(b'INVALID_FIXTURE')):
                with self.assertRaises(ValueError):module.ensure_archive(archive)
            self.assertFalse(archive.exists())
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_existing_cache_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            archive=Path(folder)/'source.tar.xz';archive.write_bytes(b'EXISTING_FIXTURE')
            with patch.object(module.urllib.request,'urlopen') as network:
                module.ensure_archive(archive)
            network.assert_not_called()
            self.assertEqual(archive.read_bytes(),b'EXISTING_FIXTURE')


if __name__=='__main__':unittest.main()
