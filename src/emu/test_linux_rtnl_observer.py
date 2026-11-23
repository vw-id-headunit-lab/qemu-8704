import unittest

import linux_rtnl_observer
from linux_pid1_context import LINUX_SHA


class LinuxRtnlObserverTests(unittest.TestCase):
    def setUp(self):
        self.mutex = 0xffffffc0112069a0
        self.owner = 0xffffff8047c63900
        self.pid = 0xffffff8047004000
        self.ns = 0xffffffc01109af80
        self.pc = 0xffffffc010862c30
        self.symbols = dict(sha256=LINUX_SHA, symbols=[
            dict(name=name, address=hex(addr), type=typ) for name, addr, typ in (
                ('rtnl_mutex', self.mutex, 'D'), ('init_pid_ns', self.ns, 'D'),
                ('_text', 0xffffffc010080000, 'T'), ('_etext', 0xffffffc010b80000, 'T'),
                ('rtnl_lock', self.pc, 'T'))])
        name = b'net-owner'.ljust(16, bytes(1))
        self.words = {self.mutex: self.owner | 1, self.owner + 0x20: 0,
                      self.owner + 0x570: self.pid, self.pid + 0x58: self.ns,
                      self.pid + 0x50: 42,
                      self.owner + 0x6c0: int.from_bytes(name[:8], 'little'),
                      self.owner + 0x6c8: int.from_bytes(name[8:], 'little')}

    def test_only_uses_current_task_from_oem_linux_el1(self):
        def cpu(vmid, pstate, tag):
            return dict(thread=tag, system=dict(VTTBR_EL2=hex(vmid << 48), SP_EL0=hex(self.owner)),
                        core=dict(cpsr=hex(pstate), pc=hex(self.pc + 4), x30='0x0'))
        r = linux_rtnl_observer.capture(self.symbols, self.words.__getitem__,
                                       [cpu(4, 5, 'android'), cpu(3, 9, 'el2'), cpu(3, 5, 'linux')])
        self.assertEqual((r['owner_pid'], r['owner_flags'], r['owner_comm']), (42, 1, 'net-owner'))
        self.assertEqual(r['saved']['status'], 'runnable_or_running')
        self.assertEqual([(c['thread'], c['symbol'], c['offset']) for c in r['running_owner']],
                         [('linux', 'rtnl_lock', 4)])

    def test_no_owner_is_not_a_running_task(self):
        self.words[self.mutex] = 1
        self.assertEqual(linux_rtnl_observer.capture(self.symbols, self.words.__getitem__, [])['status'],
                         'no_owner_at_capture')


if __name__ == '__main__':
    unittest.main()
