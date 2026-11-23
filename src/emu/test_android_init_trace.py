import unittest

import android_init_trace as trace
from ufs_mailbox_platform import android_dtb
from vlm_platform import parse_dtb


class AndroidInitTrace(unittest.TestCase):
    def test_original_resource_tree_unchanged_except_debug_argument(self):
        raw=android_dtb()
        before=parse_dtb(raw)
        out,meta=trace.boot_dtb(raw)
        after=parse_dtb(out)
        self.assertEqual(after['/chosen']['bootargs'],
                         before['/chosen']['bootargs'].rstrip(b'\0 ').rstrip()+b' initcall_debug\0')
        after['/chosen']['bootargs']=before['/chosen']['bootargs']
        self.assertEqual(after,before)
        self.assertNotEqual(meta['parent_sha256'],meta['diagnostic_sha256'])
        self.assertFalse(meta['boot_policy_changed'])
        with self.assertRaises(ValueError):
            trace.boot_dtb(out)

    def test_pair_completed_and_unreturned_records(self):
        rows=[{'text':x} for x in ('calling  0xffa0 @ 1','initcall 0xffa0 returned -2 after 1 usecs',
                                    'calling  0xffa4 @ 1','Run /init as init process')]
        s=trace.summarize(rows)
        self.assertEqual(s['completed'],[dict(function='0xffa0',pid=1,ret=-2)])
        self.assertEqual(s['without_observed_return'],[dict(function='0xffa4',pid=1)])
        self.assertEqual(s['pid1_exec_log'],['Run /init as init process'])

