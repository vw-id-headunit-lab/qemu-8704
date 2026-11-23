import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ufs_dsp_media as dsp


class DspMediaTests(unittest.TestCase):
    def test_oem_exact_two_slots_per_present_core(self):
        plan=dsp.expected_plan()
        baseline=dsp.android.expected_plan()
        self.assertEqual(dsp.disk.gpt(plan),dsp.disk.gpt(baseline))
        orig={r['slot']:r for r in baseline['partitions']}
        allslots={r['slot']:r for r in plan['partitions']}
        for slot,(label,core) in dsp.CORES.items():
            row=allslots[slot]
            self.assertEqual(row['label'],label)
            self.assertEqual(row['payload']['member'],dsp.source_name(core))
            self.assertEqual(row['payload']['bytes'],1593344)
        for slot in orig.keys()-dsp.CORES.keys():
            self.assertEqual(allslots[slot],orig[slot])
        self.assertEqual(plan['profile'],dsp.PROFILE)

    def test_sparse_transfer_includes_holes_in_hash(self):
        data=b'ABCD'+bytes(8)+b'xy'
        target=io.BytesIO()
        with patch.object(dsp.disk,'CHUNK',4):
            self.assertEqual(dsp.transfer_sparse(io.BytesIO(data),target,len(data)),hashlib.sha256(data).hexdigest())
            self.assertEqual(target.getvalue(),data)
            with self.assertRaises(EOFError):
                dsp.transfer_sparse(io.BytesIO(data),target,len(data)+1)

    def test_nonzero_core3_or_padding_is_rejected(self):
        sectors=dsp.disk.SECTOR
        plan={'partitions':[dict(slot=5,label='a_audio0fw',first=0,last=0,payload={'bytes':4}),
                            dict(slot=11,label='a_audio3fw',first=1,last=1)]}
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'readback'
            raw=b'BOOT'+bytes(2*sectors-4)
            p.write_bytes(raw)
            self.assertEqual(dsp.zero_extents(p,plan),[dict(slot=5,zero_bytes=sectors-4),dict(slot=11,zero_bytes=sectors)])
            for off in (4,sectors):
                p.write_bytes(raw[:off]+b'\x01'+raw[off+1:])
                with self.assertRaises(ValueError):
                    dsp.zero_extents(p,plan)

