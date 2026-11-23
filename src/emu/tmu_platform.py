
import argparse
import os
from pathlib import Path
import socket
import struct
import subprocess
import time
import original_8704_vm as vm


def machine_options(profile, temperature_c=25, acpm_clock_hz=0):
    if profile is None:
        if temperature_c!=25:raise ValueError('TMU temperature requires an explicit profile')
        return ''
    if profile not in ('virtual-onepoint-v1','virtual-qnx-controller-v1') or not 1000000<=acpm_clock_hz<=1000000000:
        raise ValueError('TMU requires explicit virtual profile and native management CPU')
    if not isinstance(temperature_c,int) or not -40<=temperature_c<=150:
        raise ValueError('Virtual TMU temperature must be integer -40..150C')
    return ',x-exynos-tmu-profile='+('2' if profile=='virtual-qnx-controller-v1' else '1')


def profile_policy(temperature_c=25):
    return {'profile':'virtual-onepoint-v1','temperature_c':temperature_c,
            'physical_trim_verified':False,'physical_temperature_verified':False,
            'factory_images_modified':False,'ready_value_injected':False,
            'calibration':'Declared virtual one-point ADC150 at25C; bit23=0 selects original one-point math, NOT invalid trim',
            'adc':'Original formula T=ADC-trim+25; virtual environment uses one ADC unit per Celsius degree',
            'sampling':{'first_conversion_ns':50,'period_ns':1000000,'resolution_c':1,
                        'timing_is_virtual':True,'input_changes_apply_on_conversion_not_read':True},
            'limits':'Uniform environment across two banks; only original trim/sample words exist; analog thermal, control and IRQ behavior absent'}


def original_contract():
    from acpm_firmware import original_image
    raw,meta=original_image()
    if meta['source_sha256']!='a83ca762143fab69a0724843bad958f39916ec261f7a65e4de615d82345a5a44':
        raise ValueError('Original TMU source BL2 identity mismatch')
    expected=((0,0,0x40,0),(0,2,0x44,0),(0,3,0x44,9),(0,4,0x44,18),
              (0,5,0x48,0),(0,6,0x48,9),(0,7,0x48,18),(0,8,0x440,0),
              (0,9,0x440,9),(0,11,0x444,9),(1,16,0x40,0),(1,18,0x44,0),
              (1,19,0x44,9),(1,20,0x44,18),(1,21,0x48,0),(1,22,0x48,9),
              (1,23,0x48,18),(1,24,0x440,0),(1,25,0x440,9),(1,26,0x444,0))
    sensors=[]
    for i,row in enumerate(expected):
        address=0xd1ac+14*i;data=raw[address:address+14]
        bank,sensor=data[:2];sample,shift=struct.unpack_from('<HH',data,10)
        if (bank,sensor,sample,shift)!=row:raise ValueError('Original TMU descriptor mismatch at '+hex(address))
        trim_index=sensor-16 if sensor>15 else sensor
                                                                   
                                                                        
        if trim_index>7:trim_index+=248
        sensors.append({'index':i,'local':hex(address),'bank':bank,'sensor':sensor,
            'ap_base':hex((0x4024+bank)<<14),'trim_offset':4*trim_index,
            'sample_offset':sample,'sample_shift':shift,'original_bytes':data.hex()})
    return raw,{'source':meta,'sensors':sensors,'initialization':'0xd02c','conversion':'0xcdc0',
                'scope':'Original software contract; virtual trim/temperature remain separate declared inputs'}


def conversion_evidence():

    from unicorn import Uc,UC_ARCH_ARM,UC_MODE_THUMB,UC_MODE_MCLASS
    from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_PC
    raw,contract=original_contract();rows=[]
    for temperature in (-40,14,20,25,70,125,150):
        emulator=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
        emulator.mem_map(0,0x40000);emulator.mem_write(0,raw)
        calibration=bytearray(14);struct.pack_into('<H',calibration,6,150)
        emulator.mem_write(0x3f000,bytes(calibration))
        emulator.reg_write(UC_ARM_REG_SP,0x3ff00);emulator.reg_write(UC_ARM_REG_LR,0x3fff1)
        emulator.reg_write(UC_ARM_REG_R0,0x3f000);emulator.reg_write(UC_ARM_REG_R1,150+temperature-25)
        emulator.emu_start(0xcdc1,0x3fff0,count=1000)
        value=emulator.reg_read(UC_ARM_REG_R0);value=value if value<0x80000000 else value-(1<<32)
        passed=value==temperature and emulator.reg_read(UC_ARM_REG_PC)==0x3fff0
        rows.append({'input_c':temperature,'adc_code':150+temperature-25,'original_return_c':value,'returned':emulator.reg_read(UC_ARM_REG_PC)==0x3fff0,'passed':passed})
        if not passed:raise ValueError('Original TMU one-point conversion mismatch')
    return {'contract':contract,'conversions':rows,'passed':True,
            'scope':'Unmodified isolated CDC0 function with explicit synthetic cal/input only; not native boot or physical temperature'}


def check_model(output,qemu=None):
    executable=(qemu or vm.ROOT/'runtime/qemu-system-aarch64.exe').resolve()
    qmp_port,test_port=vm.free_port(),vm.free_port()
    command=[str(executable),'-machine','virt-8704-apm,virtualization=on,gic-version=2,x-exynos-vlm=on,x-exynos-acpm=on,x-exynos-acpm-hz=96000000'+machine_options('virtual-onepoint-v1',25,96000000),
             '-cpu','cortex-a57','-accel','qtest','-smp','8','-m','128','-display','none','-serial','null','-serial','null','-monitor','none','-nic','none','-S',
             '-qmp',f'tcp:127.0.0.1:{qmp_port},server=on,wait=off','-qtest',f'tcp:127.0.0.1:{test_port},server=on,wait=off','-qtest-log',str(output/'qtest.log')]
    vm.save(output/'launch.json',{'command':command,'qemu_sha256':vm.sha(executable),'scope':'Actual QEMU device checks; no original CPU execution'})
    vm.save(output/'virtual-policy.json',profile_policy())
    env=os.environ.copy();env['PATH']=str(vm.ROOT/'.local/deps/bin')+os.pathsep+env.get('PATH','')
    transcript=[];checks=[];qmp=connection=None
    with (output/'qemu.log').open('wb') as log:
        process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError('TMU QEMU exited; inspect qemu.log')
                try:qmp=vm.QMP(qmp_port);break
                except OSError:time.sleep(.1)
            if qmp is None:raise RuntimeError('TMU QMP timeout')
            qmp.execute('cont');connection=socket.create_connection(('127.0.0.1',test_port),timeout=5);stream=connection.makefile('rwb',buffering=0)
            def test(command):
                stream.write((command+'\n').encode());reply=stream.readline().decode().strip();transcript.append({'command':command,'reply':reply})
                if not reply.startswith('OK'):raise RuntimeError('QTest: '+reply)
                return reply
            def read(a):return int(test(f'readl {a:#x}').split()[1],16)
            def write(a,v):test(f'writel {a:#x} {v:#x}')
            def step(ns):test(f'clock_step {ns}')
            def get(name):return qmp.execute('qom-get',{'path':'/machine/exynos-tmu','property':name})
            def check(name,actual,expected):
                checks.append({'name':name,'actual':actual,'expected':expected,'passed':actual==expected})
                if actual!=expected:raise ValueError('TMU check failed: '+name)
            check('No instant sample before conversion',get('sample-valid'),0)
            read(0x10090040);check('Preconversion sample read fails, not invented temperature',get('rejected-accesses'),1)
            for base in (0x10090000,0x10094000):
                for off in range(0,32,4):check('Explicit onepoint calibration '+hex(base+off),read(base+off),150)
            step(49);check('Conversion not driven by polling',get('sample-valid'),0)
            step(1);check('Real conversion deadline reached',get('sample-valid'),1)
            _,contract=original_contract()
            for row in contract['sensors']:
                check('Original sensor trim address '+str(row['sensor']),read(int(row['ap_base'],16)+row['trim_offset']),150)
                code=(read(int(row['ap_base'],16)+row['sample_offset'])>>row['sample_shift'])&511
                check('Original sensor packing '+str(row['sensor']),code,150)
            qmp.execute('qom-set',{'path':'/machine/exynos-tmu','property':'temperature-c','value':70})
            check('Input change does not rewrite old sample',read(0x10090040)&511,150)
            step(1000000)
            check('New conversion actually samples input',read(0x10090040)&511,195)
            check('Both banks share declared environment',read(0x10094040)&511,195)
            for temperature in (-40,150):
                qmp.execute('qom-set',{'path':'/machine/exynos-tmu','property':'temperature-c','value':temperature});step(1000000)
                check('Bounded temperature '+str(temperature),get('sampled-code'),150+temperature-25)
            for temperature in (-41,151):
                rejected=False
                try:qmp.execute('qom-set',{'path':'/machine/exynos-tmu','property':'temperature-c','value':temperature})
                except (RuntimeError,ValueError):rejected=True
                check('Actual QOM input bounds '+str(temperature),rejected,True)
                check('Invalid input preserves environment '+str(temperature),get('temperature-c'),150)
            write(0x10090000,0);check('Calibration ROM rejects guest writes',read(0x10090000),150)
            read(0x10090020);check('Unknown controller offset faults',get('rejected-accesses'),3)
            read(0x10090410);check('Unused upper trim hole is not exposed as generic ROM',get('rejected-accesses'),4)
            qmp.execute('system_reset');check('Reset withdraws previous sample',get('sample-valid'),0)
            step(50);check('Reset restarts actual conversion',get('sample-completions'),1)
            check('Reset does not alter external environment',get('sampled-temperature-c'),150)
            return {'checks':checks,'passed':True,'guest_boot_verified':False,'scope':'Real virtual TMU ROM/ADC timing/packing checks only'}
        finally:
            vm.save(output/'qtest-transcript.json',transcript);vm.save(output/'checks.json',checks)
            if connection:connection.close()
            if qmp:
                try:qmp.execute('quit')
                except (OSError,RuntimeError,ValueError):pass
                qmp.close()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--qemu',type=Path)
    args=parser.parse_args();output=args.output.resolve()
    if not output.is_relative_to(vm.ROOT/'var') or output.exists():raise ValueError('Require new scratch output')
    output.mkdir(parents=True);vm.save(output/'original-conversion.json',conversion_evidence());vm.save(output/'result.json',check_model(output,args.qemu));print(str(output/'result.json'))


if __name__=='__main__':main()
