
import argparse
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import time
import original_8704_vm as vm


def machine_options(profile, acpm_clock_hz=0):
    if profile is None:return ''
    if profile != 'virtual-cold-v1' or not 1000000 <= acpm_clock_hz <= 1000000000:
        raise ValueError('PMU requires explicit virtual-cold-v1 and native management CPU')
    return ',x-exynos-pmu-profile=1'


def profile_policy():
    return {'profile':'virtual-cold-v1','physical_reset_snapshot_verified':False,
            'silicon_semantics_verified':False,'factory_images_modified':False,
            'ready_value_injected':False,'assumptions':[
                'Without separate link opt-in, cold inputs404/500/1524 are zero, not observed physical reset values',
                'Finite original config words retain writes; atomic8000 clears/C000 sets a bit index',
                'TimerE34/E38/E3C deadlines use26MHz or explicitB20bit4-selected50MHz virtual clock',
                '3A40 bits19..21 report only actual timer expiration, never completion on read',
                '31 event groups latch only actual named virtual GPIO inputs; clear is W1C',
                'Physical interrupt route, CPU/power release and analog sequencer are not implemented']}


def link_policy(profile, pmu_profile, acpm_clock_hz):
    if profile is None:return None
    if profile != 'virtual-pmlink-v1' or pmu_profile != 'virtual-cold-v1' or not 1000000 <= acpm_clock_hz <= 1000000000:
        raise ValueError('PMU link requires explicit virtual-pmlink-v1, cold PMU and native CPU')
    return {'profile':profile,'factory_images_modified':False,'ready_value_injected':False,
            'physical_command_effects_verified':False,'hcu_instruction_set_recovered':False,
            'latency_ns':2462,'peer':'Declared virtual byte-receiving endpoint, initially connected',
            'source_functions':['193DC','160AC(9)','19D3C','19EEC'],
            'payload_allowlist':[0x10,0x11,0x30,0x31,0x5c,0x5d,0x5e,0x5f,0x78,0x79],
            'scope':'Actual timed packet receipt and two request/ack/error channels only; unknown physical payload meanings, no rail/CPU/analog effect',
            'naming_evidence':'GS101 public header calls3C00 PMLINK_OUT and3AB0 HCU_START; cross-generation inference, NOT Auto9 specification'}


def original_link_contract():






    import acpm_firmware
    raw,meta=acpm_firmware.original_image()
    if meta['source_sha256']!='a83ca762143fab69a0724843bad958f39916ec261f7a65e4de615d82345a5a44':
        raise ValueError('Original PMU-link BL2 identity mismatch')
    if hashlib.sha256(raw).hexdigest()!='ae498979f262d28018aee012d869fd6d95fe5686d838e83e3fb9c876e84caa9a':
        raise ValueError('Original PMU-link scatter-load identity mismatch')
    return {'source':meta,'code_ranges':[{'local_start':hex(start),'bytes':size,
            'sha256':hashlib.sha256(raw[start:start+size]).hexdigest()}
            for start,size in ((0x193dc,0x5c),(0x160ac,0x60),(0x19d3c,0x1ae),(0x19eec,0x5f6))],
            'software_contract':[
                {'selector':'3C00','request':'1520[8]','ack':'1524[10]','error':'1524[11]'},
                {'selector':'3C00','request':'1CA0[13]','ack':'1CA4[5]','error':'1CA4[6]'}],
            'silicon_contract_verified':False,'physical_payload_semantics_recovered':False}


def signal_policy(profile, pmu_profile, acpm_clock_hz, status1204=0, core_status_mask=0):
    if profile is None and status1204==0 and core_status_mask==0:return None
    if profile!='virtual-latched-signals-v1' or pmu_profile!='virtual-cold-v1' or not 1000000<=acpm_clock_hz<=1000000000:
        raise ValueError('PMU signals require explicit latched profile, cold PMU and native CPU')
    if status1204 not in (0,1):raise ValueError('PMU status1204 input must be0 or1')
    if not 0<=core_status_mask<=255:raise ValueError('PMU saved core status mask must be0..255')
    return {'profile':profile,'physical_signal_meanings_verified':False,
            'physical_cpu_or_rail_routes_implemented':False,'factory_images_modified':False,
            'ready_value_injected':False,'latency_ns':2462,'virtual_status1204':status1204,
            'virtual_core_status_mask':core_status_mask,
            'channels':[{'shadow':'1564','request':'1550[0]','ack':'156C[0]'},
                        {'shadow':'1568','request':'1560[0]','ack':'1570[0]'}],
            'assumptions':['Original register handshakes are modeled as explicit shadow/latch transactions',
                           '64 virtual output pins are not assigned invented physical power/CPU effects',
                           'Connected virtual consumer commits a captured shadow word after a real virtual deadline',
                           'SYSTEM_OPTION3A0C is a declared cold zero input, not a measured reset state',
                           '1204 bit0 is an explicit input saved by original11B5C/op0, not a polled ACK',
                           'Eight1004..1184/1304..1484 op0 inputs use an explicit8-bit environment mask, not actual/forced CPU-power ACKs',
                           '1504 is only a retained original software marker;1524 power ACK bits are NOT manufactured'],
            'address_correction':'Previous1554 claim was incorrect; original MOVW195EE loads156C'}


def original_signal_contract():
    import acpm_firmware
    raw,meta=acpm_firmware.original_image()
                                                                       
    original_link_contract()
                                                                                      
    if raw[0x195ee:0x195f2].hex()!='41f26c54' or raw[0x1962e:0x19632].hex()!='41f27054':
        raise ValueError('Original signal ACK address instructions changed')
    import struct
    saved=struct.unpack_from('<5I',raw,0x11b5c)
    if saved[:3]!=(0,0xa0461204,1):raise ValueError('Original input-save descriptor changed')
    candidates=[]
    for pos in range(0x11000,0x12c00,4):
        op,address,mask,value,flags=struct.unpack_from('<5I',raw,pos)
        if op==0 and 0xa0460000<=address<0xa0464000 and mask:
            candidates.append({'local':hex(pos),'op':op,'address':hex(address),'mask':hex(mask),
                               'value':hex(value),'flags':hex(flags)})
    core_inputs=[]
    for index,off in enumerate((0x1004,0x1084,0x1104,0x1184,0x1304,0x1384,0x1404,0x1484)):
        hits=[x for x in candidates if x['address']==hex(0xa0460000+off) and x['mask']=='0x1' and x['value']=='0x0' and x['flags']=='0x0']
        if len(hits)!=1:raise ValueError('Original finite saved input literal not unique')
        core_inputs.append({'index':index,**hits[0]})
    return {'source':meta,'code_ranges':[{'local_start':hex(start),'bytes':size,
            'sha256':hashlib.sha256(raw[start:start+size]).hexdigest()}
            for start,size in ((0x195e8,0x40),(0x19628,0x40),(0x19e18,0x48),(0x1576c,0xc0))],
            'ack_addresses':['A046156C','A0461570'],'ack_addresses_original_instruction_verified':True,
            'status_save_descriptor':{'local':'0x11b5c','op':saved[0],'address':hex(saved[1]),'mask':saved[2],'initial_value':saved[3]},
            'op0_literal_candidates':candidates,
            'finite_core_input_literals':core_inputs,
            'candidate_coverage':'Pinned data-region scan; only runtime-observed descriptor11B5C is independently proven consumed here',
            'shadow_latch_effect_is_behavioral_assumption':True,'full_power_sequencer_recovered':False,
            'poll1576c':'Original returns1 after its timer timeout as well as matching input; no physical transition proof'}


def check_model(output, qemu=None):
    executable=(qemu or vm.ROOT/'runtime/qemu-system-aarch64.exe').resolve()
    qmp_port,test_port=vm.free_port(),vm.free_port()
    command=[str(executable),'-machine','virt-8704-apm,virtualization=on,gic-version=2,x-exynos-vlm=on,x-exynos-acpm=on,x-exynos-acpm-hz=96000000'+machine_options('virtual-cold-v1',96000000),
             '-cpu','cortex-a57','-accel','qtest','-smp','8','-m','128','-display','none',
             '-serial','null','-serial','null','-monitor','none','-nic','none','-S',
             '-qmp',f'tcp:127.0.0.1:{qmp_port},server=on,wait=off',
             '-qtest',f'tcp:127.0.0.1:{test_port},server=on,wait=off','-qtest-log',str(output/'qtest.log')]
    vm.save(output/'launch.json',{'command':command,'qemu_sha256':vm.sha(executable),'scope':'Real peripherals only, no guest code'})
    vm.save(output/'virtual-policy.json',profile_policy())
    env=os.environ.copy();env['PATH']=str(vm.ROOT/'.local/deps/bin')+os.pathsep+env.get('PATH','')
    transcript=[];checks=[];qmp=connection=None;gpio_levels={}
    with (output/'qemu.log').open('wb') as log:
        process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError('PMU QEMU exited; inspect qemu.log')
                try:qmp=vm.QMP(qmp_port);break
                except OSError:time.sleep(.1)
            if qmp is None:raise RuntimeError('PMU QMP timeout')
            qmp.execute('cont')
            connection=socket.create_connection(('127.0.0.1',test_port),timeout=5)
            stream=connection.makefile('rwb',buffering=0)
            def test(command):
                stream.write((command+'\n').encode());reply=stream.readline().decode().strip()
                while reply.startswith('IRQ '):
                    parts=reply.split();gpio_levels[int(parts[2])]=int(parts[1]=='raise')
                    transcript.append({'asynchronous_gpio':reply})
                    reply=stream.readline().decode().strip()
                transcript.append({'command':command,'reply':reply})
                if not reply.startswith('OK'):raise RuntimeError('QTest: '+reply)
                return reply
            def read(a):return int(test(f'readl {a:#x}').split()[1],16)
            def write(a,v):test(f'writel {a:#x} {v:#x}')
            def step(ns):test(f'clock_step {ns}')
            def get(name):return qmp.execute('qom-get',{'path':'/machine/exynos-pmu','property':name})
            def check(name,actual,expected):
                checks.append({'name':name,'actual':actual,'expected':expected,'passed':actual==expected})
                if actual!=expected:raise ValueError('PMU check failed: '+name)
            check('Virtual cold external input is not warm reset',read(0x10460404),0)
            check('FlexPMU hoisted cold-status input is explicit zero',read(0x10460500),0)
            write(0x1046090c,3000);check('Original first configuration retained',read(0x1046090c),3000)
            write(0x1046d520,7);check('Atomic SET uses bit index not mask',read(0x10461520),128)
            write(0x10469520,7);check('Atomic CLEAR changes same base word',read(0x10461520),0)
            write(0x10460110,3);write(0x10468110,1)
            check('Original FlexPMU atomic clear retains other bits',read(0x10460110),1)
            write(0x1046c110,1)
            check('Original FlexPMU atomic set changes actual base config',read(0x10460110),3)
            write(0x1046d520,32);check('Invalid atomic bit rejected',get('rejected-accesses'),1)
            check('Rejected atomic did not mutate base',read(0x10461520),0)
            write(0x10460b18,5);write(0x1046cb18,10)
            check('Original WFI preparation SET is a bit index',read(0x10460b18),0x405)
            write(0x10468b18,10)
            check('Original WFI wake CLEAR preserves other bits',read(0x10460b18),5)
            write(0x10460b14,0xffff03ff)
            write(0x10460b14,(read(0x10460b14)&~0x3ff)|0xff)
            check('Original low-clock request RMW retains upper bits',read(0x10460b14),0xffff00ff)
            write(0x10460404,0x80000000);check('Cold input is read-only',get('rejected-accesses'),2)
            write(0x10463a40,0xffffffff);check('Timer status cannot be written ready',get('rejected-accesses'),3)
            write(0x10461f04,0xffffffff);check('Undeclared register hole faults',get('rejected-accesses'),4)
            write(0x10471f08,0xffffffff);check('End of event aperture cannot alias into PMU config',get('rejected-accesses'),5)
            write(0x10463c00,0x5e);check('Unimplemented PMU link remains a fault without opt-in',get('rejected-accesses'),6)
            check('Unsupported message cannot generate timer ready',read(0x10463a40),0)
                                                                            
                                                                                
            vm.save(output/'link-virtual-policy.json',link_policy('virtual-pmlink-v1','virtual-cold-v1',96000000))
            vm.save(output/'original-link-contract.json',original_link_contract())
            check('Message link is disabled by default',get('virtual-pmlink-v1'),0)
            def set_input(name,value):
                return qmp.execute('qom-set',{'path':'/machine/exynos-pmu','property':name,'value':value})
            set_input('virtual-pmlink-v1',1);set_input('link-peer-connected',1)
            for prop in ('virtual-pmlink-v1','link-peer-connected'):
                failed=False
                try:set_input(prop,2)
                except RuntimeError:failed=True
                check('QOM input bounded '+prop,failed,True)
                check('Rejected QOM input preserved '+prop,get(prop),1)
            delivered=0
            for route,payload in ((0,0x5e),(1,0x5e),(0,0x78),(0,0x5c),(0,0x11),
                                  (0,0x30),(0,0x31),(0,0x10),(0,0x5d),(1,0x5f),(0,0x79)):
                base=0x10461520 if route==0 else 0x10461ca0;bit=8 if route==0 else 13
                ack=1<<(10 if route==0 else 5);label=f' route{route} payload{payload:02x}'
                write(0x10463c00,payload)
                check('Staging alone cannot deliver'+label,get('link-deliveries'),delivered)
                check('Staging alone cannot acknowledge'+label,read(base+4),0)
                write(base|0xc000,bit)
                check('Request starts asynchronous transport'+label,get('link-state'),1)
                if delivered==0:
                    failed=False
                    try:set_input('virtual-pmlink-v1',0)
                    except RuntimeError:failed=True
                    check('Active profile cannot be disabled mid-packet',failed,True)
                    count=get('rejected-accesses');write(0x10463c00,0x78)
                    check('Active packet cannot be replaced',get('rejected-accesses'),count+1)
                    check('Rejected replacement preserves selected payload',read(0x10463c00),payload)
                    count=get('rejected-accesses');write(0x1046dca0,13)
                    check('Second route cannot steal pending transfer',get('rejected-accesses'),count+1)
                    check('Rejected second request preserves its base',read(0x10461ca0),0)
                step(2461);check('ACK absent before deadline'+label,read(base+4),0)
                check('Mailbox unchanged before deadline'+label,get('link-deliveries'),delivered)
                step(1);delivered+=1
                check('ACK follows actual receipt'+label,read(base+4),ack)
                check('Peer mailbox receives exact byte'+label,get('link-received'+str(route)),payload)
                check('Packet committed exactly once'+label,get('link-deliveries'),delivered)
                for _ in range(3):read(base+4)
                check('Reads never redeliver'+label,get('link-deliveries'),delivered)
                write(base|0x8000,bit)
                check('Release is not immediate'+label,read(base+4),ack)
                step(2461);check('Release deadline not early'+label,read(base+4),ack)
                step(1);check('ACK clears after actual release'+label,read(base+4),0)
                check('Link returns idle'+label,get('link-state'),0)
            check('Both peer mailbox routes actually received',get('link-received-valid'),3)
            check('Both-way handshakes completed',get('link-releases'),delivered)
            check('Actual receipt history, not guessed source order',get('link-history-first32'),
                  '0:5e,1:5e,0:78,0:5c,0:11,0:30,0:31,0:10,0:5d,1:5f,0:79')
            check('Receipt records actual virtual time',get('link-last-delivery-ns')>0,True)
                                                                              
            count=get('rejected-accesses');write(0x10463c00,0xff)
            check('Unknown byte rejected',get('rejected-accesses'),count+1)
            check('Rejected byte cannot deliver',get('link-deliveries'),delivered)
            count=get('rejected-accesses');write(0x1046d520,8)
            check('No staged packet cannot start',get('rejected-accesses'),count+1)
            check('Invalid trigger preserves request word',read(0x10461520)&0x100,0)
            write(0x10463c00,0x5e);write(0x1046d520,8);write(0x10469520,8);step(5000)
            check('Cancelled transfer never delivers',get('link-deliveries'),delivered)
            check('Cancellation recorded',get('link-cancelled'),1)
            check('Cancellation cannot leave ACK',read(0x10461524),0)
            write(0x10463c00,0x5e);write(0x1046d520,8);set_input('link-peer-connected',0)
            step(2462)
            check('Disconnected peer cannot ACK',read(0x10461524),1<<11)
            check('Disconnected peer cannot receive',get('link-deliveries'),delivered)
            write(0x10469520,8);check('Error request can be withdrawn',read(0x10461524),0)
            set_input('link-peer-connected',1)
            vm.save(output/'signal-virtual-policy.json',signal_policy('virtual-latched-signals-v1','virtual-cold-v1',96000000))
            vm.save(output/'original-signal-contract.json',original_signal_contract())
            check('Signal profile disabled by default',get('virtual-latched-signals-v1'),0)
            count=get('rejected-accesses');write(0x1046d564,18)
            check('Signal config faults without opt-in',get('rejected-accesses'),count+1)
            set_input('virtual-latched-signals-v1',1);set_input('signal-peer-connected',1)
            test('irq_intercept_out /machine/exynos-pmu pmu-latched-signals')
            for prop in ('virtual-latched-signals-v1','signal-peer-connected','virtual-status1204'):
                failed=False
                try:set_input(prop,2)
                except RuntimeError:failed=True
                check('Signal input bounded '+prop,failed,True)
                check('Invalid signal input preserved '+prop,get(prop),0 if prop=='virtual-status1204' else 1)
            for value in (1,0):
                set_input('virtual-status1204',value)
                check('Saved input is external, not forced ready '+str(value),read(0x10461204),value)
            set_input('virtual-core-status-mask',0xa5)
            for i,off in enumerate((0x1004,0x1084,0x1104,0x1184,0x1304,0x1384,0x1404,0x1484)):
                check('Finite saved input follows external mask '+str(i),read(0x10460000+off),(0xa5>>i)&1)
            failed=False
            try:set_input('virtual-core-status-mask',256)
            except RuntimeError:failed=True
            check('Core input mask bounded',failed,True)
            count=get('rejected-accesses');write(0x10461004,1)
            check('External saved inputs are read-only',get('rejected-accesses'),count+1)
            committed=0
            for i,word in ((0,0x001c0000),(1,0x00000f00),(0,0x11223344),(1,0xa5a5a5a5)):
                shadow=0x10461564+4*i;req=0x10461550 if i==0 else 0x10461560
                ack=0x1046156c+4*i;label=f' channel{i} word{word:08x}'
                old=get('signal-latched'+str(i));write(shadow,word)
                check('Shadow cannot change effective output'+label,get('signal-latched'+str(i)),old)
                write(req|0xc000,0)
                check('Signal request starts work'+label,get('signal-state'+str(i)),1)
                if committed==0:
                    failed=False
                    try:set_input('virtual-latched-signals-v1',0)
                    except RuntimeError:failed=True
                    check('Cannot disable active signal profile',failed,True)
                write(shadow,word^0xffffffff)
                step(2461);check('Signal ACK not early'+label,read(ack),0)
                check('Output cannot precede commit'+label,get('signal-latched'+str(i)),old)
                step(1);committed+=1
                check('Commit ACK at real deadline'+label,read(ack),1)
                check('Commit uses captured not later shadow'+label,get('signal-latched'+str(i)),word)
                actual=sum(gpio_levels.get(i*32+bit,0)<<bit for bit in range(32))
                check('Actual named virtual output GPIOs match commit'+label,actual,word)
                for _ in range(3):read(ack)
                check('Status reads cannot recommit'+label,get('signal-commits'),committed)
                write(req|0x8000,0);step(2461)
                check('Commit release not early'+label,read(ack),1)
                step(1);check('Commit release at actual deadline'+label,read(ack),0)
                check('Withdrawing strobe does not undo latched output'+label,get('signal-latched'+str(i)),word)
            check('Two channels actually committed',get('signal-valid'),3)
            check('All commit ACKs withdrawn',get('signal-releases'),committed)
            count=get('rejected-accesses');write(0x1046156c,1)
            check('Signal ACK is read-only',get('rejected-accesses'),count+1)
            count=get('rejected-accesses');read(0x10461554)
            check('Wrong former1554 address is not invented as ACK',get('rejected-accesses'),count+1)
            count=get('rejected-accesses');write(0x1046d550,1)
            check('Undeclared strobe bits rejected',get('rejected-accesses'),count+1)
            check('Rejected strobe did not change base',read(0x10461550),0)
            write(0x10461564,0xfeedface);write(0x1046d550,0);write(0x10469550,0);step(5000)
            check('Cancelled signal cannot commit',get('signal-commits'),committed)
            check('Signal cancellation recorded',get('signal-cancelled'),1)
            previous=get('signal-latched0');write(0x1046d550,0);set_input('signal-peer-connected',0);step(2462)
            check('Absent signal consumer cannot ACK',read(0x1046156c),0)
            check('Absent consumer cannot change output',get('signal-latched0'),previous)
            check('Absent consumer records failed work',get('signal-failed'),1)
            write(0x10469550,0);set_input('signal-peer-connected',1)
            check('Declared cold SYSTEM_OPTION is not an observed reset',read(0x10463a0c),0)
            write(0x1046d520,0);write(0x1046d520,1)
            check('No manufactured power ACK bits',read(0x10461524)&7,0)
            write(0x10469520,0);write(0x10469520,1)
            for i in range(3):
                reg=0x10460e34+4*i;bit=1<<(19+i)
                write(reg,26000);write(reg|0xc000,31)
                check('Timer does not expire on read '+str(i),read(0x10463a40)&bit,0)
                step(500000);check('Timer half deadline '+str(i),read(0x10463a40)&bit,0)
                step(500000);check('Timer actual deadline '+str(i),read(0x10463a40)&bit,bit)
                write(reg|0x8000,31);check('Timer clear cancels old status '+str(i),read(0x10463a40)&bit,0)
            check('Actual timer completion count',get('timer-completions'),3)
            write(0x1046cb20,4);write(0x10460e34,50000);write(0x1046ce34,31)
            step(999999);check('Explicit alternate timer clock is not early',get('timer-completions'),3)
            step(1);check('Explicit alternate clock expires at real deadline',get('timer-completions'),4)
            write(0x10468e34,31);write(0x10468b20,4)
            write(0x10460e34,26000);write(0x1046ce34,31);write(0x10468e34,31);step(2000000)
            check('Cancellation cannot complete later',get('timer-completions'),4)
            write(0x10470a00,0xffffffff);check('Group enable does not invent pending',read(0x10470a08),0)
            test('set_irq_in /machine/exynos-pmu pmu-event 325 1')
            check('Actual virtual event latches source bit',read(0x10470a08),32)
            write(0x10470a0c,1);check('W1C preserves other pending bits',read(0x10470a08),32)
            test('set_irq_in /machine/exynos-pmu pmu-event 325 0')
            write(0x10470a0c,32);check('W1C clears actual pending',read(0x10470a08),0)
            for number in range(2):
                base=0x10560000+number*0x10000;path=f'/machine/exynos-speedy{number}'
                qmp.execute('qom-set',{'path':path,'property':'virtual-bl2-rev0','value':1})
                write(base,0x61);write(base+4,0x101);write(base+8,0x88600);step(1000)
                check('PMIC0C remains absent by default '+str(number),read(base+0x10),2)
                qmp.execute('qom-set',{'path':path,'property':'virtual-pmu-input-byte','value':0xa5})
                write(base+0x10,0xffffffff);write(base+8,0x88600);step(1000)
                check('Explicit PMIC input only controller0 '+str(number),read(base+0x10),1 if number==0 else 2)
                if number==0:
                    check('Input has real frame trailer',read(base+0x38),0)
                    check('Input returns configured byte not forced zero',read(base+0x38),0xa5)
                    write(base+0x10,0xffffffff);write(base+8,0x188600);write(base+0x34,0);step(1000)
                    check('PMIC input byte is read-only',read(base+0x10),2)
                    check('Guest write did not mutate external input',qmp.execute('qom-get',{'path':path,'property':'virtual-pmu-input-byte'}),0xa5)
            write(0x10463c00,0x78);write(0x1046d520,8)
            write(0x1046d550,0)
            write(0x10460e34,26000);write(0x1046ce34,31);qmp.execute('system_reset');step(2000000)
            check('Reset cancels unreceived link packet',get('link-deliveries'),0)
            check('Reset clears packet-valid state',get('link-received-valid'),0)
            check('Reset clears link ACK/error',read(0x10461524),0)
            check('Reset clears receipt history',get('link-history-first32'),'')
            check('Reset cancels signal commit',get('signal-commits'),0)
            check('Reset clears signal-valid mask',get('signal-valid'),0)
            check('Reset clears effective signal word',get('signal-latched0'),0)
            check('Reset lowers actual virtual output pins',sum(gpio_levels.values()),0)
            check('Reset cancels pending timers',get('timer-completions'),0)
            check('Reset clears configurations',read(0x1046090c),0)
            return {'checks':checks,'passed':True,'guest_boot_verified':False,'scope':'Actual virtual PMU/PMIC model checks, no original OS/HMI acceptance'}
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
    parser.add_argument('--contract-only',action='store_true',help='Inspect original signal contract and bounded descriptor literals, without starting any CPU/model')
    args=parser.parse_args();output=args.output.resolve()
    if not output.is_relative_to(vm.ROOT/'var') or output.exists():raise ValueError('Require new scratch output')
    output.mkdir(parents=True)
    vm.save(output/'result.json',original_signal_contract() if args.contract_only else check_model(output,args.qemu))
    print(str(output/'result.json'))


if __name__=='__main__':main()
