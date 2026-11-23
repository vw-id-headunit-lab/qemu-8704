
import copy
import unittest

import ufs_boot_luns as boot

class OriginalBootLuns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inventory=boot.inventory()
        cls.index=boot.archive_index()

    def test_plan_keeps_raw_extents_and_no_well_known_lun(self):
        d=boot.make_plan(self.inventory,self.index)
        self.assertTrue(d['read_only'])
        self.assertFalse(d['rpmb'] or d['boot_wlun'])
        self.assertEqual([(l['lun'],l['bytes']) for l in d['luns']],[(1,12398592),(2,12398592)])
        for l in d['luns']:
            self.assertEqual(sum('payload' in e for e in l['extents']),8)
            self.assertEqual(l['extents'][4]['extent'],81920)
            self.assertNotIn('payload',l['extents'][4])
            self.assertEqual(l['extents'][-1]['payload']['bytes'],4042752)

    def test_plan_rejects_unexpected_physical_offset(self):
        d=copy.deepcopy(self.inventory)
        next(e for e in d['entries'] if e['xml_lun']==2)['block_offset']=1
        with self.assertRaises(ValueError):
            boot.make_plan(d,self.index)

if __name__=='__main__':
    unittest.main()

