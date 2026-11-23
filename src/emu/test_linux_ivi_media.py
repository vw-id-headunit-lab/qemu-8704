from io import BytesIO
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import linux_data_overlay
import linux_hmi_overlay
import linux_weston_platform as weston
import ufs_vh_platform


class LinuxIviMediaTests(unittest.TestCase):
    def test_prior_weston_requires_exact_cluster_contents_and_extent_cover(self):
        from contextlib import nullcontext
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            size=2*weston.PRIVATE_CLUSTER
            base=root/'base.raw'
            base.write_bytes(b'x'*size)
            change=weston.Change(14,1,weston.BLOCK,b'x'*weston.BLOCK,b'y'*weston.BLOCK)
            plan=weston.Plan(base,size,'a'*64,(change,))
            prior=dict(early_weston_verity=dict(plan.evidence(),exact_leaf_readback=True))
            info={'cluster-size':weston.PRIVATE_CLUSTER}
            row=dict(start=0,length=weston.PRIVATE_CLUSTER,depth=0,data=True,present=True)
            valid=weston.inherited_cluster_contents(plan)[0]
            self.assertEqual(valid,b'x'*4096+b'y'*4096+b'x'*(65536-8192))
            with self.assertRaisesRegex(ValueError,'crosses its verified clusters'):
                weston.verify_inherited(plan,prior,info,root/'prior',root,[dict(row,length=size)])
            with self.assertRaisesRegex(ValueError,'missing private allocations'):
                weston.verify_inherited(plan,prior,info,root/'prior',root,[])

            class Export:
                flags=2
                def __init__(self,sock):
                    self.size=size
                def command(self,code,offset=0,length=0):
                    return None if code==2 else b'z'+valid[1:]                              

            with patch.object(weston.subprocess,'Popen'), patch.object(linux_hmi_overlay,'NBD',Export), \
                 patch.object(linux_hmi_overlay,'connect',return_value=nullcontext(None)):
                with self.assertRaisesRegex(ValueError,'unapproved bytes'):
                    weston.verify_inherited(plan,prior,info,root/'prior',root,[row])

    def test_classifies_writable_and_private_factory_cow_and_fails_closed(self):
        def row(start, length=4096, depth=0):
            return dict(start=start,length=length,depth=depth,present=True,data=True)
        classify=linux_data_overlay.classify_allocations
        writable={15:(0x1000,0x3000),26:(0x9000,0xb000)}
        factory={18:(0x5000,0x7000),23:(0xd000,0xf000)}
        rows=[row(0xd000),row(0x5000),row(0x1000,8192),row(0x9000),row(0x8000,depth=1)]
        self.assertEqual(classify(rows,writable,factory,0x10000),
                         ({15:8192,26:4096},{18:4096,23:4096}))
        for addition in (row(0x3000),row(0x6000,8192),row(0x2000),row(0xa001)):
            with self.subTest(addition=addition), self.assertRaises(ValueError):
                classify(rows+[addition],writable,factory,0x10000)

    def test_copy_native_vm3_state_requires_matching_backing_and_approved_slots(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            out, previous = root/'var'/'new', root/'var'/'old'
            out.mkdir(parents=True)
            previous.mkdir()
            base = root/'base.raw'
            backing = root/'factory.qcow2'
            backing.write_bytes(b'immutable')
            virtual_size = 1 << 20
            media = dict(overlay=str(backing), virtual_bytes=virtual_size,
                         overlay_sha256=linux_data_overlay.vm.sha(backing))
            source = previous/'ufs-vh1-data-session.qcow2'
            source.write_bytes(b'guest-written')
            (previous/'result.json').write_text('{}')
            (previous/'linux-ivi-data.json').write_text(json.dumps(dict(
                profile=linux_data_overlay.PROFILE,owner='VM3 / UFS VH1',path=str(source),
                parent=str(backing),parent_sha256=media['overlay_sha256'])))
            base.with_suffix('.json').write_text(json.dumps(dict(sector_bytes=4096, partitions=[
                dict(slot=n,label=label,first=n*4,last=n*4+3)
                for n,label in linux_data_overlay.IVI_WRITABLE.items()])))
            info = {'format':'qcow2','virtual-size':virtual_size,'backing-filename-format':'qcow2',
                    'full-backing-filename':str(backing)}
            row = dict(depth=0,present=True,data=True,start=15*16384,length=4096)
            with patch.object(linux_data_overlay,'ROOT',root), patch.object(
                    linux_data_overlay.subprocess,'check_output',side_effect=[
                        json.dumps(info),json.dumps([row,dict(row,start=18*16384)]),
                        json.dumps(info),json.dumps([row])]):
                with self.assertRaisesRegex(ValueError,'immutable or unknown'):
                    linux_data_overlay.reuse(base,media,out,previous)
                self.assertFalse(list(out.iterdir()))
                actual_sha = linux_data_overlay.vm.sha
                with patch.object(linux_data_overlay.vm, 'sha', wraps=actual_sha) as observed_sha:
                    report = linux_data_overlay.reuse(base,media,out,previous)
                calls = [call.args[0] for call in observed_sha.call_args_list]
                self.assertEqual(calls.count(source), 1)
                self.assertIn(out/'ufs-vh1-data-session.qcow2.copying', calls)
                self.assertEqual(report['prior_overlay_sha256'],actual_sha(source))
            self.assertEqual(report['prior_allocated_bytes_by_slot'],{15:4096})
            self.assertEqual(Path(report['path']).read_bytes(),source.read_bytes())
            self.assertEqual(backing.read_bytes(),b'immutable')

            for fail_path, reason in ((source, 'changed during copy'),
                                      (out/'ufs-vh1-data-session.qcow2.copying', 'copy failed verification')):
                with self.subTest(reason=reason), tempfile.TemporaryDirectory(dir=out) as failed:
                    failed = Path(failed)
                    check_path = source if fail_path == source else failed/fail_path.name
                    def fail_digest(path):
                        return '0'*64 if path == check_path else actual_sha(path)

                    with patch.object(linux_data_overlay,'ROOT',root), patch.object(
                            linux_data_overlay.subprocess,'check_output',side_effect=[
                                json.dumps(info),json.dumps([row])]), patch.object(
                            linux_data_overlay.vm,'sha',side_effect=fail_digest), self.assertRaisesRegex(
                            ValueError,reason):
                        linux_data_overlay.reuse(base,media,failed,previous)
                    self.assertEqual(list(failed.iterdir()), [])

    def test_copy_allows_guest_cow_on_hmi_navi_volumes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            out, previous = root/'var'/'new', root/'var'/'old'
            out.mkdir(parents=True)
            previous.mkdir()
            base = root/'base.raw'
            backing = root/'factory.qcow2'
            backing.write_bytes(b'immutable')
            virtual_size = 1 << 20
            partitions=[dict(slot=n,label=label,first=n*4,last=n*4+3)
                        for n,label in linux_data_overlay.IVI_WRITABLE.items()]
            partitions.append(dict(slot=18,label='navi_core',first=200,last=203))
            partitions.append(dict(slot=23,label='vw_hmi_ivi_rbhv-qia',first=204,last=207))
            media = dict(overlay=str(backing), virtual_bytes=virtual_size,
                         overlay_sha256=linux_data_overlay.vm.sha(backing),
                         slot=23, offset=204*4096, length=4*4096, complete_readback=True,
                         additional_partitions=[dict(slot=18,offset=200*4096,length=4*4096,
                                                     complete_readback=True)])
            source = previous/'ufs-vh1-data-session.qcow2'
            source.write_bytes(b'guest-written')
            (previous/'result.json').write_text('{}')
            (previous/'linux-ivi-data.json').write_text(json.dumps(dict(
                profile=linux_data_overlay.PROFILE,owner='VM3 / UFS VH1',path=str(source),
                parent=str(backing),parent_sha256=media['overlay_sha256'])))
            base.with_suffix('.json').write_text(json.dumps(dict(sector_bytes=4096, partitions=partitions)))
            info = {'format':'qcow2','virtual-size':virtual_size,'backing-filename-format':'qcow2',
                    'full-backing-filename':str(backing)}
            rows=[dict(depth=0,present=True,data=True,start=15*16384,length=4096),
                  dict(depth=0,present=True,data=True,start=200*4096,length=4096),
                  dict(depth=0,present=True,data=True,start=204*4096,length=4096)]
            with patch.object(linux_data_overlay,'ROOT',root), patch.object(
                    linux_data_overlay.subprocess,'check_output',side_effect=[
                        json.dumps(info),json.dumps(rows)]):
                report = linux_data_overlay.reuse(base,media,out,previous)
            self.assertEqual(report['prior_allocated_bytes_by_slot'],{15:4096,18:4096,23:4096})
            self.assertEqual(report['prior_factory_media_cow_bytes_by_slot'],{18:4096,23:4096})
            self.assertEqual(Path(report['path']).read_bytes(),source.read_bytes())

    def test_only_expected_slots_and_unpopulated_data(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder)/'ufs-lun0.json'
            rows = [{'slot': 15, 'label': 'userdata_ivi_rbhv-qia', 'first': 1024,
                     'last': 1024+(1<<18)-1},
                    {'slot': 30, 'label': 'lg_custom_ivi_rbhv-qia', 'first': 1<<19,
                     'last': (1<<19)+(1<<17)-1},
                    {'slot': 18, 'label': 'navi_core', 'first': 1<<18,
                     'last': (1<<18)+(1<<16)-1}]
            layout = dict(sector_bytes=4096, partitions=rows)
            manifest.write_text(json.dumps(layout))
            self.assertEqual(linux_data_overlay.geometry(manifest), (1<<22, 1<<30))
            self.assertEqual(linux_hmi_overlay.part_geometry(manifest, 30), (1<<31, 1<<29))
            self.assertEqual(linux_hmi_overlay.part_geometry(manifest, 18), (1<<30, 1<<28))
            rows[0]['payload'] = {'member': 'other'}
            manifest.write_text(json.dumps(layout))
            with self.assertRaises(ValueError):
                linux_data_overlay.geometry(manifest)

    def test_native_read_across_two_media_boundaries(self):
        with tempfile.TemporaryDirectory() as folder:
            base, hmi, custom = (Path(folder)/n for n in ('base', 'hmi', 'custom'))
            base.write_bytes(b'0123456789abcdef')
            hmi.write_bytes(b'ABC')
            custom.write_bytes(b'XYZ')
            layout = dict(offset=3, length=3, volume=hmi, virtual_bytes=16,
                          additional_partitions=[dict(offset=9, length=3, volume=custom)])
            read = linux_hmi_overlay.expected_reader(base, layout)
            self.assertEqual(read(2, 12), b'2ABC678XYZcd')
            self.assertEqual(read(16, 0), b'')
            with self.assertRaises(ValueError):
                read(15, 2)
            layout['additional_partitions'][0]['offset'] = 4
            with self.assertRaises(ValueError):
                linux_hmi_overlay.expected_reader(base, layout)

    def test_native_read_orders_navi_before_primary_hmi(self):
        with tempfile.TemporaryDirectory() as folder:
            base, hmi, navi, custom = (Path(folder)/n for n in ('base', 'hmi', 'navi', 'custom'))
            base.write_bytes(b'0123456789abcdef')
            hmi.write_bytes(b'ABC')
            navi.write_bytes(b'NV')
            custom.write_bytes(b'XY')
            layout = dict(slot=23, offset=5, length=3, volume=hmi, virtual_bytes=16,
                          additional_partitions=[dict(slot=18, offset=2, length=2, volume=navi),
                                                 dict(slot=30, offset=9, length=2, volume=custom)])
            self.assertEqual(linux_hmi_overlay.expected_reader(base, layout)(1, 11), b'1NV4ABC8XYb')

    def test_stream_only_exact_factory_tar_member(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root/'cpu.tgz'
            with tarfile.open(archive, 'w:gz') as tar:
                for name, data in [('ivi/other', b'other'), ('./ivi/navi', b'expected')]:
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    tar.addfile(member, BytesIO(data))
            with linux_hmi_overlay.factory_tar_member(archive, 'ivi/navi', 8) as src:
                self.assertEqual(src.read(), b'expected')
            self.assertEqual(list(root.iterdir()), [archive])
            with self.assertRaises(ValueError):
                with linux_hmi_overlay.factory_tar_member(archive, 'ivi/navi', 7):
                    self.fail('Wrong-size archive member was accepted')
            with self.assertRaises(FileNotFoundError):
                with linux_hmi_overlay.factory_tar_member(archive, 'ivi/not-found', 8):
                    self.fail('Missing archive member was accepted')

    def test_vm3_vm4_blocks_have_different_writable_top_nodes(self):
        args = ufs_vh_platform.devices('android.qcow2', 'media.qcow2', 'linux.qcow2')
        rows = [json.loads(args[i+1]) for i,x in enumerate(args) if x == '-blockdev']
        nodes = {row['node-name']: row for row in rows}
        self.assertEqual(nodes['ufs-vh1-private-lun0']['backing'], 'ufs-vh1-ro-lun0')
        self.assertEqual(nodes['ufs-vh2-private-lun0']['backing'], 'ufs-base')
        self.assertTrue(nodes['ufs-vh1-ro-lun0']['read-only'])
        self.assertTrue(nodes['ufs-vh1-hmi-file']['read-only'])
        self.assertIn('ufs-lu,drive=ufs-vh1-private-lun0,bus=auto9-ufs-vh1-luns,lun=0', args)
        with self.assertRaises(ValueError):
            ufs_vh_platform.devices(linux_data='linux.qcow2')

    def test_vw_install_media_keeps_the_verified_original_in_the_live_chain(self):
        from unittest.mock import Mock
        vh=ufs_vh_platform
        args=vh.devices('android.qcow2','install.qcow2','linux.qcow2','media.qcow2')
        nodes={row['node-name']:row for row in
               (json.loads(args[i+1]) for i,x in enumerate(args) if x=='-blockdev')}
        self.assertEqual(nodes[vh.HMI_PREDECESSOR]['backing'],'ufs-base')
        self.assertEqual(nodes[vh.node_name(1)]['backing'],vh.HMI_PREDECESSOR)
        self.assertEqual(nodes[vh.node_name(1,linux_data=True)]['backing'],vh.node_name(1))
        self.assertEqual(nodes[vh.node_name(2,android_overlay=True)]['backing'],'ufs-base')
        depth={vh.HMI_PREDECESSOR:1,vh.node_name(1):2,vh.node_name(1,linux_data=True):3}
        rows=[dict(row,drv=row['driver'],ro=row.get('read-only',False),
                   backing_file_depth=depth.get(name,0)) for name,row in nodes.items()
              if name!='ufs-vh2-private-file']
        rows.extend([{'node-name':name,'ro':True} for name in ('ufs-base','ufs-base-file')])
        qmp=Mock()
        qmp.execute.side_effect=lambda method,*args: rows if method=='query-named-block-nodes' else None
        self.assertIn(vh.HMI_PREDECESSOR,vh.enable(qmp,True,True,True,True))
        next(row for row in rows if row['node-name']==vh.node_name(1))['backing_file_depth']=1
        with self.assertRaisesRegex(ValueError,'missing the immutable VW predecessor'):
            vh.enable(qmp,True,True,True,True)
        with self.assertRaises(ValueError):
            vh.devices(linux_hmi_predecessor='unattached.qcow2')


if __name__ == '__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(LinuxIviMediaTests))
    raise SystemExit(0 if result.wasSuccessful() else 1)
