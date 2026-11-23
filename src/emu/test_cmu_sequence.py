
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import sys
import struct
from unittest.mock import patch
import cmu_sequence as sequence


class PublicProducerContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=sequence.vm.ROOT
        cls.source=root/'var/inputs/support/归档/固件与启动研究/pscdc-isa-public--S5E8890-dfs.c'
        cls.header=root/'var/inputs/support/归档/固件与启动研究/pscdc-isa-public--S5E8890-cmusfr.h'
        cls.session=root/'var/inputs/support/归档/固件与启动研究/PSCDC握手复现输入'
        cls.result=sequence.public_pscdc_contract(cls.source,cls.header,cls.session)

    def test_actual_producer_separates_smc_sci_namespaces(self):
        rows=self.result['queue_specific_bindings']
        self.assertEqual(len(rows),7)
        bindings={(row['queue'],row['selector_19_16']):row for row in rows}
        self.assertEqual(bindings[('SMC',2)]['parameter'],'mif_mux_mif_pll')
        self.assertEqual(bindings[('SCI',2)]['parameter'],'ccore_mux_user')
        self.assertEqual(bindings[('SCI',1)]['payload_shift'],0)
        self.assertEqual(len(bindings[('SCI',1)]['source_lines']),2)
        self.assertTrue(self.result['corrections']['selector2_is_queue_specific'])

    def test_10000_is_selector_one_but_not_public_family(self):
        row=self.result['native_queues']['queue']['words'][1]
        self.assertEqual((row['raw'],row['selector_19_16'],row['bit31']),('0x10000',1,0))
        self.assertEqual(row['public_numeric_family_candidates'],[])
        self.assertFalse(row['execution_approved'])

    def test_two_numeric_matches_do_not_explain_ten_words(self):
        self.assertEqual(self.result['native_numeric_family_match_count'],2)
        self.assertEqual(self.result['native_staged_word_count'],10)
        self.assertEqual([row['index'] for row in self.result['native_queues']['queue']['words']
                          if row['public_numeric_family_candidates']],[5,7])
        self.assertFalse(self.result['auto9_execution_approved'])
        self.assertFalse(self.result['complete_native_transaction_explained'])

    def test_public_start_wait_cannot_be_copied_to_271(self):
        self.assertEqual(self.result['public_control']['start_mask'],0x40000000)
        self.assertEqual(self.result['public_control']['wait_mask'],0xc0000000)
        self.assertFalse(self.result['corrections']['native_request_has_public_start_bit'])
        self.assertEqual(self.result['corrections']['public_wait_mask_on_native_request'],0)

    def test_gate_names_are_8890_declarations_not_auto9_routing(self):
        self.assertEqual({row['domain'] for row in self.result['public_ddrphy_gate_declarations']},
                         {'CMU_MIF0','CMU_MIF1','CMU_MIF2','CMU_MIF3'})
        self.assertFalse(self.result['corrections']['auto9_a08_target_verified'])
        self.assertTrue(all(not row['auto9_target_verified'] for queue in
                            self.result['native_queues'].values() for row in queue['words']))

    def test_changed_producer_and_header_rejected(self):
        with tempfile.TemporaryDirectory(dir=sequence.vm.ROOT/'var') as directory:
            changed=Path(directory)/'changed.c'
            for source,header in ((changed,self.header),(self.source,changed)):
                changed.write_bytes(b'changed public input')
                with self.assertRaisesRegex(ValueError,'source identity mismatch'):
                    sequence.public_pscdc_contract(source,header,self.session)

    def altered_native(self, mutation, expected):
        with tempfile.TemporaryDirectory(dir=sequence.vm.ROOT/'var') as directory:
            root=Path(directory)
            state=json.loads((self.session/'cmu-queue-runtime-state.json').read_text(encoding='utf-8'))
            for name in ('queue','mirror'):
                source=root/(name+'.bin')
                source.write_bytes(Path(state[name]['source']).read_bytes())
                state[name]['source']=str(source)
            mutation(root,state)
            (root/'cmu-queue-runtime-state.json').write_text(json.dumps(state),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,expected):
                sequence.public_pscdc_contract(self.source,self.header,root)

    def test_native_readback_digest_rejected_even_if_metadata_updated(self):
        def mutate(root,state):
            source=Path(state['queue']['source'])
            source.write_bytes(b'\x01'+source.read_bytes()[1:])
            state['queue']['sha256']=sequence.vm.sha(source)
        self.altered_native(mutate,'readback identity mismatch')

    def test_native_source_cannot_escape_session(self):
        self.altered_native(lambda root,state:state['queue'].update(
            source=str(self.session/'queue-staged-words.bin')),'outside selected session')

    def test_native_mask_and_request_mismatch_rejected(self):
        self.altered_native(lambda root,state:state.update({'queue-valid-mask':1}),
                            'staging/readback mismatch')
        self.altered_native(lambda root,state:state.update({'last-queue-request':0x40000000}),
                            'start271 capture')


class NativeDdrContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = sequence.vm.ROOT/'var/inputs/support/ufs-oem-init/native-current'
        cls.identity = json.loads((cls.session/'acpm-runtime-state.json').read_text(encoding='utf-8'))
        cls.raw = Path(cls.identity['source']).read_bytes()
        cls.result = sequence.runtime_dmc_contract(cls.session)

    def altered(self, offset, replacement, expected_error, refresh_digest=True):
        with tempfile.TemporaryDirectory(prefix='native-ddr-',dir=sequence.vm.ROOT/'var') as directory:
            root=Path(directory)
            raw=bytearray(self.raw)
            raw[offset:offset+len(replacement)] = replacement
            source=root/'snapshot.bin';source.write_bytes(raw)
            identity={**self.identity,'source':str(source)}
            if refresh_digest:
                identity['sha256']=sequence.vm.sha(source)
            (root/'acpm-runtime-state.json').write_text(json.dumps(identity),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,expected_error):
                sequence.runtime_dmc_contract(root)

    def test_actual_pic_and_two_separate_banks(self):
        self.assertEqual(self.result['got'],'0x24d44')
        first,second=self.result['mmio_tables']
        self.assertEqual((first['table'],second['table']),('0x24be8','0x24c74'))
        self.assertEqual(first['ap_bases'],['0x1b430000','0x1b480000','0x1b630000',
            '0x1b680000','0x1c430000','0x1c480000','0x1c630000','0x1c680000','0x1b290000'])
        self.assertEqual(second['ap_bases'],['0x1b450000','0x1b4a0000','0x1b650000',
            '0x1b6a0000','0x1c450000','0x1c4a0000','0x1c650000','0x1c6a0000','0x1b288000'])

    def test_function_table_lengths_do_not_spill_into_got(self):
        first,second=self.result['dispatch_tables']
        self.assertEqual(len(first['functions']),26)
        self.assertEqual(len(second['functions']),43)
        self.assertEqual(first['functions'][2]['entry'],'0x21d20')
        self.assertEqual(second['functions'][2]['entry'],'0x2327c')
        self.assertEqual(second['functions'][-1]['entry'],'0x23afc')

    def test_training_targets_keep_actual_load_width(self):
        targets=self.result['training_write_targets']
        self.assertEqual(len(targets),80)
        self.assertEqual(len([row for row in targets if row['load_bytes']==4]),4)
        self.assertEqual({row['table'] for row in self.result['training_references']},{'0x2440c'})
        self.assertNotIn('0x0',{row['register_offset'] for row in targets})
        self.assertNotIn('0xa08',{row['register_offset'] for row in targets})
        self.assertIn('not a PSCDC',self.result['scope'])

    def test_changed_original_function_rejected(self):
        self.altered(0x23280,bytes((self.raw[0x23280]^1,)),'function/literal bytes changed')

    def test_changed_source_digest_rejected(self):
        self.altered(0x30000,b'\x01','identity/size mismatch',refresh_digest=False)

    def test_mmio_table_cannot_point_outside_sram(self):
        self.altered(0x24d4c,struct.pack('<I',0x38000),'outside SRAM')

    def test_mmio_bases_cannot_point_outside_bridge(self):
        self.altered(0x24be8,struct.pack('<I',0xc0000000),'outside original AP bridge')

    def test_dispatch_cannot_point_at_non_thumb_or_outside_code(self):
        self.altered(0x24c0c,struct.pack('<I',0x21ee4),'not original Thumb code')
        self.altered(0x24c0c,struct.pack('<I',0x40001),'not original Thumb code')

    def test_changed_original_training_table_rejected(self):
        self.altered(0x24418,bytes((self.raw[0x24418]^1,)),'training offset changed')

    def test_source_must_stay_inside_selected_session(self):
        with tempfile.TemporaryDirectory(prefix='native-ddr-',dir=sequence.vm.ROOT/'var') as directory:
            root=Path(directory)
            (root/'acpm-runtime-state.json').write_text(json.dumps(self.identity),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'outside selected session'):
                sequence.runtime_dmc_contract(root)


class SequenceContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cal_path = sequence.vm.ROOT/'var/inputs/support/归档/固件与启动研究/pscdc-loader--libcal_if.so.1'
        cls.cal_raw = cls.cal_path.read_bytes()
        cls.cal = sequence.qnx_register_contract(cls.cal_path)

    def test_null_sfr_names_are_not_elf_header(self):
        self.assertIsNone(sequence.DynamicCalImage(self.cal_raw).string(0))
        for rows in self.cal['tables'].values():
            self.assertTrue(all(row['name'] is None for row in rows))

    def test_original_dynamic_got_requires_symbol_relocation(self):
        image = sequence.DynamicCalImage(self.cal_raw)
        self.assertEqual(struct.unpack('<Q',image.elf.at(0x8ef98,8))[0],0)
        self.assertNotEqual(image.pointer(0x8ef98),0)
        self.assertEqual(image.relocations[0x8ef98][0]&0xffffffff,1025)
        self.assertEqual(self.cal['counts'],{'blocks':31,'registers':2894,'fields':9386})

    def test_unknown_relocation_and_external_symbol_rejected(self):
        image = sequence.DynamicCalImage(self.cal_raw)
        image.relocations[0x8ef98] = (0xdead,0)
        with self.assertRaisesRegex(ValueError,'Unsupported pointer relocation'):
            image.pointer(0x8ef98)
        image.relocations[0x8ef98] = ((1<<32)|1025,0)
        with patch.object(image,'symbol',return_value={'section':0,'name':'external'}):
            with self.assertRaisesRegex(ValueError,'Unresolved external'):
                image.pointer(0x8ef98)

    def test_dynamic_string_bounds(self):
        image = sequence.DynamicCalImage(self.cal_raw)
        with self.assertRaisesRegex(ValueError,'not file-backed'):
            image.string(0xffffffffffffffff)
        with self.assertRaisesRegex(ValueError,'outside analysis bound'):
            image.symbol(65536)

    def test_named_mif_mux_is_four_way_shift_four(self):
        node = next(node for node in self.cal['clocks'] if node['name']=='CLKMUX_MIF_DDRPHY2X')
        field = node['fields']['member24']
        self.assertEqual((field['physical'],field['shift'],field['width']),(0x1b400600,4,2))
        self.assertEqual(node['parent_names'],['OSCCLK_MIF','CLKCMU_MIF_SWITCH','PLL_MIF_MAIN','PLL_MIF_SUB'])
        self.assertEqual(self.cal['clock_counts'],{'fixed_rate':43,'fixed_factor':6,'pll':16,
                         'mux':199,'divider':145,'gate':1488,'qch':967})

    def test_original_top_parent_order_matches_published_contract(self):
        node = next(node for node in self.cal['clocks'] if node['name']=='MUX_CLKCMU_MIF_SWITCH')
        self.assertEqual(node['parent_names'],['PLL_SHARED0','PLL_SHARED1','PLL_SHARED2',
            'PLL_SHARED4','PLL_SHARED0_DIV2','PLL_SHARED1_DIV2','PLL_SHARED2_DIV2','PLL_SHARED3'])
        self.assertEqual(node['fields']['member24']['physical'],0x1b24109c)

    def test_raw_member_coordinates_preserved_without_role_guess(self):
        node = next(node for node in self.cal['clocks'] if node['name']=='CLKMUX_MIF_DDRPHY2X')
        self.assertEqual(node['fields']['member32']['physical'],0x1b400604)
        self.assertEqual(node['fields']['member32']['shift'],28)
        self.assertEqual(node['fields']['member40']['physical'],0x1b400600)
        self.assertEqual(node['fields']['member40']['shift'],16)
        self.assertNotIn('status',node['fields'])
        self.assertNotIn('enable',node['fields'])

    def test_original_mux_setter_polls_member40_not_member32(self):
        node = next(node for node in self.cal['clocks'] if node['name']=='CLKMUX_MIF_DDRPHY2X')
        access = node['software_access']
        self.assertEqual(access['config_member'],'member24')
        self.assertEqual(access['config_field']['physical'],0x1b400600)
        self.assertEqual(access['clear_mask'],0x30)
        self.assertEqual(access['poll_member'],'member40')
        self.assertEqual(access['poll_field']['physical'],0x1b400600)
        self.assertEqual(access['poll_mask'],0x10000)
        self.assertFalse(access['member32_used_by_this_setter'])
        self.assertEqual(node['fields']['member32']['physical'],0x1b400604)
        self.assertEqual(node['fields']['member32']['shift'],28)

    def test_top_muxes_have_source_bound_write_and_poll_fields(self):
        for name,address in (('MUX_CLKCMU_CORE_BUS',0x1b241020),
                             ('MUX_CLKCMU_MIF_SWITCH',0x1b24109c)):
            node=next(node for node in self.cal['clocks'] if node['name']==name)
            access=node['software_access']
            self.assertEqual(access['config_field']['physical'],address)
            self.assertEqual(access['clear_mask'],7)
            self.assertEqual(access['poll_field']['physical'],address)
            self.assertEqual(access['poll_mask'],0x10000)

    def test_setter_contract_keeps_unmasked_input_and_bounded_sleep(self):
        nodes=[node for node in self.cal['clocks'] if node['kind'] in ('mux','divider')]
        self.assertEqual(len(nodes),344)
        for node in nodes:
            access=node['software_access']
            self.assertEqual(access['setter'],'0x50858')
            self.assertFalse(access['input_value_masked'])
            self.assertEqual((access['sleep_api'],access['sleep_argument_us'],
                              access['maximum_sleep_calls'],access['timeout_return_i32']),
                             ('usleep',1,100,-110))
            self.assertFalse(access['physical_transition_latency_verified'])
            self.assertFalse(access['pscdc_execution_semantics_verified'])

    def test_absent_config_skips_poll_even_if_poll_field_is_declared(self):
        absent=[node['software_access'] for node in self.cal['clocks']
                if 'software_access' in node and node['software_access']['config_field'] is None]
        self.assertEqual(len(absent),46)
        self.assertTrue(any(access['poll_field'] is not None for access in absent))
        for access in absent:
            self.assertFalse(access['write_enabled'])
            self.assertFalse(access['poll_enabled'])
            self.assertIsNone(access['poll_condition'])

    def test_static_table_missing_pscdc_is_not_absence_proof(self):
        self.assertFalse(any(row['offset'] in (0xa08,0x3c00,0x3e00)
                             for row in self.cal['tables']['registers']))
        self.assertTrue(any('No PSCDC' in text for text in self.cal['limitations']))

    def test_changed_qnx_library_rejected(self):
        with tempfile.TemporaryDirectory(prefix='cmu-seq-',dir=sequence.vm.ROOT/'var') as directory:
            path=Path(directory)/'changed.so';path.write_bytes(self.cal_raw[:-1]+bytes((self.cal_raw[-1]^1,)))
            with self.assertRaisesRegex(ValueError,'identity mismatch'):
                sequence.qnx_register_contract(path)

    def archive(self, path, corrupt=False, link=False):
        expected={}
        with tarfile.open(path,'w:gz') as stream:
            for index,name in enumerate(sequence.MEMBERS):
                raw=b'original boot payload '+bytes((index,))
                entry=tarfile.TarInfo('./'+name)
                entry.size=len(raw)
                if link and index==0:
                    entry.type=tarfile.SYMTYPE
                    entry.linkname='/outside'
                stream.addfile(entry,io.BytesIO(raw))
                expected[name]={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
        if corrupt:expected[sequence.MEMBERS[0]]['sha256']='0'*64
        return expected

    def test_restore_only_selected_member_bytes(self):
        with tempfile.TemporaryDirectory(prefix='cmu-seq-',dir=sequence.vm.ROOT/'var') as directory:
            root=Path(directory);expected=self.archive(root/'input.tgz');out=root/'out';out.mkdir()
            rows=sequence.restore_members(root/'input.tgz',out,expected)
            self.assertEqual(len(rows),2)
            self.assertTrue(all(sequence.vm.sha(Path(row['path']))==row['sha256'] for row in rows))

    def test_digest_rejection_writes_no_bad_input(self):
        with tempfile.TemporaryDirectory(prefix='cmu-seq-',dir=sequence.vm.ROOT/'var') as directory:
            root=Path(directory);expected=self.archive(root/'input.tgz',corrupt=True);out=root/'out';out.mkdir()
            with self.assertRaisesRegex(ValueError,'SHA256'):
                sequence.restore_members(root/'input.tgz',out,expected)
            self.assertFalse(list(out.rglob('*')))

    def test_links_rejected(self):
        with tempfile.TemporaryDirectory(prefix='cmu-seq-',dir=sequence.vm.ROOT/'var') as directory:
            root=Path(directory);expected=self.archive(root/'input.tgz',link=True);out=root/'out';out.mkdir()
            with self.assertRaisesRegex(ValueError,'type/size'):
                sequence.restore_members(root/'input.tgz',out,expected)

    def test_existing_restored_input_preserved(self):
        with tempfile.TemporaryDirectory(prefix='cmu-seq-',dir=sequence.vm.ROOT/'var') as directory:
            root=Path(directory);expected=self.archive(root/'input.tgz');out=root/'out';out.mkdir()
            name=out/sequence.MEMBERS[0];name.parent.mkdir();name.write_bytes(b'preserve')
            with self.assertRaisesRegex(ValueError,'Preserve existing'):
                sequence.restore_members(root/'input.tgz',out,expected)
            self.assertEqual(name.read_bytes(),b'preserve')

    def test_native_original_pscdc_labels(self):
        result=sequence.native_log_evidence()
        self.assertEqual([row['text'] for row in result['logs']],['mif0','pscdci','pscdcio'])
        self.assertEqual([row['producer_offset'] for row in result['logs']],['0x1f5bc','0x1f668','0x1f6a6'])

    def test_analysis_view_preserves_raw_payload(self):
        with tempfile.TemporaryDirectory(prefix='cmu-seq-',dir=sequence.vm.ROOT/'var') as directory:
            root=Path(directory);raw=b'\0'*32+bytes.fromhex('c0035fd6')
            path=root/'el3_mon.bin';path.write_bytes(raw)
            result=sequence.inventory_file(path,{'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()},root)
            wrapped=Path(result['analysis_elf']).read_bytes()
            self.assertEqual(wrapped[4096:],raw)
            self.assertEqual(int.from_bytes(wrapped[24:32],'little'),0x20)

    def test_primary_source_parents_and_programmable_fields(self):
        source=sequence.vm.ROOT/'var/inputs/support/cmu-isa/upstream-clk-exynosautov9.c'
        data=sequence.clock_contract(source)
        self.assertEqual(data['parents']['mout_clkcmu_core_bus'],
            ['dout_shared0_div2','dout_shared1_div2','dout_shared2_div2','dout_shared0_div3',
             'dout_shared4_div2','dout_shared1_div3','dout_shared2_div3','fout_shared3_pll'])
        self.assertEqual(data['parents']['mout_clkcmu_mif_switch'],
            ['fout_shared0_pll','fout_shared1_pll','fout_shared2_pll','fout_shared4_pll',
             'dout_shared0_div2','dout_shared1_div2','dout_shared2_div2','fout_shared3_pll'])
        self.assertEqual(data['shared_dividers']['dout_shared0_div2']['width'],1)
        self.assertEqual(data['shared_dividers']['dout_shared0_div3']['width'],2)
        self.assertEqual((data['core_divider']['offset'],data['core_divider']['width']),(0x181c,4))

    def test_changed_source_is_not_silently_reinterpreted(self):
        with tempfile.TemporaryDirectory(prefix='cmu-seq-',dir=sequence.vm.ROOT/'var') as directory:
            source=Path(directory)/'changed.c';source.write_text('different source',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'identity mismatch'):
                sequence.clock_contract(source)


if __name__=='__main__':
    suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(case)
                             for case in (PublicProducerContracts,NativeDdrContracts,SequenceContracts))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
