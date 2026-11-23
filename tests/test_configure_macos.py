"""Synthetic path fixtures; these tests do not validate OEM image contents."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('configure_macos', Path(__file__).resolve().parents[1] / 'tools/configure_macos.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ConfigureTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name).resolve()

    def fixture(self):
        files = [
            'runtime/darwin/qemu-system-aarch64', 'runtime/darwin/pc-bios/keymaps/en-us',
            'src/hvf_bench/runtime/alpine/vmlinuz-virt', 'src/hvf_bench/runtime/alpine/initramfs-virt',
            'src/hvf_bench/runtime/alpine/modloop-virt', 'src/hvf_bench/build/bench-platform.jar',
            'src/hvf_bench/runtime/mesa/mesa-runtime.tar.gz', 'src/hvf_bench/build/libegl-forward-bridge.so',
            'src/hvf_bench/runtime/novnc/core/rfb.js',
            'var/inputs/analysis/cpu/ivi/icas3cn-gp-image-euto-v9.ext4',
            'var/inputs/hmi/vw-restored/vw-hmi.ext4',
            'var/inputs/support/vw-verity-native/overlay-verity/lg-custom.ext4',
            'var/inputs/support/abox-dsp-native/virtual-lun0-dsp/ufs-lun0.raw',
        ]
        for name in files:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'FIXTURE_NOT_FIRMWARE')
        rows = []
        for index in range(4):
            path = self.root / f'src/hvf_android/runtime/modules/module{index}.ko'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'FIXTURE_NOT_MODULE')
            rows.append({'path': '/OLD_ROOT/' + path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        (path.parent / 'provenance.json').write_text(json.dumps(rows))
        config = self.root / 'config/hvf-8704.json'
        config.parent.mkdir()
        config.write_text('ORIGINAL_CONFIG')
        return config

    def test_missing_inputs_do_not_write(self):
        result, status = module.configure(self.root)
        self.assertEqual(status, 2)
        self.assertFalse(result['ready'])
        self.assertFalse((self.root / 'config').exists())

    def test_invalid_resource_key(self):
        self.assertEqual(module.configure(self.root, '１２３')[1], 2)

    def test_relocation_and_input_preservation(self):
        config = self.fixture()
        before = {p: p.read_bytes() for p in (self.root / 'var/inputs').rglob('*') if p.is_file()}
        result, status = module.configure(self.root)
        self.assertEqual(status, 0)
        self.assertTrue(result['ready'])
        self.assertEqual(config.with_suffix('.json.before-configure').read_text(), 'ORIGINAL_CONFIG')
        self.assertEqual(json.loads(config.read_text())['qemu'], str(self.root / 'runtime/darwin/qemu-system-aarch64'))
        self.assertEqual((self.root / 'var/hvf/android-misc.raw').stat().st_size, 1048576)
        profile = json.loads((self.root / 'src/hvf_bench/local/vehicle-profile.json').read_text())
        self.assertFalse(profile['native_diagnosis'])
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_active_checkout_not_reconfigured(self):
        config = self.fixture()
        output = self.root / 'var/hvf'
        output.mkdir()
        (output / 'current.json').write_text('{}')
        self.assertEqual(module.configure(self.root)[1], 2)
        self.assertEqual(config.read_text(), 'ORIGINAL_CONFIG')

    def test_bad_module_not_accepted(self):
        config = self.fixture()
        (self.root / 'src/hvf_android/runtime/modules/module0.ko').write_bytes(b'CHANGED')
        self.assertEqual(module.configure(self.root)[1], 2)
        self.assertEqual(config.read_text(), 'ORIGINAL_CONFIG')

    def test_personal_profile_not_overwritten(self):
        self.fixture()
        path = self.root / 'src/hvf_bench/local/vehicle-profile.json'
        path.parent.mkdir(parents=True)
        path.write_text('PERSONAL_PROFILE')
        self.assertEqual(module.configure(self.root)[1], 0)
        self.assertEqual(path.read_text(), 'PERSONAL_PROFILE')


if __name__ == '__main__':
    unittest.main()
