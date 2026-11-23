
from pathlib import Path
import json
import runpy
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src/emu'))
module=runpy.run_path(str(Path(__file__).with_name('native-linux-scanout.py')))
walk=module['walk_iova']
spans=module['contiguous_spans']
rgb=module['read_active_rgb']
gem_spans=module['gem_scatter_spans']


class ScanoutTests(unittest.TestCase):
    def test_color_edge_capture_is_bounded_and_needs_independent_active_rows(self):
        cls=runpy.run_path(str(Path(__file__).with_name('center-event-sample.py')))['CenterEventCadence']
        cadence=cls()
        footprint,content='a'*64,'b'*64
        def history(generation,**changes):
            row=dict(footprint=footprint,content=content,generation=generation,
                     color_generation=generation,rgb=146815,bytes=1560*700*4,pitch=6272,
                     clear=False,age_ns=0)
            row.update(changes)
            return json.dumps(dict(version=1,generation=generation,targets=[row]))
        self.assertTrue(cadence.probe_due(100))
        self.assertFalse(cadence.probe_due(100.79))
        self.assertTrue(cadence.probe_due(100.8))
        for unsupported in (history(1,rgb=0),history(1,clear=True),history(1,age_ns=6_000_000_001),
                            history(1,color_generation=0),history(1,pitch=4800),history(1,content='')):
            self.assertIsNone(cadence.claim(unsupported,102,90))
        self.assertIsNone(cadence.claim(history(1),102,101))
        edge=cadence.claim(history(1),102,100)
        proof=dict(active_footprint_sha256=footprint,active_content_sha256=content,
                   latest_write_content_matches=True,latest_write=dict(generation=1),
                   capture_monotonic=101)
        self.assertFalse(cadence.confirm(edge,proof,102))
        retry=cadence.claim(history(1),104,102)
        self.assertEqual(retry['attempt'],2)
        self.assertFalse(cadence.confirm(retry,dict(proof,capture_monotonic=104,active_content_sha256='c'*64),104))
        self.assertTrue(cadence.confirm(retry,dict(proof,capture_monotonic=104),104))
        self.assertIsNone(cadence.claim(history(1),108,104))
        for g in range(2,8):
            self.assertEqual(cadence.claim(history(g),110+2*g,108+2*g)['generation'],g)
        self.assertFalse(cadence.probe_due(130))
        self.assertIsNone(cadence.claim(history(8),130,120))

    def test_foreground_png_keeps_alpha_and_skips_padding(self):
        png=module['encode_foreground_rgba'](2,1,12,bytes((16,32,64,128,0,0,0,0,1,2,3,255)))
        self.assertEqual(png[:8],b'\x89PNG\r\n\x1a\n')
        self.assertEqual(struct.unpack_from('>IIBBBBB',png,16),(2,1,8,6,0,0,0))
        size,kind=struct.unpack_from('>I4s',png,33)
        self.assertEqual(kind,b'IDAT')
        self.assertEqual(zlib.decompress(png[41:41+size]),bytes((0,128,64,32,128,0,0,0,0)))

    def test_matching_foreground_reuses_png_but_refreshes_live_proof(self):
        keep=module['retain_nonzero_foreground']
        proof=dict(width=2,height=1,pitch=8,kernel_sha256='a'*64,secure=False)
        profile=dict(nonzero_rgb_pixels=1,out_of_premultiplied_range_pixels=0,captured_monotonic=10)
        active=dict(latest_write_content_matches=True,active_content_sha256='b'*64,
                    latest_write=dict(rgb=1,generation=1))
        save=lambda path,data:path.write_text(json.dumps(data),encoding='utf8')
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            rgba=bytes((16,32,64,128,0,0,0,0))
            keep(root,proof,rgba,profile,active,save)
            stem=root/'native-center-last-nonzero-foreground'
            pixels=stem.with_suffix('.png').read_bytes()
            profile['captured_monotonic']=11
            active['latest_write']['generation']=2
            with patch.dict(keep.__globals__,encode_foreground_rgba=lambda *_:self.fail('PNG was encoded twice')):
                keep(root,proof,rgba,profile,active,save)
            result=json.loads(stem.with_suffix('.json').read_bytes())
            self.assertEqual(stem.with_suffix('.png').read_bytes(),pixels)
            self.assertEqual((result['captured_monotonic'],result['gpu_generation']),(11,2))
            self.assertFalse(result['secure'])
            self.assertFalse(result['full_display'])

    def test_distinct_scanout_survives_the_live_frame_switch(self):
        keep=module['retain_distinct_scanout']
        import hashlib
        raw=b'\x10\x20\x30\xff'
        proof=dict(width=1,height=1,pitch=4,format='XR24',secure=False,
                   status='active_scanout_pages_verified',captured_monotonic=10)
        png,pixels=module['render_active_raster'](proof,raw)
        proof.update(pixels)
        active=dict(status='matching_physical_rows',latest_write_content_matches=True,
                    active_content_sha256='a'*64,latest_write=dict(rgb=1,generation=7))
        save=lambda path,data:path.write_text(json.dumps(data),encoding='utf8')
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            target=root/'native-center-distinct-scanout.png'
            for rejected in (dict(active,latest_write_content_matches=False),
                             dict(active,active_content_sha256=
                                  '334b6c564a9f1958cca5c835681f00aa1676e0193acc2db75ae3752c630cca9e')):
                keep(root,proof,png,rejected,save)
                self.assertFalse(target.exists())
            keep(root,dict(proof,secure=True),png,active,save)
            self.assertFalse(target.exists())
            keep(root,proof,png,active,save)
            keep(root,dict(proof,captured_monotonic=11),png,active,save)
            record=json.loads(target.with_suffix('.json').read_bytes())
            self.assertEqual(record['captured_monotonic'],10)
            self.assertEqual(record['png_sha256'],hashlib.sha256(target.read_bytes()).hexdigest())
            self.assertFalse(record['live_stream'])
            with self.assertRaises(ValueError):
                keep(root,proof,b'wrong PNG',active,save)
            self.assertEqual(target.read_bytes(),png)

    def test_wod_gate_binds_crtc_and_only_reports_selected_plane(self):
        dev,crtc,wod,plane0,plane1=0x1000,0x2000,0x3000,0x4000,0x5000
        ram={crtc+0x498:wod,wod+8:dev,wod+0x4e8:crtc,crtc+0x98:1<<32,
             wod+0x4f0:2,crtc+0x9760:1<<3,plane0+0x248:2<<32,
             plane1+0x248:3<<32,plane1-0x40:6}
        gate=module['inspect_wod_gate']
        members=lambda *_:[plane0,plane1]
        self.assertEqual(gate(dev,crtc,ram.__getitem__,members),
                         dict(status='observed',vdpu_vblank_gate=True,decon_pipe=2,
                              plane_mask=8,windows_tested=[dict(index=3,window=6)]))
        ram[wod+0x4e8]=0
        with self.assertRaisesRegex(ValueError,'not bound'):
            gate(dev,crtc,ram.__getitem__,members)

    def test_vdpu_wrap_reads_only_pending_control_and_vsync_status(self):
        observe=module['vdpu_pending_events']
        at_slot_9=0x38+9*24
        seen=[]
        data={at_slot_9:6|(2<<32),at_slot_9+8:7,0x38:1}
        def read(offset):
            seen.append(offset)
            return data[offset]
        self.assertEqual(observe(9,1,read),[
            dict(slot=9,kind=6,pipe=2,window_status_word=7),dict(slot=0,kind=1,pipe=0)])
        self.assertEqual(seen,[at_slot_9,at_slot_9+8,0x38])
        self.assertEqual(observe(3,3,read),[])
        with self.assertRaisesRegex(ValueError,'cursors'):
            observe(3,10,read)
        with self.assertRaisesRegex(ValueError,'exceeds'):
            module['vdpu_cursor_word'](0x2000,(0x1000,0x4000),read,296)

    def test_identical_pixels_on_a_new_buffer_are_not_a_visible_change(self):
        track=module['track_display_changes']
        prev=dict(frame_source='original_vm3_drm_primary',capture_origin='original_sysmmu',
                  pixel_sha256='same',framebuffer_id=5,last_frame_change_monotonic=9)
        cur=dict(prev,framebuffer_id=6,captured_monotonic=10)
        self.assertEqual(track(cur,prev),{'last_buffer_switch_monotonic':10})
        cur.update(track(cur,prev))
        new=dict(cur,pixel_sha256='different',captured_monotonic=11)
        self.assertEqual(track(new,cur),{'last_buffer_switch_monotonic':10,
                                        'last_pixel_change_monotonic':11})
        self.assertEqual(track(dict(new,capture_origin='original_exynos_gem_sg'),cur),{})

    def test_flip_uses_original_crtc_commit_and_matching_pipe_events(self):
        dev,crtc,array,commit,event,other=0x1000,0x2000,0x3000,0x4000,0x5000,0x6000
        blank=array+0x158
        ram={crtc:dev,dev+0x170:3,crtc+0x90:1,dev+0x130:array,
             blank+0x150:1,blank+0x50:19,commit:crtc,commit+0x10:0,
             commit+0x30:0xffffffff,commit+0x50:0,event+0x48:1,event+0x50:20,
             other+0x48:0}
        lists={(crtc+0x3f8,0x70):[commit],(dev+0x148,0x28):[event,other]}
        observe=module['inspect_flip_completion']
        result=observe(dev,crtc,ram.__getitem__,lambda a,b:lists[a,b])
        self.assertEqual(result,dict(status='observed',pipe=1,vblank_count=19,
                                     vblank_enabled=True,armed_vblank_sequences=[20],
                                     commits=[dict(flip_done=False,hw_done=True,cleanup_done=False)]))
        ram[commit]=other
        with self.assertRaisesRegex(ValueError,'commit owner'):
            observe(dev,crtc,ram.__getitem__,lambda a,b:lists[a,b])

    def test_vdpu_only_reads_cursors_from_a_verified_irq_owner(self):
        lookup={name:address for name,address,_,_ in module['VDPU_ABI']}
        lookup.update(xirq_wrappers=0xffffffc0111ef848,xirq_to_irq=0xffffffc0113fcca8)
        head=lookup['xirq_wrappers']
        wrapper,device,ring=(0xffffff8010001000,0xffffff8010002000,0xffffffc030000000)
        link=wrapper+0x108
        ram={head:link,head+8:link,link:head,link+8:head,wrapper:0x50,
             wrapper+8:lookup['vdpu_irq_hdl'],wrapper+0x10:device,
             device:2<<32,device+0x38:0x50,device+0x40:wrapper,
             device+0x68:ring,device+0x140:1,device+0x158:lookup['vdpu_irqhdl_work'],
             lookup['xirq_to_irq']+4*0x50:365}
        inspect=module['inspect_vdpu']
        calls=[]

        def shared(address):
            calls.append(address)
            return (1<<32)|2

        self.assertEqual(inspect(lookup,ram.__getitem__,shared),
                         [dict(link_id=0,display_index=2,xirq=80,linux_irq=365,
                               work_pending=True,read_index=2,write_index=1,occupancy=9,capacity=9)])
        self.assertEqual(calls,[ring])
        ram[device+0x40]=wrapper+8
        with self.assertRaisesRegex(ValueError,'does not match its owner'):
            inspect(lookup,ram.__getitem__,shared)
        self.assertEqual(calls,[ring])
        ram[device+0x40]=wrapper
        ram[device]=(2<<32)|1                                                
        with self.assertRaisesRegex(ValueError,'not a VM3 display link'):
            inspect(lookup,ram.__getitem__,shared)
        self.assertEqual(calls,[ring])

    def test_vdpu_pmem_uses_live_allocator_and_one_page_aligned_cursor_word(self):
        module['verify_vdpu_pool_abi']()
        pointer=0x80330540
        ram={module['VDPU_POOL_GLOBAL']:pointer,pointer+16:0x8e000000,pointer+24:0x90000000}
        pool=module['read_vdpu_pool'](ram.__getitem__)
        self.assertEqual(pool,(0x8e000000,0x90000000))
        seen=[]
        def read(address):
            seen.append(address)
            return 0x300000001
        fetch=module['vdpu_cursor_word']
        self.assertEqual(fetch(0x8e005000,pool,read),0x300000001)
        for a in (0x8e005008,0x88000000,0x90000000,0xb8000000):
            with self.assertRaisesRegex(ValueError,'outside its HYP PMEM page'):
                fetch(a,pool,read)
        self.assertEqual(seen,[0x8e005000])
        ram[pointer+24]=0xa0000000
        with self.assertRaisesRegex(ValueError,'unexpected bounds'):
            module['read_vdpu_pool'](ram.__getitem__)
        ram[module['VDPU_POOL_GLOBAL']]=0x8e005000
        with self.assertRaisesRegex(ValueError,'not live in HYP RAM'):
            module['read_vdpu_pool'](ram.__getitem__)

    def test_exact_original_gem_scatter_buffer_and_alias_rejection(self):
        gem=0xffffff8010000000
        sgt=0xffffff8010001000
        first=0xffffff8010002000
        second=0xffffff8010004000
        memstart=0x80000000
        anchor=module['VMEMMAP_BASE']-((memstart>>12)<<6)
        def link(pa):return anchor+(pa>>12)*64
        iova=0x10000000
        data={gem+0x140:8192,gem+0x148:0xffffff8010005000,gem+0x150:0xffffff8010006000,
              gem+0x158:sgt,gem+0x160:iova,sgt:first,sgt+8:(2<<32)|1,
              first:link(0xb8200000),first+8:4096<<32,first+0x10:iova,first+0x20:second|1,
              second:link(0xb8240000)|2,second+8:4096<<32}
        def backed(addr,n):return 0xb8000000<=addr<addr+n<=0xd0000000
        read=data.__getitem__
        self.assertEqual(gem_spans(gem,iova+4080,4080,32,memstart,read,backed),
                         [(0xb8200ff0,16),(0xb8240000,16)])
        with self.assertRaisesRegex(ValueError,'do not match'):
            gem_spans(gem,iova+4080,0,4096,memstart,read,backed)
        data[second]=link(0xb8200000)|2
        with self.assertRaisesRegex(ValueError,'aliased'):
            gem_spans(gem,iova,0,8192,memstart,read,backed)

    def test_oem_section_and_page_translation_and_scatter(self):
        root=0xaf0000000
        second=0xaf2000000
        iova=0x480ff0
        tables={root+4*4:(second>>4)|1,second+0x80*4:(0xaf3000000>>4)|2,
                second+0x81*4:(0xb812a000>>4)|2,root+5*4:(0xaf4000000>>4)|2,
                second+0xa0*4:(0xaf5000000>>4)|1}
        def backed(addr,n):
            return any(a<=addr<addr+n<=b for a,b in module['VM3_RAM'])
        self.assertEqual(spans(root,iova,32,tables.__getitem__,backed),
                         [(0xaf3000ff0,16),(0xb812a000,16)])
        self.assertEqual(walk(root,0x523456,tables.__getitem__,backed),(0xaf4023456,0xdcbaa))
        self.assertEqual(walk(root,0x4a0345,tables.__getitem__,backed),(0xaf5000345,0xfcbb))
        tables[second+0x80*4]=(0xa0100000>>4)|2
        with self.assertRaisesRegex(ValueError,'outside backed VM3'):
            walk(root,iova,tables.__getitem__,backed)

    def test_native_raster_uses_bgr_words_and_ignores_pitch_padding(self):
        base=0xb8200000
        pitch=1560*4+64
        row=b'\x11\x22\x33\xff'*1560+b'\x5a'*64
        proof=dict(status='active_scanout_pages_verified',secure=False,width=1560,height=700,
                   pitch=pitch,format='XR24',modifier=0,physical_spans=[(hex(base),pitch*700)])
        pixels,meta=rgb(proof,lambda address,n: row*700)
        self.assertTrue(meta['nonzero'])
        size,tag=struct.unpack_from('>I4s',pixels,33)
        self.assertEqual(tag,b'IDAT')
        self.assertEqual(zlib.decompress(pixels[41:41+size]),(b'\0'+b'\x33\x22\x11'*1560)*700)
        ar24=dict(proof,format='AR24')
        opaque,alpha=rgb(ar24,lambda a,n: row*700)
        self.assertEqual(opaque,pixels)
        self.assertTrue(alpha['opaque_alpha_verified'])
        with self.assertRaisesRegex(ValueError,'Nonopaque AR24'):
            rgb(ar24,lambda a,n: b'\x11\x22\x33\x80'+(row*700)[4:])
        route=dict(ar24,crtc_active=True,planes_on_crtc=1,original_wod_blend=dict(
                   verified=True,plane_alpha8=255,drm_pixel_blend_mode=2,wod_blend=0))
        no_blend,result=rgb(route,lambda a,n: b'\x11\x22\x33\x80'+(row*700)[4:])
        self.assertEqual(no_blend,pixels)
        self.assertTrue(result['alpha_ignored_by_original_plane'])
        self.assertFalse(result['opaque_alpha_verified'])
        with self.assertRaisesRegex(ValueError,'Nonopaque AR24'):
            rgb(dict(route,planes_on_crtc=2),lambda a,n: b'\x11\x22\x33\x80'+(row*700)[4:])
        route['original_wod_blend']['plane_alpha8']=128
        with self.assertRaisesRegex(ValueError,'Translucent original plane'):
            rgb(route,lambda a,n: row*700)
        with self.assertRaisesRegex(ValueError,'unprotected'):
            rgb(dict(proof,secure=True),lambda a,n: b'')
        with self.assertRaisesRegex(ValueError,'Short'):
            rgb(proof,lambda a,n: b'')

    def test_original_foreground_profile_distinguishes_black_from_invalid_alpha(self):
        profile=module['summarize_premultiplied_raster']
        blend=dict(verified=True,plane_alpha8=255,drm_pixel_blend_mode=0,wod_blend=1)
        proof=dict(format='AR24',planes_on_crtc=1,crtc_active=True,original_wod_blend=blend,
                   width=2,height=2,pitch=12,framebuffer_id=63)
        raster=(bytes((0,0,0,0, 9,0,1,0))+b'pad!'+
                bytes((1,3,2,4, 0,0,0,255))+b'pad!')
        result=profile(proof,raster)
        self.assertEqual(tuple(result[key] for key in ('total_pixels','opaque_pixels',
                         'transparent_pixels','nonzero_rgb_pixels','out_of_premultiplied_range_pixels')),
                         (4,1,2,2,1))
        self.assertIsNone(profile(dict(proof,planes_on_crtc=2),raster))
        self.assertIsNone(profile(dict(proof,original_wod_blend=dict(blend,plane_alpha8=128)),raster))
        with self.assertRaisesRegex(ValueError,'Incomplete original'):
            profile(proof,raster[:-1])

    def test_vm3_metadata_batches_words_only_within_backed_pages(self):
        first,last=module['VM3_RAM'][0]
        calls=[]
        def read(base,length):
            calls.append((base,length))
            return struct.pack('<512Q',*range(512))
        word,stats=module['vm3_metadata_reader'](read)
        self.assertEqual((word(first),word(first+8),word(first+4088),word(last-8)),(0,1,511,511))
        self.assertEqual(calls,[(first,4096),(last-4096,4096)])
        self.assertEqual((stats().hits,stats().misses),(2,2))
        for address in (first-8,first+1,last,0x10830000,0x80330000):
            with self.assertRaisesRegex(ValueError,'outside VM3 RAM'):
                word(address)
        self.assertEqual(len(calls),2)
        short,_=module['vm3_metadata_reader'](lambda *_: b'')
        with self.assertRaisesRegex(ValueError,'Short native VM3 metadata'):
            short(first)

    def test_center_capture_copies_pixels_before_resume_and_encodes_afterward(self):
        base=0xb8200000
        raster=b'\x11\x22\x33\xff'*(1560*700)
        proof=dict(status='active_scanout_pages_verified',secure=False,width=1560,height=700,
                   pitch=1560*4,format='XR24',modifier=0,physical_spans=[(hex(base),len(raster))])
        sha=module['LINUX_SHA']
        symbols=dict(sha256=sha,observed_tcr_el1='0x0',symbols=[
            dict(name='swapper_pg_dir',address='0xffffffc010090000'),
            dict(name='_text',address='0xffffffc010080000')])
        record=module['record_current']

        class Qmp:
            def __init__(self):
                self.running=True
                self.commands=[]
                self.short=False

            def execute(self,command,args=None):
                self.commands.append(command)
                if command=='query-status':
                    return dict(status='running' if self.running else 'paused',running=self.running)
                if command in ('stop','cont'):
                    self.running=command=='cont'
                    return {}
                if command=='qom-get':
                    if self.running or args!=dict(path='/machine/exynos-mali-g3d1',
                                                  property='vm3-center-target-history'):
                        raise AssertionError((command,args,self.running))
                    return json.dumps(dict(version=1,generation=0,evictions=0,targets=[]))
                self.assert_read(command,args)
                return {}

            def assert_read(self,command,args):
                if command!='pmemsave' or self.running or args['val']!=base or args['size']!=len(raster):
                    raise AssertionError((command,args,self.running))
                Path(args['filename']).write_bytes(raster[:-1] if self.short else raster)

        qmp=Qmp()
        def inspect(*_):
            self.assertFalse(qmp.running)
            return dict(proof)
        def render(packed_proof,copied):
            self.assertTrue(qmp.running)
            return module['render_active_raster'](packed_proof,copied)
        with TemporaryDirectory() as directory:
            dest=Path(directory)
            (dest/'kernel-loads.json').write_text(json.dumps([dict(guest='linux',kernel_sha256=sha,physical=base)]))
            with patch.dict(record.__globals__,inspect=inspect,render_active_raster=render), \
                    patch('sysmmu_platform.runtime_state',return_value={}):
                record(qmp,dest,symbols)
                result=json.loads((dest/'native-center-frame.json').read_bytes())
                self.assertTrue(result['nonzero'])
                self.assertTrue((dest/result['png']).is_file())
                self.assertEqual(qmp.commands,['query-status','stop','query-status','pmemsave','qom-get','query-status','cont'])
                evidence=json.loads((dest/'native-center-gpu-correlation.json').read_bytes())
                self.assertEqual(evidence['status'],'no_matching_retained_target')
                self.assertEqual(evidence['gpu_targets_sampled'],0)
                self.assertGreaterEqual(result['capture_timing']['qmp_stop_to_resume_seconds'],0)
                qmp.short=True
                with self.assertRaisesRegex(ValueError,'Short native physical read'):
                    record(qmp,dest,symbols)
                self.assertTrue(qmp.running)
                self.assertEqual(qmp.commands[-1],'cont')

                                                                        
                                                                   
                armed=dict(status='observed',vblank_enabled=True,vblank_count=1,
                           armed_vblank_sequences=[2,2])
                wod=dict(status='observed',plane_mask=0,decon_pipe=2)
                ordered=[]
                def read_ring(*_):
                    self.assertFalse(qmp.running)
                    ordered.append('ring')
                    return [dict(display_index=0,occupancy=1)]
                def read_wod(*_):
                    self.assertFalse(qmp.running)
                    ordered.append('wod')
                    return dict(status='observed')
                def short_pixels(*_):
                    ordered.append('pixels')
                    raise ValueError('Short native physical read')
                native_proof=dict(proof,crtc_active=True,connector_observations=[dict(
                    name='Virtual-1',flip_completion=armed,wod_gate=wod)])
                with patch.dict(record.__globals__,inspect=lambda *_:native_proof,
                                verify_vdpu_pool_abi=lambda:None,read_vdpu_pool=lambda _:None,
                                inspect_vdpu=read_ring,sample_wod_device_state=read_wod,
                                read_active_raster=short_pixels):
                    with self.assertRaisesRegex(ValueError,'Short native physical read'):
                        record(qmp,dest,symbols)
                self.assertTrue(qmp.running)
                self.assertEqual(ordered,['ring','wod','pixels'])
                row=json.loads((dest/'native-vdpu-armed-flip.jsonl').read_bytes())
                self.assertEqual(row['flip_completion'],armed)
                self.assertEqual(row['wod_gate'],wod)
                self.assertEqual(row['rings'][0]['occupancy'],1)


if __name__=='__main__':
    unittest.main()
