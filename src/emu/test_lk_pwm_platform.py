
import inspect
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import lk_pwm_platform as pwm
import lk_runtime
import patch_qemu_exynos as source


class PwmBindingTests(unittest.TestCase):
    def test_original_polling_addresses_and_frequency_boundary(self):
        result=pwm.contract()
        self.assertEqual(result['base'],'0x103f0000')
        self.assertEqual(result['virtual_input_hz'],24000000)
        self.assertFalse(result['physical_clock_verified']);self.assertFalse(result['irqs_connected'])
        self.assertFalse(result['external_pwm_implemented']);self.assertFalse(result['factory_images_modified'])

    def test_upstream_source_guard(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'changed';p.write_bytes(b'changed')
            with patch.object(pwm,'PWM',p):
                with self.assertRaisesRegex(ValueError,'model changed'):pwm.contract()

    def test_native_delay_validation_defaults_off(self):
        self.assertFalse(inspect.signature(lk_runtime.native_stage).parameters['pwm_delay'].default)

    def test_pwm_build_only_changes_selected_source(self):
        old="system_ss.add(when: 'CONFIG_EXYNOS4', if_true: files('exynos4210_pwm.c'))\n"
        actual=source.edits('hw/timer/meson.build',old)
        self.assertIn("CONFIG_EXYNOS4210_PWM",actual)
        self.assertIn("files('exynos4210_pwm.c')",actual)
        self.assertEqual(actual.count('files('),1)

    def test_pwm_configuration_selects_real_ptimer(self):
        result=source.edits('hw/timer/Kconfig','config EXISTING\n    bool\n')
        self.assertIn('config EXYNOS4210_PWM\n    bool\n    select PTIMER',result)


if __name__=='__main__':unittest.main()
