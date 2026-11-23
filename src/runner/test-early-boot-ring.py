
import json
from pathlib import Path
import runpy
import sys
import unittest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0,str(ROOT/'src/emu'))
OBSERVER = runpy.run_path(str(HERE/'early-boot-ring.py'))
FIXTURE = HERE/'oem-20261113-112609548'
SYMBOLS = ROOT/'var/inputs/symbols'


class NativeBootRing(unittest.TestCase):
    def test_real_terminal_chain_replay_and_scope(self):
        configs = OBSERVER['setup'](FIXTURE,SYMBOLS)
        rows = json.loads((FIXTURE/'dynamic-printk-captures.json').read_bytes())
        for row in rows:
            guest = row['guest']
            config = configs[guest]
            with self.subTest(guest=guest):
                memory = {}
                def store(name, value):
                    pa=config['addresses'][name]
                    memory[pa&~7]=memory.get(pa&~7,0)|(value<<((pa&7)*8))
                store('log_buf',int(row['buffer_virtual'],16))
                store('log_buf_len',row['bytes'])
                for name,value in row['ring'].items():
                    if name in ('first','next_index','first_seq','next_seq'):
                        symbol={'first':'log_first_idx','next_index':'log_next_idx'}.get(name,'log_'+name)
                        store(symbol,value)
                for pte in row['translation']['walk']:
                    memory[int(pte['pte_address'],16)]=int(pte['entry'],16)
                pa,size,indices=OBSERVER['locate'](config,memory.__getitem__)
                self.assertEqual((pa,size),(int(row['buffer_physical'],16),row['bytes']))
                self.assertEqual(indices['next_seq'],row['ring']['next_seq'])
                if guest=='android':
                    with self.assertRaisesRegex(ValueError,'outside original guest RAM'):
                        OBSERVER['locate'](dict(config,banks=config['banks'][:1]),memory.__getitem__)


if __name__=='__main__':
    unittest.main()
