
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/emu'))
import original_8704_vm as vm
import qnx_bts_platform as bts
import qnx_dp0_platform as dp
import qnx_dsi_platform as dsi
import decon_platform as wod
from ufs_platform import QTest


def run(qemu,out,dp_decon=False,dp_windows=False,qnx_bts=False,dp_empty_crc=False,linux_wod=False):
    out.mkdir()
    qport,tport=vm.free_port(),vm.free_port()
    board=('virt,virtualization=on,gic-version=2,x-exynos-vlm=on,x-exynos-dp0-reset=1,'
           'x-exynos-dp0-phy=1,x-exynos-dp0-link-config=1,x-exynos-dp0-irq=1,x-exynos-dp0-aux-sink=1')
    if dp_decon:
        board+=',x-exynos-qnx-dsi=1'
    if dp_windows:
        board+=',x-exynos-qnx-dpp-irq=1'
    if qnx_bts:
        board+=',x-exynos-qnx-bts=1'
    if linux_wod:
        board+=',x-exynos-linux-dpp-irq=1,x-exynos-linux-decon-window=1'
    cmd=[str(qemu),'-machine',board,'-cpu','cortex-a57','-accel','qtest','-m','128',
         '-display','none','-monitor','none','-nic','none','-serial','null','-S',
         '-qmp',f'tcp:127.0.0.1:{qport},server=on,wait=off',
         '-qtest',f'tcp:127.0.0.1:{tport},server=on,wait=off']
    env=os.environ.copy()
    env['PATH']=str(ROOT/'.local/deps/bin')+os.pathsep+env['PATH']
    with (out/'qemu.log').open('xb') as log:
        proc=subprocess.Popen(cmd,env=env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
        qmp=qt=None
        try:
            for _ in range(100):
                if proc.poll() is not None:
                    raise RuntimeError(f'QEMU exited with {proc.returncode}')
                try:
                    qmp=vm.QMP(qport)
                    break
                except OSError:
                    time.sleep(.05)
            if not qmp:
                raise TimeoutError('QMP unavailable')
            qt=QTest(tport)
            qmp.execute('cont')
            base,phy=dp.BASES
            rd=lambda off:qt.read(base+off)
            wr=lambda off,v:qt.write(base+off,v)
            step=lambda ns:qt.call(f'clock_step {ns}')
            get=lambda key:qmp.execute('qom-get',{'path':dp.PATH,'property':key})

            def aux(op,addr,n,tx=None,fail=False):
                if not 1<=n<=16:
                    raise ValueError('AUX test transaction length outside 1..16')
                wr(0x1008,1)
                if tx is not None:
                    wr(0x1030,int.from_bytes(tx,'little'))
                wr(0x1010,(op<<28)|(addr<<8)|(n-1))
                wr(0x1004,1)
                assert rd(0x1004)==1
                step(4000)
                assert rd(0x1004)==0
                assert bool(rd(0x1018)&0xf00)==fail
                return b''.join(rd(0x1040+i).to_bytes(4,'little') for i in range(0,n,4))[:n]

            assert aux(9,0,3,fail=True)==bytes(3)
            wr(4,1); step(500)
            with dp.factory_file(dp.SOURCE).open('rb') as source:
                source.seek(dp.MEMBER_OFFSET)
                original=dp.Elf(source.read(dp.MEMBER_BYTES))
            for off,val in dp.phy_defaults(original):
                qt.write(phy+off,val)
            for off,val in ((0x24,0x68),(0x2d0,0),(0x288,0xff),(0x854,0),
                            (0x1054,0),(0x1854,0),(0x2054,0),(0x38c,0xa),(0x38c,0xe)):
                qt.write(phy+off,val)
            step(5000)
            assert get('pll-locked')==1
            wr(0x10c,0xf00)
            assert rd(0x28)&0x100==0
            step(1_000_000)
            assert rd(0x28)&0x100 and rd(0x108)==0x100 and get('hpd-irq-level')==1
            wr(0x108,0x100)
            assert rd(0x28)&0x100 and rd(0x108)==0 and get('hpd-irq-level')==0

            assert aux(9,0,3)==bytes((0x12,0x0a,0x84))
            assert aux(9,0x60,15)==bytes(15) and aux(9,0x90,1)==b'\0'
            assert aux(9,0x202,3)==bytes(3) and get('link-trained')==0
            wr(0x10,4); wr(0x3000,0x11)
            for lane in range(4):
                for i,value in enumerate((33,16,66,229)):
                    off=phy+0x810+0x800*lane+4*i
                    qt.write(off,value)
                    assert qt.read(off)==value
            assert get('phy-tune-writes')==16
            for off in (0x5000,0x6000,0x7000,0x8000):
                wr(off,0x20)
                assert rd(off)==0x20
            aux(8,0x100,3,bytes((6,0x84,0x21)))
            assert aux(9,0x202,3)==bytes(3)
            step(50_000)
            assert aux(9,0x202,3)==bytes((0x11,0x11,0))
            wr(0x3000,0x21); aux(8,0x102,1,b'\x22'); step(50_000)
            assert aux(9,0x202,3)==bytes((0x77,0x77,1))
            assert get('link-trained')==0
            wr(0x3000,0); aux(8,0x102,1,b'\0')
            assert get('link-trained')==1
            aux(9,0x68000,1,fail=True)                                
            aux(4,0x50,1,b'\0')
            edid=b''.join(aux(5 if i<7 else 1,0x50,16) for i in range(8))
            assert edid[:8]==bytes.fromhex('00ffffffffffff00') and sum(edid)%256==0
            assert (edid[56]+((edid[58]&0xf0)<<4),edid[59]+((edid[61]&0xf0)<<4))==(1560,700)
            for off,val in ((0x7400,0x90),(0x7420,1560),(0x7430,700),(0x30,1),(0x2030,1)):
                wr(off,val)
                assert rd(off)==val
            assert rd(0x7434)==0 and get('sst2-timing-writes')==3
            assert get('sst2-timing-420')==1560 and get('sst2-timing-430')==700
            assert get('source-enable-sst2-value')==1 and get('sst1-timing-writes')==0
            assert get('source-functions-sst2-value')==1 and rd(0x20)==0
            wr(0x702c,0x180); wr(0x7028,0xffffffff)
            assert rd(0x702c)==0x180 and rd(0x7028)==0 and rd(0x602c)==0
            wr(0x7404,1)
            assert rd(0x7404)==1 and get('sst2-video-enable')==1 and get('sst1-video-enable')==0
            state=dp.runtime(qmp,True,True,True,True)
            decon=None
            wod_state=None
            if dp_decon:
                gic=next(row['name'] for row in qmp.execute('qom-list',{'path':'/machine/unattached'})
                         if row['type']=='child<arm_gic>')
                qt.call('irq_intercept_in /machine/unattached/'+gic)
                main=dsi.DPUS_MAIN
                update=dsi.DPUS_GLOBAL
                dget=lambda k:qmp.execute('qom-get',{'path':dsi.DECON_PATH,'property':k})
                for off,val in ((0x7044,37281),(0x7414,1700),(0x7418,731)):
                    wr(off,val)
                qt.write(main+0x120,(700<<16)|1560)
                qt.write(main+0x230,8)
                qt.write(main+0x40,0x1001)
                qt.write(main,3)
                qt.write(update,1<<31); step(10_000)
                assert not dget('running-2') and dget('rejected-updates-2')==1
                qt.write(dsi.DPUS_DP0_ROUTE,0)
                qt.write(update,1<<31); step(10_000)
                assert dget('running-2')==dget('active-dp-2')==1
                assert qt.read(dsi.DPUS_SHADOW+0x120)==((700<<16)|1560)
                period=1700*731*10**9//74562000
                assert dget('timing-ns-2')==period
                if linux_wod:
                    end=(699<<16)|1559
                    for i,addr in enumerate(wod.LINUX_WINDOWS):
                        ctl=addr+0x20000
                        qt.write(addr+0x10,end-i)
                        qt.write(addr+0x14,0x100)
                        qt.write(ctl,0x11)
                        qt.write(ctl+0x10,2)
                        qt.write(ctl+0x14,1)
                        assert qt.read(ctl+0x14)==0
                        qt.write(ctl+0x10,0)
                        qt.write(ctl+0x14,1)
                        qt.write(ctl+0x14,0)
                        assert qt.read(ctl+0x14)==0
                        qt.write(ctl+0x14,1)
                        qt.write(ctl+0x14,1)
                        assert qt.read(addr+0x14)==0x100
                        assert qt.read(addr+0x810)==0 and qt.read(ctl+0x14)==1
                step(period-1)
                assert qt.read(main+0x4c)==0
                if linux_wod:
                    assert all(qt.read(base+0x20014)==1 for base in wod.LINUX_WINDOWS)
                step(1)
                assert qt.read(main+0x4c)==0x1000 and 'IRQ raise 131' in qt.events
                if linux_wod:
                    for i,addr in enumerate(wod.LINUX_WINDOWS):
                                                                       
                        assert qt.read(addr+0x20014)==int(i==0)
                        assert qt.read(addr+0x810)==(end-i if i else 0)
                    wod_state=wod.linux_runtime(qmp)
                    for i,row in enumerate(wod_state.values()):
                        assert (row['wincon-requests'],row['wincon-commits'],
                                row['wincon-active-control'])==(2,int(i!=0),0x11 if i else 0)
                qt.write(main+0x4c,0x1000)
                assert qt.read(main+0x4c)==0 and 'IRQ lower 131' in qt.events
                if dp_empty_crc:
                    for pipe,crc_base in enumerate((*dsi.DECON_MAIN,main)):
                        for color in range(3):
                            qt.write(crc_base+0x290,(color<<16)|1)
                            assert qt.read(crc_base+0x290)==((color<<16)|1)
                            for off in dsi.DECON_CRC_WORDS:
                                assert qt.read(crc_base+off)==0
                        assert dget(f'crc-empty-reads-{pipe}')==12 and qt.read(crc_base+0x2a0)==0
                if dp_windows:
                                                                           
                                                                             
                    image_size=(700<<16)|1560
                    for off,value in ((8,0),(0x10,image_size),(0x14,0),(0x18,image_size),(0x40,0x90100000)):
                        qt.write(0x1aa68000+off,value)
                    qt.write(0x1aa28018,image_size)
                    for win in dsi.DPUS_WINDOWS:
                        addr=0x1aa30000+0x1000*win
                        ctl=addr+0x20000
                        qt.write(addr+0x0c,(3<<16)|5)
                        qt.write(addr+0x10,(699<<16)|1559)
                        qt.write(ctl,0x71 if win==0 else 1)
                        qt.write(ctl+0x14,1)
                        assert qt.read(ctl+0x14)==1
                        step(10_000)
                        assert qt.read(ctl+0x14)==0 and qt.read(addr+0x810)==((699<<16)|1559)
                        assert dget(f'dpus0-window-{win}-owner')==2
                    assert dget('dpus0-window-0-source-address')==0x90100000
                    assert dget('dpus0-window-0-source-output-size')==image_size
                    assert dget('dpus0-window-0-source-writes')==1
                    assert dget('dpus0-window-7-source-address')==0
                    qt.write(0x1aa68040,0x90200000)
                    assert dget('dpus0-window-0-source-address')==0x90100000
                    assert dget('window-0-owner')==0xffffffff
                    qt.write(0x1aa30010,(700<<16)|1560)
                    qt.write(0x1aa50014,1); step(10_000)
                    assert dget('dpus0-window-0-rejected-updates')==1
                    assert qt.read(0x1aa30810)==((699<<16)|1559)
                wr(0x2030,0); step(period)
                assert qt.read(main+0x4c)==0 and qt.read(main+0x2a0)==0
                assert dget('frame-start-acks-2')==1 and dget('frame-ticks-2')==1
                if linux_wod:
                    addr=wod.LINUX_WINDOWS[-1]
                    qt.write(addr+0x20014,1)
                    step(period)
                    assert qt.read(addr+0x20014)==1                           
                    qt.write(main,1<<28); step(2_000)
                    assert qt.read(addr+0x20014)==qt.read(addr+0x810)==0
                decon=dict(profile=dsi.policy(True,True)['decon']['dpus0'],
                           runtime=dsi.decon_runtime(qmp),scope='timing only, no pixels')
            wr(0x1010,0x90000000); wr(0x1004,1); wr(4,1); step(5000)
            assert rd(0x1004)==0 and not rd(0x28)&0x100 and get('link-trained')==0
            assert rd(0x7400)==rd(0x7404)==rd(0x7420)==rd(0x7430)==rd(0x2030)==0
            assert get('aux-replies')==state['aux-replies']
            bts_state=None
            trex_state=None
            qos_state=None
            if qnx_bts:
                abi=bts.policy(True)
                for i,addr in enumerate(abi['addresses']):
                    assert qt.read(addr)==0
                    qt.write(addr,(min(100,63)<<16)|((i+1)<<24))
                for i,addr in enumerate(abi['addresses']):
                    assert qt.read(addr)==((63<<16)|((i+1)<<24))
                qt.write(abi['addresses'][0],0x80000000)
                bts_state=bts.runtime(qmp)
                for i in range(len(abi['addresses'])):
                    assert bts_state[f'dmc{i}']==dict(limit=(63<<16)|((i+1)<<24),
                        reads=2,writes=1,rejected=int(i==0))
                for base in bts.TREX_BASES:
                    for off,expected in bts.TREX_INIT:
                        assert qt.read(base+off)==0
                        qt.write(base+off,expected)
                        assert qt.read(base+off)==expected
                qt.write(bts.TREX_BASES[1]+0x100,0x99999999)
                trex_state=bts.trex_runtime(qmp)
                for i in range(len(bts.TREX_BASES)):
                    assert trex_state[f'trex{i}']==dict(reads=10,writes=5,rejected=int(i==1))
                for i,base in enumerate(bts.QOS_BASES):
                    for off,mask in reversed(bts.QOS_MASKS):
                        assert qt.read(base+off)==0
                        value=mask&((i+1)*0x01010101)
                        qt.write(base+off,value)
                        assert qt.read(base+off)==value
                qt.write(bts.QOS_BASES[0]+0x24,0xffff0000)
                qt.read(bts.QOS_BASES[0]+4)
                qos_state=bts.qos_runtime(qmp)
                n=len(bts.QOS_BASES)*len(bts.QOS_MASKS)
                assert qos_state==dict(reads=2*n,writes=n,rejected=2,configured=len(bts.QOS_BASES))
            vm.save(out/'result.json',dict(passed=True,qemu_sha256=vm.sha(qemu),
                exact_abi=dp.policy(True,True,True,True,True),runtime=state,edid_hex=edid.hex(),
                decon=decon,linux_wod=wod_state,bts=bts_state,trex=trex_state,qos=qos_state,
                transcript=qt.transcript,firmware_started=False))
            return state
        finally:
            if qt:
                qt.close()
            if qmp:
                qmp.close()
            proc.terminate()
            proc.wait(timeout=8)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('qemu',type=Path)
    p.add_argument('output',type=Path)
    p.add_argument('--dp-decon',action='store_true')
    p.add_argument('--dp-windows',action='store_true')
    p.add_argument('--qnx-bts',action='store_true')
    p.add_argument('--dp-empty-crc',action='store_true')
    p.add_argument('--linux-wod',action='store_true')
    a=p.parse_args()
    if (a.dp_windows or a.dp_empty_crc or a.linux_wod) and not a.dp_decon:
        p.error('DP windows, Linux WOD and empty CRC latches require --dp-decon')
    print(run(a.qemu.resolve(strict=True),a.output.resolve(),a.dp_decon,a.dp_windows,a.qnx_bts,a.dp_empty_crc,a.linux_wod))
